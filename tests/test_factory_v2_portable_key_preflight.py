import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class PortableBundleKeyPreflightTests(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    @staticmethod
    def _write_executable(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def test_manage_handoff_out_requires_key_before_runtime_stop(self):
        base = self.root / "ACP"
        release = self.root / "release" / "acp"
        (release / ".venv" / "bin").mkdir(parents=True)
        shared = base / "shared"
        shared.mkdir(parents=True)
        (shared / "machine.json").write_text(
            json.dumps({
                "machine_id": "preflight-machine",
                "last_imported_generation": 0,
                "ownership": "ACTIVE",
            }) + "\n",
            encoding="utf-8",
        )
        base.mkdir(parents=True, exist_ok=True)
        (base / "acp").symlink_to(release, target_is_directory=True)

        log = self.root / "manage.log"
        fake_python = release / ".venv" / "bin" / "python"
        self._write_executable(
            fake_python,
            "#!/usr/bin/env bash\n"
            "printf 'python:%s\\n' \"$*\" >>\"${ACP_TEST_PREFLIGHT_LOG:?}\"\n"
            "exit 0\n",
        )

        fake_bin = self.root / "bin"
        self._write_executable(
            fake_bin / "git",
            "#!/usr/bin/env bash\n"
            "case \"$*\" in\n"
            "  *'remote get-url origin'*) printf 'git@github.com:luongdo03x-byte/acp-affiliate-pipeline.git\\n' ;;\n"
            "  *'rev-parse HEAD'*) printf 'abc123\\n' ;;\n"
            "  *'branch --show-current'*) printf 'feat/account-factory-android\\n' ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n",
        )
        self._write_executable(fake_bin / "gh", "#!/usr/bin/env bash\nexit 0\n")
        self._write_executable(fake_bin / "pgrep", "#!/usr/bin/env bash\nexit 1\n")

        env = os.environ.copy()
        env.pop("ACP_PORTABLE_BUNDLE_KEY", None)
        env["ACP_BASE"] = str(base)
        env["ACP_TEST_PREFLIGHT_LOG"] = str(log)
        env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

        result = subprocess.run(
            ["bash", str(self.repo_root / "manage.sh"), "handoff-out"],
            env=env,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(0, result.returncode)
        combined = result.stdout + result.stderr
        self.assertIn("PORTABLE_BUNDLE_KEY_REQUIRED", combined)
        self.assertNotIn("FACTORY_STOPPED", result.stdout)
        self.assertNotIn("ACP_STOPPED", result.stdout)
        calls = log.read_text(encoding="utf-8") if log.exists() else ""
        self.assertNotIn("core.factory_v2.portable_cli resume", calls)
        self.assertNotIn("core.factory_v2.portable_cli handoff-out", calls)

    def test_setup_requires_key_before_backup_or_restore(self):
        repo = self.root / "repo"
        repo.mkdir()
        shutil.copy2(self.repo_root / "setup.sh", repo / "setup.sh")
        (repo / "requirements.txt").write_text("\n", encoding="utf-8")
        self._write_executable(repo / "manage.sh", "#!/usr/bin/env bash\nexit 0\n")

        log = self.root / "setup.log"
        fake_python = repo / ".venv" / "bin" / "python"
        self._write_executable(
            fake_python,
            "#!/usr/bin/env bash\n"
            "printf 'python:%s\\n' \"$*\" >>\"${ACP_TEST_PREFLIGHT_LOG:?}\"\n"
            "exit 0\n",
        )

        base = self.root / "ACP-setup"
        shared = base / "shared"
        shared.mkdir(parents=True)
        marker = shared / "keep.txt"
        marker.write_text("keep", encoding="utf-8")

        env = os.environ.copy()
        env.pop("ACP_PORTABLE_BUNDLE_KEY", None)
        env["ACP_BASE"] = str(base)
        env["ACP_TEST_PREFLIGHT_LOG"] = str(log)

        result = subprocess.run(
            ["bash", str(repo / "setup.sh")],
            cwd=repo,
            env=env,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("PORTABLE_BUNDLE_KEY_REQUIRED", result.stdout + result.stderr)
        self.assertTrue(marker.is_file())
        self.assertEqual([], list(base.glob(".portable-rollback.*")))
        calls = log.read_text(encoding="utf-8") if log.exists() else ""
        self.assertNotIn("core.factory_v2.portable_cli handoff-in", calls)


if __name__ == "__main__":
    unittest.main()
