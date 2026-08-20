import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from core.factory_v2.ui_automation.adb import AdbClient, CompletedCommand
from core.factory_v2.ui_automation.hierarchy import UiBounds, UiNode, UiSnapshot
from core.factory_v2.ui_automation.instagram.screens import build_instagram_detector


XML = '<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0"><node text="Profile" /></hierarchy>'


def node(*, text="", content_desc="", clickable=False):
    return UiNode(
        text,
        content_desc,
        "",
        "android.widget.TextView",
        clickable,
        True,
        UiBounds(0, 0, 100, 100),
    )


class SlowHierarchyRunner:
    def __init__(self):
        self._guard = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.first_entered = threading.Event()

    def run(self, argv, timeout):
        del argv, timeout
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.first_entered.set()
        try:
            time.sleep(0.15)
            return CompletedCommand(0, f"UI hierarchy dumped to: /dev/tty\n{XML}\n", "")
        finally:
            with self._guard:
                self.active -= 1


class InstagramRegressionTests(unittest.TestCase):
    def test_terms_i_agree_screen_is_protected_final_signup(self):
        snapshot = UiSnapshot(
            "com.instagram.android",
            ".activity.MainTabActivity",
            (
                node(text="To sign up, read and agree to our terms and policies"),
                node(text="By signing up you agree to Instagram's Terms, Privacy Policy and Cookies Policy."),
                node(text="I agree", content_desc="I agree", clickable=True),
            ),
        )

        detected = build_instagram_detector().detect(snapshot)

        self.assertEqual("IG_FINAL_SIGNUP_SUBMIT", detected.kind)
        self.assertTrue(detected.protected)
        self.assertFalse(detected.automation_allowed)

    def test_hierarchy_dump_is_serialized_for_same_emulator(self):
        runner = SlowHierarchyRunner()
        first = AdbClient("emulator-5554", adb_path="adb", runner=runner)
        second = AdbClient("emulator-5554", adb_path="adb", runner=runner)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first_result = pool.submit(first.dump_hierarchy)
            self.assertTrue(runner.first_entered.wait(timeout=1.0))
            second_result = pool.submit(second.dump_hierarchy)
            self.assertEqual(XML, first_result.result(timeout=3.0))
            self.assertEqual(XML, second_result.result(timeout=3.0))

        self.assertEqual(1, runner.max_active)


if __name__ == "__main__":
    unittest.main()
