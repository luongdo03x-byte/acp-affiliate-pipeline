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
        return SimpleNamespace(returncode=0, stdout="Success\n", stderr="")


class FakeDriver:
    def open_package(self, package):
        pass

    def detect_screen(self):
        return DetectedScreen("UNKNOWN", 0.0, ())


class FakeFlow:
    def __init__(self):
        self.driver = FakeDriver()

    def run(self, profile, *, account_id=None):
        return FlowResult("running", "UNKNOWN")

    def observe_checkpoint(self):
        return FlowResult("running", "UNKNOWN")


class FakeBrowserFlow:
    def prepare_browser(self):
        return FlowResult("running", "BROWSER_READY")

    def run(self, username, password):
        return FlowResult("running", "LOGIN_SUCCEEDED")


class FakeAvd:
    adb = "adb"
    runner = SimpleNamespace(
        run=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr="")
    )

    def __init__(self):
        self.events = []

    def reset_browser_session(self, serial, browser_package):
        self.events.append(("reset", serial, browser_package))

    def open_package(self, serial, package):
        self.events.append(("open_package", serial, package))

    def set_package_enabled(self, serial, package, enabled):
        self.events.append(("package_enabled", serial, package, bool(enabled)))

    def open_url(self, serial, url, *, browser_package=None):
        self.events.append(("open", serial, url, browser_package))


class OAuthBrowserSessionTests(unittest.TestCase):
    def make_agent(self):
        avd = FakeAvd()
        agent = WorkerAgent(
            "worker-1",
            "acp-worker-01",
            "emulator-5554",
            avd=avd,
            instagram_flow=FakeFlow(),
            threads_flow=FakeFlow(),
            browser_login_flow=FakeBrowserFlow(),
        )
        return agent, avd

    def test_switching_account_resets_oauth_browser_but_same_account_retry_reuses_session(self):
        agent, avd = self.make_agent()
        url_1 = "https://threads.net/oauth/authorize?state=one"
        url_1_retry = "https://threads.net/oauth/authorize?state=two"
        url_2 = "https://threads.net/oauth/authorize?state=three"

        agent.execute(WorkerCommand("open-1", "OPEN_URL", "acc-1", {"url": url_1}))
        agent.execute(WorkerCommand("open-2", "OPEN_URL", "acc-1", {"url": url_1_retry}))
        agent.execute(WorkerCommand("open-3", "OPEN_URL", "acc-2", {"url": url_2}))

        self.assertEqual(
            [
                ("reset", "emulator-5554", "com.android.chrome"),
                ("open_package", "emulator-5554", "com.android.chrome"),
                ("package_enabled", "emulator-5554", "com.instagram.barcelona", False),
                ("open", "emulator-5554", url_1, "com.android.chrome"),
                ("open", "emulator-5554", url_1_retry, "com.android.chrome"),
                ("reset", "emulator-5554", "com.android.chrome"),
                ("open_package", "emulator-5554", "com.android.chrome"),
                ("package_enabled", "emulator-5554", "com.instagram.barcelona", False),
                ("open", "emulator-5554", url_2, "com.android.chrome"),
            ],
            avd.events,
        )

    def test_oauth_open_without_account_binding_fails_closed(self):
        agent, avd = self.make_agent()

        with self.assertRaisesRegex(ValueError, "account"):
            agent.execute(WorkerCommand(
                "open-unbound",
                "OPEN_URL",
                None,
                {"url": "https://threads.net/oauth/authorize?state=one"},
            ))

        self.assertEqual([], avd.events)

    def test_avd_reset_clears_same_browser_package_used_for_oauth(self):
        runner = FakeRunner()
        manager = AvdManager(runner=runner, adb_path="adb", emulator_path="emulator")
        reset = getattr(manager, "reset_browser_session", None)
        if reset is None:
            self.fail("AvdManager must expose reset_browser_session")

        reset("emulator-5554", "com.android.chrome")
        manager.open_url(
            "emulator-5554",
            "https://threads.net/oauth/authorize?state=one",
            browser_package="com.android.chrome",
        )

        self.assertEqual(
            [
                (("adb", "-s", "emulator-5554", "shell", "pm", "clear", "com.android.chrome"), 20),
                ((
                    "adb", "-s", "emulator-5554", "shell", "am", "start",
                    "-a", "android.intent.action.VIEW",
                    "-d", "https://threads.net/oauth/authorize?state=one",
                    "-p", "com.android.chrome",
                ), 20),
            ],
            runner.calls,
        )

    def test_avd_can_disable_and_restore_threads_app_for_oauth(self):
        runner = FakeRunner()
        manager = AvdManager(runner=runner, adb_path="adb", emulator_path="emulator")

        manager.set_package_enabled("emulator-5554", "com.instagram.barcelona", False)
        manager.set_package_enabled("emulator-5554", "com.instagram.barcelona", True)

        self.assertEqual(
            [
                ((
                    "adb", "-s", "emulator-5554", "shell", "pm", "disable-user",
                    "--user", "0", "com.instagram.barcelona",
                ), 20),
                ((
                    "adb", "-s", "emulator-5554", "shell", "pm", "enable",
                    "--user", "0", "com.instagram.barcelona",
                ), 20),
            ],
            runner.calls,
        )


if __name__ == "__main__":
    unittest.main()
