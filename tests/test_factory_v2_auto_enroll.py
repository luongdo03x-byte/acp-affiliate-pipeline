import hashlib
import os
import tempfile
import unittest

from core import db
from core.factory_v2.device_credentials import (
    authenticate_device_token,
    issue_device_token,
    revoke_device_token,
)
from core.factory_v2.schema import ensure_schema


class DeviceCredentialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "factory-v2.db")
        self.conn = db.connect()
        ensure_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def test_issue_stores_only_hash_and_authenticates_raw_token(self):
        token = issue_device_token(self.conn, "phone-12345678", "Pixel test")
        self.assertGreaterEqual(len(token), 32)
        row = self.conn.execute(
            "SELECT * FROM factory_device_credential WHERE device_id=?",
            ("phone-12345678",),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertNotIn(token, str(dict(row)))
        self.assertEqual(hashlib.sha256(token.encode()).hexdigest(), row["token_hash"])
        authenticated = authenticate_device_token(self.conn, token)
        self.assertEqual("phone-12345678", authenticated["device_id"])

    def test_reenroll_rotates_previous_token(self):
        first = issue_device_token(self.conn, "phone-12345678", "Phone")
        second = issue_device_token(self.conn, "phone-12345678", "Phone renamed")
        self.assertNotEqual(first, second)
        self.assertIsNone(authenticate_device_token(self.conn, first))
        current = authenticate_device_token(self.conn, second)
        self.assertEqual("Phone renamed", current["device_name"])
        count = self.conn.execute(
            "SELECT COUNT(*) FROM factory_device_credential WHERE device_id='phone-12345678'"
        ).fetchone()[0]
        self.assertEqual(1, count)

    def test_revoked_token_is_rejected(self):
        token = issue_device_token(self.conn, "phone-12345678", "Phone")
        self.assertTrue(revoke_device_token(self.conn, "phone-12345678"))
        self.assertIsNone(authenticate_device_token(self.conn, token))

    def test_device_id_validation(self):
        with self.assertRaises(ValueError):
            issue_device_token(self.conn, "short", "Phone")
        with self.assertRaises(ValueError):
            issue_device_token(self.conn, "x" * 161, "Phone")


class AutoEnrollApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_factory_key = os.environ.get("ACP_FACTORY_API_KEY")
        self.old_auto_enroll = os.environ.get("ACP_FACTORY_LAN_AUTO_ENROLL")
        db.DB_PATH = os.path.join(self.tmp.name, "factory-v2.db")
        os.environ["ACP_FACTORY_API_KEY"] = "legacy-key"
        os.environ.pop("ACP_FACTORY_LAN_AUTO_ENROLL", None)
        conn = db.connect()
        ensure_schema(conn)
        conn.close()

        from account_factory_server import build_app

        app = build_app()
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        if self.old_factory_key is None:
            os.environ.pop("ACP_FACTORY_API_KEY", None)
        else:
            os.environ["ACP_FACTORY_API_KEY"] = self.old_factory_key
        if self.old_auto_enroll is None:
            os.environ.pop("ACP_FACTORY_LAN_AUTO_ENROLL", None)
        else:
            os.environ["ACP_FACTORY_LAN_AUTO_ENROLL"] = self.old_auto_enroll
        self.tmp.cleanup()

    def test_discovery_is_public_and_secret_free(self):
        res = self.client.get("/api/factory/discovery")
        self.assertEqual(200, res.status_code)
        body = res.get_json()
        self.assertEqual("account-factory", body["service"])
        self.assertEqual(2, body["api_version"])
        text = res.get_data(as_text=True).lower()
        for forbidden in ("factory_key", "token", "secret", "master_key"):
            self.assertNotIn(forbidden, text)

    def test_auto_enroll_is_disabled_by_default(self):
        res = self.client.post(
            "/api/factory/enroll",
            json={"device_id": "phone-12345678", "device_name": "Phone"},
            environ_base={"REMOTE_ADDR": "192.168.1.21"},
        )
        self.assertEqual(403, res.status_code)

    def test_auto_enroll_rejects_public_remote(self):
        os.environ["ACP_FACTORY_LAN_AUTO_ENROLL"] = "true"
        res = self.client.post(
            "/api/factory/enroll",
            json={"device_id": "phone-12345678", "device_name": "Phone"},
            environ_base={"REMOTE_ADDR": "8.8.8.8"},
        )
        self.assertEqual(403, res.status_code)

    def test_private_lan_enroll_returns_device_token_and_token_auth_works(self):
        os.environ["ACP_FACTORY_LAN_AUTO_ENROLL"] = "true"
        enrolled = self.client.post(
            "/api/factory/enroll",
            json={"device_id": "phone-12345678", "device_name": "Phone"},
            environ_base={"REMOTE_ADDR": "192.168.1.21"},
        )
        self.assertEqual(201, enrolled.status_code)
        token = enrolled.get_json()["device_token"]
        self.assertTrue(token)

        dashboard = self.client.get(
            "/api/factory/v2/dashboard",
            headers={"X-ACP-Device-Token": token},
        )
        self.assertEqual(200, dashboard.status_code)

    def test_enrolled_token_works_in_existing_android_factory_key_slot(self):
        os.environ["ACP_FACTORY_LAN_AUTO_ENROLL"] = "true"
        enrolled = self.client.post(
            "/api/factory/enroll",
            json={"device_id": "phone-android01", "device_name": "Android"},
            environ_base={"REMOTE_ADDR": "192.168.1.22"},
        )
        self.assertEqual(201, enrolled.status_code)
        token = enrolled.get_json()["device_token"]
        dashboard = self.client.get(
            "/api/factory/v2/dashboard",
            headers={"X-ACP-Factory-Key": token},
        )
        self.assertEqual(200, dashboard.status_code)

    def test_invalid_device_token_is_401_and_legacy_key_still_works(self):
        invalid = self.client.get(
            "/api/factory/v2/dashboard",
            headers={"X-ACP-Device-Token": "invalid-token"},
        )
        self.assertEqual(401, invalid.status_code)
        legacy = self.client.get(
            "/api/factory/v2/dashboard",
            headers={"X-ACP-Factory-Key": "legacy-key"},
        )
        self.assertEqual(200, legacy.status_code)

    def test_reenroll_rotates_device_token(self):
        os.environ["ACP_FACTORY_LAN_AUTO_ENROLL"] = "true"
        first = self.client.post(
            "/api/factory/enroll",
            json={"device_id": "phone-12345678", "device_name": "Phone"},
            environ_base={"REMOTE_ADDR": "10.0.0.5"},
        ).get_json()["device_token"]
        second = self.client.post(
            "/api/factory/enroll",
            json={"device_id": "phone-12345678", "device_name": "Phone"},
            environ_base={"REMOTE_ADDR": "10.0.0.5"},
        ).get_json()["device_token"]
        self.assertNotEqual(first, second)
        old = self.client.get(
            "/api/factory/v2/dashboard",
            headers={"X-ACP-Device-Token": first},
        )
        new = self.client.get(
            "/api/factory/v2/dashboard",
            headers={"X-ACP-Device-Token": second},
        )
        self.assertEqual(401, old.status_code)
        self.assertEqual(200, new.status_code)


if __name__ == "__main__":
    unittest.main()
