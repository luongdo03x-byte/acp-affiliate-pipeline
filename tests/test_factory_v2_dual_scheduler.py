import sqlite3
import unittest

from core.factory_v2.repository import FactoryRepository
from core.factory_v2.schema import ensure_schema
from core.factory_v2.scheduler import Scheduler
from core.factory_v2.service import FactoryService


class FactoryV2DualSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        self.scheduler = Scheduler(self.repo, self.service, lease_seconds=120)

    def tearDown(self):
        self.conn.close()

    def seed_worker(self, worker_id, *, runner_type, state="READY"):
        row = {
            "id": worker_id,
            "runner_type": runner_type,
            "state": state,
        }
        if runner_type == "REMOTE_AVD":
            row["avd_name"] = f"{worker_id}-avd"
        else:
            row["device_id"] = f"{worker_id}-device"
            row["device_name"] = worker_id
        return self.repo.insert_worker(row)

    def seed_account(self, *, execution_target):
        batch = self.service.create_batch("Dual", count=1, seed=99)
        account = self.repo.list_accounts(batch["id"])[0]
        self.conn.execute(
            "UPDATE factory_account SET execution_target=? WHERE id=?",
            (execution_target, account["id"]),
        )
        return self.repo.get_account(account["id"])

    def test_exact_local_target_only_leases_to_requested_phone(self):
        phone = self.seed_worker("phone-1", runner_type="LOCAL_DEVICE")
        avd = self.seed_worker("avd-1", runner_type="REMOTE_AVD")
        account = self.seed_account(execution_target=phone["id"])

        self.assertIsNone(self.scheduler.assign_next(avd["id"]))
        job = self.scheduler.assign_next(phone["id"])

        self.assertIsNotNone(job)
        self.assertEqual(account["id"], job["account_id"])
        self.assertEqual("LOCAL_DEVICE", job["runner_type"])
        saved = self.repo.get_account(account["id"])
        self.assertEqual("RUNNER_ASSIGNED", saved["stage"])

    def test_auto_avd_does_not_lease_to_phone(self):
        phone = self.seed_worker("phone-1", runner_type="LOCAL_DEVICE")
        avd = self.seed_worker("avd-1", runner_type="REMOTE_AVD")
        self.seed_account(execution_target="AUTO_AVD")

        self.assertIsNone(self.scheduler.assign_next(phone["id"]))
        job = self.scheduler.assign_next(avd["id"])

        self.assertIsNotNone(job)
        self.assertEqual("REMOTE_AVD", job["runner_type"])

    def test_legacy_null_target_remains_avd_only(self):
        phone = self.seed_worker("phone-1", runner_type="LOCAL_DEVICE")
        avd = self.seed_worker("avd-1", runner_type="REMOTE_AVD")
        self.seed_account(execution_target=None)

        self.assertIsNone(self.scheduler.assign_next(phone["id"]))
        self.assertIsNotNone(self.scheduler.assign_next(avd["id"]))


if __name__ == "__main__":
    unittest.main()
