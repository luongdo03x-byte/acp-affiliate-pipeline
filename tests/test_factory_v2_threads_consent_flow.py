import unittest
from types import SimpleNamespace

from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.driver import ActionResult
from core.factory_v2.ui_automation.threads.consent_flow import ThreadsConsentFlow


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


class ThreadsConsentFlowTests(unittest.TestCase):
    def test_taps_allow_once_on_consent_screen(self):
        driver = FakeDriver(
            [DetectedScreen("THREADS_OAUTH_CONSENT", 0.95, ("oauth_consent",))],
            available=("oauth_allow",),
        )

        result = ThreadsConsentFlow(driver).confirm()

        self.assertEqual("completed", result.status)
        self.assertEqual([("tap", "oauth_allow")], driver.mutations)

    def test_consent_appearing_late_is_still_confirmed(self):
        driver = FakeDriver(
            [
                DetectedScreen("UNKNOWN", 0.0, ()),
                DetectedScreen("THREADS_OAUTH_CONSENT", 0.95, ("oauth_consent",)),
            ],
            available=("oauth_allow",),
        )

        result = ThreadsConsentFlow(driver).confirm()

        self.assertEqual("completed", result.status)
        self.assertEqual([("tap", "oauth_allow")], driver.mutations)

    def test_security_consent_is_never_tapped(self):
        driver = FakeDriver(
            [DetectedScreen("CONSENT_WITH_SECURITY_IMPACT", 0.9, ("security_consent",), True)],
            available=("oauth_allow",),
        )

        result = ThreadsConsentFlow(driver).confirm()

        self.assertEqual("waiting_human", result.status)
        self.assertEqual("HUMAN_VERIFICATION_REQUIRED", result.reason)
        self.assertEqual([], driver.mutations)

    def test_unrecognized_screen_falls_back_without_tapping(self):
        driver = FakeDriver(
            [DetectedScreen("UNKNOWN", 0.0, ())] * 3,
            available=("oauth_allow",),
        )

        result = ThreadsConsentFlow(driver).confirm()

        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("CONSENT_NOT_DETECTED", result.reason)
        self.assertEqual([], driver.mutations)

    def test_missing_allow_control_is_not_tapped(self):
        driver = FakeDriver(
            [DetectedScreen("THREADS_OAUTH_CONSENT", 0.95, ("oauth_consent",))],
            available=(),
        )

        result = ThreadsConsentFlow(driver).confirm()

        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("CONSENT_NOT_DETECTED", result.reason)
        self.assertEqual([], driver.mutations)

    def test_failed_tap_reports_instead_of_retrying(self):
        driver = FakeDriver(
            [DetectedScreen("THREADS_OAUTH_CONSENT", 0.95, ("oauth_consent",))],
            available=("oauth_allow",),
            tap_statuses=["not_found"],
        )

        result = ThreadsConsentFlow(driver).confirm()

        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("CONSENT_TAP_FAILED", result.reason)
        self.assertEqual([("tap", "oauth_allow")], driver.mutations)


if __name__ == "__main__":
    unittest.main()
