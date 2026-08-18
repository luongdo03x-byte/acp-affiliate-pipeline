import sqlite3
import unittest

from core.db import now, ulid
from core.factory_v2.models import AccountStage
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.runtime import FactoryControllerRuntime
from core.factory_v2.scheduler import Scheduler
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService


class FakeSupervisor:
    def tick(self):
        return None


class FakeProcesses:
    def stop_all(self):
        pass


class FakeGateway:
    def __init__(self):
        self.commands = []

    def send(self, job, action, payload=None):
        self.commands.append((action, payload or {}))
        if action == "OBSERVE_CHECKPOINT":
            return {
                "ok": True,
                "status": "completed",
                "result": {
                    "screen": "THREADS_POSTCHECK_OK",
                    "last_safe_step": "THREADS_POSTCHECK_OK",
                },
            }
        if action == "OBSERVE_FOREGROUND":
            return {"package": "com.instagram.barcelona"}
        return {"ok": True}


class FakeActivation:
    def __init__(self, repo, service):
        self.repo = repo
        self.service = service
        self.next_stage = "ACP_CONNECTING"
        self.start_calls = 0

    def start(self, account_id):
        self.start_calls += 1
        account = self.repo.get_account(account_id)
        if account["stage"] != "ACP_CONNECTING":
            self.service.transition_account(account_id, AccountStage.ACP_CONNECTING)
        return {
            "session_id": "oauth-1",
            "status": "WAITING_AUTH",
            "authorization_url": "https://threads.example/authorize?state=x",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }

    def reconcile(self, account_id):
        account = self.repo.get_account(account_id)
        if self.next_stage == "ACP_ACTIVE" and account["stage"] != "ACP_ACTIVE":
            self.service.transition_account(account_id, AccountStage.ACP_ACTIVE)
        elif self.next_stage == "RETRY_PENDING" and account["stage"] != "RETRY_PENDING":
            self.service.transition_account(
                account_id,
                AccountStage.RETRY_PENDING,
                error_code="OAUTH_FAILED",
                error_message="oauth failed",
            )
        return self.repo.get_account(account_id)


class FactoryRuntimeActivationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        self.scheduler = Scheduler(self.repo, self.service)
        self.gateway = FakeGateway()
        self.activation = FakeActivation(self.repo, self.service)
        self.runtime = FactoryControllerRuntime(
            self.repo,
            self.service,
            self.scheduler,
            FakeSupervisor(),
            FakeProcesses(),
            runner_gateway=self.gateway,
            activation_service=self.activation,
        )

    def tearDown(self):
        self.conn.close()

    def seed_threads_verifying(self):
        batch = self.service.create_batch("activation", count=1, seed=9)
        account = self.repo.list_accounts(batch["id"])[0]
        worker = self.repo.insert_worker({
            "id": "avd-1",
            "runner_type": "REMOTE_AVD",
            "avd_name": "acp-worker-01",
            "state": "RUNNING",
        })
        self.conn.execute(
            """UPDATE factory_account
               SET stage='WAITING_HUMAN', last_safe_stage='IG_CREATED',
                   assigned_worker_id=?, current_job_id='job-1'
               WHERE id=?""",
            (worker["id"], account["id"]),
        )
        self.conn.execute(
            """INSERT INTO factory_job
               (id,account_id,worker_id,runner_type,lease_token,state,desired_action,command_id,
                leased_at,lease_expires_at,heartbeat_at,started_at)
               VALUES ('job-1',?,?, 'REMOTE_AVD','lease','RUNNING','VERIFY_CHECKPOINT','verify-1',?,?,?,?)""",
            (account["id"], worker["id"], now(), "2099-01-01T00:00:00+00:00", now(), now()),
        )
        self.conn.execute(
            "UPDATE factory_worker SET current_account_id=?, current_job_id='job-1' WHERE id=?",
            (account["id"], worker["id"]),
        )
        self.repo.create_checkpoint({
            "id": "cp-threads",
            "batch_id": batch["id"],
            "account_id": account["id"],
            "worker_id": worker["id"],
            "type": "THREADS_POSTCHECK",
            "status": "VERIFYING",
            "created_at": now(),
        })
        return self.repo.get_account(account["id"]), self.conn.execute("SELECT * FROM factory_job WHERE id='job-1'").fetchone()

    def test_threads_postcheck_automatically_starts_acp_activation(self):
        account, _ = self.seed_threads_verifying()

        self.runtime.tick()

        saved = self.repo.get_account(account["id"])
        self.assertEqual("ACP_CONNECTING", saved["stage"])
        self.assertEqual("THREADS_CREATED", saved["last_safe_stage"])
        self.assertIn("OPEN_URL", [action for action, _ in self.gateway.commands])
        job = self.conn.execute("SELECT * FROM factory_job WHERE id='job-1'").fetchone()
        self.assertEqual("WAIT_ACP", job["desired_action"])
        self.assertEqual("WAITING_HUMAN", job["state"])

    def test_oauth_active_releases_runner_and_completes_account(self):
        account, _ = self.seed_threads_verifying()
        self.runtime.tick()
        self.activation.next_stage = "ACP_ACTIVE"

        self.runtime.tick()

        self.assertEqual("ACP_ACTIVE", self.repo.get_account(account["id"])["stage"])
        job = self.conn.execute("SELECT * FROM factory_job WHERE id='job-1'").fetchone()
        self.assertEqual("COMPLETED", job["state"])
        self.assertEqual("READY", self.repo.get_worker("avd-1")["state"])

    def test_oauth_failure_releases_runner_and_preserves_threads_safe_stage(self):
        account, _ = self.seed_threads_verifying()
        self.runtime.tick()
        self.activation.next_stage = "RETRY_PENDING"

        self.runtime.tick()

        saved = self.repo.get_account(account["id"])
        self.assertEqual("RETRY_PENDING", saved["stage"])
        self.assertEqual("OAUTH_FAILED", saved["last_error_code"])
        self.assertEqual("THREADS_CREATED", saved["last_safe_stage"])
        self.assertEqual("READY", self.repo.get_worker("avd-1")["state"])


if __name__ == "__main__":
    unittest.main()
