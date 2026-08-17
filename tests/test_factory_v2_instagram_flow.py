import unittest
from types import SimpleNamespace

from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.driver import ActionResult
from core.factory_v2.ui_automation.instagram.flow import InstagramFlow


class FakeDriver:
    def __init__(self, screens, available=("username", "display_name", "bio", "continue", "sign_up")):
        self.screens = list(screens)
        self.last = self.screens[-1] if self.screens else DetectedScreen("UNKNOWN", 0, ())
        self.available = set(available)
        self.mutations = []
        self.set_values = []
        self.opened = []

    def detect_screen(self):
        if self.screens:
            self.last = self.screens.pop(0)
        return self.last

    def find(self, selector):
        return SimpleNamespace() if selector.semantic in self.available else None

    def set_text(self, selector, value):
        self.mutations.append(("set_text", selector.semantic))
        self.set_values.append((selector.semantic, value))
        return ActionResult("completed")

    def tap(self, selector, **kwargs):
        self.mutations.append(("tap", selector.semantic))
        return ActionResult("completed")

    def open_package(self, package):
        self.mutations.append(("open_package", package))
        self.opened.append(package)


class InstagramFlowTests(unittest.TestCase):
    def setUp(self):
        self.profile = {"username": "sample_user", "display_name": "Sample User", "bio": "Sample bio", "password": "must-not-be-used", "otp": "000000"}

    def test_otp_stops_before_mutation(self):
        driver = FakeDriver([DetectedScreen("OTP_REQUIRED", 0.82, ("verify-marker",), True)])
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("waiting_human", result.status)
        self.assertEqual("OTP_REQUIRED", result.screen)
        self.assertEqual([], driver.mutations)

    def test_profile_setup_sets_only_approved_fields(self):
        driver = FakeDriver([DetectedScreen("IG_PROFILE_SETUP", 0.96, ("profile",), False)])
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("running", result.status)
        self.assertEqual([("username", "sample_user"), ("display_name", "Sample User"), ("bio", "Sample bio")], driver.set_values)
        self.assertNotIn(("set_text", "password"), driver.mutations)

    def test_unknown_retries_observation_without_mutation(self):
        unknown = DetectedScreen("UNKNOWN", 0.0, (), False)
        driver = FakeDriver([unknown, unknown, unknown])
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("UI_CHANGED", result.reason)
        self.assertEqual([], driver.mutations)

    def test_rate_limit_never_rapid_retries(self):
        driver = FakeDriver([DetectedScreen("RATE_LIMITED", 0.95, ("rate",), False)])
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("retry_pending", result.status)
        self.assertEqual("RATE_LIMITED", result.reason)
        self.assertEqual([], driver.mutations)

    def test_checkpoint_resume_requires_known_successor_without_mutation(self):
        driver = FakeDriver([DetectedScreen("IG_HOME", 0.96, ("home",), False)])
        result = InstagramFlow(driver).observe_checkpoint()
        self.assertEqual("completed", result.status)
        self.assertEqual([], driver.mutations)

    def test_checkpoint_does_not_resume_from_unknown(self):
        unknown = DetectedScreen("UNKNOWN", 0.0, (), False)
        driver = FakeDriver([unknown, unknown, unknown])
        result = InstagramFlow(driver).observe_checkpoint()
        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual([], driver.mutations)

    def test_app_crash_reopens_once_then_rechecks(self):
        driver = FakeDriver([DetectedScreen("APP_CRASH", 0.99, ("crash",), False), DetectedScreen("IG_HOME", 0.96, ("home",), False)])
        result = InstagramFlow(driver).run(self.profile)
        self.assertEqual("completed", result.status)
        self.assertEqual(["com.instagram.android"], driver.opened)


if __name__ == "__main__":
    unittest.main()
