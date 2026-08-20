import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.factory_v2.ui_automation.hierarchy import UiBounds, UiNode, UiSnapshot
from core.factory_v2.ui_automation.instagram.screens import build_instagram_detector
from workers.account_factory_worker import WorkerAgent


def node(*, text="", resource_id="", clickable=False):
    return UiNode(
        text,
        "",
        resource_id,
        "android.widget.Button" if clickable else "android.widget.TextView",
        clickable,
        True,
        UiBounds(0, 0, 100, 100),
    )


class FakeAdbClient:
    def __init__(self):
        self.push_calls = []

    def push_file(self, source, destination):
        self.push_calls.append((str(source), str(destination)))


class AvatarRuntimeRegressionTests(unittest.TestCase):
    def test_google_permission_controller_is_detected(self):
        snapshot = UiSnapshot(
            "com.google.android.permissioncontroller",
            "com.android.permissioncontroller.permission.ui.GrantPermissionsActivity",
            (
                node(text="Allow Instagram to access photos and videos on this device?"),
                node(
                    text="Allow limited access",
                    resource_id="com.android.permissioncontroller:id/permission_allow_selected_button",
                    clickable=True,
                ),
            ),
        )

        detected = build_instagram_detector().detect(snapshot)

        self.assertEqual("ANDROID_MEDIA_PERMISSION", detected.kind)
        self.assertTrue(detected.automation_allowed)

    def test_worker_preserves_png_extension_when_staging_avatar(self):
        adb_client = FakeAdbClient()
        agent = object.__new__(WorkerAgent)
        agent.adb_client = adb_client

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            source = repo_root / "avatars" / "sample.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"png-avatar")

            with patch("workers.account_factory_worker._REPO_ROOT", repo_root):
                agent._stage_avatar({"avatar_file": "avatars/sample.png"})

        self.assertEqual(
            [(str(source.resolve()), "/sdcard/Pictures/ACP/avatar.png")],
            adb_client.push_calls,
        )


if __name__ == "__main__":
    unittest.main()
