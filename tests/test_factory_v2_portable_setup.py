import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class FactoryV2PortableSetupTests(unittest.TestCase):
    def setUp(self):
        self.source_root = Path(__file__).resolve().parents[1]
        self.source_setup = self.source_root / "setup.sh"
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.base = self.root / "ACP"
        self.log = self.root / "setup-order.log"

    def _require_setup(self):
        if not self.source_setup.exists():
            self.skipTest("setup.sh not implemented yet")

    def _write_executable(self, path: Path, text: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def _prepare_repo(self, *, with_venv: bool, same_generation: bool = False):
        self._require_setup()
        self.repo.mkdir(parents=True)
        shutil.copy2(self.source_setup, self.repo / "setup.sh")
        (self.repo / "requirements.txt").write_text("\n", encoding="utf-8")

        python_hook = self.root / "fake-python"
        self._write_executable(
            python_hook,
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "printf 'python:%s\\n' \"$*\" >>\"${ACP_TEST_SETUP_LOG:?}\"\n"
            "case \"$*\" in\n"
            "  *'-m pip install'*) exit 0 ;;\n"
            "  *'core.factory_v2.portable_cli handoff-in'*)\n"
            "    printf 'handoff-in\\n' >>\"${ACP_TEST_SETUP_LOG:?}\"\n"
            "    if [[ \"${ACP_TEST_SAME_GENERATION:-0}\" == 1 ]]; then\n"
            "      printf 'IMPORT_OK generation=8 already-current\\n'\n"
            "    else\n"
            "      printf 'IMPORT_OK generation=8\\n'\n"
            "    fi\n"
            "    exit 0 ;;\n"
            "  *'core.factory_v2.portable_cli doctor'*)\n"
            "    printf 'doctor\\n' >>\"${ACP_TEST_SETUP_LOG:?}\"\n"
            "    if [[ \"${ACP_TEST_DOCTOR_FAIL:-0}\" == 1 ]]; then\n"
            "      printf 'PORTABLE_DOCTOR_FAILED:AVD_MISSING\\n' >&2\n"
            "      exit 17\n"
            "    fi\n"
            "    printf 'DOCTOR_OK\\n'\n"
            "    exit 0 ;;\n"
            "  *'core.factory_v2.portable_cli resume'*)\n"
            "    printf 'resume\\n' >>\"${ACP_TEST_SETUP_LOG:?}\"\n"
            "    printf 'RESUME_RECONCILED leases=0 oauth=0 gated=0\\n'\n"
            "    exit 0 ;;\n"
            "esac\n"
            "exit 0\n",
        )

        if with_venv:
            venv_python = self.repo / ".venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True)
            shutil.copy2(python_hook, venv_python)
            venv_python.chmod(0o755)

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

        fake_bin = self.root / "bin"
        fake_python3 = fake_bin / "python3"
        self._write_executable(
            fake_python3,
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "if [[ \"${1:-}\" == '-m' && \"${2:-}\" == 'venv' ]]; then\n"
            "  printf 'bootstrap-venv\\n' >>\"${ACP_TEST_SETUP_LOG:?}\"\n"
            "  mkdir -p \"$3/bin\"\n"
            "  cp \"${ACP_TEST_PYTHON_HOOK:?}\" \"$3/bin/python\"\n"
            "  chmod +x \"$3/bin/python\"\n"
            "  exit 0\n"
            "fi\n"
            "exit 91\n",
        )

        # setup.sh should not need to call these directly in the orchestration
        # tests because portable_cli owns the real GitHub/Android probes.
        for name in ("gh", "adb", "emulator"):
            self._write_executable(
                fake_bin / name,
                "#!/usr/bin/env bash\n"
                "printf 'unexpected-tool:%s\\n' \"$0 $*\" >>\"${ACP_TEST_SETUP_LOG:?}\"\n"
                "exit 92\n",
            )

        shared = self.base / "shared"
        shared.mkdir(parents=True, exist_ok=True)
        if same_generation:
            (shared / "machine.json").write_text(
                json.dumps({
                    "machine_id": "weekend-machine",
                    "last_imported_generation": 8,
                    "ownership": "ACTIVE",
                }) + "\n",
                encoding="utf-8",
            )

        env = os.environ.copy()
        env["ACP_BASE"] = str(self.base)
        env["ACP_TEST_SETUP_LOG"] = str(self.log)
        env["ACP_TEST_PYTHON_HOOK"] = str(python_hook)
        env["ACP_TEST_SAME_GENERATION"] = "1" if same_generation else "0"
        env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
        return env

    def _milestones(self):
        if not self.log.exists():
            return []
        wanted = {
            "bootstrap-venv",
            "handoff-in",
            "manage-setup",
            "doctor",
            "resume",
            "factory-start",
        }
        return [
            line for line in self.log.read_text(encoding="utf-8").splitlines()
            if line in wanted
        ]

    def test_setup_script_exists(self):
        self.assertTrue(self.source_setup.exists(), "setup.sh missing")

    def test_first_clone_orders_restore_before_manage_setup_and_start(self):
        env = self._prepare_repo(with_venv=False)
        result = subprocess.run(
            ["bash", str(self.repo / "setup.sh")],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
        )

        self.assertEqual(0, result.returncode, msg=result.stdout + result.stderr)
        self.assertEqual(
            [
                "bootstrap-venv",
                "handoff-in",
                "manage-setup",
                "doctor",
                "resume",
                "factory-start",
            ],
            self._milestones(),
        )

    def test_same_generation_still_doctors_resumes_and_starts(self):
        env = self._prepare_repo(with_venv=True, same_generation=True)
        result = subprocess.run(
            ["bash", str(self.repo / "setup.sh")],
            cwd=self.root / "repo",
            env=env,
            text=True,
            capture_output=True,
        )

        self.assertEqual(0, result.returncode, msg=result.stdout + result.stderr)
        self.assertIn("IMPORT_OK generation=8 already-current", result.stdout)
        self.assertEqual(
            ["handoff-in", "manage-setup", "doctor", "resume", "factory-start"],
            self._milestones(),
        )

    def test_doctor_failure_blocks_resume_and_factory_start(self):
        env = self._prepare_repo(with_venv=True, same_generation=True)
        env["ACP_TEST_DOCTOR_FAIL"] = "1"
        result = subprocess.run(
            ["bash", str(self.repo / "setup.sh")],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("PORTABLE_DOCTOR_FAILED:AVD_MISSING", result.stdout + result.stderr)
        milestones = self._milestones()
        self.assertEqual(["handoff-in", "manage-setup", "doctor"], milestones)
        self.assertNotIn("resume", milestones)
        self.assertNotIn("factory-start", milestones)


if __name__ == "__main__":
    unittest.main()
