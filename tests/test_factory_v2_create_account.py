import os
import sqlite3
import tempfile
import unittest

from flask import Flask

from core import db
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService
from web.factory_v2 import register_factory_v2_routes


class FactoryV2CreateAccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_factory_key = os.environ.get("ACP_FACTORY_API_KEY")
        db.DB_PATH = os.path.join(self.tmp.name, "factory-v2.db")
        os.environ["ACP_FACTORY_API_KEY"] = "test-key"

        self.conn = db.connect()
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)

        app = Flask(__name__)
        app.testing = True
        register_factory_v2_routes(app)
        self.client = app.test_client()
        self.auth = {"X-ACP-Factory-Key": "test-key"}

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_db_path
        if self.old_factory_key is None:
            os.environ.pop("ACP_FACTORY_API_KEY", None)
        else:
            os.environ["ACP_FACTORY_API_KEY"] = self.old_factory_key
        self.tmp.cleanup()

    def seed_worker(self, worker_id, runner_type, state="READY", draining=0):
        row = {
            "id": worker_id,
            "runner_type": runner_type,
            "state": state,
            "draining": draining,
        }
        if runner_type == "LOCAL_DEVICE":
            row["device_id"] = f"{worker_id}-device"
            row["device_name"] = worker_id
        else:
            row["avd_name"] = f"{worker_id}-avd"
        return self.repo.insert_worker(row)

    def bind_remote_job(self, account_id, *, worker_id="worker-username", job_id="job-username"):
        worker = self.seed_worker(worker_id, "REMOTE_AVD")
        self.repo.create_job_lease({
            "id": job_id,
            "account_id": account_id,
            "worker_id": worker_id,
            "runner_type": "REMOTE_AVD",
            "lease_token": f"lease-{job_id}",
            "state": "RUNNING",
            "desired_action": "AUTOMATE_INSTAGRAM",
            "leased_at": "2026-08-19T10:00:00+00:00",
            "lease_expires_at": "2026-08-19T10:10:00+00:00",
        })
        self.conn.execute(
            """UPDATE factory_account
               SET assigned_worker_id=?, current_job_id=?
               WHERE id=?""",
            (worker_id, job_id, account_id),
        )
        self.conn.execute(
            """UPDATE factory_worker
               SET state='RUNNING', current_account_id=?, current_job_id=?
               WHERE id=?""",
            (account_id, job_id, worker_id),
        )
        return worker_id, job_id

    def test_create_single_account_for_local_runner(self):
        phone = self.seed_worker("phone-1", "LOCAL_DEVICE")

        result = self.service.create_single_account(execution_target=phone["id"])
        account = result["account"]

        self.assertEqual("PROFILE_READY", account["stage"])
        self.assertEqual(phone["id"], account["execution_target"])
        self.assertIsNone(account["assigned_worker_id"])
        self.assertEqual(1, result["batch"]["target_count"])

    def test_create_single_account_rejects_not_ready_exact_runner(self):
        phone = self.seed_worker("phone-2", "LOCAL_DEVICE", state="RUNNING")
        with self.assertRaises(ValueError):
            self.service.create_single_account(execution_target=phone["id"])

    def test_create_single_account_accepts_auto_avd_without_preselecting_worker(self):
        result = self.service.create_single_account(execution_target="AUTO_AVD")
        self.assertEqual("AUTO_AVD", result["account"]["execution_target"])
        self.assertIsNone(result["account"]["assigned_worker_id"])

    def test_worker_selected_username_updates_bound_account(self):
        result = self.service.create_single_account(
            execution_target="AUTO_AVD",
            completion_mode="SOCIAL_ONLY",
        )
        account_id = result["account"]["id"]
        worker_id, job_id = self.bind_remote_job(account_id)

        updated = self.service.update_worker_selected_username(
            account_id,
            job_id=job_id,
            worker_id=worker_id,
            username="baongocd483102",
        )

        self.assertEqual("baongocd483102", updated["username"])

    def test_worker_selected_username_rejects_binding_mismatch(self):
        result = self.service.create_single_account(execution_target="AUTO_AVD")
        account_id = result["account"]["id"]
        worker_id, _ = self.bind_remote_job(account_id)

        with self.assertRaisesRegex(ValueError, "binding"):
            self.service.update_worker_selected_username(
                account_id,
                job_id="wrong-job",
                worker_id=worker_id,
                username="baongocd483102",
            )

    def test_worker_selected_username_rejects_invalid_value(self):
        result = self.service.create_single_account(execution_target="AUTO_AVD")
        account_id = result["account"]["id"]
        worker_id, job_id = self.bind_remote_job(account_id)

        with self.assertRaisesRegex(ValueError, "username"):
            self.service.update_worker_selected_username(
                account_id,
                job_id=job_id,
                worker_id=worker_id,
                username="INVALID USERNAME",
            )

    def test_worker_selected_username_preserves_batch_uniqueness(self):
        batch = self.service.create_batch(
            "username uniqueness",
            count=2,
            execution_target="AUTO_AVD",
            completion_mode="SOCIAL_ONLY",
        )
        first, second = self.repo.list_accounts(batch["id"])
        worker_id, job_id = self.bind_remote_job(first["id"])

        with self.assertRaises(sqlite3.IntegrityError):
            self.service.update_worker_selected_username(
                first["id"],
                job_id=job_id,
                worker_id=worker_id,
                username=second["username"],
            )

    def test_create_account_requires_execution_target(self):
        res = self.client.post("/api/factory/v2/accounts", headers=self.auth, json={})
        self.assertEqual(400, res.status_code)

    def test_create_account_for_phone(self):
        phone = self.seed_worker("phone-api", "LOCAL_DEVICE")
        res = self.client.post(
            "/api/factory/v2/accounts",
            headers=self.auth,
            json={"execution_target": phone["id"], "batch_name": "Phone pilot"},
        )
        self.assertEqual(201, res.status_code)
        body = res.get_json()
        self.assertEqual("PROFILE_READY", body["account"]["stage"])
        self.assertEqual(phone["id"], body["account"]["execution_target"])
        self.assertIsNone(body["account"]["assigned_worker_id"])
        self.assertEqual("Phone pilot", body["batch"]["name"])

    def test_create_account_rejects_client_control_fields(self):
        phone = self.seed_worker("phone-sensitive", "LOCAL_DEVICE")
        res = self.client.post(
            "/api/factory/v2/accounts",
            headers=self.auth,
            json={"execution_target": phone["id"], "stage": "ACP_ACTIVE"},
        )
        self.assertEqual(400, res.status_code)


if __name__ == "__main__":
    unittest.main()
