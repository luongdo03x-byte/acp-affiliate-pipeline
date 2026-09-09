import unittest
from types import SimpleNamespace

from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.driver import ActionResult
from core.factory_v2.ui_automation.threads.tester_flow import ThreadsTesterFlow


class FakeDriver:
    def __init__(self, screens, available=(), *, tap_statuses=None):
        self.screens = list(screens)
        self.last = self.screens[-1] if self.screens else DetectedScreen("UNKNOWN", 0, ())
        self.available = set(available)
        self.mutations = []
        self.tap_statuses = list(tap_statuses or [])

    def detect_screen(self):
        if self.screens:
            self.last = self.screens.pop(0)
        return self.last

    def find(self, selector):
        return SimpleNamespace() if selector.semantic in self.available else None

    def tap(self, selector, **kwargs):
        self.mutations.append(("tap", selector.semantic))
        status = self.tap_statuses.pop(0) if self.tap_statuses else "completed"
        return ActionResult(status)

    def open_package(self, package):
        self.mutations.append(("open_package", package))


class ThreadsTesterFlowTests(unittest.TestCase):
    def test_protected_screen_stops_before_any_tap(self):
        driver = FakeDriver(
            [DetectedScreen("OTP_REQUIRED", 0.9, ("otp",), True)],
            available=("website_permissions", "tester_invites", "tester_accept"),
        )

        result = ThreadsTesterFlow(driver).accept_invite()

        self.assertEqual("waiting_human", result.status)
        self.assertEqual("HUMAN_VERIFICATION_REQUIRED", result.reason)
        self.assertEqual([], driver.mutations)

    def test_accepts_invitation_from_settings(self):
        driver = FakeDriver(
            [
                DetectedScreen("THREADS_SETTINGS", 0.96, ("settings",)),
                DetectedScreen("THREADS_WEBSITE_PERMISSIONS", 0.96, ("website_permissions",)),
                DetectedScreen("THREADS_TESTER_INVITE_LIST", 0.96, ("tester_invites",)),
                DetectedScreen("THREADS_TESTER_INVITE_CONFIRM", 0.96, ("tester_accept",)),
            ],
            available=("website_permissions", "tester_invites", "tester_accept"),
        )

        result = ThreadsTesterFlow(driver).accept_invite()

        self.assertEqual("completed", result.status)
        self.assertEqual("THREADS_TESTER_INVITE_CONFIRM", result.screen)
        self.assertIn(("tap", "tester_accept"), driver.mutations)

    def test_empty_invite_list_reports_missing_invite(self):
        driver = FakeDriver(
            [DetectedScreen("THREADS_TESTER_INVITE_LIST", 0.96, ("tester_invites",))],
            available=("website_permissions",),
        )

        result = ThreadsTesterFlow(driver).accept_invite()

        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("NO_TESTER_INVITE", result.reason)
        self.assertEqual([], driver.mutations)

    def test_unknown_screen_stops_without_tapping(self):
        driver = FakeDriver(
            [DetectedScreen("UNKNOWN", 0.0, ())],
            available=("website_permissions", "tester_invites", "tester_accept"),
        )

        result = ThreadsTesterFlow(driver).accept_invite()

        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("UI_CHANGED", result.reason)
        self.assertEqual([], driver.mutations)

    def test_failed_tap_stops_the_flow(self):
        driver = FakeDriver(
            [
                DetectedScreen("THREADS_WEBSITE_PERMISSIONS", 0.96, ("website_permissions",)),
                DetectedScreen("THREADS_WEBSITE_PERMISSIONS", 0.96, ("website_permissions",)),
            ],
            available=("website_permissions", "tester_invites"),
            tap_statuses=["postcondition_failed"],
        )

        result = ThreadsTesterFlow(driver).accept_invite()

        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("UI_CHANGED", result.reason)
        self.assertEqual([("tap", "tester_invites")], driver.mutations)


if __name__ == "__main__":
    unittest.main()
