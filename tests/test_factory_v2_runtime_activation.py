import sqlite3
import unittest

from core.db import now
from core.factory_v2.account_credentials import get_account_password, store_account_password
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
    """Models the production gateway boundary, including remote OAuth autofill."""

    def __init__(self, repo):
        self.repo = repo
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
        if action == "OPEN_URL":
            account = self.repo.get_account(job["account_id"])
            password = get_account_password(self.repo.conn, account["id"])
            if password is not None:
                self.commands.append((
                    "TRANSIENT_BROWSER_LOGIN",
                    {"username": account["username"], "password": password},
                ))
                return {
                    "ok": True,
                    "status": "waiting_human",
                    "result": {
                        "screen": "OAUTH_CONSENT",
                        "reason": "HUMAN_CONSENT_REQUIRED",
                    },
                }
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


class ExpiringActivation(FakeActivation):
    """Models an expired OAuth attempt that moves the account back to retry."""

    def start(self, account_id):
        self.start_calls += 1
        account = self.repo.get_account(account_id)
        if account["stage"] != AccountStage.RETRY_PENDING.value:
            self.service.transition_account(
                account_id,
                AccountStage.RETRY_PENDING,
                error_code="OAUTH_FAILED",
                error_message="OAuth session đã hết hạn",
            )
        raise ValueError("account retry has not been approved")


class FactoryRuntimeActivationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        self.scheduler = Scheduler(self.repo, self.service)
        self.gateway = FakeGateway(self.repo)
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

    def seed_threads_verifying(
        self,
        *,
        completion_mode="ACP_ACTIVE",
        with_credential=True,
    ):
        batch = self.service.create_batch(
            "activation", count=1, seed=9, completion_mode=completion_mode
        )
        account = self.repo.list_accounts(batch["id"])[0]
        if with_credential:
            store_account_password(self.conn, account["id"], "example-secret")
        else:
            # create_batch may seed ACP_DEFAULT_ACCOUNT_PASSWORD from the caller's
            # environment; this case must remain deterministic regardless of shell state.
            self.conn.execute(
                "DELETE FROM factory_account_credential WHERE account_id=?",
                (account["id"],),
            )
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
        return (
            self.repo.get_account(account["id"]),
            self.conn.execute("SELECT * FROM factory_job WHERE id='job-1'").fetchone(),
        )

    def seed_expired_start_attempt(self):
        account, _ = self.seed_threads_verifying()

        # First attempt reaches ACP_CONNECTING and creates ACP_OAUTH checkpoint.
        self.runtime.tick()

        self.runtime.activation_service = ExpiringActivation(
            self.repo,
            self.service,
        )
        self.conn.execute(
            """UPDATE factory_job
               SET state='RUNNING',
                   desired_action='START_ACP',
                   command_id='expired-start-1'
               WHERE id='job-1'"""
        )
        self.conn.execute(
            """UPDATE factory_worker
               SET state='RUNNING'
               WHERE id='avd-1'"""
        )
        return self.repo.get_account(account["id"])

    def test_threads_postcheck_opens_oauth_then_gateway_uses_decrypted_transient_login(self):
        account, _ = self.seed_threads_verifying()

        self.runtime.tick()

        saved = self.repo.get_account(account["id"])
        self.assertEqual("ACP_CONNECTING", saved["stage"])
        self.assertEqual("THREADS_CREATED", saved["last_safe_stage"])
        actions = [action for action, _ in self.gateway.commands]
        open_index = actions.index("OPEN_URL")
        secret_index = actions.index("TRANSIENT_BROWSER_LOGIN")
        self.assertLess(open_index, secret_index)
        login_payload = self.gateway.commands[secret_index][1]
        self.assertEqual(saved["username"], login_payload["username"])
        self.assertEqual("example-secret", login_payload["password"])
        job = self.conn.execute("SELECT * FROM factory_job WHERE id='job-1'").fetchone()
        self.assertEqual("WAIT_ACP", job["desired_action"])
        self.assertEqual("WAITING_HUMAN", job["state"])

    def test_missing_credential_keeps_oauth_waiting_for_manual_login(self):
        account, _ = self.seed_threads_verifying(with_credential=False)

        self.runtime.tick()

        actions = [action for action, _ in self.gateway.commands]
        self.assertIn("OPEN_URL", actions)
        self.assertNotIn("TRANSIENT_BROWSER_LOGIN", actions)
        saved = self.repo.get_account(account["id"])
        self.assertEqual("ACP_CONNECTING", saved["stage"])
        job = self.conn.execute("SELECT * FROM factory_job WHERE id='job-1'").fetchone()
        self.assertEqual("WAIT_ACP", job["desired_action"])
        self.assertEqual("WAITING_HUMAN", job["state"])

    def test_social_only_threads_postcheck_completes_without_oauth(self):
        account, _ = self.seed_threads_verifying(completion_mode="SOCIAL_ONLY")

        self.runtime.tick()

        saved = self.repo.get_account(account["id"])
        job = self.conn.execute("SELECT * FROM factory_job WHERE id='job-1'").fetchone()
        worker = self.repo.get_worker("avd-1")
        self.assertEqual("THREADS_CREATED", saved["stage"])
        self.assertEqual("THREADS_CREATED", saved["last_safe_stage"])
        self.assertTrue(saved["completed_at"])
        self.assertEqual("COMPLETED", job["state"])
        self.assertEqual("READY", worker["state"])
        self.assertIsNone(worker["current_job_id"])
        self.assertEqual(0, self.activation.start_calls)
        self.assertNotIn("OPEN_URL", [action for action, _ in self.gateway.commands])
        self.assertIsNone(saved["oauth_session_id"])

    def test_oauth_active_releases_runner_and_completes_account(self):
        account, _ = self.seed_threads_verifying()
        self.runtime.tick()
        self.activation.next_stage = "ACP_ACTIVE"

        self.runtime.tick()

        self.assertEqual("ACP_ACTIVE", self.repo.get_account(account["id"])["stage"])
        job = self.conn.execute("SELECT * FROM factory_job WHERE id='job-1'").fetchone()
        self.assertEqual("COMPLETED", job["state"])
        self.assertEqual("READY", self.repo.get_worker("avd-1")["state"])
        self.assertIn(
            "RESTORE_OAUTH_APPS",
            [action for action, _ in self.gateway.commands],
        )

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
        self.assertIn(
            "RESTORE_OAUTH_APPS",
            [action for action, _ in self.gateway.commands],
        )

    def test_expired_oauth_start_releases_stale_job_instead_of_recovery_loop(self):
        account = self.seed_expired_start_attempt()

        self.runtime.tick()

        saved = self.repo.get_account(account["id"])
        job = self.conn.execute(
            "SELECT * FROM factory_job WHERE id='job-1'"
        ).fetchone()
        worker = self.repo.get_worker("avd-1")
        checkpoint = self.conn.execute(
            """SELECT * FROM factory_checkpoint
               WHERE account_id=? AND type='ACP_OAUTH'
               ORDER BY created_at DESC, id DESC
               LIMIT 1""",
            (account["id"],),
        ).fetchone()

        self.assertEqual("RETRY_PENDING", saved["stage"])
        self.assertEqual("THREADS_CREATED", saved["last_safe_stage"])
        self.assertEqual("OAUTH_FAILED", saved["last_error_code"])

        # Expired OAuth attempt is terminal for this job.
        self.assertEqual("FAILED", job["state"])
        self.assertIsNone(saved["current_job_id"])
        self.assertIsNone(saved["assigned_worker_id"])

        self.assertEqual("READY", worker["state"])
        self.assertIsNone(worker["current_job_id"])
        self.assertIsNone(worker["current_account_id"])

        self.assertEqual("RESOLVED", checkpoint["status"])
        self.assertEqual("OAUTH_FAILED", checkpoint["resolution"])

        self.assertIn(
            "RESTORE_OAUTH_APPS",
            [action for action, _ in self.gateway.commands],
        )

    def test_retry_after_expired_oauth_assigns_fresh_start_acp_job(self):
        account = self.seed_expired_start_attempt()
        self.runtime.tick()

        retried = self.service.retry_account(account["id"])

        self.assertEqual("RETRY_PENDING", retried["stage"])
        self.assertEqual("THREADS_CREATED", retried["last_safe_stage"])
        self.assertIsNone(retried["last_error_code"])
        self.assertIsNone(retried["current_job_id"])

        fresh_job = self.scheduler.assign_next("avd-1")

        self.assertIsNotNone(fresh_job)
        self.assertNotEqual("job-1", fresh_job["id"])
        self.assertEqual("RUNNING", fresh_job["state"])
        self.assertEqual("START_ACP", fresh_job["desired_action"])

        saved = self.repo.get_account(account["id"])
        self.assertEqual(fresh_job["id"], saved["current_job_id"])
        self.assertEqual("RETRY_PENDING", saved["stage"])
        self.assertEqual("THREADS_CREATED", saved["last_safe_stage"])
        self.assertIsNone(saved["last_error_code"])

    def test_runner_failure_persists_safe_error_detail(self):
        account, _ = self.seed_threads_verifying()

        def fail_send(job, action, payload=None):
            raise RuntimeError("browser account binding mismatch")

        self.gateway.send = fail_send

        with self.assertLogs("core.factory_v2.runtime", level="WARNING") as logs:
            self.runtime.tick()

        job = self.conn.execute(
            "SELECT * FROM factory_job WHERE id='job-1'"
        ).fetchone()
        worker = self.repo.get_worker("avd-1")

        self.assertEqual("RECOVERING", job["state"])
        self.assertEqual("RECOVERING", worker["state"])

        self.assertEqual(
            "runner command failed: browser account binding mismatch",
            worker["last_error"],
        )

        joined = "\n".join(logs.output)
        self.assertIn(
            "RuntimeError: browser account binding mismatch",
            joined,
        )


if __name__ == "__main__":
    unittest.main()
