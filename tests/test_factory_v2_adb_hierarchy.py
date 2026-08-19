import unittest

from core.factory_v2.ui_automation.adb import AdbClient, CompletedCommand


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def run(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        if not self.responses:
            raise AssertionError(f"unexpected command: {argv}")
        return self.responses.pop(0)


XML = '<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0"><node text="Profile" /></hierarchy>'


class AdbHierarchyTests(unittest.TestCase):
    def test_direct_tty_xml_keeps_fast_path(self):
        runner = FakeRunner([
            CompletedCommand(0, f"UI hierchary dumped to: /dev/tty\n{XML}\n", ""),
        ])
        adb = AdbClient("emulator-5554", adb_path="adb", runner=runner)

        self.assertEqual(XML, adb.dump_hierarchy())
        self.assertEqual(1, len(runner.calls))

    def test_android15_killed_tty_falls_back_to_device_file(self):
        runner = FakeRunner([
            CompletedCommand(0, "Killed \n", ""),
            CompletedCommand(0, "UI hierchary dumped to: /sdcard/acp-window.xml\n", ""),
            CompletedCommand(0, XML, ""),
            CompletedCommand(0, "", ""),
        ])
        adb = AdbClient("emulator-5554", adb_path="adb", runner=runner)

        self.assertEqual(XML, adb.dump_hierarchy())
        self.assertEqual(
            ["adb", "-s", "emulator-5554", "shell", "uiautomator", "dump", "/sdcard/acp-window.xml"],
            runner.calls[1][0],
        )
        self.assertEqual(
            ["adb", "-s", "emulator-5554", "exec-out", "cat", "/sdcard/acp-window.xml"],
            runner.calls[2][0],
        )
        self.assertEqual(
            ["adb", "-s", "emulator-5554", "shell", "rm", "-f", "/sdcard/acp-window.xml"],
            runner.calls[3][0],
        )

    def test_fallback_without_xml_fails_closed_and_cleans_up(self):
        runner = FakeRunner([
            CompletedCommand(0, "Killed \n", ""),
            CompletedCommand(0, "UI hierchary dumped to: /sdcard/acp-window.xml\n", ""),
            CompletedCommand(0, "not xml", ""),
            CompletedCommand(0, "", ""),
        ])
        adb = AdbClient("emulator-5554", adb_path="adb", runner=runner)

        with self.assertRaisesRegex(RuntimeError, "UI hierarchy XML missing"):
            adb.dump_hierarchy()
        self.assertEqual(
            ["adb", "-s", "emulator-5554", "shell", "rm", "-f", "/sdcard/acp-window.xml"],
            runner.calls[-1][0],
        )


if __name__ == "__main__":
    unittest.main()
