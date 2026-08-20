import unittest

from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.threads.flow import ThreadsFlow


class ObservationDriver:
    def __init__(self, kind):
        self.kind = kind
        self.mutations = []

    def detect_screen(self):
        return DetectedScreen(self.kind, 0.99, (self.kind.lower(),), False)


class ThreadsActivationRegressionTests(unittest.TestCase):
    def test_profile_setup_checkpoint_resumes_instead_of_marking_threads_created(self):
        driver = ObservationDriver("THREADS_PROFILE_SETUP")

        result = ThreadsFlow(driver).observe_checkpoint()

        self.assertEqual("running", result.status)
        self.assertEqual("THREADS_PROFILE_SETUP", result.screen)
        self.assertEqual([], driver.mutations)

    def test_threads_home_checkpoint_is_completed(self):
        driver = ObservationDriver("THREADS_HOME")

        result = ThreadsFlow(driver).observe_checkpoint()

        self.assertEqual("completed", result.status)
        self.assertEqual("THREADS_HOME", result.screen)
        self.assertEqual([], driver.mutations)


if __name__ == "__main__":
    unittest.main()
