import sqlite3
import unittest

from core.account_factory import ensure_schema as ensure_oauth_schema
from core.db import now
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
        self.responses = {}

    @property
    def sent(self):
        return [action for action, _ in self.commands]

    def send(self, job, action, payload=None):
        self.commands.append((action, payload or {}))
        return self.responses.get(action, {"ok": True})


class FakeActivation:
    def __init__(self, repo, service):
        self.repo = repo
        self.service = service
        self.start_calls = 0

    def start(self, account_id):
        self.start_calls += 1
        account = self.repo.get_account(account_id)
        if account["stage"] != AccountStage.ACP_CONNECTING.value:
            self.service.transition_account(account_id, AccountStage.ACP_CONNECTING)
        return {
            "session_id": "oauth-1",
            "status": "WAITING_AUTH",
            "authorization_url": "https://threads.example/authorize?state=x",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }

    def reconcile(self, account_id):
        return self.repo.get_account(account_id)


class TesterGateTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        ensure_schema(self.conn)
        ensure_oauth_schema(self.conn)
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
        batch = self.service.create_batch("tester-gate", count=1, seed=7)
        self.batch_id = batch["id"]
        account = self.repo.list_accounts(batch["id"])[0]
        self.account_id = account["id"]
        self.worker = self.repo.insert_worker({
            "id": "avd-1",
            "runner_type": "REMOTE_AVD",
            "avd_name": "acp-worker-01",
            "state": "RUNNING",
        })
        self.conn.execute(
            """UPDATE factory_account
               SET stage='THREADS_CREATED', last_safe_stage='THREADS_CREATED',
                   tester_invited_at=NULL, tester_accepted_at=NULL,
                   assigned_worker_id=?, current_job_id='job-1'
               WHERE id=?""",
            (self.worker["id"], self.account_id),
        )

    def tearDown(self):
        self.conn.close()

    def _job_for(self, account_id, *, desired_action):
        self.conn.execute("DELETE FROM factory_job WHERE id='job-1'")
        self.conn.execute(
            """INSERT INTO factory_job
               (id,account_id,worker_id,runner_type,lease_token,state,desired_action,command_id,
                leased_at,lease_expires_at,heartbeat_at,started_at)
               VALUES ('job-1',?,?, 'REMOTE_AVD','lease','RUNNING',?,'cmd-1',?,?,?,?)""",
            (
                account_id, self.worker["id"], desired_action,
                now(), "2099-01-01T00:00:00+00:00", now(), now(),
            ),
        )
        return dict(self.conn.execute("SELECT * FROM factory_job WHERE id='job-1'").fetchone())

    def _mark_invited(self):
        self.conn.execute(
            "UPDATE factory_account SET tester_invited_at=? WHERE id=?",
            ("2026-09-10T00:00:00+00:00", self.account_id),
        )

    def test_threads_created_without_acceptance_opens_checkpoint(self):
        job = self._job_for(self.account_id, desired_action="START_ACP")

        self.runtime._start_activation(job, self.repo.get_account(self.account_id))

        account = self.repo.get_account(self.account_id)
        self.assertEqual(AccountStage.THREADS_CREATED.value, account["stage"])
        self.assertIsNone(account["oauth_session_id"])
        self.assertEqual(0, self.activation.start_calls)

        checkpoint = self.runtime._tester_checkpoint(self.account_id)
        self.assertIsNotNone(checkpoint)
        self.assertEqual("TESTER_INVITE", checkpoint["type"])
        self.assertEqual("WAITING_EXTERNAL", checkpoint["status"])

        row = self.conn.execute(
            "SELECT state, desired_action FROM factory_job WHERE id=?", (job["id"],)
        ).fetchone()
        self.assertEqual("WAITING_HUMAN", row["state"])
        self.assertEqual("WAIT_TESTER", row["desired_action"])

    def test_checkpoint_is_not_duplicated(self):
        job = self._job_for(self.account_id, desired_action="START_ACP")

        self.runtime._start_activation(job, self.repo.get_account(self.account_id))
        self.runtime._start_activation(job, self.repo.get_account(self.account_id))

        count = self.conn.execute(
            "SELECT COUNT(*) AS n FROM factory_checkpoint WHERE account_id=? AND type='TESTER_INVITE'",
            (self.account_id,),
        ).fetchone()["n"]
        self.assertEqual(1, count)

    def test_wait_tester_resumes_activation_after_acceptance(self):
        job = self._job_for(self.account_id, desired_action="START_ACP")
        self.runtime._start_activation(job, self.repo.get_account(self.account_id))
        self.conn.execute(
            "UPDATE factory_account SET tester_invited_at=?, tester_accepted_at=? WHERE id=?",
            ("2026-09-10T00:00:00+00:00", "2026-09-10T00:00:00+00:00", self.account_id),
        )
        waiting_job = dict(
            self.conn.execute("SELECT * FROM factory_job WHERE id='job-1'").fetchone()
        )

        self.runtime._drive_tester_invite(
            waiting_job, self.repo.get_account(self.account_id)
        )

        account = self.repo.get_account(self.account_id)
        self.assertEqual(AccountStage.ACP_CONNECTING.value, account["stage"])
        self.assertIsNone(self.runtime._tester_checkpoint(self.account_id))

    def test_invited_account_runs_accept_flow_then_activates(self):
        self._mark_invited()
        job = self._job_for(self.account_id, desired_action="WAIT_TESTER")
        self.gateway.responses["ACCEPT_THREADS_TESTER"] = {
            "ok": True,
            "status": "completed",
            "result": {"screen": "THREADS_TESTER_INVITE_CONFIRM", "reason": None},
        }

        self.runtime._drive_tester_invite(job, self.repo.get_account(self.account_id))

        account = self.repo.get_account(self.account_id)
        self.assertIsNotNone(account["tester_accepted_at"])
        self.assertEqual(AccountStage.ACP_CONNECTING.value, account["stage"])
        self.assertIn("ACCEPT_THREADS_TESTER", self.gateway.sent)

    def test_missing_invite_keeps_account_waiting(self):
        self._mark_invited()
        job = self._job_for(self.account_id, desired_action="START_ACP")
        self.runtime._start_activation(job, self.repo.get_account(self.account_id))
        waiting_job = dict(
            self.conn.execute("SELECT * FROM factory_job WHERE id='job-1'").fetchone()
        )
        self.gateway.responses["ACCEPT_THREADS_TESTER"] = {
            "ok": True,
            "status": "needs_confirmation",
            "result": {"screen": "THREADS_TESTER_INVITE_LIST", "reason": "NO_TESTER_INVITE"},
        }

        self.runtime._drive_tester_invite(
            waiting_job, self.repo.get_account(self.account_id)
        )

        account = self.repo.get_account(self.account_id)
        self.assertIsNone(account["tester_accepted_at"])
        self.assertEqual(AccountStage.THREADS_CREATED.value, account["stage"])
        checkpoint = self.runtime._tester_checkpoint(self.account_id)
        self.assertIsNotNone(checkpoint)
        self.assertIn("Meta", checkpoint["message"])

    def test_uninvited_account_never_touches_the_device(self):
        job = self._job_for(self.account_id, desired_action="START_ACP")
        self.runtime._start_activation(job, self.repo.get_account(self.account_id))
        waiting_job = dict(
            self.conn.execute("SELECT * FROM factory_job WHERE id='job-1'").fetchone()
        )

        self.runtime._drive_tester_invite(
            waiting_job, self.repo.get_account(self.account_id)
        )

        self.assertNotIn("ACCEPT_THREADS_TESTER", self.gateway.sent)
        self.assertEqual(0, self.activation.start_calls)

    def test_activation_requests_consent_after_opening_url(self):
        self.conn.execute(
            "UPDATE factory_account SET tester_invited_at=?, tester_accepted_at=? WHERE id=?",
            ("2026-09-10T00:00:00+00:00", "2026-09-10T00:00:00+00:00", self.account_id),
        )
        job = self._job_for(self.account_id, desired_action="START_ACP")

        self.runtime._start_activation(job, self.repo.get_account(self.account_id))

        self.assertIn("OPEN_URL", self.gateway.sent)
        self.assertIn("CONFIRM_THREADS_OAUTH", self.gateway.sent)
        self.assertLess(
            self.gateway.sent.index("OPEN_URL"),
            self.gateway.sent.index("CONFIRM_THREADS_OAUTH"),
        )
        row = self.conn.execute(
            "SELECT state, desired_action FROM factory_job WHERE id=?", (job["id"],)
        ).fetchone()
        self.assertEqual("WAITING_HUMAN", row["state"])
        self.assertEqual("WAIT_ACP", row["desired_action"])


if __name__ == "__main__":
    unittest.main()
