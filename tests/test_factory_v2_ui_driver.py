import unittest

from core.factory_v2.ui_automation.driver import SafeUiDriver
from core.factory_v2.ui_automation.instagram.screens import build_instagram_detector
from core.factory_v2.ui_automation.selectors import Selector

USERNAME = Selector(semantic="username", resource_ids=("com.instagram.android:id/username",))
MISSING = Selector(semantic="bio", resource_ids=("missing:id",))


class FakeAdb:
    def __init__(self, text="sample_user"):
        self.text = text
        self.tap_calls = []
        self.input_calls = []
        self.keyevents = []

    def foreground(self):
        return "com.instagram.android", ".MainActivity"

    def dump_hierarchy(self):
        return f'''<hierarchy><node text="{self.text}" resource-id="com.instagram.android:id/username" class="android.widget.EditText" clickable="true" enabled="true" bounds="[0,0][300,100]" /><node text="Next" class="android.widget.Button" clickable="true" enabled="true" bounds="[0,120][300,220]" /></hierarchy>'''

    def tap(self, x, y):
        self.tap_calls.append((x, y))

    def keyevent(self, code):
        self.keyevents.append(code)
        if code == 67 and self.text:
            self.text = self.text[:-1]

    def set_text(self, value):
        self.input_calls.append(value)
        self.text = value


class SafeUiDriverTests(unittest.TestCase):
    def make_driver(self, text="sample_user"):
        adb = FakeAdb(text)
        return SafeUiDriver(adb, build_instagram_detector(), poll_interval=0), adb

    def test_set_text_noops_if_value_matches(self):
        driver, adb = self.make_driver()
        result = driver.set_text(USERNAME, "sample_user")
        self.assertEqual("noop", result.status)
        self.assertEqual([], adb.input_calls)
        self.assertEqual([], adb.tap_calls)

    def test_missing_selector_never_taps(self):
        driver, adb = self.make_driver()
        result = driver.tap(MISSING)
        self.assertEqual("not_found", result.status)
        self.assertEqual([], adb.tap_calls)

    def test_password_semantic_is_rejected(self):
        driver, adb = self.make_driver()
        with self.assertRaisesRegex(ValueError, "protected field automation is disabled"):
            driver.set_text(Selector(semantic="password", texts=("Password",)), "x")
        self.assertEqual([], adb.input_calls)

    def test_set_text_clears_then_inputs_once_and_verifies(self):
        driver, adb = self.make_driver("old")
        result = driver.set_text(USERNAME, "new_user")
        self.assertEqual("completed", result.status)
        self.assertEqual(["new_user"], adb.input_calls)
        self.assertEqual("new_user", adb.text)
        self.assertEqual((150, 50), adb.tap_calls[0])

    def test_tap_accepts_protected_screen_as_verified_transition(self):
        class TransitionAdb(FakeAdb):
            def __init__(self):
                super().__init__("")
                self.after_tap = False

            def dump_hierarchy(self):
                if self.after_tap:
                    return '<hierarchy><node text="Enter confirmation code" class="android.widget.TextView" enabled="true" bounds="[0,0][300,100]" /></hierarchy>'
                return '<hierarchy><node text="Continue" class="android.widget.Button" clickable="true" enabled="true" bounds="[0,0][300,100]" /></hierarchy>'

            def tap(self, x, y):
                super().tap(x, y)
                self.after_tap = True

        adb = TransitionAdb()
        driver = SafeUiDriver(adb, build_instagram_detector(), poll_interval=0)
        selector = Selector(semantic="continue", texts=("Continue",), require_clickable=True)
        result = driver.tap(selector, expected_screens=("OTP_REQUIRED",), timeout=0)
        self.assertEqual("completed", result.status)
        self.assertEqual("OTP_REQUIRED", result.after)


if __name__ == "__main__":
    unittest.main()
