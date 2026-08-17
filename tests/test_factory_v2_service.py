import sqlite3
import unittest

from core.factory_v2.models import AccountStage as S
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService


class FactoryV2ServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)

    def tearDown(self):
        self.conn.close()

    def test_create_batch_persists_50_profile_ready_accounts(self):
        batch = self.service.create_batch("Batch 01", seed=17082026)
        accounts = self.repo.list_accounts(batch["id"])
        self.assertEqual(50, len(accounts))
        self.assertTrue(all(a["stage"] == "PROFILE_READY" for a in accounts))
        self.assertTrue(all(a["last_safe_stage"] == "PROFILE_READY" for a in accounts))
        self.assertEqual(50, len({a["username"] for a in accounts}))

    def test_schema_has_no_sensitive_credential_columns(self):
        cols = {r[1].lower() for r in self.conn.execute("PRAGMA table_info(factory_account)")}
        forbidden = {"password", "otp", "captcha", "access_token", "app_secret", "master_key", "selfie"}
        self.assertTrue(forbidden.isdisjoint(cols))

    def test_illegal_transition_is_rejected(self):
        batch = self.service.create_batch("Batch 01", count=1, seed=1)
        account = self.repo.list_accounts(batch["id"])[0]
        with self.assertRaises(ValueError):
            self.service.transition_account(account["id"], S.ACP_ACTIVE)

    def test_retry_account_is_idempotent_once_retry_is_pending(self):
        batch = self.service.create_batch("Batch 01", count=1, seed=2)
        account = self.repo.list_accounts(batch["id"])[0]
        self.service.transition_account(account["id"], S.ERROR, error_code="NETWORK_TRANSIENT")
        first = self.service.retry_account(account["id"])
        second = self.service.retry_account(account["id"])

        self.assertEqual(S.RETRY_PENDING.value, first["stage"])
        self.assertEqual(S.RETRY_PENDING.value, second["stage"])
        self.assertEqual(first["id"], second["id"])


if __name__ == "__main__":
    unittest.main()
