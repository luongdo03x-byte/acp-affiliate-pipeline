import sqlite3
import unittest

from core.factory_v2.account_credentials import store_account_password
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.runner_gateway import RunnerGateway
from core.factory_v2.scheduler import Scheduler
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService


class FakeWorkerProcesses:
    def __init__(self):
        self.last_worker_id = None
        self.last_command = None
        self.commands = []
        self.open_url_response = None

    def request(self, worker_id, command):
        self.last_worker_id = worker_id
        self.last_command = command
        self.commands.append((worker_id, command))
        if command.action == "OPEN_URL" and self.open_url_response is not None:
            return self.open_url_response
        if command.action == "TRANSIENT_BROWSER_LOGIN":
            return {
                "ok": True,
                "status": "waiting_human",
                "result": {
                    "screen": "OAUTH_CONSENT",
                    "reason": "HUMAN_CONSENT_REQUIRED",
                },
            }
        return {"ok": True, "package": "com.instagram.android"}


class FactoryV2RunnerGatewayTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        self.scheduler = Scheduler(self.repo, self.service)
        self.processes = FakeWorkerProcesses()
        self.gateway = RunnerGateway(self.repo, self.processes)

    def tearDown(self):
        self.conn.close()

    def _leased_job(self, worker_id, runner_type):
        row = {"id": worker_id, "runner_type": runner_type, "state": "READY"}
        if runner_type == "REMOTE_AVD":
            row["avd_name"] = worker_id
        else:
            row["device_id"] = worker_id
            row["device_name"] = worker_id
        self.repo.insert_worker(row)
        batch = self.service.create_batch(worker_id, count=1, seed=len(worker_id))
        account = self.repo.list_accounts(batch["id"])[0]
        target = "AUTO_AVD" if runner_type == "REMOTE_AVD" else worker_id
        self.conn.execute(
            "UPDATE factory_account SET execution_target=? WHERE id=?",
            (target, account["id"]),
        )
        return self.scheduler.assign_next(worker_id)

    def test_avd_gateway_uses_worker_process_transport(self):
        job = self._leased_job("avd-1", "REMOTE_AVD")
        result = self.gateway.send(
            job, "OPEN_PACKAGE", {"package": "com.instagram.android"}
        )

        self.assertTrue(result["ok"])
        self.assertEqual("avd-1", self.processes.last_worker_id)
        self.assertEqual("OPEN_PACKAGE", self.processes.last_command.action)

    def test_remote_avd_automation_action_is_forwarded(self):
        job = self._leased_job("avd-automation", "REMOTE_AVD")
        result = self.gateway.send(
            job,
            "AUTOMATE_INSTAGRAM",
            {"profile": {"username": "sample_user", "display_name": "Sample User", "bio": "Sample bio"}},
        )

        self.assertTrue(result["ok"])
        self.assertEqual("AUTOMATE_INSTAGRAM", self.processes.last_command.action)
        self.assertEqual("sample_user", self.processes.last_command.payload["profile"]["username"])

    def test_remote_oauth_open_auto_hands_off_stored_credential_transiently(self):
        job = self._leased_job("avd-oauth", "REMOTE_AVD")
        account = self.repo.get_account(job["account_id"])
        store_account_password(self.conn, account["id"], "example-secret")

        response = self.gateway.send(
            job,
            "OPEN_URL",
            {"url": "https://threads.example/authorize?state=x"},
        )

        actions = [command.action for _, command in self.processes.commands]
        self.assertEqual(["OPEN_URL", "TRANSIENT_BROWSER_LOGIN"], actions)
        secret_command = self.processes.commands[-1][1]
        self.assertEqual(account["username"], secret_command.payload["username"])
        self.assertEqual("example-secret", secret_command.payload["password"])
        self.assertEqual("waiting_human", response["status"])
        self.assertNotIn("example-secret", repr(response))
        self.assertEqual(
            0,
            self.conn.execute("SELECT COUNT(*) FROM factory_runner_command").fetchone()[0],
        )

    def test_remote_oauth_open_without_credential_stays_manual(self):
        job = self._leased_job("avd-oauth-manual", "REMOTE_AVD")
        # create_batch may seed ACP_DEFAULT_ACCOUNT_PASSWORD from the caller's
        # shell. This test intentionally covers the no-credential path.
        self.conn.execute(
            "DELETE FROM factory_account_credential WHERE account_id=?",
            (job["account_id"],),
        )

        response = self.gateway.send(
            job,
            "OPEN_URL",
            {"url": "https://threads.example/authorize?state=x"},
        )

        actions = [command.action for _, command in self.processes.commands]
        self.assertEqual(["OPEN_URL"], actions)
        self.assertTrue(response["ok"])

    def test_remote_oauth_open_does_not_type_secret_when_browser_prep_is_unverified(self):
        job = self._leased_job("avd-oauth-unverified", "REMOTE_AVD")
        account = self.repo.get_account(job["account_id"])
        store_account_password(self.conn, account["id"], "example-secret")
        self.processes.open_url_response = {
            "ok": True,
            "status": "needs_confirmation",
            "result": {
                "screen": "CHROME_FIRST_RUN",
                "reason": "CHROME_FIRST_RUN_UNVERIFIED",
            },
        }

        response = self.gateway.send(
            job,
            "OPEN_URL",
            {"url": "https://threads.example/authorize?state=x"},
        )

        actions = [command.action for _, command in self.processes.commands]
        self.assertEqual(["OPEN_URL"], actions)
        self.assertEqual("needs_confirmation", response["status"])
        self.assertNotIn("example-secret", repr(response))

    def test_local_gateway_queues_command_without_adb(self):
        job = self._leased_job("phone-1", "LOCAL_DEVICE")
        result = self.gateway.send(
            job, "OPEN_PACKAGE", {"package": "com.instagram.android"}
        )

        self.assertEqual("pending", result["status"])
        queued = self.repo.get_runner_command(result["command_id"])
        self.assertEqual("LOCAL_DEVICE", queued["runner_type"])
        self.assertEqual("OPEN_PACKAGE", queued["action"])
        self.assertIsNone(self.processes.last_command)

    def test_local_gateway_rejects_avd_automation_action(self):
        job = self._leased_job("phone-automation", "LOCAL_DEVICE")
        with self.assertRaisesRegex(ValueError, "unsupported local runner action"):
            self.gateway.send(job, "AUTOMATE_INSTAGRAM", {"profile": {"username": "sample_user"}})
        self.assertIsNone(self.processes.last_command)

    def test_local_gateway_reuses_unfinished_command(self):
        job = self._leased_job("phone-2", "LOCAL_DEVICE")
        first = self.gateway.send(job, "OBSERVE_FOREGROUND")
        second = self.gateway.send(job, "OBSERVE_FOREGROUND")
        self.assertEqual(first["command_id"], second["command_id"])


if __name__ == "__main__":
    unittest.main()
