import sqlite3
import unittest

from core.factory_v2.repository import FactoryRepository
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService


class FactoryV2RunnerServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)

    def tearDown(self):
        self.conn.close()

    def test_register_local_runner_is_idempotent(self):
        first = self.service.register_local_runner("android-id-1", "Pixel 8")
        second = self.service.register_local_runner("android-id-1", "Pixel 8 Pro")

        self.assertEqual(first["id"], second["id"])
        saved = self.repo.get_worker(first["id"])
        self.assertEqual("LOCAL_DEVICE", saved["runner_type"])
        self.assertEqual("Pixel 8 Pro", saved["device_name"])
        self.assertEqual("READY", saved["state"])
        self.assertIsNone(saved["avd_name"])

    def test_local_runner_heartbeat_updates_last_heartbeat(self):
        worker = self.service.register_local_runner("android-id-2", "Phone")
        updated = self.service.heartbeat_runner(
            worker["id"], current_account_id=None, current_job_id=None
        )
        self.assertIsNotNone(updated["last_heartbeat_at"])

    def test_heartbeat_rejects_assignment_mismatch(self):
        worker = self.service.register_local_runner("android-id-3", "Phone")
        self.repo.update_worker_fields(
            worker["id"], current_account_id="a1", current_job_id="j1", state="RUNNING"
        )

        with self.assertRaises(ValueError):
            self.service.heartbeat_runner(
                worker["id"], current_account_id="a2", current_job_id="j2"
            )

        saved = self.repo.get_worker(worker["id"])
        self.assertEqual("a1", saved["current_account_id"])
        self.assertEqual("j1", saved["current_job_id"])


if __name__ == "__main__":
    unittest.main()
