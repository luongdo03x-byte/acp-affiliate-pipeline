import sqlite3
import unittest

from core.account_factory import ensure_schema as ensure_oauth_schema
from core.factory_v2.activation import FactoryActivationService
from core.factory_v2.oauth_config import build_factory_redirect_uri
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService


class FakeProvider:
    def authorization_url(self, state, redirect_uri):
        return f"https://threads.example/authorize?state={state}&redirect={redirect_uri}"


class FactoryV2ActivationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        ensure_oauth_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        batch = self.service.create_batch("Activation", count=1, seed=22)
        self.account = self.repo.list_accounts(batch["id"])[0]
        self.conn.execute(
            """UPDATE factory_account
               SET stage='THREADS_CREATED', last_safe_stage='THREADS_CREATED'
               WHERE id=?""",
            (self.account["id"],),
        )
        self.activation = FactoryActivationService(
            self.conn,
            provider=FakeProvider(),
            public_base_url="https://factory.example.com/",
        )

    def tearDown(self):
        self.conn.close()

    def test_factory_redirect_uri_is_exact_callback(self):
        self.assertEqual(
            "https://factory.example.com/oauth/account-factory/threads/callback",
            build_factory_redirect_uri("https://factory.example.com/"),
        )

    def test_oauth_schema_does_not_create_publish_tables(self):
        tables = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertIn("account_factory_oauth_session", tables)
        self.assertIn("channel", tables)
        self.assertNotIn("post", tables)
        self.assertNotIn("product", tables)

    def test_threads_created_starts_oauth_and_marks_connecting(self):
        result = self.activation.start(self.account["id"])
        saved = self.repo.get_account(self.account["id"])
        self.assertEqual("ACP_CONNECTING", saved["stage"])
        self.assertEqual("THREADS_CREATED", saved["last_safe_stage"])
        self.assertEqual(result["session_id"], saved["oauth_session_id"])
        self.assertTrue(result["authorization_url"].startswith("https://"))

    def test_start_is_idempotent_for_waiting_session(self):
        first = self.activation.start(self.account["id"])
        second = self.activation.start(self.account["id"])
        self.assertEqual(first["session_id"], second["session_id"])
        count = self.conn.execute(
            "SELECT COUNT(*) FROM account_factory_oauth_session WHERE account_local_id=?",
            (self.account["id"],),
        ).fetchone()[0]
        self.assertEqual(1, count)

    def test_retry_requires_threads_safe_stage(self):
        self.conn.execute(
            """UPDATE factory_account
               SET stage='RETRY_PENDING', last_safe_stage='PROFILE_READY', last_error_code='OAUTH_FAILED'
               WHERE id=?""",
            (self.account["id"],),
        )
        with self.assertRaises(ValueError):
            self.activation.start(self.account["id"])

    def test_explicit_retry_clears_failure_gate_then_starts_new_oauth(self):
        self.conn.execute(
            """UPDATE factory_account
               SET stage='RETRY_PENDING', last_safe_stage='THREADS_CREATED', last_error_code='OAUTH_FAILED'
               WHERE id=?""",
            (self.account["id"],),
        )

        gated = self.repo.get_account(self.account["id"])
        with self.assertRaises(ValueError):
            self.activation.start(gated["id"])

        approved = self.service.retry_account(self.account["id"])
        self.assertIsNone(approved["last_error_code"])
        result = self.activation.start(self.account["id"])

        saved = self.repo.get_account(self.account["id"])
        self.assertEqual("ACP_CONNECTING", saved["stage"])
        self.assertEqual("THREADS_CREATED", saved["last_safe_stage"])
        self.assertEqual(result["session_id"], saved["oauth_session_id"])


if __name__ == "__main__":
    unittest.main()
