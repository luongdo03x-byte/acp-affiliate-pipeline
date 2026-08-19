import unittest

from core.factory_v2.runtime import FactoryControllerRuntime


_ALLOWED = {
    "RUNNER_ASSIGNED": {"IG_READY_FOR_HUMAN", "RETRY_PENDING", "NEEDS_CONFIRMATION", "ERROR"},
    "AVD_ASSIGNED": {"IG_READY_FOR_HUMAN", "RETRY_PENDING", "NEEDS_CONFIRMATION", "ERROR"},
    "IG_READY_FOR_HUMAN": {"WAITING_HUMAN", "IG_CREATED", "NEEDS_VERIFICATION", "ERROR"},
    "WAITING_HUMAN": {"IG_CREATED", "THREADS_CREATED", "NEEDS_VERIFICATION", "NEEDS_CONFIRMATION", "USERNAME_UNAVAILABLE", "RETRY_PENDING", "ERROR"},
    "NEEDS_CONFIRMATION": {"WAITING_HUMAN", "IG_READY_FOR_HUMAN", "THREADS_READY_FOR_HUMAN", "RETRY_PENDING", "ERROR"},
    "IG_CREATED": {"THREADS_READY_FOR_HUMAN", "RETRY_PENDING", "ERROR", "DISABLED"},
    "THREADS_READY_FOR_HUMAN": {"WAITING_HUMAN", "THREADS_CREATED", "NEEDS_VERIFICATION", "ERROR"},
    "THREADS_CREATED": {"ACP_CONNECTING", "RETRY_PENDING", "ERROR", "DISABLED"},
}


class FakeConn:
    class Cursor:
        def fetchone(self):
            return None
        def fetchall(self):
            return []
    def execute(self, *args, **kwargs):
        return self.Cursor()


class FakeRepo:
    def __init__(self, account, worker_type="REMOTE_AVD", completion_mode="ACP_ACTIVE"):
        self.conn = FakeConn()
        self.account = account
        self.worker = {"id": "worker-1", "runner_type": worker_type}
        self.batch = {"id": account["batch_id"], "completion_mode": completion_mode}
        self.checkpoint = None
    def get_account(self, account_id):
        return self.account
    def get_batch(self, batch_id):
        return self.batch if batch_id == self.batch["id"] else None
    def get_worker(self, worker_id):
        return self.worker
    def create_checkpoint(self, row):
        self.checkpoint = dict(row)
    def resolve_checkpoint(self, *args, **kwargs):
        if self.checkpoint:
            self.checkpoint["status"] = "RESOLVED"


class FakeService:
    def __init__(self, repo):
        self.repo = repo
        self.transitions = []
        self.username_updates = []
        self.events = []
    def transition_account(self, account_id, stage, **kwargs):
        target = stage.value if hasattr(stage, "value") else str(stage)
        current = self.repo.account["stage"]
        if target not in _ALLOWED.get(current, set()):
            raise AssertionError(f"illegal transition {current}->{target}")
        self.transitions.append((current, target, kwargs.get("error_code")))
        self.repo.account["stage"] = target
        if target in {"IG_CREATED", "THREADS_CREATED"}:
            self.repo.account["last_safe_stage"] = target
        return self.repo.account
    def update_worker_selected_username(self, account_id, *, job_id, worker_id, username):
        self.username_updates.append((account_id, job_id, worker_id, username))
        self.events.append(("username", username))
        self.repo.account["username"] = username
        return self.repo.account


class FakeGateway:
    def __init__(self, responses):
        self.responses = list(responses)
        self.commands = []
    def send(self, job, action, payload=None):
        self.commands.append((action, payload or {}))
        if not self.responses:
            return {"ok": True, "status": "running", "result": {"screen": "UNKNOWN"}}
        return self.responses.pop(0)


class DummySupervisor:
    def tick(self):
        pass


class FakeScheduler:
    def __init__(self):
        self.released = []
    def reconcile_expired_leases(self, now_value):
        pass
    def release_job_in_transaction(self, job_id, final_state):
        self.released.append((job_id, final_state))


