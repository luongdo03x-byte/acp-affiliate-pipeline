import sqlite3
import unittest

from core.factory_v2.repository import FactoryRepository
from core.factory_v2.resource_policy import HostSample
from core.factory_v2.schema import ensure_schema
from core.factory_v2.supervisor import WorkerSupervisor
from core.factory_v2.worker_protocol import CommandLedger


class WorkerProtocolTests(unittest.TestCase):
    def test_duplicate_command_id_returns_stored_result_without_rerun(self):
        ledger = CommandLedger(max_entries=10)
        calls = []

        def action():
            calls.append("ran")
            return {"ok": True, "value": 7}

        first = ledger.execute("cmd-1", action)
        second = ledger.execute("cmd-1", action)
        self.assertEqual(first, second)
        self.assertEqual(["ran"], calls)


class FakeMetrics:
    def __init__(self, sample):
        self.value = sample

    def sample(self):
        return self.value


class FakeAvd:
    def __init__(self):
        self.stopped = []
        self.started = []
        self.online = []
        self.booted = set()

    def stop(self, serial):
        self.stopped.append(serial)

    def list_avds(self):
        return ["acp-worker-01", "acp-worker-02", "acp-worker-03"]

    def list_online_devices(self):
        return list(self.online)

    def start(self, avd_name, port):
        self.started.append((avd_name, port))
        return type("P", (), {"pid": 4321})()

    def is_boot_completed(self, serial):
        return serial in self.booted


class FakeWorkerProcesses:
    def __init__(self):
        self.started = []
        self.stopped = []
        self.running = set()
        self.heartbeats = []

    def is_running(self, worker_id):
        return worker_id in self.running

    def start(self, worker_id, avd_name, serial):
        self.started.append((worker_id, avd_name, serial))
        self.running.add(worker_id)
        return type("P", (), {"pid": 9876})()

    def heartbeat(self, worker_id):
        self.heartbeats.append(worker_id)
        return {
            "worker_id": worker_id,
            "adb_serial": "emulator-5554",
            "state": "READY",
            "current_account_id": None,
            "current_job_id": None,
            "observed_state": None,
            "last_progress_at": "2026-08-17T06:00:00+00:00",
        }

    def stop(self, worker_id):
        self.stopped.append(worker_id)
        self.running.discard(worker_id)


