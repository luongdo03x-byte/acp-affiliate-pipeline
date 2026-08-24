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

_LIVE_THREADS_LOGIN_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation='0'>
  <node index='0' text='Threads • Log in' resource-id='' class='android.webkit.WebView'
        package='com.android.chrome' content-desc='' clickable='false' enabled='true'
        bounds='[0,290][1080,2276]' />
  <node index='1' text='Log in with Instagram' resource-id='' class='android.widget.TextView'
        package='com.android.chrome' content-desc='' clickable='false' enabled='true'
        bounds='[338,939][742,988]' />
  <node index='2' text='' resource-id='' class='android.widget.EditText'
        package='com.android.chrome' content-desc='' clickable='true' enabled='true'
        bounds='[74,1035][1006,1186]' />
  <node index='3' text='' resource-id='' class='android.widget.EditText'
        package='com.android.chrome' content-desc='' clickable='true' enabled='true'
        password='true' bounds='[74,1205][1006,1357]' />
  <node index='4' text='Log in' resource-id='' class='android.widget.Button'
        package='com.android.chrome' content-desc='' clickable='true' enabled='true'
        bounds='[74,1395][1006,1555]' />
  <node index='5'
        text='threads.com/login?next=https%3A%2F%2Fwww.threads.com%2Foauth%2Fauthorize'
        resource-id='com.android.chrome:id/url_bar' class='android.widget.EditText'
        package='com.android.chrome' content-desc='' clickable='true' enabled='true'
        bounds='[220,144][651,282]' />
