"""Durable AVD worker supervision and adaptive pool control."""
from __future__ import annotations

from dataclasses import dataclass
import time

from core.db import now

from .models import AccountStage
from .resource_policy import CapacityState, classify_capacity, next_worker_target


@dataclass(frozen=True)
class SupervisorDecision:
    action: str
    capacity: CapacityState
    target_workers: int
    worker_id: str | None = None
    avd_name: str | None = None


_ACTIVE_STATES = ("STARTING", "READY", "RUNNING", "WAITING_HUMAN", "RECOVERING", "DRAINING")


class WorkerSupervisor:
    def __init__(
        self,
        repository,
        avd_manager,
        metrics_sampler,
        *,
        stability_seconds: int = 45,
        max_starting: int = 1,
        clock=time.monotonic,
    ):
        self.repo = repository
        self.avd = avd_manager
        self.metrics = metrics_sampler
        self.stability_seconds = max(0, int(stability_seconds))
        self.max_starting = max(1, min(2, int(max_starting)))
        self.clock = clock
        self._last_capacity = None
        self._stable_since = None

    def _workers(self):
        placeholders = ",".join("?" for _ in _ACTIVE_STATES)
        return self.repo.conn.execute(
            f"SELECT * FROM factory_worker WHERE state IN ({placeholders}) ORDER BY id",
            _ACTIVE_STATES,
        ).fetchall()

    def _is_stable(self, capacity: CapacityState) -> bool:
        current = self.clock()
        if capacity != self._last_capacity:
            self._last_capacity = capacity
            self._stable_since = current
        return self.stability_seconds == 0 or (
            self._stable_since is not None and current - self._stable_since >= self.stability_seconds
        )

    def _persist_sample(self, sample, capacity, workers, target):
        self.repo.insert_resource_sample({
            "timestamp": now(),
            "cpu_percent": sample.cpu_percent,
            "ram_total_mb": int(getattr(self.metrics, "ram_total_mb", 0) or 0),
            "ram_available_mb": sample.ram_available_mb,
            "swap_used_mb": sample.swap_used_mb,
            "swap_in_rate": sample.swap_in_rate,
            "load_1m": sample.load_1m,
            "load_5m": sample.load_5m,
            "avd_total": len(workers),
            "avd_running": sum(w["state"] == "RUNNING" for w in workers),
            "avd_waiting_human": sum(w["state"] == "WAITING_HUMAN" for w in workers),
            "capacity_state": capacity.value,
            "desired_workers": target,
        })

    def _promote_booted_starting_workers(self) -> None:
        online = set(self.avd.list_online_devices())
        workers = self.repo.conn.execute(
            "SELECT * FROM factory_worker WHERE state='STARTING' ORDER BY id"
        ).fetchall()
        for worker in workers:
            serial = worker["adb_serial"]
            if serial and serial in online and self.avd.is_boot_completed(serial):
                self.repo.conn.execute(
                    "UPDATE factory_worker SET state='READY', last_progress_at=? WHERE id=?",
                    (now(), worker["id"]),
                )

    def tick(self) -> SupervisorDecision:
        self._promote_booted_starting_workers()
        sample = self.metrics.sample()
        capacity = classify_capacity(sample)
        workers = self._workers()
        waiting = sum(w["state"] == "WAITING_HUMAN" for w in workers)
        ram_values = [w["estimated_ram_mb"] for w in workers if w["estimated_ram_mb"]]
        learned_ram = int(sum(ram_values) / len(ram_values)) if ram_values else 2048
        target = next_worker_target(len(workers), waiting, capacity, learned_ram)
        stable = self._is_stable(capacity)
        self._persist_sample(sample, capacity, workers, target)
        if not stable:
            return SupervisorDecision("HOLD", capacity, len(workers))

        if target > len(workers):
            if sum(w["state"] == "STARTING" for w in workers) >= self.max_starting:
                return SupervisorDecision("HOLD", capacity, len(workers))
            return self._start_one(capacity, target, workers)
        if target < len(workers):
            return self._drain_one(capacity, target, workers)
        return SupervisorDecision("HOLD", capacity, target)

    def _start_one(self, capacity, target, workers):
        active_names = {w["avd_name"] for w in workers}
        configured_avds = sorted(self.avd.list_avds())
        candidates = [name for name in configured_avds if name.startswith("acp-worker-") and name not in active_names]
        if not candidates:
            return SupervisorDecision("HOLD", capacity, len(workers))
        avd_name = candidates[0]
        existing = self.repo.conn.execute("SELECT * FROM factory_worker WHERE avd_name=?", (avd_name,)).fetchone()
        worker_id = existing["id"] if existing else f"worker:{avd_name}"
        worker_index = configured_avds.index(avd_name)
        port = 5554 + worker_index * 2
        adb_serial = f"emulator-{port}"
        intent_at = now()
        self.repo.conn.execute(
            """INSERT INTO factory_worker
               (id,avd_name,adb_serial,state,started_at,last_error,draining)
               VALUES (?,?,?, 'STARTING', ?, NULL, 0)
               ON CONFLICT(avd_name) DO UPDATE SET
                   adb_serial=excluded.adb_serial,
                   state='STARTING',
                   started_at=excluded.started_at,
                   last_error=NULL,
                   draining=0""",
            (worker_id, avd_name, adb_serial, intent_at),
        )
        try:
            process = self.avd.start(avd_name, port)
            self.repo.conn.execute("UPDATE factory_worker SET pid=? WHERE id=?", (getattr(process, "pid", None), worker_id))
        except Exception as exc:
            self.record_boot_failure(worker_id, str(exc))
            return SupervisorDecision("START_FAILED", capacity, len(workers), worker_id, avd_name)
        return SupervisorDecision("START", capacity, target, worker_id, avd_name)

    def _drain_one(self, capacity, target, workers):
        by_priority = {
            "READY": 0,
            "RECOVERING": 1,
            "DRAINING": 2,
            "RUNNING": 3,
            "STARTING": 4,
            "WAITING_HUMAN": 99,
        }
        candidates = sorted(workers, key=lambda w: (by_priority.get(w["state"], 50), w["id"]))
        candidate = next((w for w in candidates if w["state"] != "WAITING_HUMAN"), None)
        if candidate is None:
            return SupervisorDecision("HOLD", capacity, len(workers))
        worker_id = candidate["id"]
        if candidate["state"] == "RUNNING":
            self.repo.conn.execute("UPDATE factory_worker SET state='DRAINING', draining=1 WHERE id=?", (worker_id,))
            return SupervisorDecision("DRAIN_PENDING", capacity, target, worker_id, candidate["avd_name"])

        self.repo.conn.execute("UPDATE factory_worker SET state='DRAINING', draining=1 WHERE id=?", (worker_id,))
        try:
            if candidate["adb_serial"]:
                self.avd.stop(candidate["adb_serial"])
            self.repo.conn.execute(
                """UPDATE factory_worker
                   SET state='STOPPED', draining=0, current_account_id=NULL, current_job_id=NULL
                   WHERE id=?""",
                (worker_id,),
            )
        except Exception as exc:
            self.repo.conn.execute(
                "UPDATE factory_worker SET state='ERROR', last_error=? WHERE id=?",
                (str(exc)[:500], worker_id),
            )
            return SupervisorDecision("DRAIN_FAILED", capacity, len(workers), worker_id, candidate["avd_name"])
        return SupervisorDecision("DRAIN", capacity, target, worker_id, candidate["avd_name"])

    def reconcile_missing_heartbeat(self, worker_id: str) -> None:
        worker = self.repo.get_worker(worker_id)
        if worker is None:
            return
        self.repo.conn.execute(
            """UPDATE factory_worker
               SET state='RECOVERING', recovery_count=recovery_count+1,
                   last_error='worker heartbeat missing'
               WHERE id=?""",
            (worker_id,),
        )

    def record_boot_failure(self, worker_id: str, error: str) -> None:
        worker = self.repo.get_worker(worker_id)
        if worker is None:
            return
        attempts = int(worker["recovery_count"] or 0) + 1
        state = "ERROR" if attempts >= 3 else "RECOVERING"
        self.repo.conn.execute(
            "UPDATE factory_worker SET recovery_count=?, state=?, last_error=? WHERE id=?",
            (attempts, state, str(error)[:500], worker_id),
        )

    def reconcile_on_boot(self) -> None:
        online = set(self.avd.list_online_devices())
        workers = self.repo.conn.execute("SELECT * FROM factory_worker ORDER BY id").fetchall()
        for worker in workers:
            if worker["state"] == "WAITING_HUMAN" and worker["current_account_id"]:
                account = self.repo.get_account(worker["current_account_id"])
                if account and account["stage"] == "WAITING_HUMAN":
                    try:
                        from .service import FactoryService
                        FactoryService(self.repo).transition_account(
                            account["id"],
                            AccountStage.NEEDS_CONFIRMATION,
                            error_code="WORKER_TIMEOUT",
                            error_message="Controller restarted during human checkpoint",
                        )
                    except ValueError:
                        pass
            serial = worker["adb_serial"]
            if serial and serial in online and self.avd.is_boot_completed(serial):
                state = "RECOVERING" if worker["current_account_id"] else "READY"
                self.repo.conn.execute("UPDATE factory_worker SET state=? WHERE id=?", (state, worker["id"]))
            elif worker["state"] in _ACTIVE_STATES:
                self.repo.conn.execute("UPDATE factory_worker SET state='RECOVERING' WHERE id=?", (worker["id"],))