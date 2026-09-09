import os
import tempfile
import unittest

from flask import Flask

from core import db
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.runner_gateway import RunnerGateway
from core.factory_v2.scheduler import Scheduler
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService
from account_factory_server import register_factory_v2_routes


class NeverAvd:
    def request(self, worker_id, command):
        raise AssertionError("local command must not use AVD transport")


class FactoryV2RunnerCommandApiTests(unittest.TestCase):
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
        self.worker = self.service.register_local_runner("android-1", "Pixel")
        batch = self.service.create_batch("Local", count=1, seed=4)
        account = self.repo.list_accounts(batch["id"])[0]
        self.conn.execute(
            "UPDATE factory_account SET execution_target=? WHERE id=?",
            (self.worker["id"], account["id"]),
        )
        self.scheduler = Scheduler(self.repo, self.service)
        self.job = self.scheduler.assign_next(self.worker["id"])
        self.gateway = RunnerGateway(self.repo, NeverAvd())

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

    def _queue(self, action="OPEN_PACKAGE"):
        return self.gateway.send(
            self.job, action, {"package": "com.instagram.android"}
        )

    def test_next_command_is_delivered_once_while_delivery_is_fresh(self):
        command = self._queue()
        url = f"/api/factory/v2/runners/{self.worker['id']}/commands/next"

        first = self.client.get(url, headers=self.auth)
        second = self.client.get(url, headers=self.auth)

        self.assertEqual(200, first.status_code)
        self.assertEqual(command["command_id"], first.get_json()["command"]["id"])
        self.assertIsNone(second.get_json()["command"])

    def test_stale_delivered_command_is_redelivered_after_runner_restart(self):
        command = self._queue()
        self.conn.execute(
            """UPDATE factory_runner_command
               SET status='DELIVERED', delivered_at='2000-01-01T00:00:00+00:00'
               WHERE id=?""",
            (command["command_id"],),
        )
        url = f"/api/factory/v2/runners/{self.worker['id']}/commands/next"

        res = self.client.get(url, headers=self.auth)

        self.assertEqual(200, res.status_code)
        self.assertEqual(command["command_id"], res.get_json()["command"]["id"])

    def test_result_rejects_stage_injection(self):
        command = self.gateway.send(self.job, "OBSERVE_FOREGROUND")
        url = (
            f"/api/factory/v2/runners/{self.worker['id']}/commands/"
            f"{command['command_id']}/result"
        )
        account_before = self.repo.get_account(self.job["account_id"])

        res = self.client.post(
            url,
            headers=self.auth,
            json={
                "status": "COMPLETED",
                "result": {"package": "com.instagram.android", "stage": "IG_CREATED"},
            },
        )

        self.assertEqual(400, res.status_code)
        account_after = self.repo.get_account(self.job["account_id"])
        self.assertEqual(account_before["stage"], account_after["stage"])

    def test_valid_observation_result_is_persisted(self):
        command = self.gateway.send(self.job, "OBSERVE_FOREGROUND")
        next_url = f"/api/factory/v2/runners/{self.worker['id']}/commands/next"
        self.client.get(next_url, headers=self.auth)
        result_url = (
            f"/api/factory/v2/runners/{self.worker['id']}/commands/"
            f"{command['command_id']}/result"
        )

        res = self.client.post(
            result_url,
            headers=self.auth,
            json={"status": "COMPLETED", "result": {"package": "com.instagram.android"}},
        )

        self.assertEqual(202, res.status_code)
        saved = self.repo.get_runner_command(command["command_id"])
        self.assertEqual("COMPLETED", saved["status"])
        self.assertIn("com.instagram.android", saved["result_json"])

    def test_safe_ui_flow_result_round_trips_to_runtime_shape(self):
        command = self.gateway.send(
            self.job,
            "AUTOMATE_INSTAGRAM",
            {"profile": {"username": "sample_user"}},
        )
        self.client.get(
            f"/api/factory/v2/runners/{self.worker['id']}/commands/next",
            headers=self.auth,
        )
        result_url = (
            f"/api/factory/v2/runners/{self.worker['id']}/commands/"
            f"{command['command_id']}/result"
        )
        res = self.client.post(
            result_url,
            headers=self.auth,
            json={
                "status": "COMPLETED",
                "result": {
                    "flow_status": "waiting_human",
                    "screen": "PASSWORD_REQUIRED",
                    "reason": "HUMAN_VERIFICATION_REQUIRED",
                },
            },
        )

        self.assertEqual(202, res.status_code)
        response = self.gateway.send(
            self.job,
            "AUTOMATE_INSTAGRAM",
            {"profile": {"username": "sample_user"}},
        )
        self.assertEqual("waiting_human", response["status"])
        self.assertEqual("PASSWORD_REQUIRED", response["result"]["screen"])

    def test_sanitized_actual_username_round_trips_to_runtime(self):
        command = self.gateway.send(
            self.job,
            "AUTOMATE_INSTAGRAM",
            {"profile": {"username": "phuongthaopham6"}},
        )
        self.client.get(
            f"/api/factory/v2/runners/{self.worker['id']}/commands/next",
            headers=self.auth,
        )
        result_url = (
            f"/api/factory/v2/runners/{self.worker['id']}/commands/"
            f"{command['command_id']}/result"
        )

        response = self.client.post(
            result_url,
            headers=self.auth,
            json={
                "status": "COMPLETED",
                "result": {
                    "flow_status": "running",
                    "screen": "IG_PROFILE_SETUP",
                    "actual_username": "Phuo.NgThaoPham6",
                },
            },
        )

        self.assertEqual(202, response.status_code)
        result = self.gateway.send(
            self.job,
            "AUTOMATE_INSTAGRAM",
            {"profile": {"username": "phuongthaopham6"}},
        )
        self.assertEqual("phuo.ngthaopham6", result["actual_username"])

    def test_invalid_actual_username_is_rejected(self):
        command = self.gateway.send(
            self.job,
            "AUTOMATE_INSTAGRAM",
            {"profile": {"username": "sample_user"}},
        )
        self.client.get(
            f"/api/factory/v2/runners/{self.worker['id']}/commands/next",
            headers=self.auth,
        )
        response = self.client.post(
            f"/api/factory/v2/runners/{self.worker['id']}/commands/{command['command_id']}/result",
            headers=self.auth,
            json={
                "status": "COMPLETED",
                "result": {"flow_status": "running", "actual_username": "bad name!"},
            },
        )

        self.assertEqual(400, response.status_code)


if __name__ == "__main__":
    unittest.main()
