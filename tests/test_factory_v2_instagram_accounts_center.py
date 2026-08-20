import unittest
from types import SimpleNamespace

from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.driver import ActionResult
from core.factory_v2.ui_automation.hierarchy import UiBounds, UiNode, UiSnapshot
from core.factory_v2.ui_automation.instagram.flow import InstagramFlow
from core.factory_v2.ui_automation.instagram.screens import build_instagram_detector


def node(
    *,
    text="",
    content_desc="",
    resource_id="",
    class_name="android.widget.TextView",
    clickable=False,
    enabled=True,
):
    return UiNode(
        text,
        content_desc,
        resource_id,
        class_name,
        clickable,
        enabled,
        UiBounds(0, 0, 100, 100),
    )


class FakeDriver:
    def __init__(self, *, available=(), tap_status="completed"):
        self.available = set(available)
        self.tap_status = tap_status
        self.mutations = []

    def detect_screen(self):
        return DetectedScreen(
            "IG_ACCOUNTS_CENTER_CONSENT",
            0.99,
            ("accounts_center_title", "accounts_center_allow"),
            False,
        )

    def find(self, selector):
        if selector.semantic not in self.available:
            return None
        return SimpleNamespace(text="", content_desc="Allow and continue")

    def tap(self, selector, **kwargs):
        self.mutations.append(("tap", selector.semantic))
        return ActionResult(self.tap_status)


class InstagramAccountsCenterTests(unittest.TestCase):
    def test_accounts_center_consent_matches_android15_accessibility_tree(self):
        snapshot = UiSnapshot(
            "com.instagram.android",
            ".activity.MainTabActivity",
            (
                node(
                    text=(
                        "To create a new Instagram account in this Accounts Center, "
                        "allow the following"
                    ),
                    content_desc=(
                        "To create a new Instagram account in this Accounts Center, "
                        "allow the following"
                    ),
                    class_name="android.view.View",
                ),
                node(
                    content_desc="Accounts Center, lann.ie06, lann.ie06",
                    class_name="android.view.ViewGroup",
                    clickable=True,
                ),
                node(
                    content_desc="Allow and continue",
                    class_name="android.widget.Button",
                    clickable=True,
                ),
                node(
                    content_desc="Use mobile number or email",
                    class_name="android.widget.Button",
                    clickable=True,
                ),
            ),
        )

        detected = build_instagram_detector().detect(snapshot)

        self.assertEqual("IG_ACCOUNTS_CENTER_CONSENT", detected.kind)
        self.assertFalse(detected.protected)
        self.assertTrue(detected.automation_allowed)

    def test_allow_button_without_accounts_center_context_does_not_match(self):
        snapshot = UiSnapshot(
            "com.instagram.android",
            ".activity.MainTabActivity",
            (
                node(
                    content_desc="Allow and continue",
                    class_name="android.widget.Button",
                    clickable=True,
                ),
            ),
        )

        detected = build_instagram_detector().detect(snapshot)

        self.assertNotEqual("IG_ACCOUNTS_CENTER_CONSENT", detected.kind)

    def test_accounts_center_consent_taps_only_allow_and_continue(self):
        driver = FakeDriver(available=("accounts_center_allow",))

        result = InstagramFlow(driver).run({"username": "sample_user"})

        self.assertEqual("running", result.status)
        self.assertEqual("IG_ACCOUNTS_CENTER_CONSENT", result.screen)
        self.assertEqual("IG_ACCOUNTS_CENTER_CONSENT", result.last_safe_step)
        self.assertEqual([("tap", "accounts_center_allow")], driver.mutations)

    def test_accounts_center_missing_allow_button_fails_closed(self):
        driver = FakeDriver()

        result = InstagramFlow(driver).run({"username": "sample_user"})

        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("UI_CHANGED", result.reason)
        self.assertEqual([], driver.mutations)


if __name__ == "__main__":
    unittest.main()
