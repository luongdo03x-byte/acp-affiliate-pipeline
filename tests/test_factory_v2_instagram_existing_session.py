import unittest
from types import SimpleNamespace

from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.driver import ActionResult
from core.factory_v2.ui_automation.hierarchy import UiBounds, UiNode, UiSnapshot
from core.factory_v2.ui_automation.instagram.flow import InstagramFlow
from core.factory_v2.ui_automation.instagram.screens import build_instagram_detector


class FakeDriver:
    def __init__(self, screens, *, available=("profile", "account_switcher", "add_account", "sign_up")):
        self.screens = list(screens)
        self.last = self.screens[-1] if self.screens else DetectedScreen("UNKNOWN", 0.0, ())
        self.available = set(available)
        self.mutations = []

    def detect_screen(self):
        if self.screens:
            self.last = self.screens.pop(0)
        return self.last

    def find(self, selector):
        return SimpleNamespace() if selector.semantic in self.available else None

    def tap(self, selector, **kwargs):
        self.mutations.append(("tap", selector.semantic))
        return ActionResult("completed")

    def set_text(self, selector, value):
        raise AssertionError("existing-session navigation must not type text")

    def open_package(self, package):
        self.mutations.append(("open_package", package))


def node(*, text="", content_desc="", resource_id="", clickable=False):
    return UiNode(
        text,
        content_desc,
        resource_id,
        "android.widget.TextView",
        clickable,
        True,
        UiBounds(0, 0, 100, 100),
    )


class InstagramExistingSessionFlowTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "username": "new_account",
            "signup_contact_type": "email",
            "signup_contact": "new@example.test",
            "birth_date": "2000-05-20",
        }

    def test_logged_in_postcheck_screen_opens_profile_instead_of_completing(self):
        driver = FakeDriver([
            DetectedScreen("IG_POSTCHECK_OK", 0.99, ("home", "profile"), False)
        ])

        result = InstagramFlow(driver).run(self.profile)

        self.assertEqual("running", result.status)
        self.assertEqual([("tap", "profile")], driver.mutations)
        self.assertEqual("IG_EXISTING_SESSION", result.last_safe_step)

    def test_existing_profile_opens_account_switcher(self):
        driver = FakeDriver([
            DetectedScreen("IG_EXISTING_PROFILE", 0.97, ("profile", "account_switcher"), False)
        ])

        result = InstagramFlow(driver).run(self.profile)

        self.assertEqual("running", result.status)
        self.assertEqual([("tap", "account_switcher")], driver.mutations)
        self.assertEqual("IG_EXISTING_PROFILE", result.last_safe_step)

    def test_account_switcher_taps_add_account(self):
        driver = FakeDriver([
            DetectedScreen("IG_ACCOUNT_SWITCHER", 0.98, ("add_account",), False)
        ])

        result = InstagramFlow(driver).run(self.profile)

        self.assertEqual("running", result.status)
        self.assertEqual([("tap", "add_account")], driver.mutations)
        self.assertEqual("IG_ACCOUNT_SWITCHER", result.last_safe_step)

    def test_existing_profile_without_known_switcher_fails_closed(self):
        driver = FakeDriver(
            [DetectedScreen("IG_EXISTING_PROFILE", 0.97, ("profile",), False)],
            available=("profile",),
        )

        result = InstagramFlow(driver).run(self.profile)

        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("UI_CHANGED", result.reason)
        self.assertEqual([], driver.mutations)

    def test_postcheck_observation_still_completes_without_add_account_navigation(self):
        driver = FakeDriver([
            DetectedScreen("IG_POSTCHECK_OK", 0.99, ("home", "profile"), False)
        ])

        result = InstagramFlow(driver).observe_checkpoint()

        self.assertEqual("completed", result.status)
        self.assertEqual([], driver.mutations)


class InstagramExistingSessionDetectorTests(unittest.TestCase):
    def test_profile_with_switch_accounts_control_is_existing_profile(self):
        snapshot = UiSnapshot(
            "com.instagram.android",
            ".MainActivity",
            (
                node(content_desc="Profile", resource_id="com.instagram.android:id/profile_tab", clickable=True),
                node(content_desc="Switch accounts", clickable=True),
            ),
        )

        detected = build_instagram_detector().detect(snapshot)

        self.assertEqual("IG_EXISTING_PROFILE", detected.kind)
        self.assertTrue(detected.automation_allowed)

    def test_add_instagram_account_sheet_is_account_switcher(self):
        snapshot = UiSnapshot(
            "com.instagram.android",
            ".MainActivity",
            (node(text="Add Instagram account", clickable=True),),
        )

        detected = build_instagram_detector().detect(snapshot)

        self.assertEqual("IG_ACCOUNT_SWITCHER", detected.kind)
        self.assertTrue(detected.automation_allowed)


if __name__ == "__main__":
    unittest.main()
