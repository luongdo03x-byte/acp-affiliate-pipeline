import unittest
from types import SimpleNamespace

from core.factory_v2.avd import AvdManager
from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.flow_result import FlowResult
from core.factory_v2.worker_protocol import WorkerCommand
from workers.account_factory_worker import WorkerAgent


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run(self, argv, timeout):
        self.calls.append((tuple(argv), timeout))
        return SimpleNamespace(returncode=0, stdout="", stderr="")


class FakeDriver:
    def __init__(self):
        self.opened = []

    def open_package(self, package):
        self.opened.append(package)

    def detect_screen(self):
        return DetectedScreen("THREADS_ONBOARDING", 0.99, ())


class FakeFlow:
    def __init__(self):
        self.driver = FakeDriver()
        self.run_calls = []

    def run(self, profile, **kwargs):
        self.run_calls.append(dict(profile))
        return FlowResult("running", "THREADS_ONBOARDING")

    def observe_checkpoint(self):
        return FlowResult("running", "THREADS_ONBOARDING")


class FakeAvd:
    adb = "adb"
    runner = FakeRunner()

    def __init__(self):
        self.app_resets = []

    def reset_app_session(self, serial, package):
        self.app_resets.append((serial, package))

    def open_package(self, serial, package):
        pass


class ThreadsSessionIsolationTests(unittest.TestCase):
    def test_avd_can_reset_validated_app_session(self):
        runner = FakeRunner()
        manager = AvdManager(
            runner=runner,
            adb_path="adb",
            emulator_path="emulator",
        )

        manager.reset_app_session("emulator-5554", "com.instagram.barcelona")

        self.assertIn(
            ((
                "adb", "-s", "emulator-5554", "shell", "pm", "clear",
                "com.instagram.barcelona",
            ), 20),
            runner.calls,
        )

    def test_threads_session_resets_on_account_change_but_not_same_account(self):
        avd = FakeAvd()
        instagram = FakeFlow()
        threads = FakeFlow()
        agent = WorkerAgent(
            "worker-1",
            "acp-worker-01",
            "emulator-5554",
            avd=avd,
            instagram_flow=instagram,
            threads_flow=threads,
        )

        profile = {"username": "sample_user"}
        agent.execute(WorkerCommand(
            "threads-a-1", "AUTOMATE_THREADS", "acc-a", {"profile": profile}
        ))
        agent.execute(WorkerCommand(
            "threads-a-2", "AUTOMATE_THREADS", "acc-a", {"profile": profile}
        ))
        agent.execute(WorkerCommand(
            "threads-b-1", "AUTOMATE_THREADS", "acc-b", {"profile": profile}
        ))

        self.assertEqual(
            [
                ("emulator-5554", "com.instagram.barcelona"),
                ("emulator-5554", "com.instagram.barcelona"),
            ],
            avd.app_resets,
        )

    def test_threads_session_binding_is_not_changed_when_reset_fails(self):
        class FailingAvd(FakeAvd):
            def reset_app_session(self, serial, package):
                raise RuntimeError("reset failed")

        avd = FailingAvd()
        agent = WorkerAgent(
            "worker-1",
            "acp-worker-01",
            "emulator-5554",
            avd=avd,
            instagram_flow=FakeFlow(),
            threads_flow=FakeFlow(),
        )

        with self.assertRaisesRegex(RuntimeError, "reset failed"):
            agent.execute(WorkerCommand(
                "threads-fail", "AUTOMATE_THREADS", "acc-a",
                {"profile": {"username": "sample_user"}},
            ))

        self.assertIsNone(getattr(agent, "threads_account_id", None))


if __name__ == "__main__":
    unittest.main()