class TestRuntime(FactoryControllerRuntime):
    def __init__(self, repo, service, gateway):
        scheduler = FakeScheduler()
        super().__init__(
            repo, service, scheduler, DummySupervisor(), object(), runner_gateway=gateway
        )
        self.running_actions = []
        self.waiting = []
        self.released = scheduler.released
        self.activation = []
    def _checkpoint_for_account(self, account_id):
        return self.repo.checkpoint
    def _set_remote_running(self, job, desired_action):
        self.running_actions.append(desired_action)
        self.service.events.append(("running", desired_action))
        job["desired_action"] = desired_action
    def _set_remote_waiting(self, job):
        self.waiting.append(job["id"])
    def _refresh_remote_waiting(self, job):
        self.waiting.append(job["id"])
    def _resolve_remote_checkpoint(self, account_id, resolution):
        pass
    def _start_activation(self, job, account):
        self.activation.append(account["id"])


def account(stage="RUNNER_ASSIGNED"):
    return {
        "id": "acc-1",
        "batch_id": "batch-1",
        "username": "sample_user",
        "display_name": "Sample User",
        "bio": "Sample bio",
        "stage": stage,
        "last_safe_stage": "IG_CREATED" if stage == "IG_CREATED" else "PROFILE_READY",
        "assigned_worker_id": "worker-1",
        "current_job_id": "job-1",
    }


def job(action="PREPARE_INSTAGRAM", runner_type="REMOTE_AVD"):
    return {
        "id": "job-1",
        "account_id": "acc-1",
        "worker_id": "worker-1",
        "runner_type": runner_type,
        "desired_action": action,
    }