class FactoryV2SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.avd = FakeAvd()
        self.worker_processes = FakeWorkerProcesses()

    def tearDown(self):
        self.conn.close()

    def _supervisor(self, sample):
        return WorkerSupervisor(
            self.repo,
            self.avd,
            FakeMetrics(sample),
            worker_processes=self.worker_processes,
            stability_seconds=0,
        )

    def test_missing_heartbeat_moves_worker_to_recovering_not_ready(self):
        self.repo.insert_worker({
            "id": "worker-03", "avd_name": "acp-worker-03", "state": "RUNNING",
            "current_account_id": "a17",
        })
        supervisor = self._supervisor(HostSample(40, 8192, 0, 0, 1, 1))
        supervisor.reconcile_missing_heartbeat("worker-03")
        worker = self.repo.get_worker("worker-03")
        self.assertEqual("RECOVERING", worker["state"])
        self.assertEqual("a17", worker["current_account_id"])

    def test_red_drains_ready_worker_before_waiting_human(self):
        self.repo.insert_worker({"id": "ready", "avd_name": "acp-worker-01", "adb_serial": "emulator-5554", "state": "READY"})
        self.repo.insert_worker({"id": "human", "avd_name": "acp-worker-02", "adb_serial": "emulator-5556", "state": "WAITING_HUMAN"})
        decision = self._supervisor(HostSample(90, 2500, 0, 0, 4, 4)).tick()
        self.assertEqual("DRAIN", decision.action)
        self.assertEqual("ready", decision.worker_id)
        self.assertEqual("STOPPED", self.repo.get_worker("ready")["state"])
        self.assertEqual("WAITING_HUMAN", self.repo.get_worker("human")["state"])

    def test_red_does_not_drain_recovering_worker_with_active_job(self):
        self.repo.insert_worker({
            "id": "active-recovering",
            "avd_name": "acp-worker-01",
            "adb_serial": "emulator-5554",
            "state": "RECOVERING",
            "current_account_id": "account-1",
            "current_job_id": "job-1",
        })

        decision = self._supervisor(HostSample(90, 2500, 0, 0, 4, 4)).tick()

        self.assertEqual("HOLD", decision.action)
        self.assertEqual([], self.avd.stopped)
        self.assertEqual([], self.worker_processes.stopped)
        worker = self.repo.get_worker("active-recovering")
        self.assertEqual("RECOVERING", worker["state"])
        self.assertEqual("job-1", worker["current_job_id"])
        self.assertEqual("account-1", worker["current_account_id"])

    def test_red_does_not_drain_ready_worker_on_same_tick_agent_is_attached(self):
        self.repo.insert_worker({
            "id": "fresh-ready",
            "avd_name": "acp-worker-01",
            "adb_serial": "emulator-5554",
            "state": "READY",
        })
        self.avd.online = ["emulator-5554"]
        self.avd.booted.add("emulator-5554")
        supervisor = self._supervisor(HostSample(20, 2200, 0, 1, 1, 1))

        decision = supervisor.tick()

        self.assertEqual("HOLD", decision.action)
        self.assertEqual(
            [("fresh-ready", "acp-worker-01", "emulator-5554")],
            self.worker_processes.started,
        )
        self.assertEqual([], self.worker_processes.stopped)
        self.assertEqual([], self.avd.stopped)
        self.assertEqual("READY", self.repo.get_worker("fresh-ready")["state"])

    def test_emergency_preserves_waiting_human_when_ready_can_drain(self):
        self.repo.insert_worker({"id": "ready", "avd_name": "acp-worker-01", "adb_serial": "emulator-5554", "state": "READY"})
        self.repo.insert_worker({"id": "human", "avd_name": "acp-worker-02", "adb_serial": "emulator-5556", "state": "WAITING_HUMAN"})
        decision = self._supervisor(HostSample(40, 1200, 0, 0, 1, 1)).tick()
        self.assertEqual("DRAIN", decision.action)
        self.assertEqual("ready", decision.worker_id)
        self.assertEqual("WAITING_HUMAN", self.repo.get_worker("human")["state"])

    def test_boot_retry_stops_after_three_attempts(self):
        self.repo.insert_worker({"id": "boot", "avd_name": "acp-worker-03", "state": "STARTING"})
        supervisor = self._supervisor(HostSample(40, 8192, 0, 0, 1, 1))
        supervisor.record_boot_failure("boot", "boot failed")
        supervisor.record_boot_failure("boot", "boot failed")
        supervisor.record_boot_failure("boot", "boot failed")
        worker = self.repo.get_worker("boot")
        self.assertEqual(3, worker["recovery_count"])
        self.assertEqual("ERROR", worker["state"])

    def test_started_avd_records_serial_reaches_ready_and_heartbeats_worker_agent(self):
        supervisor = self._supervisor(HostSample(40, 8192, 0, 0, 1, 1))

        decision = supervisor.tick()

        self.assertEqual("START", decision.action)
        worker = self.repo.get_worker(decision.worker_id)
        self.assertEqual("emulator-5554", worker["adb_serial"])
        self.assertEqual("STARTING", worker["state"])

        self.avd.online = ["emulator-5554"]
        self.avd.booted.add("emulator-5554")
        supervisor.tick()

        worker = self.repo.get_worker(decision.worker_id)
        self.assertEqual("READY", worker["state"])
        self.assertEqual(
            [(decision.worker_id, "acp-worker-01", "emulator-5554")],
            self.worker_processes.started,
        )
        self.assertEqual([decision.worker_id], self.worker_processes.heartbeats)
        self.assertIsNotNone(worker["last_heartbeat_at"])
        self.assertEqual("2026-08-17T06:00:00+00:00", worker["last_progress_at"])

    def test_recovering_online_worker_relaunches_agent_and_returns_ready_when_idle(self):
        self.repo.insert_worker({
            "id": "worker-01",
            "avd_name": "acp-worker-01",
            "adb_serial": "emulator-5554",
            "state": "RECOVERING",
            "last_error": "worker heartbeat missing",
        })
        self.avd.online = ["emulator-5554"]
        self.avd.booted.add("emulator-5554")
        supervisor = self._supervisor(HostSample(55, 5000, 0, 0, 1, 1))

        supervisor.tick()

        worker = self.repo.get_worker("worker-01")
        self.assertEqual("READY", worker["state"])
        self.assertEqual(
            [("worker-01", "acp-worker-01", "emulator-5554")],
            self.worker_processes.started,
        )
        self.assertIsNotNone(worker["last_heartbeat_at"])

    def test_manual_drain_intent_stops_idle_worker_before_green_scale_up(self):
        self.repo.insert_worker({
            "id": "drain-me",
            "avd_name": "acp-worker-01",
            "adb_serial": "emulator-5554",
            "state": "DRAINING",
            "draining": 1,
        })
        self.worker_processes.running.add("drain-me")
        supervisor = self._supervisor(HostSample(40, 8192, 0, 0, 1, 1))

        decision = supervisor.tick()

        self.assertEqual("DRAIN", decision.action)
        self.assertEqual("drain-me", decision.worker_id)
        self.assertEqual(["drain-me"], self.worker_processes.stopped)
        self.assertEqual(["emulator-5554"], self.avd.stopped)
        self.assertEqual([], self.avd.started)
        self.assertEqual("STOPPED", self.repo.get_worker("drain-me")["state"])

    def test_manual_restart_intent_stops_first_and_restarts_on_next_tick(self):
        self.repo.insert_worker({
            "id": "restart-me",
            "avd_name": "acp-worker-01",
            "adb_serial": "emulator-5554",
            "state": "RECOVERING",
            "last_error": "manual restart requested",
        })
        self.worker_processes.running.add("restart-me")
        supervisor = self._supervisor(HostSample(40, 8192, 0, 0, 1, 1))

        stop_decision = supervisor.tick()

        self.assertEqual("RESTART_STOP", stop_decision.action)
        self.assertEqual(["restart-me"], self.worker_processes.stopped)
        self.assertEqual(["emulator-5554"], self.avd.stopped)
        self.assertEqual([], self.avd.started)
        self.assertEqual("STOPPED", self.repo.get_worker("restart-me")["state"])

        start_decision = supervisor.tick()

        self.assertEqual("START", start_decision.action)
        self.assertEqual("restart-me", start_decision.worker_id)
        self.assertEqual([("acp-worker-01", 5554)], self.avd.started)


if __name__ == "__main__":
    unittest.main()
