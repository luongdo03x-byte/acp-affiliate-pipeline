import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

# acp.web composes the package from ``..core``, so the repository's parent must
# be importable before the wizard module is loaded.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from flask import Flask

from core import db
from core.db import now
from core.account_factory import ensure_schema as ensure_oauth_schema
from core.factory_v2.models import AccountStage
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService


class FakeThreadsOAuth:
    def __init__(self, username="guided.user", user_id="uid-guided"):
        self.username = username
        self.user_id = user_id
        self.state = None
        self.redirect_uri = None

    def authorization_url(self, state, redirect_uri):
        self.state = state
        self.redirect_uri = redirect_uri
        return f"https://threads.example/authorize?state={state}&redirect_uri={redirect_uri}"

    def exchange_code(self, code, redirect_uri):
        self.code = code
        self.redirect_uri = redirect_uri
        return {"access_token": "short-guided", "user_id": self.user_id}

    def exchange_long_lived(self, short_token):
        return {"access_token": "long-guided", "expires_in": 3600}

    def fetch_profile(self, token):
        return {"id": self.user_id, "username": self.username}


class ThreadsOnboardingWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "guided.db")
        self.old_db_path = db.DB_PATH
        self.old_env = os.environ.get("ACP_ENV")
        self.old_master_key = os.environ.get("ACP_MASTER_KEY")
        self.old_public_base = os.environ.get("ACP_PUBLIC_BASE_URL")
        self.old_meta_testers_url = os.environ.get("META_APP_TESTERS_URL")
        db.DB_PATH = self.db_path
        os.environ["ACP_ENV"] = "development"
        os.environ.pop("ACP_MASTER_KEY", None)
        os.environ["ACP_PUBLIC_BASE_URL"] = "https://acp.example"
        os.environ["META_APP_TESTERS_URL"] = "https://developers.facebook.com/apps/123/app-roles/"

        # The wizard resolves acp.core.db, a different module object from the
        # core.db this fixture rebinds. Without this patch the routes would open
        # the real shared database instead of the temporary one.
        for target in (
            "acp.web.threads_onboarding.connect",
            "acp.web.account_factory.connect",
        ):
            patcher = patch(target, db.connect)
            patcher.start()
            self.addCleanup(patcher.stop)

        conn = db.connect()
        conn.executescript("""
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
        ensure_schema(conn)
        ensure_oauth_schema(conn)
        repo = FactoryRepository(conn)
        service = FactoryService(repo)
        batch = service.create_batch("Guided", count=1, seed=9)
        account = repo.list_accounts(batch["id"])[0]
        conn.execute(
            "UPDATE factory_account SET username='guided.user' WHERE id=?",
            (account["id"],),
        )
        for stage in (
            AccountStage.AVD_ASSIGNED,
            AccountStage.IG_READY_FOR_HUMAN,
            AccountStage.IG_CREATED,
            AccountStage.THREADS_READY_FOR_HUMAN,
            AccountStage.THREADS_CREATED,
        ):
            service.transition_account(account["id"], stage)
        self.account_id = account["id"]
        conn.close()
        self.provider = FakeThreadsOAuth()

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()
        for key, old in (
            ("ACP_ENV", self.old_env),
            ("ACP_MASTER_KEY", self.old_master_key),
            ("ACP_PUBLIC_BASE_URL", self.old_public_base),
            ("META_APP_TESTERS_URL", self.old_meta_testers_url),
        ):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    def _build_app(self):
        from acp.web.threads_onboarding import register_threads_onboarding_routes

        app = Flask(__name__, template_folder="../web/templates")
        app.secret_key = "test-secret"
        app.config["THREADS_ONBOARDING_OAUTH_FACTORY"] = lambda: self.provider
        register_threads_onboarding_routes(app, admin_password="")
        return app

    def _seed_batch_waiting_invite(self, name, usernames):
        conn = db.connect()
        try:
            repo = FactoryRepository(conn)
            service = FactoryService(conn and repo)
            batch = service.create_batch(name, count=len(usernames), seed=31)
            account_ids = []
            for account, username in zip(repo.list_accounts(batch["id"]), usernames):
                conn.execute(
                    """UPDATE factory_account
                       SET username=?, stage='THREADS_CREATED', last_safe_stage='THREADS_CREATED',
                           tester_invited_at=NULL, tester_accepted_at=NULL
                       WHERE id=?""",
                    (username, account["id"]),
                )
                conn.execute(
                    """INSERT INTO factory_checkpoint
                       (id,batch_id,account_id,type,status,message,created_at)
                       VALUES (?,?,?,'TESTER_INVITE','WAITING_EXTERNAL',?,?)""",
                    (
                        f"cp-{account['id']}", batch["id"], account["id"],
                        "Chờ mời tài khoản này làm Threads Tester trên Meta.", now(),
                    ),
                )
                account_ids.append(account["id"])
            return batch["id"], account_ids
        finally:
            conn.close()

    def _account_row(self, account_id):
        conn = db.connect()
        try:
            return conn.execute(
                """SELECT tester_invited_at, tester_accepted_at
                   FROM factory_account WHERE id=?""",
                (account_id,),
            ).fetchone()
        finally:
            conn.close()

    def test_batch_invite_marks_every_pending_account(self):
        first_batch, first_ids = self._seed_batch_waiting_invite(
            "Batch mot", ["acc_one", "acc_two"]
        )
        _, other_ids = self._seed_batch_waiting_invite("Batch hai", ["acc_three"])
        app = self._build_app()

        response = app.test_client().post(
            f"/kenh/threads/onboarding/batch/{first_batch}/tester-invited"
        )

        self.assertEqual(302, response.status_code)
        for account_id in first_ids:
            row = self._account_row(account_id)
            self.assertIsNotNone(row["tester_invited_at"])
            self.assertIsNone(row["tester_accepted_at"])
        self.assertIsNone(self._account_row(other_ids[0])["tester_invited_at"])

        conn = db.connect()
        try:
            open_rows = conn.execute(
                """SELECT COUNT(*) AS n FROM factory_checkpoint
                   WHERE batch_id=? AND type='TESTER_INVITE' AND status='WAITING_EXTERNAL'""",
                (first_batch,),
            ).fetchone()["n"]
            still_open = conn.execute(
                """SELECT COUNT(*) AS n FROM factory_checkpoint
                   WHERE batch_id!=? AND type='TESTER_INVITE' AND status='WAITING_EXTERNAL'""",
                (first_batch,),
            ).fetchone()["n"]
        finally:
            conn.close()
        self.assertEqual(0, open_rows)
        self.assertEqual(1, still_open)

    def test_wizard_lists_pending_invite_batch(self):
        batch_id, _ = self._seed_batch_waiting_invite("Batch ba", ["acc_four", "acc_five"])
        app = self._build_app()

        body = app.test_client().get("/kenh/threads/onboarding").get_data(as_text=True)

        self.assertIn("Đã thêm trên Meta", body)
        self.assertIn(f"/kenh/threads/onboarding/batch/{batch_id}/tester-invited", body)
        self.assertIn("acc_four", body)
        self.assertIn("acc_five", body)

    def test_wizard_shows_next_account_and_configured_meta_tester_link(self):
        app = self._build_app()
        response = app.test_client().get("/kenh/threads/onboarding")
        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("@guided.user", body)
        self.assertIn("NEEDS_TESTER_INVITE", body)
        self.assertIn("https://developers.facebook.com/apps/123/app-roles/", body)
        self.assertIn("Đã accept", body)

    def test_mark_invited_advances_without_starting_oauth(self):
        app = self._build_app()
        response = app.test_client().post(
            f"/kenh/threads/onboarding/{self.account_id}/tester-invited"
        )
        self.assertEqual(302, response.status_code)
        conn = db.connect()
        row = conn.execute(
            "SELECT tester_invited_at,tester_accepted_at,stage FROM factory_account WHERE id=?",
            (self.account_id,),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row["tester_invited_at"])
        self.assertIsNone(row["tester_accepted_at"])
        self.assertEqual(AccountStage.THREADS_CREATED.value, row["stage"])
        self.assertIsNone(self.provider.state)

    def test_accept_shortcut_marks_both_and_redirects_directly_to_oauth(self):
        app = self._build_app()
        response = app.test_client().post(
            f"/kenh/threads/onboarding/{self.account_id}/continue"
        )
        self.assertEqual(302, response.status_code)
        authorize = urlparse(response.headers["Location"])
        self.assertEqual("threads.example", authorize.netloc)
        query = parse_qs(authorize.query)
        self.assertTrue(query["state"][0])
        self.assertEqual(
            ["https://acp.example/oauth/threads/onboarding/callback"],
            query["redirect_uri"],
        )

        conn = db.connect()
        row = conn.execute(
            """SELECT tester_invited_at,tester_accepted_at,stage,oauth_session_id
               FROM factory_account WHERE id=?""",
            (self.account_id,),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row["tester_invited_at"])
        self.assertIsNotNone(row["tester_accepted_at"])
        self.assertEqual(AccountStage.ACP_CONNECTING.value, row["stage"])
        self.assertTrue(row["oauth_session_id"])

    def test_callback_completes_account_and_returns_to_wizard(self):
        app = self._build_app()
        client = app.test_client()
        start = client.post(f"/kenh/threads/onboarding/{self.account_id}/continue")
        state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]

        callback = client.get(
            f"/oauth/threads/onboarding/callback?state={state}&code=guided-code"
        )

        self.assertEqual(302, callback.status_code)
        self.assertIn("/kenh/threads/onboarding?summary=", callback.headers["Location"])
        conn = db.connect()
        account = conn.execute(
            "SELECT * FROM factory_account WHERE id=?", (self.account_id,)
        ).fetchone()
        channel = conn.execute(
            "SELECT * FROM channel WHERE external_user_id='uid-guided'"
        ).fetchone()
        conn.close()
        self.assertEqual(AccountStage.ACP_ACTIVE.value, account["stage"])
        self.assertIsNotNone(channel)
        self.assertEqual("@guided.user", channel["handle"])
        self.assertEqual("ACTIVE", channel["status"])

    def test_callback_denial_is_retryable_without_reaccepting_tester(self):
        app = self._build_app()
        client = app.test_client()
        start = client.post(f"/kenh/threads/onboarding/{self.account_id}/continue")
        state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]

        callback = client.get(
            f"/oauth/threads/onboarding/callback?state={state}&error=access_denied"
        )

        self.assertEqual(302, callback.status_code)
        self.assertIn("/kenh/threads/onboarding?err=", callback.headers["Location"])
        conn = db.connect()
        account = conn.execute(
            "SELECT * FROM factory_account WHERE id=?", (self.account_id,)
        ).fetchone()
        conn.close()
        self.assertEqual(AccountStage.RETRY_PENDING.value, account["stage"])
        self.assertEqual("OAUTH_FAILED", account["last_error_code"])
        self.assertIsNotNone(account["tester_accepted_at"])

    def test_account_mismatch_returns_to_retry_without_losing_tester_acceptance(self):
        self.provider.username = "wrong.user"
        self.provider.user_id = "uid-wrong"
        app = self._build_app()
        client = app.test_client()
        start = client.post(f"/kenh/threads/onboarding/{self.account_id}/continue")
        state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]

        callback = client.get(
            f"/oauth/threads/onboarding/callback?state={state}&code=wrong-account-code"
        )

        self.assertEqual(302, callback.status_code)
        self.assertIn("/kenh/threads/onboarding?err=", callback.headers["Location"])
        conn = db.connect()
        account = conn.execute(
            "SELECT * FROM factory_account WHERE id=?", (self.account_id,)
        ).fetchone()
        channel_count = conn.execute("SELECT COUNT(*) FROM channel").fetchone()[0]
        conn.close()
        self.assertEqual(AccountStage.RETRY_PENDING.value, account["stage"])
        self.assertEqual(AccountStage.THREADS_CREATED.value, account["last_safe_stage"])
        self.assertIsNone(account["last_error_code"])
        self.assertIsNotNone(account["tester_accepted_at"])
        self.assertEqual(0, channel_count)

    def test_reopening_wizard_reconciles_expired_oauth_to_retry(self):
        app = self._build_app()
        client = app.test_client()
        client.post(f"/kenh/threads/onboarding/{self.account_id}/continue")

        conn = db.connect()
        account = conn.execute(
            "SELECT * FROM factory_account WHERE id=?", (self.account_id,)
        ).fetchone()
        conn.execute(
            "UPDATE account_factory_oauth_session SET expires_at=? WHERE id=?",
            ("2000-01-01T00:00:00+00:00", account["oauth_session_id"]),
        )
        conn.close()

        response = client.get("/kenh/threads/onboarding")
        body = response.get_data(as_text=True)

        self.assertEqual(200, response.status_code)
        self.assertIn("READY_FOR_OAUTH", body)
        conn = db.connect()
        account = conn.execute(
            "SELECT * FROM factory_account WHERE id=?", (self.account_id,)
        ).fetchone()
        conn.close()
        self.assertEqual(AccountStage.RETRY_PENDING.value, account["stage"])
        self.assertEqual("OAUTH_FAILED", account["last_error_code"])
        self.assertIsNotNone(account["tester_accepted_at"])


if __name__ == "__main__":
    unittest.main()
