import sqlite3
import unittest

from core.account_factory import ensure_schema as ensure_oauth_schema, get_session
from core.factory_v2.models import AccountStage
from core.factory_v2.oauth_bridge import start_account_oauth, sync_account_from_oauth_session
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService


class FakeAuthorizationProvider:
    def authorization_url(self, state, redirect_uri):
        return f"https://threads.example/authorize?state={state}"


class FactoryV2OAuthExpiryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript("""
            CREATE TABLE channel (
                id TEXT PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                platform TEXT NOT NULL DEFAULT 'threads',
                handle TEXT NOT NULL,
                external_user_id TEXT,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                token_encrypted BLOB,
                token_expires_at TEXT,
                daily_post_cap INTEGER NOT NULL DEFAULT 12,
                min_gap_minutes INTEGER NOT NULL DEFAULT 90,
                niches TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );
        """)
        ensure_schema(self.conn)
        ensure_oauth_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)

    def tearDown(self):
        self.conn.close()

    def test_waiting_auth_past_expiry_becomes_retry_pending(self):
        batch = self.service.create_batch("Expiry Batch", count=1, seed=23)
        account = self.repo.list_accounts(batch["id"])[0]
        for stage in (
            AccountStage.AVD_ASSIGNED,
            AccountStage.IG_READY_FOR_HUMAN,
            AccountStage.IG_CREATED,
            AccountStage.THREADS_READY_FOR_HUMAN,
            AccountStage.THREADS_CREATED,
        ):
            self.service.transition_account(account["id"], stage)

        started = start_account_oauth(
            self.conn,
            account["id"],
            "https://acp.example/oauth/account-factory/threads/callback",
            FakeAuthorizationProvider(),
        )
        self.conn.execute(
            "UPDATE account_factory_oauth_session SET expires_at=? WHERE id=?",
            ("2020-01-01T00:00:00+00:00", started["session_id"]),
        )

        updated = sync_account_from_oauth_session(self.conn, started["session_id"])
        session = get_session(self.conn, started["session_id"])

        self.assertEqual("SESSION_EXPIRED", session["status"])
        self.assertEqual(AccountStage.RETRY_PENDING.value, updated["stage"])
        self.assertEqual("OAUTH_FAILED", updated["last_error_code"])


if __name__ == "__main__":
    unittest.main()
