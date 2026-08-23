import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.factory_v2.ui_automation.adb import AdbClient
from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.driver import ActionResult
from core.factory_v2.ui_automation.flow_result import FlowResult
from core.factory_v2.ui_automation.hierarchy import UiBounds, UiNode, UiSnapshot
from core.factory_v2.ui_automation.instagram.flow import InstagramFlow
from core.factory_v2.ui_automation.instagram.screens import build_instagram_detector
from core.factory_v2.worker_protocol import WorkerCommand
from tests.test_factory_v2_avd_worker_agent import FakeAvd, FakeFlow
from workers.account_factory_worker import WorkerAgent


MEDIA_PICKER = "com.google.android.providers.media.module"


def node(
    *,
    text="",
    content_desc="",
    resource_id="",
    clickable=False,
    class_name="android.widget.TextView",
):
    return UiNode(
        text,
        content_desc,
        resource_id,
        class_name,
        clickable,
        True,
        UiBounds(0, 0, 100, 100),
    )


class PickerDriver:
    def __init__(self, screen, available, *, confirm_text="Allow (1)"):
        self.screen = screen
        self.available = set(available)
        self.confirm_text = confirm_text
        self.mutations = []

    def detect_screen(self):
        return DetectedScreen(self.screen, 0.99, (self.screen.lower(),), False)

    def find(self, selector):
        if selector.semantic not in self.available:
            return None
        if selector.semantic == "media_picker_confirm":
            return SimpleNamespace(text=self.confirm_text)
        return SimpleNamespace(text="")

    def tap(self, selector, **kwargs):
        del kwargs
        self.mutations.append(("tap", selector.semantic))
        return ActionResult("completed")


class RecordingRunner:
    def __init__(self):
        self.calls = []

    def run(self, argv, timeout):
        self.calls.append((tuple(argv), timeout))
        return SimpleNamespace(returncode=0, stdout="", stderr="")


class AccountAvatarAdb:
    def __init__(self):
        self.stage_calls = []
        self.push_calls = []

    def stage_avatar(self, source, account_id):
        self.stage_calls.append((str(source), str(account_id)))

    def push_file(self, source, destination):
        self.push_calls.append((str(source), str(destination)))


class FactoryV2AvatarAutoselectRegressions(unittest.TestCase):
    def test_photo_picker_initial_state_is_detected_with_thumbnail_only(self):
        snapshot = UiSnapshot(
            MEDIA_PICKER,
            "com.android.providers.media.photopicker.PhotoPickerUserSelectActivity",
            (
                node(
                    content_desc="Photo taken on Aug 23, 2026, 6:57:00 PM",
                    class_name="android.widget.FrameLayout",
                    clickable=True,
                ),
            ),
        )

        detected = build_instagram_detector().detect(snapshot)

        self.assertEqual("ANDROID_MEDIA_PICKER_INITIAL", detected.kind)
        self.assertTrue(detected.automation_allowed)

    def test_photo_picker_initial_state_taps_thumbnail_then_returns_running(self):
        driver = PickerDriver(
            "ANDROID_MEDIA_PICKER_INITIAL",
            {"media_picker_photo"},
        )

        result = InstagramFlow(driver).run({"avatar_file": "/tmp/avatar.png"})

        self.assertEqual("running", result.status)
        self.assertEqual("ANDROID_MEDIA_PICKER_INITIAL", result.screen)
        self.assertEqual([("tap", "media_picker_photo")], driver.mutations)

    def test_selected_photo_picker_confirms_without_tapping_thumbnail_again(self):
        driver = PickerDriver(
            "ANDROID_MEDIA_PICKER",
            {"media_picker_photo", "media_picker_confirm"},
            confirm_text="Allow (1)",
        )

        result = InstagramFlow(driver).run({"avatar_file": "/tmp/avatar.png"})

        self.assertEqual("running", result.status)
        self.assertEqual([("tap", "media_picker_confirm")], driver.mutations)

    def test_adb_stage_avatar_cleans_old_media_and_uses_account_specific_filename(self):
        runner = RecordingRunner()
        adb = AdbClient("emulator-5554", adb_path="adb", runner=runner)

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "profile.png"
            source.write_bytes(b"avatar")
            destination = adb.stage_avatar(source, "01M0Q4VXHTX7GPRNKHM896")

        self.assertEqual(
            "/sdcard/Pictures/ACP/01M0Q4VXHTX7GPRNKHM896_avatar.png",
            destination,
        )
        commands = [call[0] for call in runner.calls]
        self.assertIn(
            (
                "adb", "-s", "emulator-5554", "shell", "content", "delete",
                "--uri", "content://media/external/images/media",
                "--where", "relative_path='Pictures/ACP/'",
            ),
            commands,
        )
        self.assertIn(
            (
                "adb", "-s", "emulator-5554", "shell", "rm", "-rf",
                "/sdcard/Pictures/ACP",
            ),
            commands,
        )
        self.assertIn(
            (
                "adb", "-s", "emulator-5554", "push", str(source.resolve()),
                "/sdcard/Pictures/ACP/01M0Q4VXHTX7GPRNKHM896_avatar.png",
            ),
            commands,
        )
        self.assertIn(
            (
                "adb", "-s", "emulator-5554", "shell", "am", "broadcast",
                "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                "-d", "file:///sdcard/Pictures/ACP/01M0Q4VXHTX7GPRNKHM896_avatar.png",
            ),
            commands,
        )

    def test_worker_stages_avatar_once_per_account_and_reference(self):
        adb_client = AccountAvatarAdb()
        instagram = FakeFlow(FlowResult("running", "IG_AVATAR_SETUP"))
        threads = FakeFlow(FlowResult("running", "THREADS_ONBOARDING"))
        agent = WorkerAgent(
            "worker-1",
            "acp-worker-01",
            "emulator-5554",
            avd=FakeAvd(),
            instagram_flow=instagram,
            threads_flow=threads,
            browser_login_flow=SimpleNamespace(),
            adb_client=adb_client,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            source = repo_root / "avatars" / "sample.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"avatar")
            profile = {"avatar_file": "avatars/sample.png"}
            with patch("workers.account_factory_worker._REPO_ROOT", repo_root):
                agent.execute(WorkerCommand(
                    "stage-1", "AUTOMATE_INSTAGRAM", "acc-1", {"profile": profile}
                ))
                agent.execute(WorkerCommand(
                    "stage-2", "AUTOMATE_INSTAGRAM", "acc-1", {"profile": profile}
                ))

        self.assertEqual(
            [(str(source.resolve()), "acc-1")],
            adb_client.stage_calls,
        )
        self.assertEqual([], adb_client.push_calls)


if __name__ == "__main__":
    unittest.main()
