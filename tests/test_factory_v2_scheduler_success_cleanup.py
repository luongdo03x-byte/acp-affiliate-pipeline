import sqlite3
import unittest

from core.factory_v2.repository import FactoryRepository
from core.factory_v2.schema import ensure_schema
from core.factory_v2.scheduler import Scheduler
from core.factory_v2.service import FactoryService


class FactoryV2SchedulerSuccessCleanupTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        self.batch = self.service.create_batch(
            "Success cleanup", count=1, seed=71, completion_mode="SOCIAL_ONLY"
        )
        self.repo.insert_worker({
            "id": "worker-01",
            "avd_name": "acp-worker-01",
            "state": "READY",
        })
        self.scheduler = Scheduler(self.repo, self.service)

    def tearDown(self):
        self.conn.close()

    def _assigned_with_recovery_history(self):
        job = self.scheduler.assign_next("worker-01")
        self.assertIsNotNone(job)
        self.conn.execute(
            "UPDATE factory_worker SET recovery_count=17 WHERE id='worker-01'"
        )
        return job

    def test_release_job_completed_resets_recovery_count(self):
        job = self._assigned_with_recovery_history()

        self.scheduler.release_job(job["id"], "COMPLETED")

        worker = self.repo.get_worker("worker-01")
        self.assertEqual("READY", worker["state"])
        self.assertEqual(0, worker["recovery_count"])
        self.assertIsNone(worker["current_account_id"])
        self.assertIsNone(worker["current_job_id"])

    def test_release_job_in_transaction_completed_resets_recovery_count(self):
        job = self._assigned_with_recovery_history()

        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.scheduler.release_job_in_transaction(job["id"], "COMPLETED")
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

        worker = self.repo.get_worker("worker-01")
        self.assertEqual("READY", worker["state"])
        self.assertEqual(0, worker["recovery_count"])

    def test_failed_release_preserves_recovery_count(self):
        job = self._assigned_with_recovery_history()

        self.scheduler.release_job(job["id"], "FAILED")

        worker = self.repo.get_worker("worker-01")
        self.assertEqual("READY", worker["state"])
        self.assertEqual(17, worker["recovery_count"])


if __name__ == "__main__":
    unittest.main()
