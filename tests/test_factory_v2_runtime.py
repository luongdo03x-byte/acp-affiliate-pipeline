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
        self.foreground_package = "com.instagram.android"

    def request(self, worker_id, command):
        self.commands.append((worker_id, command))
        if command.action == "OBSERVE_FOREGROUND":
            return {"ok": True, "package": self.foreground_package}
        if command.action == "REPORT_WAITING_HUMAN":
            return {
                "ok": True,
                "heartbeat": {
                    "worker_id": worker_id,
                    "state": "WAITING_HUMAN",
                },
            }
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

    def test_ready_worker_prepares_instagram_then_waits_for_human(self):
        self.runtime.tick()

        account = self.repo.list_accounts(self.batch["id"])[0]
        worker = self.repo.get_worker("worker-01")
        checkpoints = self.repo.list_checkpoints(batch_id=self.batch["id"])
        actions = [command.action for _, command in self.worker_processes.commands]

        self.assertEqual("WAITING_HUMAN", account["stage"])
        self.assertEqual("PROFILE_READY", account["last_safe_stage"])
        self.assertEqual("WAITING_HUMAN", worker["state"])
        self.assertEqual(["PREPARE_TEXT", "OPEN_PACKAGE", "REPORT_WAITING_HUMAN"], actions)
        self.assertEqual(1, len(checkpoints))
        self.assertEqual("IG_POSTCHECK", checkpoints[0]["type"])
        self.assertEqual("OPEN", checkpoints[0]["status"])

    def test_continue_runs_postcheck_before_advancing_then_prepares_threads(self):
        self.runtime.tick()
        checkpoint = self.repo.list_checkpoints(batch_id=self.batch["id"])[0]
        self.service.request_checkpoint_verification(checkpoint["id"])
        self.worker_processes.commands.clear()

        self.runtime.tick()

        account = self.repo.get_account(checkpoint["account_id"])
        job = self.repo.get_active_job_for_account(account["id"])
        actions = [command.action for _, command in self.worker_processes.commands]
        self.assertEqual(["OBSERVE_FOREGROUND"], actions)
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
        self.assertEqual(["PREPARE_TEXT", "OPEN_PACKAGE", "REPORT_WAITING_HUMAN"], actions)
        self.assertEqual("THREADS_POSTCHECK", checkpoints[-1]["type"])

    def test_failed_postcheck_never_marks_account_created(self):
        self.runtime.tick()
        checkpoint = self.repo.list_checkpoints(batch_id=self.batch["id"])[0]
        self.service.request_checkpoint_verification(checkpoint["id"])
        self.worker_processes.foreground_package = "com.android.settings"
        self.worker_processes.commands.clear()

        self.runtime.tick()

        account = self.repo.get_account(checkpoint["account_id"])
        checkpoint = self.repo.get_checkpoint(checkpoint["id"])
        self.assertEqual("NEEDS_CONFIRMATION", account["stage"])
        self.assertEqual("PROFILE_READY", account["last_safe_stage"])
        self.assertEqual("OPEN", checkpoint["status"])


if __name__ == "__main__":
    unittest.main()
