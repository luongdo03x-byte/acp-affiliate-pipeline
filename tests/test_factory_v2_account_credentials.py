import base64
import os
import sqlite3
import unittest
from unittest.mock import patch

from core.factory_v2.account_credentials import (
    get_account_password,
    has_account_password,
    store_account_password,
)
from core.factory_v2.schema import ensure_schema


class AccountCredentialTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        with patch.dict(
            os.environ,
            {"ACP_MASTER_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="},
        ):
            ensure_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def _insert_account(self):
        self.conn.execute(
            "INSERT INTO factory_batch(id,name,target_count,status,created_at,completion_mode) "
            "VALUES('b1','b',1,'READY','2026-08-20T00:00:00+00:00','ACP_ACTIVE')"
        )
        self.conn.execute(
            """INSERT INTO factory_account(
                id,batch_id,sequence,group_no,username,display_name,stage,last_safe_stage,created_at,updated_at
            ) VALUES(
                'a1','b1',1,1,'user1','User 1','PROFILE_READY','PROFILE_READY',
                '2026-08-20T00:00:00+00:00','2026-08-20T00:00:00+00:00'
            )"""
        )

    def test_password_round_trips_but_plaintext_is_not_stored(self):
        self._insert_account()
        with patch.dict(
            os.environ,
            {"ACP_MASTER_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="},
        ):
            store_account_password(self.conn, "a1", "example-secret")
            self.assertEqual("example-secret", get_account_password(self.conn, "a1"))
        row = self.conn.execute(
            "SELECT password_encrypted FROM factory_account_credential WHERE account_id='a1'"
        ).fetchone()
        self.assertIsInstance(row["password_encrypted"], bytes)
        self.assertNotIn(b"example-secret", row["password_encrypted"])

    def test_missing_credential_returns_none(self):
        self._insert_account()
        self.assertIsNone(get_account_password(self.conn, "a1"))
        self.assertFalse(has_account_password(self.conn, "a1"))

    def test_empty_password_is_rejected(self):
        self._insert_account()
        with self.assertRaisesRegex(ValueError, "password"):
            store_account_password(self.conn, "a1", "")

    def test_wrong_master_key_raises_safe_domain_error(self):
        self._insert_account()
        key_a = base64.b64encode(b"\x01" * 32).decode()
        key_b = base64.b64encode(b"\x02" * 32).decode()
        with patch.dict(os.environ, {"ACP_MASTER_KEY": key_a}):
            store_account_password(self.conn, "a1", "example-secret")
        with patch.dict(os.environ, {"ACP_MASTER_KEY": key_b}):
            with self.assertRaisesRegex(RuntimeError, "^CREDENTIAL_DECRYPT_FAILED$"):
                get_account_password(self.conn, "a1")


if __name__ == "__main__":
    unittest.main()
