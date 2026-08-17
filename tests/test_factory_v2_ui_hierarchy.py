import unittest
from types import SimpleNamespace

from core.factory_v2.ui_automation.hierarchy import UiHierarchyReader
from core.factory_v2.ui_automation.adb import AdbClient


class FakeRunner:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def run(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        if self.responses:
            return self.responses.pop(0)
        return SimpleNamespace(returncode=0, stdout="", stderr="")


class UiHierarchyTests(unittest.TestCase):
    def test_parse_exposes_sanitized_metadata(self):
        xml = '''<hierarchy><node text="Username"
          resource-id="com.instagram.android:id/username"
          class="android.widget.EditText" clickable="true" enabled="true"
          bounds="[10,20][210,80]" /></hierarchy>'''
        snap = UiHierarchyReader().parse(xml, package="com.instagram.android", activity=".MainActivity")
        self.assertEqual("Username", snap.nodes[0].text)
        self.assertEqual("com.instagram.android:id/username", snap.nodes[0].resource_id)
        self.assertEqual((110, 50), snap.nodes[0].bounds.center)
        self.assertTrue(snap.nodes[0].clickable)

    def test_password_node_text_is_redacted(self):
        xml = '''<hierarchy><node text="secret" password="true"
          content-desc="secret" class="android.widget.EditText"
          bounds="[0,0][100,100]" /></hierarchy>'''
        snap = UiHierarchyReader().parse(xml, package="x", activity="y")
        self.assertEqual("", snap.nodes[0].text)
        self.assertEqual("", snap.nodes[0].content_desc)

    def test_sensitive_edit_text_hint_is_redacted(self):
        xml = '''<hierarchy><node text="123456" password="false"
          resource-id="com.instagram.android:id/confirmation_code"
          class="android.widget.EditText" bounds="[0,0][100,100]" /></hierarchy>'''
        snap = UiHierarchyReader().parse(xml, package="x", activity="y")
        self.assertEqual("", snap.nodes[0].text)

    def test_malformed_bounds_node_is_skipped(self):
        xml = '''<hierarchy>
          <node text="bad" bounds="not-bounds" />
          <node text="good" bounds="[0,0][10,10]" />
        </hierarchy>'''
        snap = UiHierarchyReader().parse(xml, package="x", activity="y")
        self.assertEqual(["good"], [node.text for node in snap.nodes])


class AdbClientTests(unittest.TestCase):
    def test_tap_is_scoped_to_serial(self):
        runner = FakeRunner()
        AdbClient("emulator-5554", adb_path="adb", runner=runner).tap(120, 480)
        self.assertEqual(["adb", "-s", "emulator-5554"], runner.calls[-1][0][:3])
        self.assertEqual(["shell", "input", "tap", "120", "480"], runner.calls[-1][0][3:])

    def test_set_text_rejects_control_characters(self):
        client = AdbClient("emulator-5554", adb_path="adb", runner=FakeRunner())
        with self.assertRaisesRegex(ValueError, "control"):
            client.set_text("abc\n123")

    def test_foreground_extracts_package_and_activity(self):
        result = SimpleNamespace(
            returncode=0,
            stdout="mCurrentFocus=Window{123 u0 com.instagram.android/com.instagram.mainactivity.MainActivity}",
            stderr="",
        )
        client = AdbClient("emulator-5554", adb_path="adb", runner=FakeRunner([result]))
        self.assertEqual(
            ("com.instagram.android", "com.instagram.mainactivity.MainActivity"),
            client.foreground(),
        )


if __name__ == "__main__":
    unittest.main()
