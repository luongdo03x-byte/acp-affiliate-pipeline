import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.flow_result import FlowResult
from core.factory_v2.worker_protocol import WorkerCommand
from workers.account_factory_worker import WorkerAgent


class FakeDriver:
    def __init__(self, screen):
        self.screen = screen
        self.opened = []

    def open_package(self, package):
        self.opened.append(package)

    def detect_screen(self):
        return self.screen


class FakeFlow:
    def __init__(self, result, screen=None):
        self.result = result
        self.driver = FakeDriver(screen or DetectedScreen(result.screen, 0.99, ()))
        self.run_calls = []
        self.account_ids = []
        self.observe_calls = 0

    def run(self, profile, *, account_id=None):
        self.run_calls.append(dict(profile))
        self.account_ids.append(account_id)
        return self.result

    def observe_checkpoint(self, profile=None):
        self.observe_calls += 1
        return self.result


class FakeBrowserLoginFlow:
    def __init__(self, result=None):
        self.result = result or FlowResult(
            "waiting_human", "OAUTH_CONSENT", "HUMAN_CONSENT_REQUIRED"
        )
        self.calls = []

    def run(self, username, password):
        self.calls.append((username, password))
        return self.result


class FakeAvd:
    adb = "adb"
    runner = SimpleNamespace(
        run=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr="")
    )

    def __init__(self):
        self.urls = []
        self.packages = []
        self.browser_resets = []
        self.app_resets = []

    def reset_browser_session(self, serial, browser_package):
        self.browser_resets.append((serial, browser_package))

    def reset_app_session(self, serial, package):
        self.app_resets.append((serial, package))

    def open_url(self, serial, url, *, browser_package=None):
        self.urls.append((serial, url, browser_package))

    def open_package(self, serial, package):
        self.packages.append((serial, package))


class FakeAdbClient:
    def __init__(self):
        self.push_calls = []

    def push_file(self, source, destination):
        self.push_calls.append((str(source), str(destination)))


class AvdWorkerAgentTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "username": "sample_user",
            "display_name": "Sample User",
            "bio": "Sample bio",
            "signup_contact_type": "phone",
            "signup_contact": "+84901234567",
            "birth_date": "2000-05-20",
            "password": "secret-must-not-pass",
            "otp": "123456",
            "verification_code": "654321",
            "recovery_code": "recover-me",
            "arbitrary": "must-not-pass",
        }

    def make_agent(
        self,
        instagram_result=None,
        threads_result=None,
        *,
        adb_client=None,
        browser_login_flow=None,
    ):
        instagram = FakeFlow(
            instagram_result
            or FlowResult(
                "waiting_human", "OTP_REQUIRED", "HUMAN_VERIFICATION_REQUIRED"
            )
        )
        threads = FakeFlow(
            threads_result or FlowResult("running", "THREADS_ONBOARDING")
        )
        kwargs = {
            "avd": FakeAvd(),
            "instagram_flow": instagram,
            "threads_flow": threads,
        }
        if adb_client is not None:
            kwargs["adb_client"] = adb_client
        if browser_login_flow is not None:
            kwargs["browser_login_flow"] = browser_login_flow
        agent = WorkerAgent(
            "worker-1",
            "acp-worker-01",
            "emulator-5554",
            **kwargs,
        )
        return agent, instagram, threads

    def test_default_worker_constructs_browser_login_flow(self):
        agent, _, _ = self.make_agent()
        self.assertIsNotNone(agent.browser_login_flow)

    def test_transient_browser_login_requires_same_oauth_browser_account(self):
        flow = FakeBrowserLoginFlow()
        agent, _, _ = self.make_agent(browser_login_flow=flow)
        agent.oauth_browser_account_id = "acc-a"
        with self.assertRaisesRegex(ValueError, "browser account"):
            agent.execute(WorkerCommand(
                "secret-1", "TRANSIENT_BROWSER_LOGIN", "acc-b",
                {"username": "userb", "password": "example-secret"},
            ))
        self.assertEqual([], flow.calls)

    def test_transient_browser_login_result_never_echoes_secret(self):
        flow = FakeBrowserLoginFlow()
        agent, _, _ = self.make_agent(browser_login_flow=flow)
        agent.oauth_browser_account_id = "acc-a"
        response = agent.execute(WorkerCommand(
            "secret-2", "TRANSIENT_BROWSER_LOGIN", "acc-a",
            {"username": "usera", "password": "example-secret"},
        ))
        self.assertEqual([("usera", "example-secret")], flow.calls)
        self.assertNotIn("example-secret", repr(response))
        self.assertNotIn("usera", repr(response.get("result", {})))

    def test_waiting_human_result_contains_no_sensitive_keys(self):
        agent, instagram, _ = self.make_agent()
        response = agent.execute(WorkerCommand(
            command_id="cmd-1",
            action="AUTOMATE_INSTAGRAM",
            account_id="acc-1",
            payload={"job_id": "job-1", "profile": self.profile},
        ))
        self.assertEqual("waiting_human", response["status"])
        self.assertEqual("OTP_REQUIRED", response["result"]["screen"])
        for key in ("password", "code", "raw_xml", "token", "otp"):
            self.assertNotIn(key, response["result"])
        self.assertEqual(
            {
                "username", "display_name", "bio", "signup_contact_type",
                "signup_contact", "birth_date",
            },
            set(instagram.run_calls[0]),
        )
        for forbidden in (
            "password", "otp", "verification_code", "recovery_code", "arbitrary"
        ):
            self.assertNotIn(forbidden, instagram.run_calls[0])

    def test_instagram_flow_receives_stable_account_id(self):
        agent, instagram, _ = self.make_agent(FlowResult("running", "IG_USERNAME_ENTRY"))
        agent.execute(WorkerCommand(
            "account-seed", "AUTOMATE_INSTAGRAM", "acc-123", {"profile": self.profile}
        ))
        self.assertEqual(["acc-123"], instagram.account_ids)

    def test_worker_returns_only_sanitized_username_profile_update(self):
        agent, _, _ = self.make_agent(
            FlowResult(
                "running",
                "IG_USERNAME_VALID",
                last_safe_step="IG_USERNAME_ENTRY",
                profile_updates={"username": "baongocd483102"},
            )
        )
        response = agent.execute(WorkerCommand(
            "username-update", "AUTOMATE_INSTAGRAM", "acc-1", {"profile": self.profile}
        ))
        self.assertEqual(
            {"username": "baongocd483102"},
            response["result"]["profile_updates"],
        )

    def test_worker_rejects_unknown_or_sensitive_profile_update_keys(self):
        agent, _, _ = self.make_agent(
            FlowResult(
                "running",
                "IG_USERNAME_VALID",
                profile_updates={"username": "safe_name", "password": "secret"},
            )
        )
        with self.assertRaisesRegex(ValueError, "profile_updates"):
            agent.execute(WorkerCommand(
                "bad-update", "AUTOMATE_INSTAGRAM", "acc-1", {"profile": self.profile}
            ))

    def test_worker_rejects_invalid_username_profile_update(self):
        agent, _, _ = self.make_agent(
            FlowResult(
                "running",
                "IG_USERNAME_VALID",
                profile_updates={"username": "INVALID USERNAME"},
            )
        )
        with self.assertRaisesRegex(ValueError, "profile_updates"):
            agent.execute(WorkerCommand(
                "bad-username-update", "AUTOMATE_INSTAGRAM", "acc-1", {"profile": self.profile}
            ))

    def test_existing_avatar_is_staged_to_fixed_device_path(self):
        adb_client = FakeAdbClient()
        agent, instagram, _ = self.make_agent(
            FlowResult("running", "IG_AVATAR_SETUP"),
            adb_client=adb_client,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            source = repo_root / "avatars" / "sample.jpg"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"test-avatar")
            profile = dict(self.profile, avatar_file="avatars/sample.jpg")
            with patch("workers.account_factory_worker._REPO_ROOT", repo_root):
                agent.execute(WorkerCommand(
                    "stage-avatar", "AUTOMATE_INSTAGRAM", "acc-1", {"profile": profile}
                ))

        self.assertEqual(
            [(str(source.resolve()), "/sdcard/Pictures/ACP/avatar.jpg")],
            adb_client.push_calls,
        )
        self.assertEqual("avatars/sample.jpg", instagram.run_calls[0]["avatar_file"])

    def test_missing_avatar_file_is_rejected_before_flow_mutation(self):
        adb_client = FakeAdbClient()
        agent, instagram, _ = self.make_agent(adb_client=adb_client)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            profile = dict(self.profile, avatar_file="avatars/missing.jpg")
            with patch("workers.account_factory_worker._REPO_ROOT", repo_root):
                with self.assertRaisesRegex(ValueError, "avatar_file"):
                    agent.execute(WorkerCommand(
                        "missing-avatar", "AUTOMATE_INSTAGRAM", "acc-1", {"profile": profile}
                    ))
        self.assertEqual([], adb_client.push_calls)
        self.assertEqual([], instagram.run_calls)

    def test_invalid_contact_type_is_rejected_before_flow_mutation(self):
        agent, instagram, _ = self.make_agent()
        profile = dict(self.profile, signup_contact_type="username")
        with self.assertRaisesRegex(ValueError, "signup_contact_type"):
            agent.execute(WorkerCommand(
                "invalid-contact", "AUTOMATE_INSTAGRAM", "acc-1", {"profile": profile}
            ))
        self.assertEqual([], instagram.run_calls)

    def test_invalid_birth_date_is_rejected_before_flow_mutation(self):
        agent, instagram, _ = self.make_agent()
        profile = dict(self.profile, birth_date="20/05/2000")
        with self.assertRaisesRegex(ValueError, "birth_date"):
            agent.execute(WorkerCommand(
                "invalid-birthday", "AUTOMATE_INSTAGRAM", "acc-1", {"profile": profile}
            ))
        self.assertEqual([], instagram.run_calls)

    def test_unsafe_avatar_path_is_rejected_before_flow_mutation(self):
        agent, instagram, _ = self.make_agent()
        profile = dict(self.profile, avatar_file="../outside.jpg")
        with self.assertRaisesRegex(ValueError, "avatar_file"):
            agent.execute(WorkerCommand(
                "invalid-avatar", "AUTOMATE_INSTAGRAM", "acc-1", {"profile": profile}
            ))
        self.assertEqual([], instagram.run_calls)

    def test_duplicate_command_is_at_most_once(self):
        agent, instagram, _ = self.make_agent(
            FlowResult("running", "IG_PROFILE_SETUP")
        )
        command = WorkerCommand(
            "same-id", "AUTOMATE_INSTAGRAM", "acc-1", {"profile": self.profile}
        )
        first = agent.execute(command)
        second = agent.execute(command)
        self.assertEqual(first, second)
        self.assertEqual(1, len(instagram.run_calls))

    def test_observe_checkpoint_is_observation_only(self):
        agent, instagram, _ = self.make_agent(FlowResult("completed", "IG_HOME"))
        response = agent.execute(WorkerCommand(
            "observe-1", "OBSERVE_CHECKPOINT", "acc-1", {"flow": "instagram"}
        ))
        self.assertEqual("completed", response["status"])
        self.assertEqual(1, instagram.observe_calls)
        self.assertEqual([], instagram.run_calls)

    def test_prepare_instagram_only_opens_and_detects(self):
        agent, instagram, _ = self.make_agent(FlowResult("running", "IG_SIGNUP_ENTRY"))
        response = agent.execute(WorkerCommand(
            "prepare-1", "PREPARE_INSTAGRAM", "acc-1", {}
        ))
        self.assertEqual("completed", response["status"])
        self.assertEqual(["com.instagram.android"], instagram.driver.opened)
        self.assertEqual([], instagram.run_calls)

    def test_threads_action_opens_official_app_and_sanitizes_profile(self):
        agent, _, threads = self.make_agent(
            threads_result=FlowResult("running", "THREADS_ONBOARDING")
        )
        response = agent.execute(WorkerCommand(
            "threads-1", "AUTOMATE_THREADS", "acc-1", {"profile": self.profile}
        ))
        self.assertEqual("running", response["status"])
        self.assertEqual(["com.instagram.barcelona"], threads.driver.opened)
        self.assertNotIn("password", threads.run_calls[0])
        self.assertNotIn("otp", threads.run_calls[0])
        self.assertNotIn("arbitrary", threads.run_calls[0])

    def test_heartbeat_contains_only_sanitized_recovery_metadata(self):
        agent, _, _ = self.make_agent(FlowResult("running", "IG_PROFILE_SETUP"))
        agent.execute(WorkerCommand(
            "cmd-hb", "AUTOMATE_INSTAGRAM", "acc-1", {"profile": self.profile}
        ))
        heartbeat = agent.heartbeat()
        self.assertEqual("instagram", heartbeat["flow"])
        self.assertEqual("IG_PROFILE_SETUP", heartbeat["last_known_screen"])
        serialized = repr(heartbeat)
        self.assertNotIn("secret-must-not-pass", serialized)
        self.assertNotIn("123456", serialized)
        self.assertNotIn("Sample bio", serialized)
        self.assertNotIn("+84901234567", serialized)


if __name__ == "__main__":
    unittest.main()
