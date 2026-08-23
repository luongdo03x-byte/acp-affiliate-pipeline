import unittest

from core.factory_v2.ui_automation.driver import ActionResult
from core.factory_v2.ui_automation.hierarchy import UiBounds, UiNode, UiSnapshot
from core.factory_v2.ui_automation.instagram.flow import InstagramFlow, _AFTER_USERNAME
from core.factory_v2.ui_automation.instagram.screens import build_instagram_detector
from core.factory_v2.ui_automation.instagram.selectors import CHOOSE_FROM_LIBRARY, FINAL_SIGNUP_SUBMIT
from core.factory_v2.ui_automation.threads.flow import ThreadsFlow
from core.factory_v2.ui_automation.threads.screens import build_threads_detector
from tests.test_factory_v2_runtime_remote import (
    FakeGateway,
    FakeRepo,
    FakeService,
    TestRuntime,
    account,
    job,
)


INSTAGRAM = "com.instagram.android"
THREADS = "com.instagram.barcelona"
PERMISSION = "com.google.android.permissioncontroller"


def node(
    *,
    text="",
    content_desc="",
    resource_id="",
    clickable=False,
    enabled=True,
    class_name="android.widget.TextView",
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


class SnapshotDriver:
    def __init__(self, snapshot, detector):
        self._snapshot = snapshot
        self.detector = detector
        self.mutations = []

    def snapshot(self):
        return self._snapshot

    def detect_screen(self):
        return self.detector.detect(self._snapshot)

    def find(self, selector):
        return selector.find(self._snapshot)

    def tap(self, selector, **kwargs):
        self.mutations.append(("tap", selector.semantic, tuple(selector.texts)))
        return ActionResult("completed")

    def open_package(self, package):
        self.mutations.append(("open_package", package, ()))


class PilotUiRegressionTests(unittest.TestCase):
    def test_instagram_gallery_selector_accepts_current_gallery_label(self):
        snapshot = UiSnapshot(
            INSTAGRAM,
            ".activity.MainTabActivity",
            (node(text="Choose from Gallery", clickable=True),),
        )

        self.assertIsNotNone(CHOOSE_FROM_LIBRARY.find(snapshot))

    def test_instagram_navigation_tip_preempts_home_and_taps_got_it(self):
        snapshot = UiSnapshot(
            INSTAGRAM,
            ".activity.MainTabActivity",
            (
                node(resource_id="com.instagram.android:id/feed_tab"),
                node(text="Swipe to easily access Reels and messages"),
                node(text="Got it", clickable=True),
            ),
        )
        driver = SnapshotDriver(snapshot, build_instagram_detector())

        result = InstagramFlow(driver).run({"username": "sample_user"})

        self.assertEqual("running", result.status)
        self.assertEqual("IG_NAV_TIP", result.screen)
        self.assertEqual("nav_tip_got_it", driver.mutations[0][1])

    def test_instagram_username_can_transition_to_accounts_center_consent(self):
        self.assertIn("IG_ACCOUNTS_CENTER_CONSENT", _AFTER_USERNAME)

    def test_instagram_final_terms_auto_accepts_exact_i_agree(self):
        snapshot = UiSnapshot(
            INSTAGRAM,
            ".activity.MainTabActivity",
            (
                node(text="To sign up, read and agree to our terms and policies"),
                node(text="I agree", content_desc="I agree", clickable=True),
            ),
        )
        driver = SnapshotDriver(snapshot, build_instagram_detector())

        result = InstagramFlow(driver).run({"username": "sample_user"})

        self.assertEqual("running", result.status)
        self.assertEqual("IG_FINAL_SIGNUP_SUBMIT", result.screen)
        self.assertEqual("final_signup_submit", driver.mutations[0][1])
        self.assertEqual(("I agree",), driver.mutations[0][2])

    def test_instagram_final_submit_selector_does_not_match_generic_sign_up(self):
        snapshot = UiSnapshot(
            INSTAGRAM,
            ".activity.MainTabActivity",
            (node(text="Sign up", clickable=True),),
        )

        self.assertIsNone(FINAL_SIGNUP_SUBMIT.find(snapshot))

    def test_threads_account_picker_selects_unique_generated_username(self):
        snapshot = UiSnapshot(
            THREADS,
            ".mainactivity.BarcelonaActivity",
            (
                node(text="Log into Threads"),
                node(text="myduyenn681999"),
                node(text="baongocd806415"),
                node(text="sample_user253132"),
            ),
        )
        driver = SnapshotDriver(snapshot, build_threads_detector())

        result = ThreadsFlow(driver).run({"username": "sample_user"})

        self.assertEqual("running", result.status)
        self.assertEqual("THREADS_ACCOUNT_PICKER", result.screen)
        self.assertEqual({"username": "sample_user253132"}, result.profile_updates)
        self.assertEqual("threads_account_option", driver.mutations[0][1])
        self.assertEqual(("sample_user253132",), driver.mutations[0][2])

    def test_threads_account_picker_opens_hidden_accounts_before_failing(self):
        snapshot = UiSnapshot(
            THREADS,
            ".mainactivity.BarcelonaActivity",
            (
                node(text="Log into Threads"),
                node(text="myduyenn681999"),
                node(text="baongocd806415"),
                node(text="2 others", clickable=True),
            ),
        )
        driver = SnapshotDriver(snapshot, build_threads_detector())

        result = ThreadsFlow(driver).run({"username": "sample_user"})

        self.assertEqual("running", result.status)
        self.assertEqual("threads_other_accounts", driver.mutations[0][1])
        self.assertEqual(("2 others",), driver.mutations[0][2])

    def test_threads_notification_permission_is_denied_without_human_checkpoint(self):
        snapshot = UiSnapshot(
            PERMISSION,
            ".permission.ui.GrantPermissionsActivity",
            (
                node(text="Allow Threads to send you notifications?"),
                node(text="Allow", clickable=True),
                node(text="Don’t allow", clickable=True),
            ),
        )
        driver = SnapshotDriver(snapshot, build_threads_detector())

        result = ThreadsFlow(driver).run({"username": "sample_user"})

        self.assertEqual("running", result.status)
        self.assertEqual("THREADS_NOTIFICATION_PERMISSION", result.screen)
        self.assertEqual("threads_notification_deny", driver.mutations[0][1])

    def test_threads_follow_suggestions_is_skipped_never_follow_all(self):
        snapshot = UiSnapshot(
            THREADS,
            ".mainactivity.BarcelonaActivity",
            (
                node(text="Follow suggestions based on your Instagram activity"),
                node(content_desc="Close", clickable=True),
                node(text="Follow all (50)", clickable=True),
            ),
        )
        driver = SnapshotDriver(snapshot, build_threads_detector())

        result = ThreadsFlow(driver).run({"username": "sample_user"})

        self.assertEqual("running", result.status)
        self.assertEqual("THREADS_FOLLOW_SUGGESTIONS", result.screen)
        self.assertEqual("threads_follow_suggestions_close", driver.mutations[0][1])
        self.assertFalse(any("follow_all" in mutation[1] for mutation in driver.mutations))

    def test_runtime_accepts_unique_threads_generated_username_before_continuing(self):
        acc = account("IG_CREATED")
        repo = FakeRepo(acc, completion_mode="ACP_ACTIVE")
        service = FakeService(repo)
        runtime = TestRuntime(
            repo,
            service,
            FakeGateway([{
                "ok": True,
                "status": "running",
                "result": {
                    "screen": "THREADS_ACCOUNT_PICKER",
                    "profile_updates": {"username": "sample_user253132"},
                },
            }]),
        )

        runtime._drive_job(job("AUTOMATE_THREADS"))

        self.assertEqual("sample_user253132", acc["username"])
        self.assertEqual(
            [("username", "sample_user253132"), ("running", "AUTOMATE_THREADS")],
            service.events,
        )
        self.assertEqual("AUTOMATE_THREADS", runtime.running_actions[-1])


if __name__ == "__main__":
    unittest.main()