class RemoteRuntimeTests(unittest.TestCase):
    def test_profile_payload_includes_only_selected_safe_signup_input(self):
        acc = account()
        acc.update({
            "signup_contact_type": "phone",
            "phone": "+84901234567",
            "email": "backup@example.com",
            "birth_date": "2000-05-20",
            "avatar_file": "var/factory_avatars/sample.jpg",
            "password": "must-not-pass",
            "otp": "123456",
        })

        payload = FactoryControllerRuntime._profile_payload(acc)

        self.assertEqual({
            "username": "sample_user",
            "display_name": "Sample User",
            "bio": "Sample bio",
            "signup_contact_type": "phone",
            "signup_contact": "+84901234567",
            "birth_date": "2000-05-20",
            "avatar_file": "var/factory_avatars/sample.jpg",
        }, payload["profile"])
        self.assertNotIn("email", payload["profile"])
        self.assertNotIn("password", payload["profile"])
        self.assertNotIn("otp", payload["profile"])
        serialized = repr(payload)
        self.assertNotIn("backup@example.com", serialized)
        self.assertNotIn("must-not-pass", serialized)

    def test_running_instagram_result_persists_username_before_next_action(self):
        acc = account()
        repo = FakeRepo(acc, completion_mode="SOCIAL_ONLY")
        service = FakeService(repo)
        runtime = TestRuntime(
            repo,
            service,
            FakeGateway([{
                "ok": True,
                "status": "running",
                "result": {
                    "screen": "IG_USERNAME_VALID",
                    "profile_updates": {"username": "baongocd483102"},
                },
            }]),
        )
        runtime._drive_job(job("AUTOMATE_INSTAGRAM"))
        self.assertEqual("baongocd483102", acc["username"])
        self.assertEqual(
            [("username", "baongocd483102"), ("running", "AUTOMATE_INSTAGRAM")],
            service.events,
        )
        self.assertEqual("AUTOMATE_INSTAGRAM", runtime.running_actions[-1])

    def test_running_instagram_result_without_profile_update_leaves_username_unchanged(self):
        acc = account()
        repo = FakeRepo(acc)
        service = FakeService(repo)
        runtime = TestRuntime(
            repo,
            service,
            FakeGateway([{
                "ok": True,
                "status": "running",
                "result": {"screen": "IG_PROFILE_SETUP"},
            }]),
        )
        runtime._drive_job(job("AUTOMATE_INSTAGRAM"))
        self.assertEqual("sample_user", acc["username"])
        self.assertEqual([], service.username_updates)
        self.assertEqual("AUTOMATE_INSTAGRAM", runtime.running_actions[-1])

    def test_malformed_instagram_profile_update_opens_confirmation_without_advancing(self):
        acc = account()
        repo = FakeRepo(acc)
        service = FakeService(repo)
        runtime = TestRuntime(
            repo,
            service,
            FakeGateway([{
                "ok": True,
                "status": "running",
                "result": {
                    "screen": "IG_USERNAME_VALID",
                    "profile_updates": {"username": "safe_name", "password": "secret"},
                },
            }]),
        )
        runtime._drive_job(job("AUTOMATE_INSTAGRAM"))
        self.assertEqual("NEEDS_CONFIRMATION", acc["stage"])
        self.assertEqual([], service.username_updates)
        self.assertEqual([], runtime.running_actions)

    def test_threads_profile_update_is_rejected_without_advancing(self):
        acc = account("IG_CREATED")
        repo = FakeRepo(acc, completion_mode="SOCIAL_ONLY")
        service = FakeService(repo)
        runtime = TestRuntime(
            repo,
            service,
            FakeGateway([{
                "ok": True,
                "status": "running",
                "result": {
                    "screen": "THREADS_ONBOARDING",
                    "profile_updates": {"username": "unexpected"},
                },
            }]),
        )
        runtime._drive_job(job("AUTOMATE_THREADS"))
        self.assertEqual("NEEDS_CONFIRMATION", acc["stage"])
        self.assertEqual([], service.username_updates)
        self.assertEqual([], runtime.running_actions)

    def test_remote_prepare_routes_to_avd_automation_and_waits_legally(self):
        acc = account()
        repo = FakeRepo(acc)
        service = FakeService(repo)
        gateway = FakeGateway([
            {"ok": True, "status": "completed", "result": {"screen": "IG_SIGNUP_ENTRY"}},
            {"ok": True, "status": "waiting_human", "result": {"screen": "OTP_REQUIRED", "reason": "HUMAN_VERIFICATION_REQUIRED"}},
        ])
        runtime = TestRuntime(repo, service, gateway)
        runtime._drive_job(job())
        self.assertEqual(["PREPARE_INSTAGRAM", "AUTOMATE_INSTAGRAM"], [x[0] for x in gateway.commands])
        self.assertEqual("WAITING_HUMAN", acc["stage"])
        self.assertEqual(
            [("RUNNER_ASSIGNED", "IG_READY_FOR_HUMAN", None), ("IG_READY_FOR_HUMAN", "WAITING_HUMAN", None)],
            service.transitions,
        )
        self.assertEqual("IG_POSTCHECK", repo.checkpoint["type"])

    def test_remote_completed_instagram_advances_to_threads_without_checkpoint(self):
        acc = account()
        repo = FakeRepo(acc)
        service = FakeService(repo)
        gateway = FakeGateway([
            {"ok": True, "status": "completed", "result": {"screen": "IG_SIGNUP_ENTRY"}},
            {"ok": True, "status": "completed", "result": {"screen": "IG_POSTCHECK_OK"}},
        ])
        runtime = TestRuntime(repo, service, gateway)
        runtime._drive_job(job())
        self.assertEqual("IG_CREATED", acc["stage"])
        self.assertIsNone(repo.checkpoint)
        self.assertEqual("PREPARE_THREADS", runtime.running_actions[-1])

    def test_remote_unknown_opens_confirmation_checkpoint_with_legal_chain(self):
        acc = account()
        repo = FakeRepo(acc)
        service = FakeService(repo)
        gateway = FakeGateway([
            {"ok": True, "status": "completed", "result": {"screen": "IG_SIGNUP_ENTRY"}},
            {"ok": True, "status": "needs_confirmation", "result": {"screen": "UNKNOWN", "reason": "UI_CHANGED"}},
        ])
        runtime = TestRuntime(repo, service, gateway)
        runtime._drive_job(job())
        self.assertEqual("NEEDS_CONFIRMATION", acc["stage"])
        self.assertEqual(
            ["IG_READY_FOR_HUMAN", "WAITING_HUMAN", "NEEDS_CONFIRMATION"],
            [transition[1] for transition in service.transitions],
        )

    def test_remote_rate_limit_releases_retry_pending(self):
        acc = account()
        repo = FakeRepo(acc)
        service = FakeService(repo)
        runtime = TestRuntime(
            repo,
            service,
            FakeGateway([{"ok": True, "status": "retry_pending", "result": {"screen": "RATE_LIMITED", "reason": "RATE_LIMITED"}}]),
        )
        runtime._drive_job(job("AUTOMATE_INSTAGRAM"))
        self.assertEqual("RETRY_PENDING", acc["stage"])
        self.assertEqual("RATE_LIMITED", service.transitions[-1][2])
        self.assertEqual([("job-1", "FAILED")], runtime.released)

    def test_remote_threads_completion_uses_existing_activation_path(self):
        acc = account("IG_CREATED")
        repo = FakeRepo(acc)
        service = FakeService(repo)
        runtime = TestRuntime(
            repo,
            service,
            FakeGateway([{"ok": True, "status": "completed", "result": {"screen": "THREADS_POSTCHECK_OK"}}]),
        )
        runtime._drive_job(job("PREPARE_THREADS"))
        self.assertEqual("THREADS_CREATED", acc["stage"])
        self.assertEqual(
            ["THREADS_READY_FOR_HUMAN", "THREADS_CREATED"],
            [transition[1] for transition in service.transitions],
        )
        self.assertEqual(["acc-1"], runtime.activation)

    def test_remote_threads_completion_social_only_releases_without_activation(self):
        acc = account("IG_CREATED")
        repo = FakeRepo(acc, completion_mode="SOCIAL_ONLY")
        service = FakeService(repo)
        runtime = TestRuntime(
            repo,
            service,
            FakeGateway([{"ok": True, "status": "completed", "result": {"screen": "THREADS_POSTCHECK_OK"}}]),
        )

        runtime._drive_job(job("PREPARE_THREADS"))

        self.assertEqual("THREADS_CREATED", acc["stage"])
        self.assertEqual([], runtime.activation)
        self.assertNotIn("START_ACP", runtime.running_actions)
        self.assertEqual([("job-1", "COMPLETED")], runtime.released)

    def test_local_device_keeps_existing_manual_checkpoint_path(self):
        acc = account()
        repo = FakeRepo(acc, worker_type="LOCAL_DEVICE")
        service = FakeService(repo)
        gateway = FakeGateway([])
        runtime = TestRuntime(repo, service, gateway)
        runtime._open_human_checkpoint = lambda *args, **kwargs: gateway.commands.append(("LOCAL_MANUAL", kwargs))
        runtime._drive_job(job(runner_type="LOCAL_DEVICE"))
        self.assertEqual("LOCAL_MANUAL", gateway.commands[0][0])

    def test_remote_waiting_checkpoint_auto_resumes_on_known_successor(self):
        acc = account("WAITING_HUMAN")
        repo = FakeRepo(acc)
        repo.checkpoint = {"id": "cp-1", "type": "IG_POSTCHECK", "status": "OPEN"}
        service = FakeService(repo)
        gateway = FakeGateway([{"ok": True, "status": "completed", "result": {"screen": "IG_HOME"}}])
        runtime = TestRuntime(repo, service, gateway)
        runtime._drive_job(job("OBSERVE_CHECKPOINT"))
        self.assertEqual("IG_CREATED", acc["stage"])
        self.assertEqual(["OBSERVE_CHECKPOINT"], [x[0] for x in gateway.commands])
        self.assertEqual("PREPARE_THREADS", runtime.running_actions[-1])

    def test_remote_checkpoint_still_waiting_does_not_mutate_account(self):
        acc = account("WAITING_HUMAN")
        repo = FakeRepo(acc)
        repo.checkpoint = {"id": "cp-1", "type": "IG_POSTCHECK", "status": "OPEN"}
        service = FakeService(repo)
        runtime = TestRuntime(
            repo,
            service,
            FakeGateway([{"ok": True, "status": "waiting_human", "result": {"screen": "OTP_REQUIRED"}}]),
        )
        runtime._drive_job(job("OBSERVE_CHECKPOINT"))
        self.assertEqual("WAITING_HUMAN", acc["stage"])
        self.assertEqual([], service.transitions)
        self.assertEqual(["job-1"], runtime.waiting)


if __name__ == "__main__":
    unittest.main()
