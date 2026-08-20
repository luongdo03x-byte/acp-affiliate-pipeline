import os
import sqlite3
import unittest
from unittest.mock import patch

from core.factory_v2.account_credentials import get_account_password
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService


class DefaultAccountPasswordTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_create_batch_encrypts_default_password_without_exposing_it(self):
        with patch.dict(
            os.environ,
            {
                "ACP_ENV": "development",
                "ACP_DEFAULT_ACCOUNT_PASSWORD": "example-secret",
                "ACP_MASTER_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            },
            clear=False,
        ):
            service = FactoryService(self.repo)
            batch = service.create_batch("credential batch", count=1, seed=1)
            account = self.repo.list_accounts(batch["id"])[0]
            self.assertNotIn("password", account)
            self.assertNotIn("password_encrypted", account)
            self.assertEqual(
                "example-secret",
                get_account_password(self.conn, account["id"]),
            )

    def test_production_creation_fails_before_commit_when_default_password_missing(self):
        with patch.dict(os.environ, {"ACP_ENV": "production"}, clear=False):
            os.environ.pop("ACP_DEFAULT_ACCOUNT_PASSWORD", None)
            service = FactoryService(self.repo)
            with self.assertRaisesRegex(RuntimeError, "ACP_DEFAULT_ACCOUNT_PASSWORD"):
                service.create_batch("credential batch", count=1, seed=1)
        self.assertEqual(
            0,
            self.conn.execute("SELECT COUNT(*) FROM factory_batch").fetchone()[0],
        )
        self.assertEqual(
            0,
            self.conn.execute("SELECT COUNT(*) FROM factory_account").fetchone()[0],
        )


if __name__ == "__main__":
    unittest.main()
