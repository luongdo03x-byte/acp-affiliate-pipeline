import sqlite3
import unittest

from core.factory_v2.repository import FactoryRepository
from core.factory_v2.scheduler import Scheduler
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService
from core.factory_v2.runtime import FactoryControllerRuntime


class FakeSupervisor:
    def tick(self):
        return None


class FakeWorkerProcesses:
    def request(self, worker_id, command):
        if command.action == "REPORT_WAITING_HUMAN":
            return {"ok": True, "heartbeat": {"worker_id": worker_id, "state": "WAITING_HUMAN"}}
        return {"ok": True}


class FactoryV2RuntimeAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        self.scheduler = Scheduler(self.repo, self.service)
        self.batch = self.service.create_batch("Atomic Batch", count=1, seed=41)
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
            FakeWorkerProcesses(),
        )

    def tearDown(self):
        self.conn.close()

    def test_checkpoint_insert_failure_rolls_back_waiting_human_transition(self):
        original_create_checkpoint = self.repo.create_checkpoint

        def fail_create_checkpoint(row):
            raise RuntimeError("simulated checkpoint write failure")

        self.repo.create_checkpoint = fail_create_checkpoint
        try:
            self.runtime.tick()
        finally:
            self.repo.create_checkpoint = original_create_checkpoint

        account = self.repo.list_accounts(self.batch["id"])[0]
        job = self.repo.get_active_job_for_account(account["id"])
        worker = self.repo.get_worker("worker-01")
        self.assertEqual("AVD_ASSIGNED", account["stage"])
        self.assertEqual("PROFILE_READY", account["last_safe_stage"])
        self.assertEqual("RECOVERING", job["state"])
        self.assertEqual("RECOVERING", worker["state"])
        self.assertEqual([], self.repo.list_checkpoints(batch_id=self.batch["id"]))


if __name__ == "__main__":
    unittest.main()
