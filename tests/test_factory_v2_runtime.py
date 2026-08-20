import sqlite3
import unittest

from core.factory_v2.repository import FactoryRepository
from core.factory_v2.scheduler import Scheduler
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService
from core.factory_v2.runtime import FactoryControllerRuntime


class FakeSupervisor:
    def __init__(self):
        self.ticks = 0

    def tick(self):
        self.ticks += 1
        return None


class FakeWorkerProcesses:
    def __init__(self):
        self.commands = []
        self.instagram_result = {
            "ok": True,
            "status": "waiting_human",
            "result": {"screen": "OTP_REQUIRED", "reason": "HUMAN_VERIFICATION_REQUIRED"},
        }
        self.checkpoint_result = {
            "ok": True,
            "status": "completed",
            "result": {"screen": "IG_HOME", "last_safe_step": "IG_HOME"},
        }
        self.threads_result = {
            "ok": True,
            "status": "waiting_human",
            "result": {"screen": "SECURITY_CHALLENGE", "reason": "HUMAN_VERIFICATION_REQUIRED"},
        }

    def request(self, worker_id, command):
        self.commands.append((worker_id, command))
        if command.action == "PREPARE_INSTAGRAM":
            return {
                "ok": True,
                "status": "completed",
                "result": {"screen": "IG_SIGNUP_ENTRY"},
            }
        if command.action == "AUTOMATE_INSTAGRAM":
            return dict(self.instagram_result)
        if command.action == "OBSERVE_CHECKPOINT":
            return dict(self.checkpoint_result)
        if command.action == "AUTOMATE_THREADS":
            return dict(self.threads_result)
        return {"ok": True}


class FactoryV2RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        self.scheduler = Scheduler(self.repo, self.service, lease_seconds=120)
        self.supervisor = FakeSupervisor()
        self.worker_processes = FakeWorkerProcesses()
        self.batch = self.service.create_batch("Runtime Batch", count=1, seed=17)
        self.repo.insert_worker({
            "id": "worker-01",
            "runner_type": "REMOTE_AVD",
            "avd_name": "acp-worker-01",
            "adb_serial": "emulator-5554",
            "state": "READY",
        })
        self.runtime = FactoryControllerRuntime(
            self.repo,
            self.service,
            self.scheduler,
            self.supervisor,
            self.worker_processes,
        )

    def tearDown(self):
        self.conn.close()

    def test_ready_worker_automates_instagram_then_waits_for_protected_step(self):
        self.runtime.tick()

        account = self.repo.list_accounts(self.batch["id"])[0]
        worker = self.repo.get_worker("worker-01")
        job = self.repo.get_active_job_for_account(account["id"])
        checkpoints = self.repo.list_checkpoints(batch_id=self.batch["id"])
        actions = [command.action for _, command in self.worker_processes.commands]

        self.assertEqual("WAITING_HUMAN", account["stage"])
        self.assertEqual("PROFILE_READY", account["last_safe_stage"])
        self.assertEqual("WAITING_HUMAN", worker["state"])
        self.assertEqual("OBSERVE_CHECKPOINT", job["desired_action"])
        self.assertEqual(["PREPARE_INSTAGRAM", "AUTOMATE_INSTAGRAM"], actions)
        self.assertEqual(1, len(checkpoints))
        self.assertEqual("IG_POSTCHECK", checkpoints[0]["type"])
        self.assertEqual("OPEN", checkpoints[0]["status"])

    def test_continue_observes_known_successor_then_prepares_threads(self):
        self.runtime.tick()
        checkpoint = self.repo.list_checkpoints(batch_id=self.batch["id"])[0]
        self.service.request_checkpoint_verification(checkpoint["id"])
        self.worker_processes.commands.clear()

        self.runtime.tick()

        account = self.repo.get_account(checkpoint["account_id"])
        job = self.repo.get_active_job_for_account(account["id"])
        actions = [command.action for _, command in self.worker_processes.commands]
        self.assertEqual(["OBSERVE_CHECKPOINT"], actions)
        self.assertEqual("IG_CREATED", account["stage"])
        self.assertEqual("IG_CREATED", account["last_safe_stage"])
        self.assertEqual("PREPARE_THREADS", job["desired_action"])
        self.assertEqual("RESOLVED", self.repo.get_checkpoint(checkpoint["id"])["status"])

        self.worker_processes.commands.clear()
        self.runtime.tick()

        account = self.repo.get_account(account["id"])
        checkpoints = self.repo.list_checkpoints(batch_id=self.batch["id"])
        actions = [command.action for _, command in self.worker_processes.commands]
        self.assertEqual("WAITING_HUMAN", account["stage"])
        self.assertEqual("IG_CREATED", account["last_safe_stage"])
        self.assertEqual(["AUTOMATE_THREADS"], actions)
        self.assertEqual("THREADS_POSTCHECK", checkpoints[-1]["type"])
        self.assertEqual("OPEN", checkpoints[-1]["status"])

    def test_unknown_postcheck_never_marks_account_created(self):
        self.runtime.tick()
        checkpoint = self.repo.list_checkpoints(batch_id=self.batch["id"])[0]
        self.service.request_checkpoint_verification(checkpoint["id"])
        self.worker_processes.checkpoint_result = {
            "ok": True,
            "status": "needs_confirmation",
            "result": {"screen": "UNKNOWN", "reason": "UI_CHANGED"},
        }
        self.worker_processes.commands.clear()

        self.runtime.tick()

        account = self.repo.get_account(checkpoint["account_id"])
        checkpoint = self.repo.get_checkpoint(checkpoint["id"])
        self.assertEqual("NEEDS_CONFIRMATION", account["stage"])
        self.assertEqual("PROFILE_READY", account["last_safe_stage"])
        self.assertEqual("OPEN", checkpoint["status"])
        self.assertEqual(["OBSERVE_CHECKPOINT"], [command.action for _, command in self.worker_processes.commands])


if __name__ == "__main__":
    unittest.main()
