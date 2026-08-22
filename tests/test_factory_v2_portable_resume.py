import importlib.util
import os
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from core.account_factory import ensure_schema as ensure_oauth_schema
from core.factory_v2.activation import FactoryActivationService
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.schema import ensure_schema
from core.factory_v2.scheduler import Scheduler
from core.factory_v2.service import FactoryService


_MODULE = "core.factory_v2.portable_resume"
_MODULE_AVAILABLE = importlib.util.find_spec(_MODULE) is not None


class PortableResumeModuleContractTests(unittest.TestCase):
    def test_portable_resume_module_exists(self):
        self.assertTrue(_MODULE_AVAILABLE, "portable_resume module missing")


class FakeProvider:
    def authorization_url(self, state, redirect_uri):
        return f"https://threads.example/authorize?state={state}"


@unittest.skipUnless(_MODULE_AVAILABLE, "portable_resume module not implemented yet")
class PortableResumeTests(unittest.TestCase):
    def setUp(self):
        from core.factory_v2.portable_resume import reconcile_for_portable_resume

        self.reconcile_for_portable_resume = reconcile_for_portable_resume
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        ensure_oauth_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)

    def tearDown(self):
        self.conn.close()

    def _env(self):
        return patch.dict(
            os.environ,
            {
                "THREADS_APP_ID": "test-app-id",
                "THREADS_APP_SECRET": "test-app-secret",
                "ACP_PUBLIC_BASE_URL": "https://factory.example.com",
            },
        )

    def test_expired_dead_lease_is_reconciled_and_preserves_last_safe_stage(self):
        batch = self.service.create_batch("Lease", count=1, seed=41)
        account = self.repo.list_accounts(batch["id"])[0]
        self.repo.insert_worker({
            "id": "worker-01",
            "avd_name": "acp-worker-01",
            "state": "READY",
        })
        scheduler = Scheduler(self.repo, self.service, lease_seconds=120)
        job = scheduler.assign_next("worker-01")
        self.assertIsNotNone(job)

        now = datetime.now(timezone.utc)
        stale = now - timedelta(minutes=5)
        self.conn.execute(
            "UPDATE factory_job SET state='RECOVERING', lease_expires_at=? WHERE id=?",
            ((now - timedelta(seconds=5)).isoformat(timespec="seconds"), job["id"]),
        )
        self.conn.execute(
            "UPDATE factory_worker SET state='STOPPED', last_heartbeat_at=? WHERE id='worker-01'",
            (stale.isoformat(timespec="seconds"),),
        )

        with self._env():
            result = self.reconcile_for_portable_resume(
                self.conn,
                now.isoformat(timespec="seconds"),
            )

        saved_job = self.conn.execute(
            "SELECT * FROM factory_job WHERE id=?", (job["id"],)
        ).fetchone()
        saved_account = self.repo.get_account(account["id"])
        self.assertEqual("EXPIRED", saved_job["state"])
        self.assertEqual("RETRY_PENDING", saved_account["stage"])
        self.assertEqual("PROFILE_READY", saved_account["last_safe_stage"])
        self.assertIsNone(saved_account["current_job_id"])
        self.assertEqual(1, result["leases_reconciled"])
        self.assertEqual(0, result["oauth_reconciled"])

    def test_expired_waiting_oauth_reconciles_to_gated_retry(self):
        batch = self.service.create_batch("OAuth", count=1, seed=43)
        account = self.repo.list_accounts(batch["id"])[0]
        self.conn.execute(
            """UPDATE factory_account
               SET stage='THREADS_CREATED', last_safe_stage='THREADS_CREATED'
               WHERE id=?""",
            (account["id"],),
        )
        activation = FactoryActivationService(
            self.conn,
            provider=FakeProvider(),
            public_base_url="https://factory.example.com",
        )
        started = activation.start(account["id"])
        self.conn.execute(
            "UPDATE account_factory_oauth_session SET expires_at=? WHERE id=?",
            (
                (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds"),
                started["session_id"],
            ),
        )

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._env():
            result = self.reconcile_for_portable_resume(self.conn, now)

        saved = self.repo.get_account(account["id"])
        session = self.conn.execute(
            "SELECT * FROM account_factory_oauth_session WHERE id=?",
            (started["session_id"],),
        ).fetchone()
        self.assertEqual("SESSION_EXPIRED", session["status"])
        self.assertEqual("RETRY_PENDING", saved["stage"])
        self.assertEqual("THREADS_CREATED", saved["last_safe_stage"])
        self.assertEqual("OAUTH_FAILED", saved["last_error_code"])
        self.assertEqual(1, result["oauth_reconciled"])
        self.assertEqual(1, result["oauth_gated"])

    def test_waiting_unexpired_oauth_is_reconciled_once_without_advancing(self):
        batch = self.service.create_batch("Waiting OAuth", count=1, seed=47)
        account = self.repo.list_accounts(batch["id"])[0]
        self.conn.execute(
            """UPDATE factory_account
               SET stage='THREADS_CREATED', last_safe_stage='THREADS_CREATED'
               WHERE id=?""",
            (account["id"],),
        )
        activation = FactoryActivationService(
            self.conn,
            provider=FakeProvider(),
            public_base_url="https://factory.example.com",
        )
        activation.start(account["id"])

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._env():
            result = self.reconcile_for_portable_resume(self.conn, now)

        saved = self.repo.get_account(account["id"])
        self.assertEqual("ACP_CONNECTING", saved["stage"])
        self.assertEqual("THREADS_CREATED", saved["last_safe_stage"])
        self.assertIsNone(saved["last_error_code"])
        self.assertEqual(1, result["oauth_reconciled"])
        self.assertEqual(0, result["oauth_gated"])

    def test_gated_retry_is_not_cleared_or_restarted(self):
        batch = self.service.create_batch("Gated", count=1, seed=53)
        account = self.repo.list_accounts(batch["id"])[0]
        self.conn.execute(
            """UPDATE factory_account
               SET stage='RETRY_PENDING', last_safe_stage='THREADS_CREATED',
                   last_error_code='OAUTH_FAILED', last_error_message='expired'
               WHERE id=?""",
            (account["id"],),
        )

        with self._env():
            result = self.reconcile_for_portable_resume(
                self.conn,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )

        saved = self.repo.get_account(account["id"])
        self.assertEqual("RETRY_PENDING", saved["stage"])
        self.assertEqual("OAUTH_FAILED", saved["last_error_code"])
        self.assertEqual(0, result["oauth_reconciled"])
        self.assertEqual(1, result["oauth_gated"])


if __name__ == "__main__":
    unittest.main()
