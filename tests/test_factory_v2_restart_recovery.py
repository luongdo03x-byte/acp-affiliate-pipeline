import sqlite3
import unittest

from core.factory_v2.repository import FactoryRepository
from core.factory_v2.resource_policy import HostSample
from core.factory_v2.runtime import FactoryControllerRuntime
from core.factory_v2.scheduler import Scheduler
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService
from core.factory_v2.supervisor import WorkerSupervisor


class FakeAvd:
    def list_online_devices(self):
        return ["emulator-5554"]

    def is_boot_completed(self, serial):
        return serial == "emulator-5554"

    def list_avds(self):
        return ["acp-worker-01"]

    def start(self, avd_name, port):
        return type("P", (), {"pid": 1234})()

    def stop(self, serial):
        return None


class FakeMetrics:
    ram_total_mb = 16384

    def sample(self):
        return HostSample(55, 5000, 0, 0, 1, 1)


class FakeWorkerProcesses:
    def __init__(self):
        self.running = set()
        self.foreground_package = "com.instagram.android"

    def is_running(self, worker_id):
        return worker_id in self.running

    def start(self, worker_id, avd_name, serial):
        self.running.add(worker_id)
        return type("P", (), {"pid": 9876})()

    def heartbeat(self, worker_id):
        return {
            "worker_id": worker_id,
            "adb_serial": "emulator-5554",
            "state": "READY",
            "current_account_id": None,
            "current_job_id": None,
            "observed_state": None,
            "last_progress_at": "2026-08-17T06:00:00+00:00",
        }

    def request(self, worker_id, command):
        if command.action == "OBSERVE_FOREGROUND":
            return {"ok": True, "package": self.foreground_package}
        if command.action == "REPORT_WAITING_HUMAN":
            return {"ok": True, "heartbeat": {"worker_id": worker_id, "state": "WAITING_HUMAN"}}
        return {"ok": True}

    def stop(self, worker_id):
        self.running.discard(worker_id)


class NoopSupervisor:
    def tick(self):
        return None


class FactoryV2RestartRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        self.scheduler = Scheduler(self.repo, self.service)
        self.worker_processes = FakeWorkerProcesses()
        self.batch = self.service.create_batch("Restart Batch", count=1, seed=71)
        self.repo.insert_worker({
            "id": "worker-01",
            "avd_name": "acp-worker-01",
            "adb_serial": "emulator-5554",
            "state": "READY",
        })

    def tearDown(self):
        self.conn.close()

    def test_restart_marks_ambiguous_human_state_then_continue_rechecks_before_advancing(self):
        before_restart = FactoryControllerRuntime(
            self.repo,
            self.service,
            self.scheduler,
            NoopSupervisor(),
            self.worker_processes,
        )
        before_restart.tick()
        checkpoint = self.repo.list_checkpoints(batch_id=self.batch["id"])[0]
        account = self.repo.get_account(checkpoint["account_id"])
        self.assertEqual("WAITING_HUMAN", account["stage"])
        self.assertEqual("PROFILE_READY", account["last_safe_stage"])

        supervisor = WorkerSupervisor(
            self.repo,
            FakeAvd(),
            FakeMetrics(),
            worker_processes=self.worker_processes,
            stability_seconds=0,
        )
        supervisor.reconcile_on_boot()

        recovered = self.repo.get_account(account["id"])
        worker = self.repo.get_worker("worker-01")
        job = self.repo.get_active_job_for_account(account["id"])
        self.assertEqual("NEEDS_CONFIRMATION", recovered["stage"])
        self.assertEqual("PROFILE_READY", recovered["last_safe_stage"])
        self.assertEqual("RECOVERING", worker["state"])
        self.assertEqual("WAITING_HUMAN", job["state"])
        self.assertEqual("OPEN", self.repo.get_checkpoint(checkpoint["id"])["status"])

        self.service.request_checkpoint_verification(checkpoint["id"])
        after_restart = FactoryControllerRuntime(
            self.repo,
            self.service,
            self.scheduler,
            NoopSupervisor(),
            self.worker_processes,
        )
        after_restart.tick()

        final_account = self.repo.get_account(account["id"])
        final_checkpoint = self.repo.get_checkpoint(checkpoint["id"])
        final_job = self.repo.get_active_job_for_account(account["id"])
        self.assertEqual("IG_CREATED", final_account["stage"])
        self.assertEqual("IG_CREATED", final_account["last_safe_stage"])
        self.assertEqual("RESOLVED", final_checkpoint["status"])
        self.assertEqual("PREPARE_THREADS", final_job["desired_action"])


if __name__ == "__main__":
    unittest.main()
