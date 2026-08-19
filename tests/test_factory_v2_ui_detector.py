import unittest

from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.hierarchy import UiBounds, UiNode, UiSnapshot
from core.factory_v2.ui_automation.instagram.screens import build_instagram_detector
from core.factory_v2.ui_automation.instagram.selectors import SIGN_UP
from core.factory_v2.ui_automation.threads.screens import build_threads_detector
from core.factory_v2.ui_automation.selectors import Selector


def node(*, text="", content_desc="", resource_id="", class_name="android.widget.TextView", clickable=False, enabled=True):
    return UiNode(text, content_desc, resource_id, class_name, clickable, enabled, UiBounds(0, 0, 100, 100))


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

    def test_text_contains_all_is_normalized_and_requires_every_term(self):
        snapshot = UiSnapshot("x", "y", (
            node(text="The username BAONGOCD   is not available."),
            node(text="Username is valid."),
        ))
        selector = Selector(
            semantic="username_unavailable",
            text_contains_all=("username", "is not available"),
        )
        self.assertEqual(
            "The username BAONGOCD   is not available.",
            selector.find(snapshot).text,
        )

    def test_selector_can_observe_disabled_marker_without_enabling_mutation(self):
        snapshot = UiSnapshot("x", "y", (
            node(content_desc="Input Username is valid.", enabled=False),
        ))
        selector = Selector(
            semantic="username_valid",
            content_descs=("Input Username is valid.",),
            require_enabled=False,
        )
        self.assertEqual("Input Username is valid.", selector.find(snapshot).content_desc)

    def test_initial_signup_selector_does_not_match_final_sign_up_submit(self):
        snapshot = UiSnapshot(
            "com.instagram.android",
            ".MainActivity",
            (node(text="Sign up", class_name="android.widget.Button", clickable=True),),
        )
        self.assertIsNone(SIGN_UP.find(snapshot))


