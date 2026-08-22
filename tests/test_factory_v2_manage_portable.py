import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class FactoryV2ManagePortableTests(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.manage = self.repo_root / "manage.sh"
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / "ACP"

    def _env(self):
        env = os.environ.copy()
        env["ACP_BASE"] = str(self.base)
        return env

    def _seed_release(self, *, ownership="ACTIVE"):
        release = self.root / "release" / "acp"
        (release / ".venv" / "bin").mkdir(parents=True)
        (release / "account_factory_server.py").write_text("# test launcher\n", encoding="utf-8")
        fake_python = release / ".venv" / "bin" / "python"
        fake_python.write_text(
            "#!/usr/bin/env bash\n"
            "if [[ \"$*\" == *account_factory_server.py* ]]; then\n"
            "  printf launched >\"${ACP_TEST_FACTORY_LAUNCH_MARKER:?}\"\n"
            "  exit 0\n"
            "fi\n"
            "printf 'MACHINE_HANDED_OFF\\n' >&2\n"
            "exit 1\n",
            encoding="utf-8",
        )
        fake_python.chmod(0o755)

        shared = self.base / "shared"
        shared.mkdir(parents=True)
        (shared / "machine.json").write_text(
            json.dumps({
                "machine_id": "test-machine",
                "last_imported_generation": 3,
                "ownership": ownership,
            }) + "\n",
            encoding="utf-8",
        )
        (release / ".env.local").write_text(
            f"ACP_DB={shared / 'var' / 'acp-live.db'}\n",
            encoding="utf-8",
        )
        self.base.mkdir(parents=True, exist_ok=True)
        (self.base / "acp").symlink_to(release, target_is_directory=True)
        return release

    def test_usage_exposes_portable_factory_commands(self):
        result = subprocess.run(
            ["bash", str(self.manage), "--help"],
            env=self._env(),
            text=True,
            capture_output=True,
        )

        self.assertEqual(0, result.returncode, msg=result.stderr)
        self.assertIn("handoff-out", result.stdout)
        self.assertIn("factory-start", result.stdout)
        self.assertIn("factory-stop", result.stdout)

    def test_factory_start_refuses_handed_off_machine_before_launch(self):
        self._seed_release(ownership="HANDED_OFF")
        marker = self.root / "factory-launched"
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        fake_nohup = fake_bin / "nohup"
        fake_nohup.write_text(
            "#!/usr/bin/env bash\n"
            "printf launched >\"${ACP_TEST_NOHUP_MARKER:?}\"\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake_nohup.chmod(0o755)

        env = self._env()
        env["ACP_TEST_FACTORY_LAUNCH_MARKER"] = str(marker)
        env["ACP_TEST_NOHUP_MARKER"] = str(marker)
        env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
        result = subprocess.run(
            ["bash", str(self.manage), "factory-start"],
            env=env,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("MACHINE_HANDED_OFF", result.stdout + result.stderr)
        self.assertFalse(marker.exists(), "factory launcher must not run after handoff")

    def test_status_includes_factory_controller_state(self):
        self._seed_release(ownership="ACTIVE")
        env = self._env()
        env["ACP_TEST_FACTORY_LAUNCH_MARKER"] = str(self.root / "unused")
        result = subprocess.run(
            ["bash", str(self.manage), "status"],
            env=env,
            text=True,
            capture_output=True,
        )

        self.assertEqual(0, result.returncode, msg=result.stderr)
        self.assertIn("Factory : STOPPED", result.stdout)


if __name__ == "__main__":
    unittest.main()
