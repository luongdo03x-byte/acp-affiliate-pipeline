import unittest

from core.factory_v2.ui_automation.browser.flow import BrowserLoginFlow
from core.factory_v2.ui_automation.browser.screens import build_browser_detector
from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.driver import ActionResult
from core.factory_v2.ui_automation.hierarchy import UiHierarchyReader
from core.factory_v2.ui_automation.flow_result import FlowResult
from core.factory_v2.worker_protocol import WorkerCommand
from workers.account_factory_worker import WorkerAgent


class FakeSocialFlow:
    def __init__(self):
        self.driver = type("Driver", (), {
            "open_package": lambda self, package: None,
            "detect_screen": lambda self: DetectedScreen("UNKNOWN", 0.0, ()),
        })()

    def run(self, profile, *, account_id=None):
        return FlowResult("running", "UNKNOWN")

    def observe_checkpoint(self, *args, **kwargs):
        return FlowResult("running", "UNKNOWN")


class FakeBrowserFlow:
    def __init__(self):
        self.prepare_calls = 0

    def prepare_browser(self):
        self.prepare_calls += 1
        return FlowResult("running", "BROWSER_READY")

    def run(self, username, password):
        return FlowResult("running", "LOGIN_SUCCEEDED")


class FakeAvd:
    adb = "adb"
    runner = type("Runner", (), {
        "run": lambda *args, **kwargs: type(
            "Result", (), {"returncode": 0, "stdout": "", "stderr": ""}
        )()
    })()

    def __init__(self):
        self.events = []

    def reset_browser_session(self, serial, browser_package):
        self.events.append(("reset", serial, browser_package))

    def open_package(self, serial, package):
        self.events.append(("open_package", serial, package))

    def set_package_enabled(self, serial, package, enabled):
        self.events.append(("package_enabled", serial, package, bool(enabled)))

    def open_url(self, serial, url, *, browser_package=None):
        self.events.append(("open_url", serial, url, browser_package))


class FirstRunDriver:
    def __init__(self):
        self.actions = []
        self.wait_calls = 0

    def wait_for(self, screens, timeout):
        self.wait_calls += 1
        self.actions.append(("wait_for", tuple(screens), float(timeout)))
        if self.wait_calls == 1:
            return DetectedScreen("CHROME_FIRST_RUN", 0.99, ("use_without_account",))
        return DetectedScreen("UNKNOWN", 0.0, ())

    def tap_use_without_account(self):
        self.actions.append(("tap", "use_without_account"))
        return ActionResult("completed", before="CHROME_FIRST_RUN", after="UNKNOWN")


class StalledFirstRunDriver(FirstRunDriver):
    def wait_for(self, screens, timeout):
        self.wait_calls += 1
        self.actions.append(("wait_for", tuple(screens), float(timeout)))
        return DetectedScreen("CHROME_FIRST_RUN", 0.99, ("use_without_account",))


class PrivacyBootstrapDriver(FirstRunDriver):
    def wait_for(self, screens, timeout):
        self.wait_calls += 1
        self.actions.append(("wait_for", tuple(screens), float(timeout)))
        if self.wait_calls == 1:
            return DetectedScreen("CHROME_FIRST_RUN", 0.99, ("use_without_account",))
        if self.wait_calls == 2:
            return DetectedScreen("CHROME_AD_PRIVACY", 0.99, ("got_it",))
        return DetectedScreen("UNKNOWN", 0.0, ())

    def tap_got_it(self):
        self.actions.append(("tap", "got_it"))
        return ActionResult("completed", before="CHROME_AD_PRIVACY", after="UNKNOWN")


