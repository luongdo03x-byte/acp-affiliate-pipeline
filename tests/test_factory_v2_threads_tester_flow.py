import unittest
from types import SimpleNamespace

from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.driver import ActionResult
from core.factory_v2.ui_automation.threads.tester_flow import ThreadsTesterFlow


class FakeDriver:
    """Available selectors are named by semantic, matching the real UI tree."""

    def __init__(self, screen, available=(), *, tap_statuses=None):
        self.screen = screen
        self.available = set(available)
        self.mutations = []
        self.tap_statuses = list(tap_statuses or [])

    def detect_screen(self):
        return self.screen

    def find(self, selector):
        return SimpleNamespace() if selector.semantic in self.available else None

    def tap(self, selector, **kwargs):
        self.mutations.append(("tap", selector.semantic))
        status = self.tap_statuses.pop(0) if self.tap_statuses else "completed"
        return ActionResult(status)


UNKNOWN = DetectedScreen("UNKNOWN", 0.0, ())
HOME = DetectedScreen("THREADS_HOME", 0.96, ("home",))


class ThreadsTesterFlowTests(unittest.TestCase):
    def test_protected_screen_stops_before_any_tap(self):
        driver = FakeDriver(
            DetectedScreen("OTP_REQUIRED", 0.9, ("otp",), True),
            available=("tester_accept", "profile"),
        )

        result = ThreadsTesterFlow(driver).accept_invite()

        self.assertEqual("waiting_human", result.status)
        self.assertEqual("HUMAN_VERIFICATION_REQUIRED", result.reason)
        self.assertEqual([], driver.mutations)

    def test_feed_opens_the_profile_first(self):
        driver = FakeDriver(HOME, available=("profile",))

        result = ThreadsTesterFlow(driver).accept_invite()

        self.assertEqual("running", result.status)
        self.assertEqual("THREADS_HOME", result.screen)
        self.assertEqual([("tap", "profile")], driver.mutations)

    def test_profile_with_navigation_bar_taps_the_settings_icon(self):
        # The profile page still shows the bottom tabs. Checking the profile tab
        # first would tap the tab it already stands on and loop forever.
        driver = FakeDriver(HOME, available=("profile", "profile_settings"))

        result = ThreadsTesterFlow(driver).accept_invite()

        self.assertEqual("THREADS_PROFILE", result.screen)
        self.assertEqual([("tap", "profile_settings")], driver.mutations)

    def test_settings_opens_website_permissions(self):
        driver = FakeDriver(UNKNOWN, available=("website_permissions",))

        result = ThreadsTesterFlow(driver).accept_invite()

        self.assertEqual("running", result.status)
        self.assertEqual("THREADS_SETTINGS", result.screen)
        self.assertEqual([("tap", "website_permissions")], driver.mutations)

    def test_other_tab_switches_to_invites(self):
        driver = FakeDriver(
            UNKNOWN, available=("apps_and_websites", "tester_invites")
        )

        result = ThreadsTesterFlow(driver).accept_invite()

        self.assertEqual("running", result.status)
        self.assertEqual("THREADS_APPS_AND_WEBSITES", result.screen)
        self.assertEqual([("tap", "tester_invites")], driver.mutations)

    def test_accept_tap_completes_immediately(self):
        driver = FakeDriver(
            UNKNOWN,
            available=("apps_and_websites", "invites_hint", "tester_accept"),
        )

        result = ThreadsTesterFlow(driver).accept_invite()

        self.assertEqual("completed", result.status)
        self.assertEqual("THREADS_TESTER_INVITE_LIST", result.screen)
        self.assertEqual([("tap", "tester_accept")], driver.mutations)

    def test_empty_invite_tab_reports_missing_invite(self):
        driver = FakeDriver(
            UNKNOWN, available=("apps_and_websites", "invites_hint", "tester_invites")
        )

        result = ThreadsTesterFlow(driver).accept_invite()

        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("NO_TESTER_INVITE", result.reason)
        self.assertEqual([], driver.mutations)

    def test_failed_accept_tap_is_reported(self):
        driver = FakeDriver(
            UNKNOWN,
            available=("apps_and_websites", "invites_hint", "tester_accept"),
            tap_statuses=["not_found"],
        )

        result = ThreadsTesterFlow(driver).accept_invite()

        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("ACCEPT_TAP_FAILED", result.reason)

    def test_unrecognized_screen_stops_without_tapping(self):
        driver = FakeDriver(UNKNOWN, available=())

        result = ThreadsTesterFlow(driver).accept_invite()

        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("UI_CHANGED", result.reason)
        self.assertEqual([], driver.mutations)


if __name__ == "__main__":
    unittest.main()
