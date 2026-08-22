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

    def _install_handoff_fakes(self, release, *, rogue_worker=False, gh_fail=False):
        log = self.root / "handoff.log"
        fake_bin = self.root / "handoff-bin"
        fake_bin.mkdir(exist_ok=True)

        fake_python = release / ".venv" / "bin" / "python"
        fake_python.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'python:%s\\n' \"$*\" >>\"${ACP_TEST_HANDOFF_LOG:?}\"\n"
            "if [[ -n \"${ACP_TEST_EXPECT_PORTABLE_CWD:-}\" && \"$*\" == *'core.factory_v2.portable_cli '* ]]; then\n"
            "  if [[ \"$(pwd -P)\" != \"${ACP_TEST_EXPECT_PORTABLE_CWD}\" ]]; then\n"
            "    printf 'PORTABLE_CLI_WRONG_CWD=%s\\n' \"$(pwd -P)\" >&2\n"
            "    exit 44\n"
            "  fi\n"
            "fi\n"
            "if [[ \"$*\" == *'core.factory_v2.portable_cli resume'* ]]; then\n"
            "  printf 'RESUME_RECONCILED leases=0 oauth=0 gated=0\\n'\n"
            "  exit 0\n"
            "fi\n"
            "if [[ \"$*\" == *'core.factory_v2.portable_cli handoff-out'* ]]; then\n"
            "  printf 'HANDOFF_OK generation=4\\n'\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake_python.chmod(0o755)

        fake_git = fake_bin / "git"
        fake_git.write_text(
            "#!/usr/bin/env bash\n"
            "case \"$*\" in\n"
            "  *remote*origin*|*remote.origin.url*) printf 'git@github.com:luongdo03x-byte/acp-affiliate-pipeline.git\\n' ;;\n"
            "  *rev-parse*HEAD*) printf 'abc123\\n' ;;\n"
            "  *branch*--show-current*) printf 'feat/account-factory-android\\n' ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)

        fake_gh = fake_bin / "gh"
        fake_gh.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'gh:%s\\n' \"$*\" >>\"${ACP_TEST_HANDOFF_LOG:?}\"\n"
            "if [[ \"${ACP_TEST_GH_FAIL:-0}\" == 1 ]]; then\n"
            "  printf 'secret-marker-must-not-escape\\n' >&2\n"
            "  exit 1\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake_gh.chmod(0o755)

        fake_pgrep = fake_bin / "pgrep"
        fake_pgrep.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'pgrep:%s\\n' \"$*\" >>\"${ACP_TEST_HANDOFF_LOG:?}\"\n"
            "if [[ \"${ACP_TEST_ROGUE_WORKER:-0}\" == 1 && \"$*\" == *account_factory_worker.py* ]]; then\n"
            "  printf '4242 python workers/account_factory_worker.py --worker-id worker:test\\n'\n"
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
            encoding="utf-8",
        )
        fake_pgrep.chmod(0o755)

        fake_adb = fake_bin / "adb"
        fake_adb.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'adb:%s\\n' \"$*\" >>\"${ACP_TEST_HANDOFF_LOG:?}\"\n"
            "exit 99\n",
            encoding="utf-8",
        )
        fake_adb.chmod(0o755)

        env = self._env()
        env["ACP_TEST_HANDOFF_LOG"] = str(log)
        env["ACP_TEST_ROGUE_WORKER"] = "1" if rogue_worker else "0"
        env["ACP_TEST_GH_FAIL"] = "1" if gh_fail else "0"
        env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
        return log, env

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

    def test_handoff_out_quiesces_resumes_then_exports_without_stopping_emulator(self):
        release = self._seed_release(ownership="ACTIVE")
        log, env = self._install_handoff_fakes(release)

        result = subprocess.run(
            ["bash", str(self.manage), "handoff-out"],
            env=env,
            text=True,
            capture_output=True,
        )

        self.assertEqual(0, result.returncode, msg=result.stdout + result.stderr)
        self.assertIn("FACTORY_STOPPED", result.stdout)
        self.assertIn("ACP_STOPPED", result.stdout)
        self.assertIn("RESUME_RECONCILED leases=0 oauth=0 gated=0", result.stdout)
        self.assertIn("HANDOFF_OK generation=4", result.stdout)

        lines = log.read_text(encoding="utf-8").splitlines()
        pgrep_indexes = [i for i, line in enumerate(lines) if line.startswith("pgrep:")]
        resume_index = next(
            i for i, line in enumerate(lines)
            if "core.factory_v2.portable_cli resume" in line
        )
        handoff_index = next(
            i for i, line in enumerate(lines)
            if "core.factory_v2.portable_cli handoff-out" in line
        )
        self.assertTrue(pgrep_indexes, lines)
        self.assertLess(max(pgrep_indexes), resume_index)
        self.assertLess(resume_index, handoff_index)
        handoff_line = lines[handoff_index]
        self.assertIn(f"--base {self.base}", handoff_line)
        self.assertIn("--repo luongdo03x-byte/acp-affiliate-pipeline", handoff_line)
        self.assertIn("--git-commit abc123", handoff_line)
        self.assertIn("--git-branch feat/account-factory-android", handoff_line)
        self.assertFalse(any(line.startswith("adb:") for line in lines), lines)

    def test_handoff_out_fails_closed_when_worker_process_remains(self):
        release = self._seed_release(ownership="ACTIVE")
        log, env = self._install_handoff_fakes(release, rogue_worker=True)

        result = subprocess.run(
            ["bash", str(self.manage), "handoff-out"],
            env=env,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("FACTORY_NOT_QUIESCENT", result.stdout + result.stderr)
        lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        self.assertFalse(
            any("core.factory_v2.portable_cli resume" in line for line in lines),
            lines,
        )
        self.assertFalse(
            any("core.factory_v2.portable_cli handoff-out" in line for line in lines),
            lines,
        )
        self.assertFalse(any(line.startswith("adb:") for line in lines), lines)

    def test_handoff_out_runs_portable_cli_from_release_when_invoked_elsewhere(self):
        release = self._seed_release(ownership="ACTIVE")
        _, env = self._install_handoff_fakes(release)
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        env["ACP_TEST_EXPECT_PORTABLE_CWD"] = str(release.resolve())

        result = subprocess.run(
            ["bash", str(self.manage), "handoff-out"],
            cwd=elsewhere,
            env=env,
            text=True,
            capture_output=True,
        )

        self.assertEqual(0, result.returncode, msg=result.stdout + result.stderr)
        self.assertIn("RESUME_RECONCILED leases=0 oauth=0 gated=0", result.stdout)
        self.assertIn("HANDOFF_OK generation=4", result.stdout)

    def test_handoff_out_github_auth_failure_happens_before_runtime_stop(self):
        release = self._seed_release(ownership="ACTIVE")
        log, env = self._install_handoff_fakes(release, gh_fail=True)

        result = subprocess.run(
            ["bash", str(self.manage), "handoff-out"],
            env=env,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("GITHUB_AUTH_REQUIRED", result.stdout + result.stderr)
        self.assertNotIn("secret-marker-must-not-escape", result.stdout + result.stderr)
        self.assertNotIn("FACTORY_STOPPED", result.stdout)
        self.assertNotIn("ACP_STOPPED", result.stdout)
        lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        self.assertTrue(any(line.startswith("gh:") for line in lines), lines)
        self.assertFalse(any(line.startswith("pgrep:") for line in lines), lines)
        self.assertFalse(
            any("core.factory_v2.portable_cli resume" in line for line in lines),
            lines,
        )
        self.assertFalse(
            any("core.factory_v2.portable_cli handoff-out" in line for line in lines),
            lines,
        )


if __name__ == "__main__":
    unittest.main()
