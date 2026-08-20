import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.factory_v2.ui_automation.adb import AdbClient
from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.driver import ActionResult
from core.factory_v2.ui_automation.hierarchy import UiBounds, UiNode, UiSnapshot
from core.factory_v2.ui_automation.instagram.flow import InstagramFlow
from core.factory_v2.ui_automation.instagram.screens import build_instagram_detector


def node(
    *,
    text="",
    content_desc="",
    resource_id="",
    class_name="android.widget.TextView",
    clickable=False,
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


class AvatarStepDriver:
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


class FactoryV2AvatarUploadTests(unittest.TestCase):
    def test_change_avatar_source_menu_is_detected(self):
        snapshot = UiSnapshot(
            "com.instagram.android",
            ".activity.MainTabActivity",
            (
                node(text="Choose from library", clickable=True),
                node(text="Take photo", clickable=True),
            ),
        )

        detected = build_instagram_detector().detect(snapshot)

        self.assertEqual("IG_AVATAR_SOURCE_MENU", detected.kind)
        self.assertTrue(detected.automation_allowed)

    def test_android_limited_photo_permission_is_detected(self):
        snapshot = UiSnapshot(
            "com.android.permissioncontroller",
            "com.android.permissioncontroller.permission.ui.GrantPermissionsActivity",
            (
                node(text="Allow Instagram to access photos and videos on this device?"),
                node(
                    text="Allow limited access",
                    resource_id="com.android.permissioncontroller:id/permission_allow_selected_button",
                    class_name="android.widget.Button",
                    clickable=True,
                ),
            ),
        )

        detected = build_instagram_detector().detect(snapshot)

        self.assertEqual("ANDROID_MEDIA_PERMISSION", detected.kind)
        self.assertTrue(detected.automation_allowed)

    def test_android_photo_permission_picker_is_detected(self):
        snapshot = UiSnapshot(
            "com.google.android.providers.media.module",
            "com.android.providers.media.photopicker.PhotoPickerUserSelectActivity",
            (
                node(
                    content_desc="Photo taken on Aug 20, 2026, 4:45:34 AM",
                    class_name="android.widget.FrameLayout",
                    clickable=True,
                ),
                node(
                    text="Allow none",
                    resource_id="com.google.android.providers.media.module:id/button_add",
                    class_name="android.widget.Button",
                    clickable=True,
                ),
            ),
        )

        detected = build_instagram_detector().detect(snapshot)

        self.assertEqual("ANDROID_MEDIA_PICKER", detected.kind)
        self.assertTrue(detected.automation_allowed)

    def test_instagram_avatar_crop_done_is_detected(self):
        snapshot = UiSnapshot(
            "com.instagram.android",
            ".activity.MainTabActivity",
            (
                node(
                    text="Done",
                    content_desc="Done",
                    resource_id="com.instagram.android:id/next_button_textview",
                    clickable=True,
                ),
            ),
        )

        detected = build_instagram_detector().detect(snapshot)

        self.assertEqual("IG_AVATAR_CROP", detected.kind)
        self.assertTrue(detected.automation_allowed)

    def test_avatar_source_menu_chooses_library(self):
        driver = AvatarStepDriver("IG_AVATAR_SOURCE_MENU", {"choose_from_library"})

        result = InstagramFlow(driver).run({"avatar_file": "/tmp/avatar.png"})

        self.assertEqual("running", result.status)
        self.assertEqual([("tap", "choose_from_library")], driver.mutations)

    def test_limited_permission_uses_only_selected_photos(self):
        driver = AvatarStepDriver("ANDROID_MEDIA_PERMISSION", {"allow_limited_photos"})

        result = InstagramFlow(driver).run({"avatar_file": "/tmp/avatar.png"})

        self.assertEqual("running", result.status)
        self.assertEqual([("tap", "allow_limited_photos")], driver.mutations)

    def test_photo_picker_selects_one_photo_then_confirms_one(self):
        driver = AvatarStepDriver(
            "ANDROID_MEDIA_PICKER",
            {"media_picker_photo", "media_picker_confirm"},
            confirm_text="Allow (1)",
        )

        result = InstagramFlow(driver).run({"avatar_file": "/tmp/avatar.png"})

        self.assertEqual("running", result.status)
        self.assertEqual(
            [("tap", "media_picker_photo"), ("tap", "media_picker_confirm")],
            driver.mutations,
        )

    def test_photo_picker_never_confirms_allow_none(self):
        driver = AvatarStepDriver(
            "ANDROID_MEDIA_PICKER",
            {"media_picker_photo", "media_picker_confirm"},
            confirm_text="Allow none",
        )

        result = InstagramFlow(driver).run({"avatar_file": "/tmp/avatar.png"})

        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual([("tap", "media_picker_photo")], driver.mutations)

    def test_avatar_crop_done_completes_instagram(self):
        driver = AvatarStepDriver("IG_AVATAR_CROP", {"avatar_crop_done"})

        result = InstagramFlow(driver).run({"avatar_file": "/tmp/avatar.png"})

        self.assertEqual("completed", result.status)
        self.assertEqual([("tap", "avatar_crop_done")], driver.mutations)

    def test_adb_push_accepts_png_avatar_and_refreshes_media_store(self):
        runner = RecordingRunner()
        adb = AdbClient("emulator-5554", adb_path="adb", runner=runner)
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "avatar.png"
            source.write_bytes(b"png")

            adb.push_file(source, "/sdcard/Pictures/ACP/avatar.png")

        commands = [call[0] for call in runner.calls]
        self.assertIn(
            ("adb", "-s", "emulator-5554", "push", str(source.resolve()), "/sdcard/Pictures/ACP/avatar.png"),
            commands,
        )
        self.assertIn(
            (
                "adb", "-s", "emulator-5554", "shell", "am", "broadcast",
                "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                "-d", "file:///sdcard/Pictures/ACP/avatar.png",
            ),
            commands,
        )


if __name__ == "__main__":
    unittest.main()
