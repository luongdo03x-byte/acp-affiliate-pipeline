import os
import sqlite3
import tempfile
import unittest

from core import crypto
from core.account_factory import (
    AccountMismatchError,
    ThreadsOAuthClient,
    complete_oauth_session,
    create_oauth_session,
    ensure_schema,
    get_session,
)


class FakeThreadsOAuth:
    def __init__(self, username="alice", user_id="uid-alice"):
        self.username = username
        self.user_id = user_id

    def exchange_code(self, code, redirect_uri):
        self.last_code = code
        self.last_redirect_uri = redirect_uri
        return {"access_token": "short-secret", "user_id": self.user_id}

    def exchange_long_lived(self, short_token):
        assert short_token == "short-secret"
        return {"access_token": "long-secret", "expires_in": 3600}

    def fetch_profile(self, token):
        assert token == "long-secret"
        return {"id": self.user_id, "username": self.username}


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return dict(self.payload)


class RecordingHttp:
    def __init__(self):
        self.post_calls = []
        self.get_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return FakeResponse({"access_token": "short-secret", "user_id": "uid-alice"})

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return FakeResponse({"access_token": "long-secret", "expires_in": 3600})


class ThreadsOAuthClientHttpTests(unittest.TestCase):
    def test_exchange_code_sends_form_body_not_query_params(self):
        http = RecordingHttp()
        client = ThreadsOAuthClient(app_id="123", app_secret="secret", http=http)
        redirect_uri = "https://acp.example/oauth/account-factory/threads/callback"

        result = client.exchange_code("code-1", redirect_uri)

        self.assertEqual("short-secret", result["access_token"])
        self.assertEqual(1, len(http.post_calls))
        _, kwargs = http.post_calls[0]
        self.assertNotIn("params", kwargs)
        self.assertEqual(
            {
                "client_id": "123",
                "client_secret": "secret",
                "code": "code-1",
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            kwargs.get("data"),
        )

    def test_exchange_long_lived_sends_short_token_as_access_token_query_param(self):
        http = RecordingHttp()
        client = ThreadsOAuthClient(app_id="123", app_secret="secret", http=http)

        result = client.exchange_long_lived("short-secret")

        self.assertEqual("long-secret", result["access_token"])
        self.assertEqual(1, len(http.get_calls))
        _, kwargs = http.get_calls[0]
        self.assertEqual(
            {
                "grant_type": "th_exchange_token",
                "client_secret": "secret",
                "access_token": "short-secret",
            },
            kwargs.get("params"),
        )
        self.assertNotIn("headers", kwargs)


class AccountFactoryCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_key = os.environ.get("ACP_MASTER_KEY")
        self.old_env = os.environ.get("ACP_ENV")
        os.environ.pop("ACP_MASTER_KEY", None)
        os.environ["ACP_ENV"] = "development"
        self.conn = sqlite3.connect(os.path.join(self.tmp.name, "factory.db"), isolation_level=None)
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

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()
        if self.old_key is None:
            os.environ.pop("ACP_MASTER_KEY", None)
        else:
            os.environ["ACP_MASTER_KEY"] = self.old_key
        if self.old_env is None:
            os.environ.pop("ACP_ENV", None)
        else:
            os.environ["ACP_ENV"] = self.old_env

    def test_create_session_is_waiting_and_has_one_time_state(self):
        created = create_oauth_session(
            self.conn,
            expected_username="@Alice",
            batch_id="batch-1",
            account_local_id="17",
            ttl_seconds=600,
        )
        self.assertEqual("WAITING_AUTH", created["status"])
        self.assertEqual("alice", created["expected_username"])
        self.assertGreaterEqual(len(created["state"]), 32)
        stored = get_session(self.conn, created["id"])
        self.assertEqual(created["state"], stored["state"])

    def test_account_mismatch_never_creates_or_updates_channel(self):
        created = create_oauth_session(self.conn, expected_username="alice")
        provider = FakeThreadsOAuth(username="bob", user_id="uid-bob")
        with self.assertRaises(AccountMismatchError):
            complete_oauth_session(
                self.conn,
                state=created["state"],
                code="code-1",
                redirect_uri="https://acp.example/oauth/account-factory/threads/callback",
                provider=provider,
            )
        row = self.conn.execute("SELECT COUNT(*) FROM channel").fetchone()
        self.assertEqual(0, row[0])
        session = get_session(self.conn, created["id"])
        self.assertEqual("ACCOUNT_MISMATCH", session["status"])
        self.assertEqual("bob", session["actual_username"])

    def test_success_encrypts_long_lived_token_and_activates_channel(self):
        created = create_oauth_session(self.conn, expected_username="alice")
        result = complete_oauth_session(
            self.conn,
            state=created["state"],
            code="code-2",
            redirect_uri="https://acp.example/oauth/account-factory/threads/callback",
            provider=FakeThreadsOAuth(username="Alice", user_id="uid-alice"),
        )
        self.assertEqual("ACTIVE", result["status"])
        self.assertEqual("alice", result["username"])
        self.assertNotIn("access_token", result)
        row = self.conn.execute("SELECT * FROM channel WHERE external_user_id='uid-alice'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual("@alice", row["handle"])
        self.assertEqual("ACTIVE", row["status"])
        self.assertEqual("long-secret", crypto.decrypt(row["token_encrypted"]))
        self.assertTrue(row["token_expires_at"])
        session = get_session(self.conn, created["id"])
        self.assertEqual("ACTIVE", session["status"])
        self.assertEqual(row["code"], session["channel_code"])

    def test_completed_state_cannot_be_reused(self):
        created = create_oauth_session(self.conn, expected_username="alice")
        complete_oauth_session(
            self.conn,
            state=created["state"],
            code="code-3",
            redirect_uri="https://acp.example/oauth/account-factory/threads/callback",
            provider=FakeThreadsOAuth(),
        )
        with self.assertRaises(ValueError):
            complete_oauth_session(
                self.conn,
                state=created["state"],
                code="code-4",
                redirect_uri="https://acp.example/oauth/account-factory/threads/callback",
                provider=FakeThreadsOAuth(),
            )


if __name__ == "__main__":
    unittest.main()
