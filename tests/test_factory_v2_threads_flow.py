import unittest
from types import SimpleNamespace

from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.driver import ActionResult
from core.factory_v2.ui_automation.threads.flow import ThreadsFlow


class FakeDriver:
    def __init__(self, screens, available=("display_name", "bio", "continue", "join_threads")):
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


class ThreadsFlowTests(unittest.TestCase):
    def setUp(self):
        self.profile = {"username": "sample_user", "display_name": "Sample User", "bio": "Sample bio", "password": "must-not-be-used"}

    def test_security_challenge_stops_before_mutation(self):
        driver = FakeDriver([DetectedScreen("SECURITY_CHALLENGE", 0.82, ("security",), True)])
        result = ThreadsFlow(driver).run(self.profile)
        self.assertEqual("waiting_human", result.status)
        self.assertEqual([], driver.mutations)

    def test_profile_setup_sets_only_display_name_and_bio(self):
        driver = FakeDriver([DetectedScreen("THREADS_PROFILE_SETUP", 0.96, ("profile",), False)])
        result = ThreadsFlow(driver).run(self.profile)
        self.assertEqual("running", result.status)
        self.assertEqual([("display_name", "Sample User"), ("bio", "Sample bio")], driver.set_values)

    def test_onboarding_taps_known_join_control(self):
        driver = FakeDriver([DetectedScreen("THREADS_ONBOARDING", 0.94, ("join",), False)])
        result = ThreadsFlow(driver).run(self.profile)
        self.assertEqual("running", result.status)
        self.assertEqual([("tap", "join_threads")], driver.mutations)

    def test_unknown_never_mutates(self):
        unknown = DetectedScreen("UNKNOWN", 0.0, (), False)
        driver = FakeDriver([unknown, unknown, unknown])
        result = ThreadsFlow(driver).run(self.profile)
        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual([], driver.mutations)

    def test_postcheck_is_completed(self):
        driver = FakeDriver([DetectedScreen("THREADS_POSTCHECK_OK", 0.99, ("home", "profile"), False)])
        self.assertEqual("completed", ThreadsFlow(driver).run(self.profile).status)

    def test_checkpoint_resume_is_observation_only(self):
        driver = FakeDriver([DetectedScreen("THREADS_HOME", 0.96, ("home",), False)])
        result = ThreadsFlow(driver).observe_checkpoint()
        self.assertEqual("completed", result.status)
        self.assertEqual([], driver.mutations)


if __name__ == "__main__":
    unittest.main()
