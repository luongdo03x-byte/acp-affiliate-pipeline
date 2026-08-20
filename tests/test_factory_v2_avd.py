import unittest
from types import SimpleNamespace

from core.factory_v2.avd import AvdManager


class FakeRunner:
    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

    def run(self, argv, timeout):
        key = tuple(argv)
        self.calls.append((key, timeout))
        stdout = self.outputs.get(key, "")
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


class FactoryV2AvdTests(unittest.TestCase):
    def test_parse_adb_devices_ignores_offline(self):
        runner = FakeRunner({
            ("adb", "devices"): "List of devices attached\nemulator-5554\tdevice\nemulator-5556\toffline\n"
        })
        manager = AvdManager(runner=runner, adb_path="adb", emulator_path="emulator")
        self.assertEqual(["emulator-5554"], manager.list_online_devices())

    def test_boot_completed_requires_exact_one_after_stripping(self):
        key = ("adb", "-s", "emulator-5554", "shell", "getprop", "sys.boot_completed")
        manager = AvdManager(runner=FakeRunner({key: " 1\n"}), adb_path="adb", emulator_path="emulator")
        self.assertTrue(manager.is_boot_completed("emulator-5554"))
        manager = AvdManager(runner=FakeRunner({key: "1 extra\n"}), adb_path="adb", emulator_path="emulator")
        self.assertFalse(manager.is_boot_completed("emulator-5554"))

    def test_list_avds_uses_emulator_cli(self):
        manager = AvdManager(
            runner=FakeRunner({("emulator", "-list-avds"): "acp-worker-01\nacp-worker-02\n"}),
            adb_path="adb",
            emulator_path="emulator",
        )
        self.assertEqual(["acp-worker-01", "acp-worker-02"], manager.list_avds())

    def test_start_uses_low_memory_pilot_profile_and_stable_graphics(self):
        captured = {}
        process = object()

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return process

        manager = AvdManager(
            runner=FakeRunner({}),
            adb_path="adb",
            emulator_path="emulator",
            popen_factory=fake_popen,
        )

        result = manager.start("acp-worker-01", 5554)

        self.assertIs(process, result)
        self.assertEqual(
            [
                "emulator",
                "-avd", "acp-worker-01",
                "-port", "5554",
                "-memory", "1536",
                "-gpu", "swiftshader",
                "-feature", "-Vulkan",
                "-no-snapshot",
                "-noaudio",
            ],
            captured["argv"],
        )


if __name__ == "__main__":
    unittest.main()
