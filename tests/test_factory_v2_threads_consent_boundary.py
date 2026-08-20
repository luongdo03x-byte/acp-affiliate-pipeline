import unittest

from core.factory_v2.ui_automation.hierarchy import UiBounds, UiNode, UiSnapshot
from core.factory_v2.ui_automation.threads.screens import build_threads_detector


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


class ThreadsConsentBoundaryTests(unittest.TestCase):
    def test_join_threads_confirmation_is_human_only(self):
        snapshot = UiSnapshot(
            "com.instagram.barcelona",
            ".MainActivity",
            (node(text="Join Threads", class_name="android.widget.Button", clickable=True),),
        )

        detected = build_threads_detector().detect(snapshot)

        self.assertEqual("THREADS_LEGAL_CONSENT", detected.kind)
        self.assertTrue(detected.protected)
        self.assertFalse(detected.automation_allowed)

    def test_terms_context_with_continue_is_human_only(self):
        snapshot = UiSnapshot(
            "com.instagram.barcelona",
            ".MainActivity",
            (
                node(text="By joining Threads, you agree to our Terms and Privacy Policy"),
                node(text="Continue", class_name="android.widget.Button", clickable=True),
            ),
        )

        detected = build_threads_detector().detect(snapshot)

        self.assertEqual("THREADS_LEGAL_CONSENT", detected.kind)
        self.assertTrue(detected.protected)
        self.assertFalse(detected.automation_allowed)

    def test_continue_with_instagram_remains_automation_allowed(self):
        snapshot = UiSnapshot(
            "com.instagram.barcelona",
            ".MainActivity",
            (
                node(
                    text="Continue with Instagram",
                    class_name="android.widget.Button",
                    clickable=True,
                ),
            ),
        )

        detected = build_threads_detector().detect(snapshot)

        self.assertEqual("THREADS_ONBOARDING", detected.kind)
        self.assertFalse(detected.protected)
        self.assertTrue(detected.automation_allowed)

    def test_current_threads_tab_ids_detect_postcheck(self):
        snapshot = UiSnapshot(
            "com.instagram.barcelona",
            ".MainActivity",
            (
                node(
                    resource_id="com.instagram.barcelona:id/barcelona_tab_main_feed",
                    clickable=True,
                ),
                node(
                    resource_id="com.instagram.barcelona:id/barcelona_tab_profile",
                    clickable=True,
                ),
            ),
        )

        detected = build_threads_detector().detect(snapshot)

        self.assertEqual("THREADS_POSTCHECK_OK", detected.kind)
        self.assertTrue(detected.automation_allowed)


if __name__ == "__main__":
    unittest.main()
