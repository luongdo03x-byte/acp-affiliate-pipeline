"""Durable AVD worker supervision and adaptive pool control.

Only REMOTE_AVD runners belong to this supervisor. LOCAL_DEVICE runners are
owned by the Android app heartbeat/command channel and must never be started,
stopped, or marked missing based on ADB state.
"""
from __future__ import annotations

from dataclasses import dataclass
import time

from core.db import now

from .models import AccountStage, RunnerType
from .resource_policy import (
    CapacityState,
    DEFAULT_THRESHOLDS,
    classify_capacity,
    next_worker_target,
)


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
        worker_processes=None,
        stability_seconds: int = 45,
        max_starting: int = 1,
        clock=time.monotonic,
    ):
        self.repo = repository
        self.avd = avd_manager
        self.metrics = metrics_sampler
        self.worker_processes = worker_processes
        self.stability_seconds = max(0, int(stability_seconds))
        self.max_starting = max(1, min(2, int(max_starting)))
        self.clock = clock
        self._last_capacity = None
        self._stable_since = None

    def _workers(self):
        placeholders = ",".join("?" for _ in _ACTIVE_STATES)
        return self.repo.conn.execute(
            f"""SELECT * FROM factory_worker
                WHERE runner_type=? AND state IN ({placeholders})
                ORDER BY id""",
            (RunnerType.REMOTE_AVD.value, *_ACTIVE_STATES),
        ).fetchall()

    def _is_stable(self, capacity: CapacityState) -> bool:
        current = self.clock()
        if capacity != self._last_capacity:
            self._last_capacity = capacity
            self._stable_since = current
        return self.stability_seconds == 0 or (
            self._stable_since is not None and current - self._stable_since >= self.stability_seconds
        )

    def _is_social_only_batch(self) -> bool:
        batch = self.repo.latest_batch()
        return batch is not None and str(batch.get("completion_mode") or "").upper() == "SOCIAL_ONLY"

    def _limit_target_for_batch(self, target: int) -> int:
        if self._is_social_only_batch():
            return min(int(target), 1)
        return int(target)

    def _apply_social_only_worker_floor(self, sample, target: int) -> int:
        target = int(target)
        if not self._is_social_only_batch():
            return target
        thresholds = DEFAULT_THRESHOLDS
        if sample.cpu_percent >= thresholds.green_cpu_max:
            return target
        if sample.ram_available_mb <= thresholds.green_ram_min_mb:
            return target
        return max(target, 1)

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
            """SELECT * FROM factory_worker
               WHERE runner_type='REMOTE_AVD' AND state='STARTING'
               ORDER BY id"""
        ).fetchall()
        for worker in workers:
            serial = worker["adb_serial"]
            if serial and serial in online and self.avd.is_boot_completed(serial):
                self.repo.conn.execute(
                    "UPDATE factory_worker SET state='READY', last_progress_at=? WHERE id=?",
                    (now(), worker["id"]),
                )

    def _sync_worker_agents(self) -> set[str]:
        freshly_attached: set[str] = set()
        if self.worker_processes is None:
            return freshly_attached
        online = set(self.avd.list_online_devices())
        workers = self.repo.conn.execute(
            """SELECT * FROM factory_worker
               WHERE runner_type='REMOTE_AVD'
                 AND state IN ('READY','RUNNING','WAITING_HUMAN','RECOVERING')
               ORDER BY id"""
        ).fetchall()
        for worker in workers:
            if worker["state"] == "RECOVERING" and worker["last_error"] == "manual restart requested":
                continue
            worker_id = worker["id"]
            serial = worker["adb_serial"]
            if not serial or serial not in online:
                if (
                    worker["state"] == "RECOVERING"
                    and not worker["current_job_id"]
                    and not worker["current_account_id"]
                ):
                    self.worker_processes.stop(worker_id)
                    self.repo.conn.execute(
                        """UPDATE factory_worker
                           SET state='STOPPED', pid=NULL, draining=0
                           WHERE id=?""",
                        (worker_id,),
                    )
                continue
            if not self.avd.is_boot_completed(serial):
                continue
            started_here = False
            try:
                if not self.worker_processes.is_running(worker_id):
                    self.worker_processes.start(worker_id, worker["avd_name"], serial)
                    started_here = True
                heartbeat = self.worker_processes.heartbeat(worker_id)
                if heartbeat.get("worker_id") != worker_id or heartbeat.get("adb_serial") != serial:
                    raise RuntimeError("worker heartbeat identity mismatch")
                progress = heartbeat.get("last_progress_at") or now()
                recovered_state = worker["state"]
                if (
                    worker["state"] == "RECOVERING"
                    and not worker["current_job_id"]
                    and not worker["current_account_id"]
                ):
                    recovered_state = "READY"
                self.repo.conn.execute(
                    """UPDATE factory_worker
                       SET state=?, last_heartbeat_at=?, last_progress_at=?, last_error=NULL
                       WHERE id=?""",
                    (recovered_state, now(), progress, worker_id),
                )
                if started_here:
                    freshly_attached.add(worker_id)
            except Exception:
                self.reconcile_missing_heartbeat(worker_id)
        return freshly_attached

    def _stop_persisted_worker(self, worker, *, action: str, capacity, target) -> SupervisorDecision:
        worker_id = worker["id"]
        self.repo.conn.execute(
            "UPDATE factory_worker SET state='DRAINING', draining=1 WHERE id=?",
            (worker_id,),
        )
        try:
            if self.worker_processes is not None:
                self.worker_processes.stop(worker_id)
            if worker["adb_serial"]:
                self.avd.stop(worker["adb_serial"])
            self.repo.conn.execute(
                """UPDATE factory_worker
                   SET state='STOPPED', draining=0, current_account_id=NULL,
                       current_job_id=NULL, pid=NULL
                   WHERE id=?""",
                (worker_id,),
            )
        except Exception as exc:
            self.repo.conn.execute(
                "UPDATE factory_worker SET state='ERROR', last_error=? WHERE id=?",
                (str(exc)[:500], worker_id),
            )
            return SupervisorDecision(
                f"{action}_FAILED", capacity, len(self._workers()), worker_id, worker["avd_name"]
            )
        return SupervisorDecision(action, capacity, target, worker_id, worker["avd_name"])

    def _process_operator_intent(self, capacity, target, workers) -> SupervisorDecision | None:
        restart = next((
            w for w in workers
            if w["state"] == "RECOVERING"
            and w["last_error"] == "manual restart requested"
            and not w["current_job_id"]
            and not w["current_account_id"]
        ), None)
        if restart is not None:
            return self._stop_persisted_worker(
                restart, action="RESTART_STOP", capacity=capacity, target=target
            )

        drain = next((
            w for w in workers
            if w["draining"]
            and not w["current_job_id"]
            and not w["current_account_id"]
            and w["state"] != "WAITING_HUMAN"
        ), None)
        if drain is not None:
            return self._stop_persisted_worker(
                drain, action="DRAIN", capacity=capacity, target=target
            )
        return None

    def tick(self) -> SupervisorDecision:
        self._promote_booted_starting_workers()
        freshly_attached = self._sync_worker_agents()
        sample = self.metrics.sample()
        capacity = classify_capacity(sample)
        workers = self._workers()
        waiting = sum(w["state"] == "WAITING_HUMAN" for w in workers)
        ram_values = [w["estimated_ram_mb"] for w in workers if w["estimated_ram_mb"]]
        learned_ram = int(sum(ram_values) / len(ram_values)) if ram_values else 2048
        target = next_worker_target(len(workers), waiting, capacity, learned_ram)
        target = self._limit_target_for_batch(target)
        target = self._apply_social_only_worker_floor(sample, target)
        stable = self._is_stable(capacity)
        self._persist_sample(sample, capacity, workers, target)

        operator_decision = self._process_operator_intent(capacity, target, workers)
        if operator_decision is not None:
            return operator_decision

        if not stable:
            return SupervisorDecision("HOLD", capacity, len(workers))

        if target > len(workers):
            if sum(w["state"] == "STARTING" for w in workers) >= self.max_starting:
                return SupervisorDecision("HOLD", capacity, len(workers))
            return self._start_one(capacity, target, workers)
        if target < len(workers):
            return self._drain_one(
                capacity,
                target,
                workers,
                protected_worker_ids=freshly_attached,
            )
        return SupervisorDecision("HOLD", capacity, target)

    def _start_one(self, capacity, target, workers):
        active_names = {w["avd_name"] for w in workers if w["avd_name"]}
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
               (id,runner_type,avd_name,adb_serial,state,started_at,last_error,draining)
               VALUES (?, 'REMOTE_AVD', ?, ?, 'STARTING', ?, NULL, 0)
               ON CONFLICT(avd_name) DO UPDATE SET
                   runner_type='REMOTE_AVD',
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

    def _drain_one(self, capacity, target, workers, *, protected_worker_ids=()):
        protected = set(protected_worker_ids)
        by_priority = {
            "READY": 0,
            "RECOVERING": 1,
            "DRAINING": 2,
            "RUNNING": 3,
            "STARTING": 4,
            "WAITING_HUMAN": 99,
        }
        candidates = sorted(workers, key=lambda w: (by_priority.get(w["state"], 50), w["id"]))
        candidate = next((
            w for w in candidates
            if w["id"] not in protected
            and w["state"] != "WAITING_HUMAN"
            and not w["current_job_id"]
            and not w["current_account_id"]
        ), None)
        if candidate is None:
            return SupervisorDecision("HOLD", capacity, len(workers))
        worker_id = candidate["id"]
        if candidate["state"] == "RUNNING":
            self.repo.conn.execute("UPDATE factory_worker SET state='DRAINING', draining=1 WHERE id=?", (worker_id,))
            return SupervisorDecision("DRAIN_PENDING", capacity, target, worker_id, candidate["avd_name"])

        return self._stop_persisted_worker(
            candidate, action="DRAIN", capacity=capacity, target=target
        )

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
        workers = self.repo.conn.execute(
            "SELECT * FROM factory_worker WHERE runner_type='REMOTE_AVD' ORDER BY id"
        ).fetchall()
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
