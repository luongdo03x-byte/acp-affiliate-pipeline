import unittest
from pathlib import Path

from core.factory_v2.ui_automation.browser.flow import BrowserLoginFlow
from core.factory_v2.ui_automation.browser.screens import (
    BROWSER_LOGIN,
    OAUTH_CONSENT,
    SECURITY_CHALLENGE,
    build_browser_detector,
)
from core.factory_v2.ui_automation.browser.secret_driver import BrowserSecretDriver
from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.driver import ActionResult
from core.factory_v2.ui_automation.hierarchy import UiBounds, UiHierarchyReader, UiNode


_FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FakeDriver:
    def __init__(self, screens):
        self.screens = list(screens)
        self.actions = []

    def detect_screen(self):
        if len(self.screens) > 1:
            return self.screens.pop(0)
        return self.screens[0]

    def set_username(self, value):
        self.actions.append("username")
        return ActionResult("completed", before=BROWSER_LOGIN, after=BROWSER_LOGIN)

    def set_password(self, value):
        self.actions.append("password")
        return ActionResult("completed", before=BROWSER_LOGIN, after=BROWSER_LOGIN)

    def tap_login(self):
        self.actions.append("login")
        return ActionResult("completed", before=BROWSER_LOGIN)


class ExplodingPasswordDriver(BrowserSecretDriver):
    def __init__(self):
        pass

    def _login_nodes(self):
        password_node = UiNode(
            text="",
            content_desc="",
            resource_id="password",
            class_name="android.widget.EditText",
            clickable=True,
            enabled=True,
            bounds=UiBounds(0, 0, 10, 10),
        )
        return (
            DetectedScreen(BROWSER_LOGIN, 0.99, ("login",)),
            None,
            password_node,
            None,
        )

    def _replace_text(self, node, value):
        raise RuntimeError(f"simulated adb error {value}")


class BrowserLoginFlowTests(unittest.TestCase):
    def _detect_fixture(self, name):
        xml = (_FIXTURES / name).read_text()
        snapshot = UiHierarchyReader().parse(
            xml,
            package="com.android.chrome",
            activity="ChromeActivity",
        )
        return build_browser_detector().detect(snapshot)

    def test_detector_recognizes_login_form_even_when_password_hint_is_redacted(self):
        detected = self._detect_fixture("browser_login_form.xml")
        self.assertEqual(BROWSER_LOGIN, detected.kind)
        self.assertTrue(detected.automation_allowed)

    def test_detector_prioritizes_security_challenge_over_generic_continue(self):
        detected = self._detect_fixture("browser_security_challenge.xml")
        self.assertEqual(SECURITY_CHALLENGE, detected.kind)
        self.assertTrue(detected.protected)

    def test_detector_recognizes_oauth_consent_as_protected(self):
        detected = self._detect_fixture("browser_oauth_consent.xml")
        self.assertEqual(OAUTH_CONSENT, detected.kind)
        self.assertTrue(detected.protected)

    def test_recognized_login_form_fills_username_and_password_then_submits(self):
        driver = FakeDriver([
            DetectedScreen(BROWSER_LOGIN, 0.99, ("login",)),
            DetectedScreen(OAUTH_CONSENT, 0.99, ("allow",), True),
        ])
        result = BrowserLoginFlow(driver).run("user1", "example-secret")
        self.assertEqual("running", result.status)
        self.assertEqual("LOGIN_SUCCEEDED", result.screen)
        self.assertEqual(["username", "password", "login"], driver.actions)
        self.assertNotIn("example-secret", repr(result))
        self.assertNotIn("user1", repr(result))

    def test_password_adb_failure_is_sanitized(self):
        result = ExplodingPasswordDriver().set_password("example-secret")
        self.assertEqual("postcondition_failed", result.status)
        self.assertNotIn("example-secret", repr(result))

    def test_oauth_consent_is_human_only(self):
        driver = FakeDriver([
            DetectedScreen(OAUTH_CONSENT, 0.99, ("allow",), True),
        ])
        result = BrowserLoginFlow(driver).run("user1", "example-secret")
        self.assertEqual("waiting_human", result.status)
        self.assertEqual(OAUTH_CONSENT, result.screen)
        self.assertEqual([], driver.actions)

    def test_security_challenge_is_human_only(self):
        driver = FakeDriver([
            DetectedScreen(SECURITY_CHALLENGE, 0.99, ("challenge",), True),
        ])
        result = BrowserLoginFlow(driver).run("user1", "example-secret")
        self.assertEqual("waiting_human", result.status)
        self.assertEqual(SECURITY_CHALLENGE, result.screen)
        self.assertEqual([], driver.actions)

    def test_unknown_screen_never_types_secret(self):
        driver = FakeDriver([
            DetectedScreen("UNKNOWN", 0.0, ()),
        ])
        result = BrowserLoginFlow(driver).run("user1", "example-secret")
        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual([], driver.actions)
        self.assertNotIn("example-secret", repr(result))


if __name__ == "__main__":
    unittest.main()
