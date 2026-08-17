import sqlite3
import unittest

from core.factory_v2.models import AccountStage
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.runtime import FactoryControllerRuntime
from core.factory_v2.scheduler import Scheduler
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService


class FakeSupervisor:
    def tick(self):
        return None


class FakeWorkerProcesses:
    def __init__(self):
        self.actions = []

    def request(self, worker_id, command):
        self.actions.append(command.action)
        if command.action == "REPORT_WAITING_HUMAN":
            return {"ok": True, "heartbeat": {"worker_id": worker_id, "state": "WAITING_HUMAN"}}
        return {"ok": True}


class FactoryV2RuntimeResumeTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        self.scheduler = Scheduler(self.repo, self.service)
        self.worker_processes = FakeWorkerProcesses()
        self.repo.insert_worker({
            "id": "worker-01",
            "avd_name": "acp-worker-01",
            "state": "READY",
        })
        self.runtime = FactoryControllerRuntime(
            self.repo,
            self.service,
            self.scheduler,
            FakeSupervisor(),
            self.worker_processes,
        )

    def tearDown(self):
        self.conn.close()

    def test_retry_from_ig_safe_stage_opens_threads_checkpoint(self):
        batch = self.service.create_batch("Resume", count=1, seed=61)
        account = self.repo.list_accounts(batch["id"])[0]
        for stage in (
            AccountStage.AVD_ASSIGNED,
            AccountStage.IG_READY_FOR_HUMAN,
            AccountStage.IG_CREATED,
            AccountStage.RETRY_PENDING,
        ):
            self.service.transition_account(account["id"], stage)
        self.conn.execute(
            "UPDATE factory_account SET last_error_code='WORKER_TIMEOUT' WHERE id=?",
            (account["id"],),
        )

        self.runtime.tick()

        updated = self.repo.get_account(account["id"])
        checkpoints = self.repo.list_checkpoints(batch_id=batch["id"])
        self.assertEqual("WAITING_HUMAN", updated["stage"])
        self.assertEqual("IG_CREATED", updated["last_safe_stage"])
        self.assertEqual("THREADS_POSTCHECK", checkpoints[-1]["type"])
        self.assertEqual(
            ["PREPARE_TEXT", "OPEN_PACKAGE", "REPORT_WAITING_HUMAN"],
            self.worker_processes.actions,
        )


if __name__ == "__main__":
    unittest.main()