class DetectorTests(unittest.TestCase):
    def test_otp_wins_over_continue_button(self):
        snapshot = UiSnapshot("com.instagram.android", ".MainActivity", (node(text="Enter confirmation code"), node(text="Continue", clickable=True)))
        detected = build_instagram_detector().detect(snapshot)
        self.assertEqual("OTP_REQUIRED", detected.kind)
        self.assertTrue(detected.protected)
        self.assertFalse(detected.automation_allowed)

    def test_contact_entry_is_safe_normal_screen(self):
        snapshot = UiSnapshot(
            "com.instagram.android",
            ".MainActivity",
            (
                node(
                    text="Mobile number or email",
                    resource_id="com.instagram.android:id/email_or_phone",
                    class_name="android.widget.EditText",
                    clickable=True,
                ),
                node(text="Continue", resource_id="com.instagram.android:id/continue_button", clickable=True),
            ),
        )
        detected = build_instagram_detector().detect(snapshot)
        self.assertEqual("IG_CONTACT_ENTRY", detected.kind)
        self.assertFalse(detected.protected)
        self.assertTrue(detected.automation_allowed)

    def test_actual_contact_verification_remains_protected(self):
        snapshot = UiSnapshot(
            "com.instagram.android",
            ".MainActivity",
            (node(text="Confirm your phone number"), node(text="Continue", clickable=True)),
        )
        detected = build_instagram_detector().detect(snapshot)
        self.assertEqual("EMAIL_OR_PHONE_VERIFICATION", detected.kind)
        self.assertTrue(detected.protected)
        self.assertFalse(detected.automation_allowed)

    def test_final_signup_submit_is_protected(self):
        snapshot = UiSnapshot(
            "com.instagram.android",
            ".MainActivity",
            (
                node(text="Create account", class_name="android.widget.Button", clickable=True),
                node(text="By signing up, you agree to our Terms"),
            ),
        )
        detected = build_instagram_detector().detect(snapshot)
        self.assertEqual("IG_FINAL_SIGNUP_SUBMIT", detected.kind)
        self.assertTrue(detected.protected)
        self.assertFalse(detected.automation_allowed)

    def test_supported_birthday_text_entry_is_automation_allowed(self):
        snapshot = UiSnapshot(
            "com.instagram.android",
            ".MainActivity",
            (
                node(
                    text="Birthday",
                    resource_id="com.instagram.android:id/birthday",
                    class_name="android.widget.EditText",
                    clickable=True,
                ),
                node(text="Next", resource_id="com.instagram.android:id/next_button", clickable=True),
            ),
        )
        detected = build_instagram_detector().detect(snapshot)
        self.assertEqual("IG_BIRTHDAY_ENTRY", detected.kind)
        self.assertTrue(detected.automation_allowed)

    def test_known_avatar_setup_is_automation_allowed(self):
        snapshot = UiSnapshot(
            "com.instagram.android",
            ".MainActivity",
            (node(text="Add profile photo", class_name="android.widget.Button", clickable=True),),
        )
        detected = build_instagram_detector().detect(snapshot)
        self.assertEqual("IG_AVATAR_SETUP", detected.kind)
        self.assertTrue(detected.automation_allowed)

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

    def test_username_entry_matches_android15_accessibility_tree(self):
        snapshot = UiSnapshot(
            "com.instagram.android",
            ".activity.MainTabActivity",
            (
                node(
                    text="Create a username",
                    content_desc="Create a username",
                    class_name="android.view.View",
                ),
                node(
                    text="dragon.3275826",
                    content_desc="Username,dragon.3275826",
                    class_name="android.widget.EditText",
                    clickable=True,
                ),
                node(
                    content_desc="Next",
                    class_name="android.widget.Button",
                    clickable=True,
                ),
            ),
        )

        detected = build_instagram_detector().detect(snapshot)

        self.assertEqual("IG_USERNAME_ENTRY", detected.kind)
        self.assertTrue(detected.automation_allowed)

    def test_username_unavailable_requires_create_username_context(self):
        snapshot = UiSnapshot(
            "com.instagram.android",
            ".activity.MainTabActivity",
            (
                node(text="Create a username", content_desc="Create a username", class_name="android.view.View"),
                node(
                    text="baongocd",
                    content_desc="Username,dragon.3275826",
                    class_name="android.widget.EditText",
                    clickable=True,
                ),
                node(text="The username baongocd is not available."),
            ),
        )
        detected = build_instagram_detector().detect(snapshot)
        self.assertEqual("IG_USERNAME_UNAVAILABLE", detected.kind)
        self.assertTrue(detected.automation_allowed)

    def test_username_valid_accepts_disabled_accessibility_marker_and_next(self):
        snapshot = UiSnapshot(
            "com.instagram.android",
            ".activity.MainTabActivity",
            (
                node(text="Create a username", content_desc="Create a username", class_name="android.view.View"),
                node(
                    text="baongocd483102",
                    content_desc="Username,baongocd483102",
                    class_name="android.widget.EditText",
                    clickable=True,
                ),
                node(
                    content_desc="Input Username is valid.",
                    class_name="android.widget.ImageView",
                    clickable=True,
                    enabled=False,
                ),
                node(content_desc="Next", class_name="android.widget.Button", clickable=True),
            ),
        )
        detected = build_instagram_detector().detect(snapshot)
        self.assertEqual("IG_USERNAME_VALID", detected.kind)
        self.assertTrue(detected.automation_allowed)

    def test_generic_not_available_text_does_not_become_username_unavailable(self):
        snapshot = UiSnapshot(
            "com.instagram.android",
            ".activity.MainTabActivity",
            (node(text="This feature is not available."),),
        )
        self.assertNotEqual(
            "IG_USERNAME_UNAVAILABLE",
            build_instagram_detector().detect(snapshot).kind,
        )

    def test_rate_limit_still_wins_over_username_context(self):
        snapshot = UiSnapshot(
            "com.instagram.android",
            ".activity.MainTabActivity",
            (
                node(text="Create a username", content_desc="Create a username", class_name="android.view.View"),
                node(text="baongocd", class_name="android.widget.EditText", clickable=True),
                node(text="The username baongocd is not available."),
                node(text="Try again later"),
            ),
        )
        self.assertEqual("RATE_LIMITED", build_instagram_detector().detect(snapshot).kind)

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
