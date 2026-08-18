import os
import tempfile
import unittest

from flask import Flask

from core import db
from core.account_factory import get_session
from core.db import now, ulid
from core.factory_v2.models import AccountStage
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.scheduler import Scheduler
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService
from web.factory_v2 import register_factory_v2_routes


class FakeAuthorizationProvider:
    def authorization_url(self, state, redirect_uri):
        self.state = state
        self.redirect_uri = redirect_uri
        return f"https://threads.example/authorize?state={state}"


class FactoryV2ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_factory_key = os.environ.get("ACP_FACTORY_API_KEY")
        self.old_public_base = os.environ.get("ACP_PUBLIC_BASE_URL")
        db.DB_PATH = os.path.join(self.tmp.name, "factory-v2.db")
        os.environ["ACP_FACTORY_API_KEY"] = "test-key"
        os.environ["ACP_PUBLIC_BASE_URL"] = "https://acp.example"

        self.conn = db.connect()
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        self.batch = self.service.create_batch("Batch 01", count=3, seed=7)
        self.repo.insert_worker({
            "id": "worker-01",
            "avd_name": "acp-worker-01",
            "adb_serial": "emulator-5554",
            "state": "READY",
        })
        self.repo.insert_resource_sample({
            "timestamp": "2026-08-17T06:00:00+00:00",
            "cpu_percent": 58.0,
            "ram_total_mb": 16384,
            "ram_available_mb": 8400,
            "swap_used_mb": 200,
            "swap_in_rate": 0.0,
            "load_1m": 1.2,
            "load_5m": 1.0,
            "avd_total": 1,
            "avd_running": 0,
            "avd_waiting_human": 0,
            "capacity_state": "YELLOW",
            "desired_workers": 1,
        })

        self.oauth_provider = FakeAuthorizationProvider()
        app = Flask(__name__)
        app.testing = True
        app.config["ACCOUNT_FACTORY_OAUTH_FACTORY"] = lambda: self.oauth_provider
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
        if self.old_public_base is None:
            os.environ.pop("ACP_PUBLIC_BASE_URL", None)
        else:
            os.environ["ACP_PUBLIC_BASE_URL"] = self.old_public_base
        self.tmp.cleanup()

    def seed_running_job(self):
        scheduler = Scheduler(self.repo, self.service, lease_seconds=120)
        return scheduler.assign_next("worker-01")

    def seed_waiting_checkpoint(self):
        job = self.seed_running_job()
        self.service.transition_account(job["account_id"], AccountStage.IG_READY_FOR_HUMAN)
        self.service.transition_account(job["account_id"], AccountStage.WAITING_HUMAN)
        self.conn.execute(
            "UPDATE factory_job SET state='WAITING_HUMAN' WHERE id=?", (job["id"],)
        )
        self.conn.execute(
            "UPDATE factory_worker SET state='WAITING_HUMAN' WHERE id='worker-01'"
        )
        checkpoint_id = ulid()
        self.repo.create_checkpoint({
            "id": checkpoint_id,
            "batch_id": self.batch["id"],
            "account_id": job["account_id"],
            "worker_id": "worker-01",
            "type": "IG_POSTCHECK",
            "status": "OPEN",
            "message": "Confirm Instagram account state",
            "created_at": now(),
        })
        return checkpoint_id

    def seed_threads_created(self, username="maianh.le"):
        account = self.repo.list_accounts(self.batch["id"])[0]
        self.conn.execute(
            "UPDATE factory_account SET username=? WHERE id=?", (username, account["id"])
        )
        for stage in (
            AccountStage.AVD_ASSIGNED,
            AccountStage.IG_READY_FOR_HUMAN,
            AccountStage.IG_CREATED,
            AccountStage.THREADS_READY_FOR_HUMAN,
            AccountStage.THREADS_CREATED,
        ):
            self.service.transition_account(account["id"], stage)
        return self.repo.get_account(account["id"])

    def assert_no_sensitive_keys(self, value):
        forbidden = ("token", "password", "otp", "captcha", "secret", "master_key")
        if isinstance(value, dict):
            for key, child in value.items():
                lowered = str(key).lower()
                self.assertFalse(any(term in lowered for term in forbidden), key)
                self.assert_no_sensitive_keys(child)
        elif isinstance(value, list):
            for child in value:
                self.assert_no_sensitive_keys(child)

    def test_dashboard_requires_factory_key(self):
        res = self.client.get("/api/factory/v2/dashboard")
        self.assertEqual(401, res.status_code)

    def test_dashboard_returns_controller_counts(self):
        res = self.client.get("/api/factory/v2/dashboard", headers=self.auth)
        self.assertEqual(200, res.status_code)
        body = res.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(self.batch["id"], body["batch"]["id"])
        self.assertEqual(3, body["accounts"]["total"])
        self.assertEqual(3, body["accounts"]["queued"])
        self.assertEqual(1, body["workers"]["total"])
        self.assertEqual("YELLOW", body["host"]["capacity_state"])
        self.assertNotIn("adb_serial", str(body))

    def test_dashboard_counts_threads_created_as_active_for_social_only(self):
        batch = self.service.create_batch(
            "Social only", count=1, seed=17, completion_mode="SOCIAL_ONLY"
        )
        account = self.repo.list_accounts(batch["id"])[0]
        self.conn.execute(
            """UPDATE factory_account
               SET stage='THREADS_CREATED', last_safe_stage='THREADS_CREATED', completed_at=?
               WHERE id=?""",
            (now(), account["id"]),
        )

        res = self.client.get("/api/factory/v2/dashboard", headers=self.auth)

        self.assertEqual(200, res.status_code)
        body = res.get_json()
        self.assertEqual("SOCIAL_ONLY", body["batch"]["completion_mode"])
        self.assertEqual(1, body["accounts"]["active"])
        self.assertEqual(0, body["accounts"]["queued"])

    def test_dashboard_keeps_threads_created_incomplete_for_acp_active_mode(self):
        batch = self.service.create_batch(
            "ACP completion", count=1, seed=18, completion_mode="ACP_ACTIVE"
        )
        account = self.repo.list_accounts(batch["id"])[0]
        self.conn.execute(
            """UPDATE factory_account
               SET stage='THREADS_CREATED', last_safe_stage='THREADS_CREATED'
               WHERE id=?""",
            (account["id"],),
        )

        res = self.client.get("/api/factory/v2/dashboard", headers=self.auth)

        self.assertEqual(200, res.status_code)
        body = res.get_json()
        self.assertEqual("ACP_ACTIVE", body["batch"]["completion_mode"])
        self.assertEqual(0, body["accounts"]["active"])
        self.assertEqual(1, body["accounts"]["queued"])

    def test_read_endpoints_return_controller_rows(self):
        batch = self.client.get(
            f"/api/factory/v2/batches/{self.batch['id']}", headers=self.auth
        )
        accounts = self.client.get("/api/factory/v2/accounts", headers=self.auth)
        workers = self.client.get("/api/factory/v2/workers", headers=self.auth)
        checkpoints = self.client.get("/api/factory/v2/checkpoints", headers=self.auth)
        self.assertEqual(200, batch.status_code)
        self.assertEqual(3, len(accounts.get_json()["accounts"]))
        self.assertEqual(1, len(workers.get_json()["workers"]))
        self.assertEqual([], checkpoints.get_json()["checkpoints"])
        self.assertNotIn("adb_serial", workers.get_data(as_text=True))

    def test_continue_does_not_blindly_mark_checkpoint_success(self):
        checkpoint_id = self.seed_waiting_checkpoint()
        res = self.client.post(
            f"/api/factory/v2/checkpoints/{checkpoint_id}/continue",
            headers=self.auth,
        )
        self.assertEqual(202, res.status_code)
        cp = self.repo.get_checkpoint(checkpoint_id)
        self.assertEqual("VERIFYING", cp["status"])
        account = self.repo.get_account(cp["account_id"])
        self.assertNotEqual("IG_CREATED", account["stage"])
        self.assertTrue(res.get_json()["command_id"])

    def test_batch_pause_and_resume_are_controller_commands(self):
        paused = self.client.post(
            f"/api/factory/v2/batches/{self.batch['id']}/pause", headers=self.auth
        )
        self.assertEqual(202, paused.status_code)
        self.assertEqual("PAUSED", self.repo.get_batch(self.batch["id"])["status"])
        resumed = self.client.post(
            f"/api/factory/v2/batches/{self.batch['id']}/resume", headers=self.auth
        )
        self.assertEqual(202, resumed.status_code)
        self.assertEqual("RUNNING", self.repo.get_batch(self.batch["id"])["status"])

    def test_snooze_accepts_only_approved_presets(self):
        checkpoint_id = self.seed_waiting_checkpoint()
        invalid = self.client.post(
            f"/api/factory/v2/checkpoints/{checkpoint_id}/snooze",
            headers=self.auth,
            json={"minutes": 15},
        )
        self.assertEqual(400, invalid.status_code)
        valid = self.client.post(
            f"/api/factory/v2/checkpoints/{checkpoint_id}/snooze",
            headers=self.auth,
            json={"minutes": 30},
        )
        self.assertEqual(202, valid.status_code)
        self.assertTrue(self.repo.get_checkpoint(checkpoint_id)["snoozed_until"])

    def test_read_responses_never_expose_sensitive_keys(self):
        responses = [
            self.client.get("/api/factory/v2/dashboard", headers=self.auth),
            self.client.get("/api/factory/v2/accounts", headers=self.auth),
            self.client.get("/api/factory/v2/workers", headers=self.auth),
            self.client.get("/api/factory/v2/checkpoints", headers=self.auth),
        ]
        for response in responses:
            self.assertEqual(200, response.status_code)
            self.assert_no_sensitive_keys(response.get_json())

    def test_drain_busy_worker_preserves_running_job_until_release(self):
        job = self.seed_running_job()
        res = self.client.post(
            "/api/factory/v2/workers/worker-01/drain", headers=self.auth
        )
        self.assertEqual(202, res.status_code)
        worker = self.repo.get_worker("worker-01")
        self.assertEqual("RUNNING", worker["state"])
        self.assertEqual(1, worker["draining"])
        self.assertEqual(job["id"], worker["current_job_id"])

    def test_restart_rejects_worker_with_active_job(self):
        self.seed_running_job()
        res = self.client.post(
            "/api/factory/v2/workers/worker-01/restart", headers=self.auth
        )
        self.assertEqual(409, res.status_code)
        worker = self.repo.get_worker("worker-01")
        self.assertEqual("RUNNING", worker["state"])
        self.assertIsNotNone(worker["current_job_id"])

    def test_oauth_start_ignores_client_supplied_username(self):
        account = self.seed_threads_created("maianh.le")

        res = self.client.post(
            f"/api/factory/v2/accounts/{account['id']}/oauth/start",
            json={"expected_username": "wrong.user"},
            headers=self.auth,
        )

        self.assertEqual(201, res.status_code)
        body = res.get_json()
        oauth = get_session(self.conn, body["session_id"])
        updated = self.repo.get_account(account["id"])
        self.assertEqual("maianh.le", oauth["expected_username"])
        self.assertEqual(AccountStage.ACP_CONNECTING.value, updated["stage"])
        self.assertNotIn("access_token", body)

    def test_oauth_status_syncs_authoritative_active_account(self):
        account = self.seed_threads_created("maianh.le")
        started = self.client.post(
            f"/api/factory/v2/accounts/{account['id']}/oauth/start",
            headers=self.auth,
        ).get_json()
        self.conn.execute(
            """UPDATE account_factory_oauth_session
               SET status='ACTIVE', actual_username='maianh.le', threads_user_id='threads-17',
                   channel_id='channel-17', channel_code='threads_maianh_le'
               WHERE id=?""",
            (started["session_id"],),
        )

        res = self.client.get(
            f"/api/factory/v2/accounts/{account['id']}/oauth/status",
            headers=self.auth,
        )

        self.assertEqual(200, res.status_code)
        body = res.get_json()
        self.assertEqual(AccountStage.ACP_ACTIVE.value, body["account"]["stage"])
        self.assertEqual("threads_maianh_le", body["account"]["channel_code"])
        self.assert_no_sensitive_keys(body)


class FactoryV2LauncherTests(unittest.TestCase):
    def test_companion_launcher_registers_oauth_and_v2_routes(self):
        from account_factory_server import build_app

        app = build_app()
        rules = {rule.rule for rule in app.url_map.iter_rules()}
        self.assertIn("/oauth/account-factory/start", rules)
        self.assertIn("/api/factory/v2/dashboard", rules)


if __name__ == "__main__":
    unittest.main()
