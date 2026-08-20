import sqlite3
import unittest

from core.factory_v2.repository import FactoryRepository
from core.factory_v2.resource_policy import HostSample
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService
from core.factory_v2.supervisor import WorkerSupervisor


class FakeMetrics:
    ram_total_mb = 16384

    def sample(self):
        return HostSample(40, 8192, 0, 0, 1, 1)


class FakeAvd:
    def __init__(self):
        self.started = []

    def list_online_devices(self):
        return []

    def list_avds(self):
        return ["acp-worker-01"]

    def is_boot_completed(self, serial):
        return False

    def start(self, avd_name, port):
        self.started.append((avd_name, port))
        return type("P", (), {"pid": 1})()

    def stop(self, serial):
        pass


class FactoryV2LocalRunnerSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        self.avd = FakeAvd()
        self.supervisor = WorkerSupervisor(
            self.repo,
            self.avd,
            FakeMetrics(),
            worker_processes=None,
            stability_seconds=0,
        )

    def tearDown(self):
        self.conn.close()

    def test_reconcile_on_boot_does_not_treat_phone_as_missing_avd(self):
        phone = self.service.register_local_runner("android-id", "Pixel")

        self.supervisor.reconcile_on_boot()

        saved = self.repo.get_worker(phone["id"])
        self.assertEqual("READY", saved["state"])

    def test_resource_sample_counts_only_remote_avds(self):
        self.service.register_local_runner("android-id", "Pixel")

        self.supervisor.tick()

        sample = self.repo.latest_resource_sample()
        self.assertEqual(0, sample["avd_total"])


if __name__ == "__main__":
    unittest.main()
