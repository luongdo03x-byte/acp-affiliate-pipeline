import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class FactoryV2PortableSetupRollbackTests(unittest.TestCase):
    def setUp(self):
        self.source_root = Path(__file__).resolve().parents[1]
        self.source_setup = self.source_root / "setup.sh"
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.base = self.root / "ACP"
        self.log = self.root / "rollback.log"

    def _write_executable(self, path: Path, text: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def _prepare_repo(self):
        self.repo.mkdir(parents=True)
        shutil.copy2(self.source_setup, self.repo / "setup.sh")
        (self.repo / "requirements.txt").write_text("\n", encoding="utf-8")

        python = self.repo / ".venv" / "bin" / "python"
        self._write_executable(
            python,
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "case \"$*\" in\n"
            "  *'-m pip install'*) exit 0 ;;\n"
            "  *'core.factory_v2.portable_cli handoff-in'*)\n"
            "    printf 'handoff-in\\n' >>\"${ACP_TEST_ROLLBACK_LOG:?}\"\n"
            "    rm -rf \"${ACP_BASE:?}/shared\"\n"
            "    mkdir -p \"${ACP_BASE}/shared/var\" \"${ACP_BASE}/shared/avatars\"\n"
            "    printf 'IMPORTED_ENV=1\\n' >\"${ACP_BASE}/shared/.env.local\"\n"
            "    chmod 600 \"${ACP_BASE}/shared/.env.local\"\n"
            "    printf 'imported-db\\n' >\"${ACP_BASE}/shared/var/acp-live.db\"\n"
            "    printf 'imported-avatar\\n' >\"${ACP_BASE}/shared/avatars/avatar.txt\"\n"
            "    printf '%s\\n' '{\"machine_id\":\"new-machine\",\"last_imported_generation\":9,\"ownership\":\"ACTIVE\"}' >\"${ACP_BASE}/shared/machine.json\"\n"
            "    printf 'IMPORT_OK generation=9\\n'\n"
            "    exit 0 ;;\n"
            "  *'setup-avd-prereq'*) exit 0 ;;\n"
            "  *'core.factory_v2.portable_cli doctor'*)\n"
            "    printf 'doctor-fail\\n' >>\"${ACP_TEST_ROLLBACK_LOG:?}\"\n"
            "    printf 'PORTABLE_DOCTOR_FAILED:TEST_FAILURE\\n' >&2\n"
            "    exit 17 ;;\n"
            "  *'core.factory_v2.portable_cli resume'*)\n"
            "    printf 'resume-unexpected\\n' >>\"${ACP_TEST_ROLLBACK_LOG:?}\"\n"
            "    exit 0 ;;\n"
            "esac\n"
            "exit 0\n",
        )

        manage = self.repo / "manage.sh"
        self._write_executable(
            manage,
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "case \"${1:-}\" in\n"
            "  setup)\n"
            "    printf 'manage-setup\\n' >>\"${ACP_TEST_ROLLBACK_LOG:?}\"\n"
            "    mkdir -p \"${ACP_BASE:?}\"\n"
            "    cp \"$0\" \"${ACP_BASE}/manage.sh\"\n"
            "    chmod +x \"${ACP_BASE}/manage.sh\" ;;\n"
            "  factory-start)\n"
            "    printf 'factory-start-unexpected\\n' >>\"${ACP_TEST_ROLLBACK_LOG:?}\" ;;\n"
            "  *) exit 2 ;;\n"
            "esac\n",
        )

        env = os.environ.copy()
        env["ACP_BASE"] = str(self.base)
        env["ACP_PORTABLE_BUNDLE_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        env["ACP_TEST_ROLLBACK_LOG"] = str(self.log)
        return env

    def _seed_old_shared(self):
        shared = self.base / "shared"
        (shared / "var").mkdir(parents=True)
        (shared / "avatars").mkdir(parents=True)
        (shared / ".env.local").write_text("OLD_ENV=1\n", encoding="utf-8")
        (shared / ".env.local").chmod(0o600)
        (shared / "var" / "acp-live.db").write_text("old-db\n", encoding="utf-8")
        (shared / "avatars" / "avatar.txt").write_text("old-avatar\n", encoding="utf-8")
        (shared / "machine.json").write_text(
            json.dumps({
                "machine_id": "old-machine",
                "last_imported_generation": 8,
                "ownership": "ACTIVE",
            }) + "\n",
            encoding="utf-8",
        )
        (shared / "machine.json").chmod(0o600)

    def _run(self, env):
        return subprocess.run(
            ["bash", str(self.repo / "setup.sh")],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
        )

    def test_doctor_failure_restores_previous_shared_state(self):
        env = self._prepare_repo()
        self._seed_old_shared()

        result = self._run(env)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("PORTABLE_DOCTOR_FAILED:TEST_FAILURE", result.stdout + result.stderr)
        shared = self.base / "shared"
        self.assertEqual("OLD_ENV=1\n", (shared / ".env.local").read_text(encoding="utf-8"))
        self.assertEqual("old-db\n", (shared / "var" / "acp-live.db").read_text(encoding="utf-8"))
        self.assertEqual("old-avatar\n", (shared / "avatars" / "avatar.txt").read_text(encoding="utf-8"))
        metadata = json.loads((shared / "machine.json").read_text(encoding="utf-8"))
        self.assertEqual("old-machine", metadata["machine_id"])
        self.assertEqual(8, metadata["last_imported_generation"])
        self.assertEqual("ACTIVE", metadata["ownership"])
        log = self.log.read_text(encoding="utf-8")
        self.assertNotIn("resume-unexpected", log)
        self.assertNotIn("factory-start-unexpected", log)

    def test_first_clone_doctor_failure_removes_imported_active_state(self):
        env = self._prepare_repo()

        result = self._run(env)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("PORTABLE_DOCTOR_FAILED:TEST_FAILURE", result.stdout + result.stderr)
        machine = self.base / "shared" / "machine.json"
        self.assertFalse(machine.exists(), "failed first-clone import must not remain ACTIVE")
        self.assertFalse((self.base / "shared" / ".env.local").exists())
        self.assertFalse((self.base / "shared" / "var" / "acp-live.db").exists())


if __name__ == "__main__":
    unittest.main()
