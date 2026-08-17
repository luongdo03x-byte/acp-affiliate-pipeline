import os
import tempfile
import unittest

from flask import Flask

from core import db
from core.account_factory import ensure_schema as ensure_oauth_schema, get_session
from core.factory_v2.models import AccountStage
from core.factory_v2.oauth_bridge import start_account_oauth, sync_account_from_oauth_session
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService
from web.account_factory import register_account_factory_routes


class FakeThreadsOAuth:
    def __init__(self, username="maianh.le", user_id="uid-maianh"):
        self.username = username
        self.user_id = user_id

    def authorization_url(self, state, redirect_uri):
        self.state = state
        self.redirect_uri = redirect_uri
        return f"https://threads.example/authorize?state={state}"

    def exchange_code(self, code, redirect_uri):
        return {"access_token": "short-test-token", "user_id": self.user_id}

    def exchange_long_lived(self, short_token):
        self.last_short_token = short_token
        return {"access_token": "long-test-token", "expires_in": 3600}

    def fetch_profile(self, token):
        self.last_profile_token = token
        return {"id": self.user_id, "username": self.username}


class FactoryV2OAuthBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_env = os.environ.get("ACP_ENV")
        self.old_master_key = os.environ.get("ACP_MASTER_KEY")
        self.old_public_base = os.environ.get("ACP_PUBLIC_BASE_URL")
        db.DB_PATH = os.path.join(self.tmp.name, "factory-v2-oauth.db")
        os.environ["ACP_ENV"] = "development"
        os.environ.pop("ACP_MASTER_KEY", None)
        os.environ["ACP_PUBLIC_BASE_URL"] = "https://acp.example"

        self.conn = db.connect()
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
        self.provider = FakeThreadsOAuth()
        self.redirect_uri = "https://acp.example/oauth/account-factory/threads/callback"

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()
        if self.old_env is None:
            os.environ.pop("ACP_ENV", None)
        else:
            os.environ["ACP_ENV"] = self.old_env
        if self.old_master_key is None:
            os.environ.pop("ACP_MASTER_KEY", None)
        else:
            os.environ["ACP_MASTER_KEY"] = self.old_master_key
        if self.old_public_base is None:
            os.environ.pop("ACP_PUBLIC_BASE_URL", None)
        else:
            os.environ["ACP_PUBLIC_BASE_URL"] = self.old_public_base

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

    def start(self, username="maianh.le", provider=None):
        account = self.seed_threads_created(username)
        provider = provider or self.provider
        result = start_account_oauth(
            self.conn, account["id"], self.redirect_uri, provider
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

    def test_callback_success_syncs_oauth_and_factory_account(self):
        provider = FakeThreadsOAuth(username="maianh.le", user_id="uid-maianh")
        account, result = self.start(provider=provider)
        app = Flask(__name__)
        app.testing = True
        app.config["ACCOUNT_FACTORY_OAUTH_FACTORY"] = lambda: provider
        register_account_factory_routes(app)

        response = app.test_client().get(
            f"/oauth/account-factory/threads/callback?code=code-ok&state={provider.state}"
        )

        self.assertEqual(200, response.status_code)
        session = get_session(self.conn, result["session_id"])
        updated = self.repo.get_account(account["id"])
        self.assertEqual("ACTIVE", session["status"])
        self.assertEqual(AccountStage.ACP_ACTIVE.value, updated["stage"])
        self.assertEqual(session["channel_code"], updated["channel_code"])

    def test_callback_mismatch_never_creates_channel_and_marks_factory_error(self):
        provider = FakeThreadsOAuth(username="bob", user_id="uid-bob")
        account, result = self.start(username="alice", provider=provider)
        app = Flask(__name__)
        app.testing = True
        app.config["ACCOUNT_FACTORY_OAUTH_FACTORY"] = lambda: provider
        register_account_factory_routes(app)
        before = self.conn.execute("SELECT COUNT(*) FROM channel").fetchone()[0]

        response = app.test_client().get(
            f"/oauth/account-factory/threads/callback?code=code-bad&state={provider.state}"
        )

        self.assertEqual(409, response.status_code)
        after = self.conn.execute("SELECT COUNT(*) FROM channel").fetchone()[0]
        session = get_session(self.conn, result["session_id"])
        updated = self.repo.get_account(account["id"])
        self.assertEqual(before, after)
        self.assertEqual("ACCOUNT_MISMATCH", session["status"])
        self.assertEqual(AccountStage.ERROR.value, updated["stage"])
        self.assertEqual("ACCOUNT_MISMATCH", updated["last_error_code"])


if __name__ == "__main__":
    unittest.main()
