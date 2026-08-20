import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from core.factory_v2.runtime import FactoryControllerRuntime
from core.factory_v2.ui_automation.adb import AdbClient, CompletedCommand
from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.driver import ActionResult
from core.factory_v2.ui_automation.hierarchy import UiBounds, UiNode, UiSnapshot
from core.factory_v2.ui_automation.instagram.flow import InstagramFlow
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


class AvatarFlowDriver:
    def __init__(self):
        self.mutations = []

    def detect_screen(self):
        return DetectedScreen("IG_AVATAR_SETUP", 0.96, ("add_profile_photo",), False)

    def find(self, selector):
        if selector.semantic in {"add_profile_photo", "avatar_skip"}:
            return SimpleNamespace(text="")
        return None

    def tap(self, selector, **kwargs):
        del kwargs
        self.mutations.append(("tap", selector.semantic))
        return ActionResult("completed")


class CheckpointRouteRuntime(FactoryControllerRuntime):
    def __init__(self):
        self.refreshed = False
        self.handled = []

    def _checkpoint_for_account(self, account_id):
        del account_id
        return {"id": "cp-1", "type": "IG_POSTCHECK", "status": "OPEN"}

    def _command(self, job, action, payload=None):
        del job, action, payload
        return {
            "ok": True,
            "status": "running",
            "result": {"screen": "IG_AVATAR_SETUP"},
        }

    def _refresh_remote_waiting(self, job):
        del job
        self.refreshed = True

    def _handle_remote_result(self, job, account, *, flow, response):
        del job, account
        self.handled.append((flow, response["status"], response["result"]["screen"]))


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

    def test_android15_add_profile_picture_screen_is_safe_avatar_setup(self):
        snapshot = UiSnapshot(
            "com.instagram.android",
            ".activity.MainTabActivity",
            (
                node(text="Add a profile picture"),
                node(text="Add picture", content_desc="Add picture", clickable=True),
                node(text="Skip", content_desc="Skip", clickable=True),
            ),
        )

        detected = build_instagram_detector().detect(snapshot)

        self.assertEqual("IG_AVATAR_SETUP", detected.kind)
        self.assertTrue(detected.automation_allowed)

    def test_avatar_setup_without_staged_avatar_uses_skip(self):
        driver = AvatarFlowDriver()

        result = InstagramFlow(driver).run({})

        self.assertEqual("running", result.status)
        self.assertEqual([("tap", "avatar_skip")], driver.mutations)
        self.assertEqual("IG_AVATAR_SETUP", result.last_safe_step)

    def test_avatar_checkpoint_is_resumable_without_mutation(self):
        driver = AvatarFlowDriver()

        result = InstagramFlow(driver).observe_checkpoint()

        self.assertEqual("running", result.status)
        self.assertEqual("IG_AVATAR_SETUP", result.screen)
        self.assertEqual([], driver.mutations)

    def test_running_checkpoint_result_routes_back_to_automation(self):
        runtime = CheckpointRouteRuntime()

        runtime._observe_remote_checkpoint({"id": "job-1"}, {"id": "acc-1"})

        self.assertFalse(runtime.refreshed)
        self.assertEqual(
            [("instagram", "running", "IG_AVATAR_SETUP")],
            runtime.handled,
        )


if __name__ == "__main__":
    unittest.main()
