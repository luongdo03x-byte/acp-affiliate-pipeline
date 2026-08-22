import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


_MODULE = "core.factory_v2.portable_state"
_MODULE_AVAILABLE = importlib.util.find_spec(_MODULE) is not None


class PortableStateModuleContractTests(unittest.TestCase):
    def test_portable_state_module_exists(self):
        self.assertTrue(_MODULE_AVAILABLE, "portable_state module missing")


@unittest.skipUnless(_MODULE_AVAILABLE, "portable_state module not implemented yet")
class PortableStateTests(unittest.TestCase):
    def setUp(self):
        from core.factory_v2.portable_state import MachineState, write_machine_state

        self.MachineState = MachineState
        self.write_machine_state = write_machine_state
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.machine_file = Path(self.tmp.name) / "machine.json"

    def test_generation_parser_accepts_only_contract_name(self):
        from core.factory_v2.portable_state import generation_from_asset

        self.assertEqual(42, generation_from_asset("acp-state-g000042.tar.gz"))
        self.assertIsNone(generation_from_asset("state-latest.tar.gz"))
        self.assertIsNone(generation_from_asset("acp-state-g42.tar.gz"))
        self.assertIsNone(generation_from_asset("acp-state-g000042.tar.gz.bak"))

    def test_next_generation_uses_remote_and_local_maximum(self):
        from core.factory_v2.portable_state import next_generation

        self.assertEqual(
            10,
            next_generation(
                [
                    "notes.txt",
                    "acp-state-g000004.tar.gz",
                    "acp-state-g000009.tar.gz",
                ],
                7,
            ),
        )
        self.assertEqual(12, next_generation([], 11))

    def test_machine_state_round_trips_without_secret_fields(self):
        from core.factory_v2.portable_state import load_machine_state

        state = self.MachineState("m1", 7, "ACTIVE")
        self.write_machine_state(self.machine_file, state)

        self.assertEqual(state, load_machine_state(self.machine_file))
        raw = json.loads(self.machine_file.read_text(encoding="utf-8"))
        self.assertEqual(
            {
                "machine_id": "m1",
                "last_imported_generation": 7,
                "ownership": "ACTIVE",
            },
            raw,
        )

    def test_handed_off_machine_cannot_start(self):
        from core.factory_v2.portable_state import require_active_ownership

        self.write_machine_state(
            self.machine_file,
            self.MachineState("m1", 7, "HANDED_OFF"),
        )

        with self.assertRaisesRegex(RuntimeError, "^MACHINE_HANDED_OFF$"):
            require_active_ownership(self.machine_file)

    def test_missing_machine_state_cannot_start(self):
        from core.factory_v2.portable_state import require_active_ownership

        with self.assertRaisesRegex(RuntimeError, "^MACHINE_HANDED_OFF$"):
            require_active_ownership(self.machine_file)

    def test_invalid_ownership_is_rejected_on_write(self):
        with self.assertRaisesRegex(ValueError, "^INVALID_MACHINE_OWNERSHIP$"):
            self.write_machine_state(
                self.machine_file,
                self.MachineState("m1", 7, "UNKNOWN"),
            )


if __name__ == "__main__":
    unittest.main()
