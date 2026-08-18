import os
import sqlite3
import tempfile
import unittest
from datetime import date

from flask import Flask

from core import db
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService
from web.factory_v2 import register_factory_v2_routes


class FactoryV2SocialInputSchemaTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)

    def tearDown(self):
        self.conn.close()

    def _insert_remote_worker(self):
        self.repo.insert_worker({
            "id": "worker-social-01",
            "runner_type": "REMOTE_AVD",
            "avd_name": "acp-worker-01",
            "adb_serial": "emulator-5554",
            "state": "READY",
        })

    def test_schema_adds_social_signup_columns_and_default_completion_mode(self):
        batch_columns = {
            row[1]: row for row in self.conn.execute("PRAGMA table_info(factory_batch)")
        }
        account_columns = {
            row[1]: row for row in self.conn.execute("PRAGMA table_info(factory_account)")
        }

        self.assertIn("completion_mode", batch_columns)
        self.assertIn("signup_contact_type", account_columns)
        self.assertIn("phone", account_columns)
        self.assertIn("email", account_columns)
        self.assertIn("birth_date", account_columns)

        batch = self.service.create_batch("Backward compatible", count=1, seed=1)
        self.assertEqual("ACP_ACTIVE", batch["completion_mode"])

    def test_service_persists_social_only_signup_input(self):
        self._insert_remote_worker()

        result = self.service.create_single_account(
            execution_target="worker-social-01",
            batch_name="Social only",
            completion_mode="SOCIAL_ONLY",
            signup_contact_type="phone",
            phone="+84901234567",
            email="owner@example.com",
            birth_date="2000-05-20",
        )

        self.assertEqual("SOCIAL_ONLY", result["batch"]["completion_mode"])
        account = result["account"]
        self.assertEqual("phone", account["signup_contact_type"])
        self.assertEqual("+84901234567", account["phone"])
        self.assertEqual("owner@example.com", account["email"])
        self.assertEqual("2000-05-20", account["birth_date"])

    def test_service_requires_selected_contact(self):
        self._insert_remote_worker()

        with self.assertRaisesRegex(ValueError, "phone"):
            self.service.create_single_account(
                execution_target="worker-social-01",
                completion_mode="SOCIAL_ONLY",
                signup_contact_type="phone",
                phone=None,
                birth_date="2000-05-20",
            )

    def test_service_rejects_non_adult_birth_date(self):
        self._insert_remote_worker()

        with self.assertRaisesRegex(ValueError, "18"):
            self.service.create_single_account(
                execution_target="worker-social-01",
                completion_mode="SOCIAL_ONLY",
                signup_contact_type="email",
                email="owner@example.com",
                birth_date=date.today().isoformat(),
            )

    def test_service_rejects_avatar_path_traversal(self):
        self._insert_remote_worker()

        with self.assertRaisesRegex(ValueError, "avatar_file"):
            self.service.create_single_account(
                execution_target="worker-social-01",
                completion_mode="SOCIAL_ONLY",
                signup_contact_type="email",
                email="owner@example.com",
                birth_date="2000-05-20",
                avatar_file="../outside.jpg",
            )


class FactoryV2SocialInputApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_factory_key = os.environ.get("ACP_FACTORY_API_KEY")
        db.DB_PATH = os.path.join(self.tmp.name, "factory-v2-social.db")
        os.environ["ACP_FACTORY_API_KEY"] = "test-key"

        conn = db.connect()
        ensure_schema(conn)
        repo = FactoryRepository(conn)
        repo.insert_worker({
            "id": "worker-social-01",
            "runner_type": "REMOTE_AVD",
            "avd_name": "acp-worker-01",
            "adb_serial": "emulator-5554",
            "state": "READY",
        })
        conn.close()

        app = Flask(__name__)
        app.testing = True
        register_factory_v2_routes(app)
        self.client = app.test_client()
        self.auth = {"X-ACP-Factory-Key": "test-key"}

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        if self.old_factory_key is None:
            os.environ.pop("ACP_FACTORY_API_KEY", None)
        else:
            os.environ["ACP_FACTORY_API_KEY"] = self.old_factory_key
        self.tmp.cleanup()

    def test_create_social_only_account_accepts_routine_signup_input(self):
        response = self.client.post(
            "/api/factory/v2/accounts",
            headers=self.auth,
            json={
                "execution_target": "worker-social-01",
                "batch_name": "Social pilot",
                "completion_mode": "SOCIAL_ONLY",
                "signup_contact_type": "email",
                "email": "owner@example.com",
                "phone": "+84901234567",
                "birth_date": "2000-05-20",
            },
        )

        self.assertEqual(201, response.status_code, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual("SOCIAL_ONLY", body["batch"]["completion_mode"])
        self.assertEqual("email", body["account"]["signup_contact_type"])
        self.assertEqual("owner@example.com", body["account"]["email"])
        self.assertEqual("2000-05-20", body["account"]["birth_date"])
        self.assertNotIn("password", response.get_data(as_text=True).lower())

    def test_create_account_still_rejects_password_input(self):
        response = self.client.post(
            "/api/factory/v2/accounts",
            headers=self.auth,
            json={
                "execution_target": "worker-social-01",
                "completion_mode": "SOCIAL_ONLY",
                "signup_contact_type": "email",
                "email": "owner@example.com",
                "birth_date": "2000-05-20",
                "password": "must-not-be-stored",
            },
        )

        self.assertEqual(400, response.status_code)
        self.assertNotIn("must-not-be-stored", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