</hierarchy>"""


class FakeDriver:
    def __init__(self, screens):
        self.screens = list(screens)
        self.actions = []
        self.wait_calls = []

    def detect_screen(self):
        if len(self.screens) > 1:
            return self.screens.pop(0)
        return self.screens[0]

    def wait_for(self, screens, timeout):
        self.wait_calls.append((tuple(screens), timeout))
        return self.detect_screen()

    def set_username(self, value):
        self.actions.append("username")
        return ActionResult("completed", before=BROWSER_LOGIN, after=BROWSER_LOGIN)

    def set_password(self, value):
        self.actions.append("password")
        return ActionResult("completed", before=BROWSER_LOGIN, after=BROWSER_LOGIN)

    def tap_login(self):
        self.actions.append("login")
        return ActionResult("completed", before=BROWSER_LOGIN)


class LiveLoginAdb:
    def foreground(self):
        return "com.android.chrome", "ChromeActivity"

    def dump_hierarchy(self):
        return _LIVE_THREADS_LOGIN_XML


class KeyboardOccludingLoginAdb:
    """Simulates Chrome hiding the login button while the soft keyboard is open."""

    def __init__(self):
        self.keyboard_open = False
        self.back_calls = 0
        self.text_values = []

    def foreground(self):
        return "com.android.chrome", "ChromeActivity"

    def dump_hierarchy(self):
        if not self.keyboard_open:
            return _LIVE_THREADS_LOGIN_XML

        # Live behavior observed on the AVD: both web EditTexts remain visible,
        # but the Log in button is no longer exposed as clickable while the
        # soft keyboard covers it.
        return _LIVE_THREADS_LOGIN_XML.replace(
            "text='Log in' resource-id='' class='android.widget.Button'\n"
            "        package='com.android.chrome' content-desc='' "
            "clickable='true' enabled='true'",
            "text='Log in' resource-id='' class='android.widget.Button'\n"
            "        package='com.android.chrome' content-desc='' "
            "clickable='false' enabled='true'",
        )

    def tap(self, x, y):
        self.keyboard_open = True

    def keyevent(self, keycode):
        pass

    def set_text(self, value):
        self.text_values.append(value)
        self.keyboard_open = True

    def back(self):
        self.back_calls += 1
        self.keyboard_open = False


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
    def _snapshot_fixture(self, name, *, package="com.android.chrome"):
        xml = (_FIXTURES / name).read_text()
        return UiHierarchyReader().parse(
            xml,
            package=package,
            activity="ChromeActivity",
        )

    def _detect_fixture(self, name):
        return build_browser_detector().detect(self._snapshot_fixture(name))

    def test_detector_recognizes_login_form_even_when_password_hint_is_redacted(self):
        detected = self._detect_fixture("browser_login_form.xml")
        self.assertEqual(BROWSER_LOGIN, detected.kind)
        self.assertTrue(detected.automation_allowed)

    def test_live_threads_login_ignores_chrome_url_bar_and_missing_placeholders(self):
        snapshot = UiHierarchyReader().parse(
            _LIVE_THREADS_LOGIN_XML,
            package="com.android.chrome",
            activity="ChromeActivity",
        )

        detected = build_browser_detector().detect(snapshot)

        self.assertEqual(BROWSER_LOGIN, detected.kind)
        self.assertTrue(detected.automation_allowed)

    def test_secret_driver_uses_two_web_fields_not_chrome_url_bar(self):
        driver = BrowserSecretDriver(LiveLoginAdb(), build_browser_detector())

        detected, username_node, password_node, login_button = driver._login_nodes()

        self.assertEqual(BROWSER_LOGIN, detected.kind)
        self.assertEqual(1035, username_node.bounds.top)
        self.assertEqual(1205, password_node.bounds.top)
        self.assertEqual("", username_node.resource_id)
        self.assertEqual("", password_node.resource_id)
        self.assertEqual("Log in", login_button.text)

    def test_login_form_wins_over_auxiliary_permissions_marker(self):
        xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
        <hierarchy rotation='0'>
          <node index='0' text='Username, phone or email' resource-id='username'
                class='android.widget.EditText' package='com.android.chrome'
                content-desc='' clickable='true' enabled='true' bounds='[20,200][380,260]' />
          <node index='1' text='' resource-id='password'
                class='android.widget.EditText' package='com.android.chrome'
                content-desc='' clickable='true' enabled='true' bounds='[20,280][380,340]' />
          <node index='2' text='Log in' resource-id='login'
                class='android.widget.Button' package='com.android.chrome'
                content-desc='' clickable='true' enabled='true' bounds='[20,360][380,420]' />
          <node index='3' text='' resource-id='com.android.chrome:id/permissions_helper'
                class='android.view.View' package='com.android.chrome'
                content-desc='' clickable='false' enabled='true' bounds='[0,0][1,1]' />
        </hierarchy>"""
        snapshot = UiHierarchyReader().parse(
            xml,
            package="com.android.chrome",
            activity="ChromeActivity",
        )

        detected = build_browser_detector().detect(snapshot)

        self.assertEqual(BROWSER_LOGIN, detected.kind)
        self.assertTrue(detected.automation_allowed)

    def test_consent_marker_without_allow_button_is_not_consent(self):
        xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
        <hierarchy rotation='0'>
          <node index='0' text='' resource-id='com.android.chrome:id/permissions_helper'
                class='android.view.View' package='com.android.chrome'
                content-desc='' clickable='false' enabled='true' bounds='[0,0][1,1]' />
        </hierarchy>"""
        snapshot = UiHierarchyReader().parse(
            xml,
            package="com.android.chrome",
            activity="ChromeActivity",
        )

        detected = build_browser_detector().detect(snapshot)

        self.assertEqual("UNKNOWN", detected.kind)
        self.assertFalse(detected.automation_allowed)

    def test_login_form_in_non_chrome_package_is_never_automatable(self):
        detected = build_browser_detector().detect(
            self._snapshot_fixture("browser_login_form.xml", package="com.evil.fake")
        )
        self.assertEqual("UNKNOWN", detected.kind)
        self.assertFalse(detected.automation_allowed)

    def test_detector_prioritizes_security_challenge_over_generic_continue(self):
        detected = self._detect_fixture("browser_security_challenge.xml")
        self.assertEqual(SECURITY_CHALLENGE, detected.kind)
        self.assertTrue(detected.protected)

    def test_detector_recognizes_oauth_consent_as_protected(self):
        detected = self._detect_fixture("browser_oauth_consent.xml")
        self.assertEqual(OAUTH_CONSENT, detected.kind)
        self.assertTrue(detected.protected)

    def test_recognized_login_form_waits_then_fills_and_submits(self):
        driver = FakeDriver([
            DetectedScreen(BROWSER_LOGIN, 0.99, ("login",)),
            DetectedScreen(OAUTH_CONSENT, 0.99, ("allow",), True),
        ])
        result = BrowserLoginFlow(driver).run("user1", "example-secret")
        self.assertEqual("running", result.status)
        self.assertEqual("LOGIN_SUCCEEDED", result.screen)
        self.assertEqual(["username", "password", "login"], driver.actions)
        self.assertEqual(2, len(driver.wait_calls))
        self.assertNotIn("example-secret", repr(result))
        self.assertNotIn("user1", repr(result))

    def test_secret_driver_closes_keyboard_between_credential_fields(self):
        adb = KeyboardOccludingLoginAdb()
        driver = BrowserSecretDriver(
            adb,
            build_browser_detector(),
            poll_interval=0,
            sleeper=lambda _: None,
        )

        username_result = driver.set_username("user1")

        self.assertEqual("completed", username_result.status)
        self.assertFalse(adb.keyboard_open)
        self.assertEqual(BROWSER_LOGIN, driver.detect_screen().kind)

        password_result = driver.set_password("example-secret")

        self.assertEqual("completed", password_result.status)
        self.assertFalse(adb.keyboard_open)
        self.assertEqual(BROWSER_LOGIN, driver.detect_screen().kind)

        self.assertEqual(2, adb.back_calls)
        self.assertEqual(
            ["user1", "example-secret"],
            adb.text_values,
        )

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
