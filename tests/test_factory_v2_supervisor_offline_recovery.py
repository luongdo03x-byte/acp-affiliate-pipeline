import sqlite3
import unittest

from core.factory_v2.repository import FactoryRepository
from core.factory_v2.resource_policy import HostSample
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService
from core.factory_v2.supervisor import WorkerSupervisor


class FakeMetrics:
    def __init__(self, sample):
        self.value = sample
        self.ram_total_mb = 14814

    def sample(self):
        return self.value


class FakeAvd:
    def __init__(self):
        self.started = []
        self.stopped = []

    def list_online_devices(self):
        return []

    def list_avds(self):
        return ["acp-worker-01"]

    def is_boot_completed(self, serial):
        return False

    def start(self, avd_name, port):
        self.started.append((avd_name, port))
        return type("P", (), {"pid": 4321})()

    def stop(self, serial):
        self.stopped.append(serial)


class FakeWorkerProcesses:
    def __init__(self):
        self.stopped = []

    def is_running(self, worker_id):
        return False

    def stop(self, worker_id):
        self.stopped.append(worker_id)


class FactoryV2SupervisorOfflineRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        self.avd = FakeAvd()
        self.worker_processes = FakeWorkerProcesses()
        self.service.create_batch(
            "Social-only pilot", count=1, seed=51, completion_mode="SOCIAL_ONLY"
        )

    def tearDown(self):
        self.conn.close()

    def test_idle_offline_recovering_worker_restarts_instead_of_blocking_slot(self):
        self.repo.insert_worker({
            "id": "worker:acp-worker-01",
            "runner_type": "REMOTE_AVD",
            "avd_name": "acp-worker-01",
            "adb_serial": "emulator-5554",
            "state": "RECOVERING",
            "current_account_id": None,
            "current_job_id": None,
            "pid": 1198614,
            "draining": 0,
            "recovery_count": 341,
            "last_error": "runner command failed",
        })
        sample = HostSample(
            cpu_percent=20.0,
            ram_available_mb=8192,
            swap_used_mb=4096,
            swap_in_rate=0.0,
            load_1m=1.0,
            load_5m=1.0,
        )
        supervisor = WorkerSupervisor(
            self.repo,
            self.avd,
            FakeMetrics(sample),
            worker_processes=self.worker_processes,
            stability_seconds=0,
        )

        decision = supervisor.tick()

        self.assertEqual("START", decision.action)
        self.assertEqual("worker:acp-worker-01", decision.worker_id)
        self.assertEqual([("acp-worker-01", 5554)], self.avd.started)
        worker = self.repo.get_worker("worker:acp-worker-01")
        self.assertEqual("STARTING", worker["state"])
        self.assertEqual("emulator-5554", worker["adb_serial"])
        self.assertEqual(0, worker["draining"])
        self.assertIsNone(worker["last_error"])

    def test_offline_recovering_worker_with_active_job_is_not_restarted(self):
        self.repo.insert_worker({
            "id": "worker:acp-worker-01",
            "runner_type": "REMOTE_AVD",
            "avd_name": "acp-worker-01",
            "adb_serial": "emulator-5554",
            "state": "RECOVERING",
            "current_account_id": "account-1",
            "current_job_id": "job-1",
            "draining": 0,
            "last_error": "worker heartbeat missing",
        })
        sample = HostSample(
            cpu_percent=20.0,
            ram_available_mb=8192,
            swap_used_mb=4096,
            swap_in_rate=0.0,
            load_1m=1.0,
            load_5m=1.0,
        )
        supervisor = WorkerSupervisor(
            self.repo,
            self.avd,
            FakeMetrics(sample),
            worker_processes=self.worker_processes,
            stability_seconds=0,
        )

        decision = supervisor.tick()

        self.assertEqual("HOLD", decision.action)
        self.assertEqual([], self.avd.started)
        worker = self.repo.get_worker("worker:acp-worker-01")
        self.assertEqual("RECOVERING", worker["state"])
        self.assertEqual("job-1", worker["current_job_id"])
        self.assertEqual("account-1", worker["current_account_id"])


if __name__ == "__main__":
    unittest.main()
