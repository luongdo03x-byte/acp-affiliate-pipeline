import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from flask import Flask

from core.account_factory import create_oauth_session, ensure_schema


class FakeThreadsOAuth:
    def authorization_url(self, state, redirect_uri):
        return f"https://threads.example/authorize?state={state}&redirect_uri={redirect_uri}"

    def exchange_code(self, code, redirect_uri):
        self.code = code
        self.redirect_uri = redirect_uri
        return {"access_token": "short-secret", "user_id": "uid-browser"}

    def exchange_long_lived(self, short_token):
        self.short_token = short_token
        return {"access_token": "long-secret", "expires_in": 3600}

    def fetch_profile(self, token):
        self.profile_token = token
        return {"id": "uid-browser", "username": "browser.account"}


class ThreadsChannelOAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "oauth.db")
        self.old_env = os.environ.get("ACP_ENV")
        self.old_public_base = os.environ.get("ACP_PUBLIC_BASE_URL")
        os.environ["ACP_ENV"] = "development"
        os.environ.pop("ACP_PUBLIC_BASE_URL", None)
        conn = self._connect()
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
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()
        if self.old_env is None:
            os.environ.pop("ACP_ENV", None)
        else:
            os.environ["ACP_ENV"] = self.old_env
        if self.old_public_base is None:
            os.environ.pop("ACP_PUBLIC_BASE_URL", None)
        else:
            os.environ["ACP_PUBLIC_BASE_URL"] = self.old_public_base

    def _connect(self):
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _build_app(self, *, admin_password=""):
        from web.threads_oauth import register_threads_channel_oauth_routes

        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.config["THREADS_CHANNEL_OAUTH_FACTORY"] = FakeThreadsOAuth

        @app.get("/kenh")
        def channels():
            return "channels"

        register_threads_channel_oauth_routes(app, admin_password=admin_password)
        return app

    def test_start_and_callback_auto_discover_and_activate_threads_account(self):
        app = self._build_app()
        with patch("web.threads_oauth.connect", side_effect=self._connect):
            client = app.test_client()
            start = client.get("/oauth/threads/start", base_url="https://acp.example")
            self.assertEqual(302, start.status_code)
            authorize = urlparse(start.headers["Location"])
            query = parse_qs(authorize.query)
            state = query["state"][0]
            self.assertTrue(state)
            self.assertEqual(
                ["https://acp.example/oauth/threads/connect/callback"],
                query["redirect_uri"],
            )

            callback = client.get(
                f"/oauth/threads/connect/callback?state={state}&code=browser-code",
                base_url="https://acp.example",
            )
            self.assertEqual(302, callback.status_code)
            self.assertIn("/kenh?summary=", callback.headers["Location"])

        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM channel WHERE external_user_id='uid-browser'"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual("@browser.account", row["handle"])
        self.assertEqual("ACTIVE", row["status"])

    def test_start_uses_configured_public_base_url_for_redirect_uri(self):
        os.environ["ACP_PUBLIC_BASE_URL"] = "https://public-acp.example/"
        app = self._build_app()
        with patch("web.threads_oauth.connect", side_effect=self._connect):
            response = app.test_client().get(
                "/oauth/threads/start", base_url="http://127.0.0.1:5000"
            )
        query = parse_qs(urlparse(response.headers["Location"]).query)
        self.assertEqual(
            ["https://public-acp.example/oauth/threads/connect/callback"],
            query["redirect_uri"],
        )

    def test_callback_rejects_unknown_state_without_creating_channel(self):
        app = self._build_app()
        with patch("web.threads_oauth.connect", side_effect=self._connect):
            client = app.test_client()
            response = client.get(
                "/oauth/threads/connect/callback?state=unknown&code=browser-code",
                base_url="https://acp.example",
            )
            self.assertEqual(302, response.status_code)
            self.assertIn("/kenh?err=", response.headers["Location"])

        conn = self._connect()
        count = conn.execute("SELECT COUNT(*) FROM channel").fetchone()[0]
        conn.close()
        self.assertEqual(0, count)

    def test_callback_completes_one_time_state_without_dashboard_session(self):
        app = self._build_app(admin_password="dashboard-password")
        conn = self._connect()
        oauth_session = create_oauth_session(conn)
        conn.close()

        with patch("web.threads_oauth.connect", side_effect=self._connect):
            response = app.test_client().get(
                f"/oauth/threads/connect/callback?state={oauth_session['state']}&code=browser-code",
                base_url="https://acp.example",
            )

        self.assertEqual(302, response.status_code)
        self.assertIn("/kenh?summary=", response.headers["Location"])
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM channel WHERE external_user_id='uid-browser'"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()
