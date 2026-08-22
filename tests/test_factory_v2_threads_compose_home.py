import unittest

from core.factory_v2.ui_automation.hierarchy import UiBounds, UiNode, UiSnapshot
from core.factory_v2.ui_automation.threads.flow import ThreadsFlow
from core.factory_v2.ui_automation.threads.screens import build_threads_detector


PACKAGE = "com.instagram.barcelona"


def node(*, text="", resource_id="", clickable=False, enabled=True):
    return UiNode(
        text,
        "",
        resource_id,
        "android.widget.TextView",
        clickable,
        enabled,
        UiBounds(0, 0, 100, 100),
    )


def compose_home(username):
    return UiSnapshot(
        PACKAGE,
        ".mainactivity.BarcelonaActivity",
        (
            node(resource_id="tabs_screen"),
            node(resource_id="MainFeedScreen"),
            node(text=username, resource_id="ig_text"),
            node(text="What's new?", resource_id="ig_text"),
            node(resource_id="FeedPostRow"),
        ),
    )


class SnapshotDriver:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.detector = build_threads_detector()
        self.mutations = []

    def detect_screen(self):
        return self.detector.detect(self.snapshot)

    def find(self, selector):
        return selector.find(self.snapshot)

    def open_package(self, package):
        self.mutations.append(("open_package", package))


class ThreadsComposeHomeTests(unittest.TestCase):
    def test_main_feed_screen_is_detected_as_threads_home(self):
        detected = build_threads_detector().detect(compose_home("myduyenn681999"))

        self.assertEqual("THREADS_HOME", detected.kind)
        self.assertTrue(detected.automation_allowed)

    def test_normal_flow_home_rejects_different_account(self):
        driver = SnapshotDriver(compose_home("baongocd806415"))

        result = ThreadsFlow(driver).run({"username": "myduyenn681999"})

        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("ACCOUNT_MISMATCH", result.reason)
        self.assertEqual([], driver.mutations)

    def test_checkpoint_home_completes_when_expected_username_is_visible(self):
        driver = SnapshotDriver(compose_home("myduyenn681999"))

        result = ThreadsFlow(driver).observe_checkpoint({"username": "myduyenn681999"})

        self.assertEqual("completed", result.status)
        self.assertEqual("THREADS_HOME", result.screen)
        self.assertEqual([], driver.mutations)

    def test_checkpoint_home_rejects_different_account(self):
        driver = SnapshotDriver(compose_home("baongocd806415"))

        result = ThreadsFlow(driver).observe_checkpoint({"username": "myduyenn681999"})

        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("ACCOUNT_MISMATCH", result.reason)
        self.assertEqual([], driver.mutations)


if __name__ == "__main__":
    unittest.main()
