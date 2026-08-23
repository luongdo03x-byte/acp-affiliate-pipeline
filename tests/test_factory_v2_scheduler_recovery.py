import sqlite3
import unittest

from core.factory_v2.models import AccountStage
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.scheduler import Scheduler
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService


class FactoryV2SchedulerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        self.scheduler = Scheduler(self.repo, self.service)
        self.repo.insert_worker({
            "id": "worker-01",
            "avd_name": "acp-worker-01",
            "state": "READY",
        })

    def tearDown(self):
        self.conn.close()

    def _account_at_ig_created(self):
        batch = self.service.create_batch("Recovery", count=1, seed=51)
        account = self.repo.list_accounts(batch["id"])[0]
        for stage in (
            AccountStage.AVD_ASSIGNED,
            AccountStage.IG_READY_FOR_HUMAN,
            AccountStage.IG_CREATED,
        ):
            self.service.transition_account(account["id"], stage)
        return self.repo.get_account(account["id"])

    def _account_at_threads_created(self):
        account = self._account_at_ig_created()
        for stage in (
            AccountStage.THREADS_READY_FOR_HUMAN,
            AccountStage.THREADS_CREATED,
        ):
            self.service.transition_account(account["id"], stage)
        return self.repo.get_account(account["id"])

    def test_retry_after_ig_created_resumes_threads_not_instagram(self):
        account = self._account_at_ig_created()
        self.service.transition_account(
            account["id"],
            AccountStage.RETRY_PENDING,
            error_code="WORKER_TIMEOUT",
            error_message="worker lost",
        )

        job = self.scheduler.assign_next("worker-01")
        updated = self.repo.get_account(account["id"])

        self.assertIsNotNone(job)
        self.assertEqual("PREPARE_THREADS", job["desired_action"])
        self.assertEqual("IG_CREATED", updated["last_safe_stage"])
        self.assertEqual("THREADS_READY_FOR_HUMAN", updated["stage"])

    def test_oauth_retry_is_never_leased_to_avd_worker(self):
        account = self._account_at_threads_created()
        self.service.transition_account(account["id"], AccountStage.ACP_CONNECTING)
        self.service.transition_account(
            account["id"],
            AccountStage.RETRY_PENDING,
            error_code="OAUTH_FAILED",
            error_message="oauth expired",
        )

        job = self.scheduler.assign_next("worker-01")
        updated = self.repo.get_account(account["id"])

        self.assertIsNone(job)
        self.assertEqual("RETRY_PENDING", updated["stage"])
        self.assertEqual("THREADS_CREATED", updated["last_safe_stage"])
        self.assertEqual("OAUTH_FAILED", updated["last_error_code"])

    def test_expired_recovering_job_with_live_heartbeat_returns_to_running(self):
        account = self._account_at_ig_created()
        self.service.transition_account(
            account["id"],
            AccountStage.RETRY_PENDING,
            error_code="WORKER_TIMEOUT",
            error_message="worker lost",
        )
        job = self.scheduler.assign_next("worker-01")
        self.assertIsNotNone(job)

        self.conn.execute(
            """UPDATE factory_job
               SET state='RECOVERING', lease_expires_at='2026-08-23T09:00:00+00:00'
               WHERE id=?""",
            (job["id"],),
        )
        self.conn.execute(
            """UPDATE factory_worker
               SET state='RECOVERING', last_heartbeat_at='2026-08-23T09:00:20+00:00'
               WHERE id='worker-01'"""
        )

        reconciled = self.scheduler.reconcile_expired_leases("2026-08-23T09:00:30+00:00")

        refreshed_job = self.conn.execute(
            "SELECT * FROM factory_job WHERE id=?", (job["id"],)
        ).fetchone()
        worker = self.repo.get_worker("worker-01")
        self.assertEqual([job["id"]], reconciled)
        self.assertEqual("RUNNING", refreshed_job["state"])
        self.assertEqual("RUNNING", worker["state"])


if __name__ == "__main__":
    unittest.main()
