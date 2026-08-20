import sqlite3
import unittest

from core.db import now, ulid
from core.factory_v2.models import AccountStage
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.scheduler import Scheduler
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService


class FactoryV2CheckpointRetryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        self.scheduler = Scheduler(self.repo, self.service)
        self.batch = self.service.create_batch("Retry Batch", count=1, seed=81)
        self.repo.insert_worker({
            "id": "worker-01",
            "avd_name": "acp-worker-01",
            "state": "READY",
        })

    def tearDown(self):
        self.conn.close()

    def _waiting_checkpoint(self):
        job = self.scheduler.assign_next("worker-01")
        self.service.transition_account(job["account_id"], AccountStage.IG_READY_FOR_HUMAN)
        self.service.transition_account(job["account_id"], AccountStage.WAITING_HUMAN)
        self.conn.execute("UPDATE factory_job SET state='WAITING_HUMAN' WHERE id=?", (job["id"],))
        self.conn.execute("UPDATE factory_worker SET state='WAITING_HUMAN' WHERE id='worker-01'")
        checkpoint_id = ulid()
        self.repo.create_checkpoint({
            "id": checkpoint_id,
            "batch_id": self.batch["id"],
            "account_id": job["account_id"],
            "worker_id": "worker-01",
            "type": "IG_POSTCHECK",
            "status": "OPEN",
            "message": "Confirm Instagram",
            "created_at": now(),
        })
        return job, checkpoint_id

    def test_retry_checkpoint_with_live_job_requests_worker_recheck(self):
        job, checkpoint_id = self._waiting_checkpoint()

        result = self.service.retry_checkpoint(checkpoint_id)

        checkpoint = self.repo.get_checkpoint(checkpoint_id)
        updated_job = self.repo.get_active_job_for_account(job["account_id"])
        self.assertEqual("VERIFYING", checkpoint["status"])
        self.assertEqual("RUNNING", updated_job["state"])
        self.assertEqual("RETRY_CHECKPOINT", updated_job["desired_action"])
        self.assertEqual("VERIFYING", result["status"])

    def test_retry_checkpoint_after_dead_lease_requeues_from_safe_stage(self):
        job, checkpoint_id = self._waiting_checkpoint()
        self.conn.execute("UPDATE factory_job SET state='EXPIRED', finished_at=? WHERE id=?", (now(), job["id"]))
        self.conn.execute(
            "UPDATE factory_account SET assigned_worker_id=NULL, current_job_id=NULL WHERE id=?",
            (job["account_id"],),
        )
        self.conn.execute(
            "UPDATE factory_worker SET state='RECOVERING', current_account_id=NULL, current_job_id=NULL WHERE id='worker-01'"
        )
        self.service.transition_account(
            job["account_id"],
            AccountStage.NEEDS_CONFIRMATION,
            error_code="WORKER_TIMEOUT",
            error_message="lease expired",
        )

        result = self.service.retry_checkpoint(checkpoint_id)

        account = self.repo.get_account(job["account_id"])
        checkpoint = self.repo.get_checkpoint(checkpoint_id)
        self.assertEqual("RETRY_PENDING", account["stage"])
        self.assertEqual("PROFILE_READY", account["last_safe_stage"])
        self.assertEqual("RESOLVED", checkpoint["status"])
        self.assertEqual("RETRY_REQUESTED", checkpoint["resolution"])
        self.assertEqual("RETRY_PENDING", result["status"])
        self.assertTrue(result["command_id"])

    def test_generic_account_retry_resolves_stale_actionable_checkpoints(self):
        job, checkpoint_id = self._waiting_checkpoint()
        self.conn.execute("UPDATE factory_job SET state='EXPIRED', finished_at=? WHERE id=?", (now(), job["id"]))
        self.conn.execute(
            "UPDATE factory_account SET assigned_worker_id=NULL, current_job_id=NULL WHERE id=?",
            (job["account_id"],),
        )
        self.service.transition_account(
            job["account_id"],
            AccountStage.NEEDS_CONFIRMATION,
            error_code="WORKER_TIMEOUT",
            error_message="lease expired",
        )

        account = self.service.retry_account(job["account_id"])

        checkpoint = self.repo.get_checkpoint(checkpoint_id)
        self.assertEqual("RETRY_PENDING", account["stage"])
        self.assertEqual("RESOLVED", checkpoint["status"])
        self.assertEqual("RETRY_REQUESTED", checkpoint["resolution"])


if __name__ == "__main__":
    unittest.main()
