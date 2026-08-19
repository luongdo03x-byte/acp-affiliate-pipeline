import sqlite3
import unittest

from core.factory_v2.repository import FactoryRepository
from core.factory_v2.resource_policy import HostSample
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService
from core.factory_v2.supervisor import WorkerSupervisor


class FakeMetrics:
    def __init__(self):
        self.ram_total_mb = 14814

    def sample(self):
        return HostSample(
            cpu_percent=20.0,
            ram_available_mb=8192,
            swap_used_mb=0,
            swap_in_rate=0.0,
            load_1m=1.0,
            load_5m=1.0,
        )


class FakeAvd:
    def __init__(self):
        self.started = []

    def list_online_devices(self):
        return []

    def list_avds(self):
        # Unrelated AVD names must never shift Account Factory worker ports.
        return ["aaa-personal-avd", "acp-worker-01", "acp-worker-02"]

    def is_boot_completed(self, serial):
        return False

    def start(self, avd_name, port):
        self.started.append((avd_name, port))
        return type("P", (), {"pid": 4321})()

    def stop(self, serial):
        pass


class FactoryV2SupervisorPortMappingTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        self.avd = FakeAvd()

    def tearDown(self):
        self.conn.close()

    def supervisor(self):
        return WorkerSupervisor(
            self.repo,
            self.avd,
            FakeMetrics(),
            stability_seconds=0,
        )

    def test_first_factory_avd_uses_5554_even_when_unrelated_avd_sorts_first(self):
        self.service.create_batch(
            "Social-only pilot", count=1, seed=61, completion_mode="SOCIAL_ONLY"
        )

        decision = self.supervisor().tick()

        self.assertEqual("START", decision.action)
        self.assertEqual("acp-worker-01", decision.avd_name)
        self.assertEqual([("acp-worker-01", 5554)], self.avd.started)
        worker = self.repo.get_worker(decision.worker_id)
        self.assertEqual("emulator-5554", worker["adb_serial"])

    def test_second_factory_avd_uses_5556_independent_of_unrelated_avds(self):
        self.service.create_batch("Default pool", count=2, seed=62)
        self.repo.insert_worker({
            "id": "worker:acp-worker-01",
            "runner_type": "REMOTE_AVD",
            "avd_name": "acp-worker-01",
            "adb_serial": "emulator-5554",
            "state": "RUNNING",
            "current_account_id": "account-1",
            "current_job_id": "job-1",
        })

        decision = self.supervisor().tick()

        self.assertEqual("START", decision.action)
        self.assertEqual("acp-worker-02", decision.avd_name)
        self.assertEqual([("acp-worker-02", 5556)], self.avd.started)
        worker = self.repo.get_worker(decision.worker_id)
        self.assertEqual("emulator-5556", worker["adb_serial"])


if __name__ == "__main__":
    unittest.main()
