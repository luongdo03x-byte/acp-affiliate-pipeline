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
        self.state = state
        self.redirect_uri = redirect_uri
        return f"https://threads.example/authorize?state={state}"


class FactoryV2OAuthBridgeTests(unittest.TestCase):
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
        self.provider = FakeAuthorizationProvider()
        self.redirect_uri = "https://acp.example/oauth/account-factory/threads/callback"

    def tearDown(self):
        self.conn.close()

    def seed_threads_created(self, username="maianh.le"):
        batch = self.service.create_batch("OAuth Batch", count=1, seed=11)
        account = self.repo.list_accounts(batch["id"])[0]
        self.conn.execute(
            "UPDATE factory_account SET username=? WHERE id=?", (username, account["id"])
        )
        for stage in (
            AccountStage.AVD_ASSIGNED,
            AccountStage.IG_READY_FOR_HUMAN,
            AccountStage.IG_CREATED,
            AccountStage.THREADS_READY_FOR_HUMAN,
            AccountStage.THREADS_CREATED,
        ):
            self.service.transition_account(account["id"], stage)
        return self.repo.get_account(account["id"])

    def start(self, username="maianh.le"):
        account = self.seed_threads_created(username)
        result = start_account_oauth(
            self.conn, account["id"], self.redirect_uri, self.provider
        )
        return account, result

    def test_start_oauth_uses_authoritative_username_and_marks_connecting(self):
        account, result = self.start("maianh.le")
        updated = self.repo.get_account(account["id"])
        oauth = get_session(self.conn, result["session_id"])

        self.assertEqual(AccountStage.ACP_CONNECTING.value, updated["stage"])
        self.assertEqual(result["session_id"], updated["oauth_session_id"])
        self.assertEqual("maianh.le", oauth["expected_username"])
        self.assertEqual(account["id"], oauth["account_local_id"])
        self.assertEqual(account["batch_id"], oauth["batch_id"])
        self.assertEqual("WAITING_AUTH", result["status"])
        self.assertNotIn("access_token", result)

    def test_active_oauth_copies_server_metadata_and_marks_active(self):
        account, result = self.start()
        self.conn.execute(
            """UPDATE account_factory_oauth_session
               SET status='ACTIVE', threads_user_id='threads-17', channel_id='channel-17',
                   channel_code='threads_maianh_le', actual_username='maianh.le'
               WHERE id=?""",
            (result["session_id"],),
        )

        updated = sync_account_from_oauth_session(self.conn, result["session_id"])

        self.assertEqual(AccountStage.ACP_ACTIVE.value, updated["stage"])
        self.assertEqual("threads-17", updated["threads_user_id"])
        self.assertEqual("channel-17", updated["channel_id"])
        self.assertEqual("threads_maianh_le", updated["channel_code"])

    def test_account_mismatch_marks_terminal_error(self):
        account, result = self.start()
        self.conn.execute(
            """UPDATE account_factory_oauth_session
               SET status='ACCOUNT_MISMATCH', actual_username='bob',
                   last_error='Tài khoản OAuth không khớp account đang onboarding'
               WHERE id=?""",
            (result["session_id"],),
        )

        updated = sync_account_from_oauth_session(self.conn, result["session_id"])

        self.assertEqual(AccountStage.ERROR.value, updated["stage"])
        self.assertEqual("ACCOUNT_MISMATCH", updated["last_error_code"])

    def test_oauth_error_and_expiry_are_retryable(self):
        for status in ("OAUTH_ERROR", "SESSION_EXPIRED"):
            with self.subTest(status=status):
                account, result = self.start(username=f"retry.{status.lower()}")
                self.conn.execute(
                    "UPDATE account_factory_oauth_session SET status=?, last_error='retryable oauth failure' WHERE id=?",
                    (status, result["session_id"]),
                )

                updated = sync_account_from_oauth_session(self.conn, result["session_id"])

                self.assertEqual(AccountStage.RETRY_PENDING.value, updated["stage"])
                self.assertEqual("OAUTH_FAILED", updated["last_error_code"])

    def test_waiting_auth_does_not_advance_account(self):
        account, result = self.start()

        updated = sync_account_from_oauth_session(self.conn, result["session_id"])

        self.assertEqual(AccountStage.ACP_CONNECTING.value, updated["stage"])
        self.assertEqual(result["session_id"], updated["oauth_session_id"])


if __name__ == "__main__":
    unittest.main()
