import sqlite3
import unittest

from core.db import now, ulid
from core.factory_v2.models import AccountStage
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.scheduler import Scheduler
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService


class FactoryV2StopAccountTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        self.batch = self.service.create_batch("Stop Batch", count=1, seed=31)
        self.repo.insert_worker({
            "id": "worker-01",
            "avd_name": "acp-worker-01",
            "state": "READY",
        })
        self.scheduler = Scheduler(self.repo, self.service)

    def tearDown(self):
        self.conn.close()

    def test_stop_cancels_job_and_resolves_actionable_checkpoint(self):
        job = self.scheduler.assign_next("worker-01")
        self.service.transition_account(job["account_id"], AccountStage.IG_READY_FOR_HUMAN)
        self.service.transition_account(job["account_id"], AccountStage.WAITING_HUMAN)
        self.conn.execute(
            "UPDATE factory_job SET state='WAITING_HUMAN' WHERE id=?", (job["id"],)
        )
        self.conn.execute(
            "UPDATE factory_worker SET state='WAITING_HUMAN' WHERE id='worker-01'"
        )
        checkpoint_id = ulid()
        self.repo.create_checkpoint({
            "id": checkpoint_id,
            "batch_id": self.batch["id"],
            "account_id": job["account_id"],
            "worker_id": "worker-01",
            "type": "IG_POSTCHECK",
            "status": "OPEN",
            "message": "Confirm",
            "created_at": now(),
        })

        stopped = self.service.stop_account(job["account_id"])

        job_row = self.conn.execute("SELECT * FROM factory_job WHERE id=?", (job["id"],)).fetchone()
        worker = self.repo.get_worker("worker-01")
        checkpoint = self.repo.get_checkpoint(checkpoint_id)
        self.assertEqual("DISABLED", stopped["stage"])
        self.assertEqual("CANCELLED", job_row["state"])
        self.assertEqual("READY", worker["state"])
        self.assertEqual("RESOLVED", checkpoint["status"])
        self.assertEqual("ACCOUNT_STOPPED", checkpoint["resolution"])
        self.assertIsNotNone(checkpoint["resolved_at"])


if __name__ == "__main__":
    unittest.main()