class OAuthBrowserHardeningTests(unittest.TestCase):
    def make_agent(self):
        avd = FakeAvd()
        browser_flow = FakeBrowserFlow()
        agent = WorkerAgent(
            "worker-1",
            "acp-worker-01",
            "emulator-5554",
            avd=avd,
            instagram_flow=FakeSocialFlow(),
            threads_flow=FakeSocialFlow(),
            browser_login_flow=browser_flow,
        )
        return agent, avd, browser_flow

    def test_chrome_first_run_is_detected_only_with_exact_skip_control(self):
        xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
        <hierarchy rotation='0'>
          <node index='0' text='Welcome to Chrome' resource-id='' class='android.widget.TextView'
                package='com.android.chrome' content-desc='' clickable='false' enabled='true'
                bounds='[0,0][400,100]' />
          <node index='1' text='Use without an account' resource-id='' class='android.widget.Button'
                package='com.android.chrome' content-desc='' clickable='true' enabled='true'
                bounds='[20,700][380,780]' />
        </hierarchy>"""
        snapshot = UiHierarchyReader().parse(
            xml,
            package="com.android.chrome",
            activity="ChromeActivity",
        )

        detected = build_browser_detector().detect(snapshot)

        self.assertEqual("CHROME_FIRST_RUN", detected.kind)
        self.assertFalse(detected.protected)
        self.assertTrue(detected.automation_allowed)

    def test_chrome_ad_privacy_is_detected_only_with_exact_got_it(self):
        xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
        <hierarchy rotation='0'>
          <node index='0' text='Enhanced ad privacy in Chrome' resource-id='' class='android.widget.TextView'
                package='com.android.chrome' content-desc='' clickable='false' enabled='true'
                bounds='[0,0][400,100]' />
          <node index='1' text='Settings' resource-id='' class='android.widget.Button'
                package='com.android.chrome' content-desc='' clickable='true' enabled='true'
                bounds='[20,700][190,780]' />
          <node index='2' text='Got it' resource-id='' class='android.widget.Button'
                package='com.android.chrome' content-desc='' clickable='true' enabled='true'
                bounds='[210,700][380,780]' />
        </hierarchy>"""
        snapshot = UiHierarchyReader().parse(
            xml,
            package="com.android.chrome",
            activity="ChromeActivity",
        )

        detected = build_browser_detector().detect(snapshot)

        self.assertEqual("CHROME_AD_PRIVACY", detected.kind)
        self.assertFalse(detected.protected)
        self.assertTrue(detected.automation_allowed)

    def test_prepare_browser_skips_chrome_first_run_before_oauth_navigation(self):
        driver = FirstRunDriver()
        flow = BrowserLoginFlow(driver, load_timeout=8.0)

        result = flow.prepare_browser()

        self.assertEqual("running", result.status)
        self.assertEqual("BROWSER_READY", result.screen)
        self.assertEqual(
            ("wait_for", ("CHROME_FIRST_RUN",), 8.0),
            driver.actions[0],
        )
        self.assertEqual(("tap", "use_without_account"), driver.actions[1])
        self.assertEqual("wait_for", driver.actions[2][0])
        self.assertIn("CHROME_AD_PRIVACY", driver.actions[2][1])

    def test_prepare_browser_clears_ad_privacy_after_first_run(self):
        driver = PrivacyBootstrapDriver()
        flow = BrowserLoginFlow(driver, load_timeout=8.0)

        result = flow.prepare_browser()

        self.assertEqual("running", result.status)
        self.assertEqual("BROWSER_READY", result.screen)
        self.assertIn(("tap", "use_without_account"), driver.actions)
        self.assertIn(("tap", "got_it"), driver.actions)
        self.assertEqual("wait_for", driver.actions[-1][0])
        self.assertIn("UNKNOWN", driver.actions[-1][1])

    def test_prepare_browser_fails_closed_if_first_run_does_not_exit_after_tap(self):
        driver = StalledFirstRunDriver()
        flow = BrowserLoginFlow(driver, load_timeout=8.0)

        result = flow.prepare_browser()

        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("CHROME_FIRST_RUN", result.screen)
        self.assertEqual("CHROME_FIRST_RUN_UNVERIFIED", result.reason)

    def test_open_url_prepares_chrome_and_disables_threads_before_navigation(self):
        agent, avd, browser_flow = self.make_agent()
        url = "https://threads.net/oauth/authorize?state=one"

        agent.execute(WorkerCommand("open-1", "OPEN_URL", "acc-1", {"url": url}))

        self.assertEqual(1, browser_flow.prepare_calls)
        self.assertEqual(
            [
                ("reset", "emulator-5554", "com.android.chrome"),
                ("open_package", "emulator-5554", "com.android.chrome"),
                ("package_enabled", "emulator-5554", "com.instagram.barcelona", False),
                ("open_url", "emulator-5554", url, "com.android.chrome"),
            ],
            avd.events,
        )

    def test_restore_oauth_apps_reenables_threads_after_browser_session(self):
        agent, avd, _ = self.make_agent()
        url = "https://threads.net/oauth/authorize?state=one"
        agent.execute(WorkerCommand("open-1", "OPEN_URL", "acc-1", {"url": url}))

        agent.execute(WorkerCommand("restore-1", "RESTORE_OAUTH_APPS", "acc-1", {}))

        self.assertEqual(
            ("package_enabled", "emulator-5554", "com.instagram.barcelona", True),
            avd.events[-1],
        )


if __name__ == "__main__":
    unittest.main()
