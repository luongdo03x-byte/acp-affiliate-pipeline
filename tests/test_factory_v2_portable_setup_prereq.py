import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


class FactoryV2PortableSetupPrereqTests(unittest.TestCase):
    def setUp(self):
        self.source_root = Path(__file__).resolve().parents[1]
        self.source_setup = self.source_root / "setup.sh"
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.base = self.root / "ACP"
        self.log = self.root / "setup-prereq.log"

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
            "printf 'python:%s\\n' \"$*\" >>\"${ACP_TEST_SETUP_LOG:?}\"\n"
            "case \"$*\" in\n"
            "  *'-m pip install'*) exit 0 ;;\n"
            "  *'core.factory_v2.portable_cli handoff-in'*)\n"
            "    printf 'handoff-in\\n' >>\"${ACP_TEST_SETUP_LOG:?}\"\n"
            "    printf 'IMPORT_OK generation=8\\n'\n"
            "    exit 0 ;;\n"
            "  *'setup-avd-prereq'*)\n"
            "    if [[ \"${ACP_TEST_AVD_MISSING:-0}\" == 1 ]]; then\n"
            "      printf 'ANDROID_AVD_PREREQUISITE: acp-worker-01 missing\\n' >&2\n"
            "      exit 18\n"
            "    fi\n"
            "    exit 0 ;;\n"
            "  *'core.factory_v2.portable_cli doctor'*)\n"
            "    printf 'doctor\\n' >>\"${ACP_TEST_SETUP_LOG:?}\"\n"
            "    printf 'DOCTOR_OK\\n'\n"
            "    exit 0 ;;\n"
            "  *'core.factory_v2.portable_cli resume'*)\n"
            "    printf 'resume\\n' >>\"${ACP_TEST_SETUP_LOG:?}\"\n"
            "    printf 'RESUME_RECONCILED leases=0 oauth=0 gated=0\\n'\n"
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
            "    printf 'manage-setup\\n' >>\"${ACP_TEST_SETUP_LOG:?}\"\n"
            "    mkdir -p \"${ACP_BASE:?}\"\n"
            "    cp \"$0\" \"${ACP_BASE}/manage.sh\"\n"
            "    chmod +x \"${ACP_BASE}/manage.sh\"\n"
            "    printf 'SETUP_OK\\n' ;;\n"
            "  factory-start)\n"
            "    printf 'factory-start\\n' >>\"${ACP_TEST_SETUP_LOG:?}\"\n"
            "    printf 'FACTORY_STARTED pid=123\\n' ;;\n"
            "  *) exit 2 ;;\n"
            "esac\n",
        )

        env = os.environ.copy()
        env["ACP_BASE"] = str(self.base)
        env["ACP_TEST_SETUP_LOG"] = str(self.log)
        env["ACP_TEST_AVD_MISSING"] = "1"
        return env

    def _milestones(self):
        if not self.log.exists():
            return []
        wanted = {"handoff-in", "manage-setup", "doctor", "resume", "factory-start"}
        return [
            line for line in self.log.read_text(encoding="utf-8").splitlines()
            if line in wanted
        ]

    def test_setup_script_is_executable(self):
        mode = self.source_setup.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR, "setup.sh must be executable for ./setup.sh")

    def test_missing_factory_avd_fails_before_doctor_and_start(self):
        env = self._prepare_repo()
        result = subprocess.run(
            ["bash", str(self.repo / "setup.sh")],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn(
            "ANDROID_AVD_PREREQUISITE: acp-worker-01 missing",
            result.stdout + result.stderr,
        )
        self.assertEqual(["handoff-in", "manage-setup"], self._milestones())


if __name__ == "__main__":
    unittest.main()
