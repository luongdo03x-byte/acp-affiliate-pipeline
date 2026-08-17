import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from core.factory_v2.models import AccountStage
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.schema import ensure_schema
from core.factory_v2.scheduler import Scheduler
from core.factory_v2.service import FactoryService


class FactoryV2SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        self.batch = self.service.create_batch("Batch", count=3, seed=7)
        self.repo.insert_worker({"id": "worker-01", "avd_name": "acp-worker-01", "state": "READY"})
        self.repo.insert_worker({"id": "worker-02", "avd_name": "acp-worker-02", "state": "READY"})
        self.scheduler = Scheduler(self.repo, self.service, lease_seconds=120)

    def tearDown(self):
        self.conn.close()

    def test_two_workers_cannot_receive_same_account(self):
        first = self.scheduler.assign_next("worker-01")
        second = self.scheduler.assign_next("worker-02")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first["account_id"], second["account_id"])
        active = self.repo.get_active_job_for_account(first["account_id"])
        self.assertEqual("worker-01", active["worker_id"])

    def test_expired_lease_with_live_heartbeat_enters_reconciliation(self):
        assigned = self.scheduler.assign_next("worker-01")
        now = datetime.now(timezone.utc)
        self.conn.execute(
            "UPDATE factory_job SET lease_expires_at=? WHERE id=?",
            ((now - timedelta(seconds=5)).isoformat(timespec="seconds"), assigned["id"]),
        )
        self.conn.execute(
            "UPDATE factory_worker SET last_heartbeat_at=? WHERE id='worker-01'",
            ((now - timedelta(seconds=5)).isoformat(timespec="seconds"),),
        )
        reconciled = self.scheduler.reconcile_expired_leases(now.isoformat(timespec="seconds"))
        self.assertEqual([assigned["id"]], reconciled)
        active = self.repo.get_active_job_for_account(assigned["account_id"])
        self.assertEqual("RECOVERING", active["state"])
        self.assertEqual("RECOVERING", self.repo.get_worker("worker-01")["state"])

    def test_expired_human_checkpoint_with_dead_worker_requires_confirmation(self):
        assigned = self.scheduler.assign_next("worker-01")
        self.service.transition_account(assigned["account_id"], AccountStage.IG_READY_FOR_HUMAN)
        self.service.transition_account(assigned["account_id"], AccountStage.WAITING_HUMAN)
        now = datetime.now(timezone.utc)
        stale = now - timedelta(minutes=5)
        self.conn.execute(
            "UPDATE factory_job SET state='WAITING_HUMAN', lease_expires_at=? WHERE id=?",
            ((now - timedelta(seconds=5)).isoformat(timespec="seconds"), assigned["id"]),
        )
        self.conn.execute(
            "UPDATE factory_worker SET state='WAITING_HUMAN', last_heartbeat_at=? WHERE id='worker-01'",
            (stale.isoformat(timespec="seconds"),),
        )

        reconciled = self.scheduler.reconcile_expired_leases(now.isoformat(timespec="seconds"))

        self.assertEqual([assigned["id"]], reconciled)
        job = self.conn.execute("SELECT * FROM factory_job WHERE id=?", (assigned["id"],)).fetchone()
        account = self.repo.get_account(assigned["account_id"])
        worker = self.repo.get_worker("worker-01")
        self.assertEqual("EXPIRED", job["state"])
        self.assertEqual(AccountStage.NEEDS_CONFIRMATION.value, account["stage"])
        self.assertEqual(AccountStage.PROFILE_READY.value, account["last_safe_stage"])
        self.assertIsNone(account["assigned_worker_id"])
        self.assertIsNone(account["current_job_id"])
        self.assertEqual("RECOVERING", worker["state"])
        self.assertIsNone(worker["current_account_id"])
        self.assertIsNone(worker["current_job_id"])


if __name__ == "__main__":
    unittest.main()