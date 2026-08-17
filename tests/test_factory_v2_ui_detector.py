import unittest

from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.hierarchy import UiBounds, UiNode, UiSnapshot
from core.factory_v2.ui_automation.instagram.screens import build_instagram_detector
from core.factory_v2.ui_automation.threads.screens import build_threads_detector
from core.factory_v2.ui_automation.selectors import Selector


def node(*, text="", content_desc="", resource_id="", class_name="android.widget.TextView", clickable=False):
    return UiNode(text, content_desc, resource_id, class_name, clickable, True, UiBounds(0, 0, 100, 100))


class SelectorTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = UiSnapshot("com.instagram.android", ".MainActivity", (
            node(text="Continue", resource_id="other", clickable=True),
            node(text="Next", resource_id="com.instagram.android:id/next_button", clickable=True),
        ))

    def test_resource_id_has_priority(self):
        selector = Selector(semantic="continue", resource_ids=("com.instagram.android:id/next_button",), texts=("Next", "Continue", "Tiếp tục", "Tiếp"), require_clickable=True)
        self.assertEqual("com.instagram.android:id/next_button", selector.find(self.snapshot).resource_id)

    def test_normalized_alias_matches_case_and_whitespace(self):
        snapshot = UiSnapshot("x", "y", (node(text="  tiếp   TỤC  ", clickable=True),))
        selector = Selector(semantic="continue", texts=("Tiếp tục",), require_clickable=True)
        self.assertEqual("  tiếp   TỤC  ", selector.find(snapshot).text)


class DetectorTests(unittest.TestCase):
    def test_otp_wins_over_continue_button(self):
        snapshot = UiSnapshot("com.instagram.android", ".MainActivity", (node(text="Enter confirmation code"), node(text="Continue", clickable=True)))
        detected = build_instagram_detector().detect(snapshot)
        self.assertEqual("OTP_REQUIRED", detected.kind)
        self.assertTrue(detected.protected)
        self.assertFalse(detected.automation_allowed)

    def test_unknown_never_allows_automation(self):
        detected = build_instagram_detector().detect(UiSnapshot("com.instagram.android", ".MainActivity", (node(text="Unexpected screen"),)))
        self.assertEqual("UNKNOWN", detected.kind)
        self.assertFalse(detected.automation_allowed)

    def test_instagram_profile_is_automation_allowed(self):
        snapshot = UiSnapshot("com.instagram.android", ".MainActivity", (
            node(resource_id="com.instagram.android:id/username", class_name="android.widget.EditText"),
            node(resource_id="com.instagram.android:id/full_name", class_name="android.widget.EditText"),
            node(text="Next", clickable=True),
        ))
        detected = build_instagram_detector().detect(snapshot)
        self.assertEqual("IG_PROFILE_SETUP", detected.kind)
        self.assertTrue(detected.automation_allowed)

    def test_threads_postcheck_requires_home_and_profile(self):
        snapshot = UiSnapshot("com.instagram.barcelona", ".MainActivity", (node(content_desc="Home", clickable=True), node(content_desc="Profile", clickable=True)))
        detected = build_threads_detector().detect(snapshot)
        self.assertEqual("THREADS_POSTCHECK_OK", detected.kind)
        self.assertTrue(detected.automation_allowed)

    def test_detected_screen_threshold(self):
        self.assertFalse(DetectedScreen("IG_PROFILE_SETUP", 0.89, (), False).automation_allowed)
        self.assertTrue(DetectedScreen("IG_PROFILE_SETUP", 0.90, (), False).automation_allowed)


if __name__ == "__main__":
    unittest.main()
