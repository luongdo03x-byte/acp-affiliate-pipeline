import base64
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from core.factory_v2.portable_doctor import run_portable_doctor
from core.factory_v2.schema import ensure_schema


_KEY = base64.b64encode(b"\x03" * 32).decode()
_BRANCH = "feat/account-factory-android"
_COMMIT = "a" * 40
_SECRET_MARKER = "ghp_doctor_secret_must_not_escape"


class FakeAvd:
    def list_avds(self):
        return ["acp-worker-01"]

    def list_online_devices(self):
        return ["emulator-5554"]

    def is_boot_completed(self, serial):
        return serial == "emulator-5554"


class PortableDoctorReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / "ACP"
        self.shared = self.base / "shared"
        self.var = self.shared / "var"
        self.var.mkdir(parents=True)
        self.repo_root = self.root / "repo"
        self.repo_root.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()

    def _write_executable(self, name: str, text: str):
        path = self.bin / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)
        return path

    def _install_fake_git(self, *, branch=_BRANCH, commit=_COMMIT):
        self._write_executable(
            "git",
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "case \"$*\" in\n"
            "  *'rev-parse HEAD'*) printf '%s\\n' \"${ACP_TEST_GIT_COMMIT:?}\" ;;\n"
            "  *'branch --show-current'*) printf '%s\\n' \"${ACP_TEST_GIT_BRANCH:?}\" ;;\n"
            "  *) exit 9 ;;\n"
            "esac\n",
        )
        return {
            "ACP_TEST_GIT_BRANCH": branch,
            "ACP_TEST_GIT_COMMIT": commit,
        }

    def _install_fake_gh(self, *, authenticated: bool):
        if authenticated:
            body = "printf 'authenticated\\n'; exit 0"
        else:
            body = f"printf '{_SECRET_MARKER}\\n' >&2; exit 1"
        self._write_executable(
            "gh",
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            f"{body}\n",
        )

    def _write_machine(self, *, branch=_BRANCH, commit=_COMMIT):
        self.shared.mkdir(parents=True, exist_ok=True)
        path = self.shared / "machine.json"
        path.write_text(
            json.dumps({
                "machine_id": "weekend-machine",
                "last_imported_generation": 8,
                "ownership": "ACTIVE",
                "source_git_commit": commit,
                "source_branch": branch,
            }) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)

    def _write_env(self, *, oauth=True):
        lines = [
            f"ACP_DB={self.var / 'acp-live.db'}",
            f"ACP_AVATAR_DIR={self.shared / 'avatars'}",
            f"ACP_MASTER_KEY={_KEY}",
            "ACP_PUBLIC_BASE_URL=https://factory.example.com",
        ]
        if oauth:
            lines.extend([
                "THREADS_APP_ID=test-app-id",
                "THREADS_APP_SECRET=test-app-secret",
            ])
        path = self.shared / ".env.local"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def _seed_db(self, *, completion_mode="ACP_ACTIVE"):
        db_path = self.var / "acp-live.db"
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO factory_batch(id,name,target_count,status,created_at,completion_mode) "
            "VALUES('b1','b',1,'READY','2026-08-22T00:00:00+00:00',?)",
            (completion_mode,),
        )
        conn.execute(
            """INSERT INTO factory_account(
                id,batch_id,sequence,group_no,username,display_name,stage,last_safe_stage,created_at,updated_at
            ) VALUES(
                'a1','b1',1,1,'doctor_ready','Doctor Ready','PROFILE_READY','PROFILE_READY',
                '2026-08-22T00:00:00+00:00','2026-08-22T00:00:00+00:00'
            )"""
        )
        conn.close()

    def _run(self, *, extra_env=None):
        env = os.environ.copy()
        env["PATH"] = f"{self.bin}:{env.get('PATH', '')}"
        if extra_env:
            env.update(extra_env)
        with patch.dict(os.environ, env, clear=True):
            return run_portable_doctor(
                self.base,
                self.repo_root,
                avd=FakeAvd(),
                http_probe=lambda _url: True,
            )

    @staticmethod
    def _by_name(checks):
        return {check.name: check for check in checks}

    def test_doctor_checks_git_state_from_handoff_metadata(self):
        git_env = self._install_fake_git()
        self._install_fake_gh(authenticated=True)
        self._write_machine()
        self._write_env(oauth=True)
        self._seed_db()

        checks = self._by_name(self._run(extra_env=git_env))

        self.assertIn("GIT_STATE", checks)
        self.assertTrue(checks["GIT_STATE"].ok)
        self.assertEqual("OK", checks["GIT_STATE"].code)

        mismatch_env = dict(git_env)
        mismatch_env["ACP_TEST_GIT_COMMIT"] = "b" * 40
        mismatch = self._by_name(self._run(extra_env=mismatch_env))
        self.assertFalse(mismatch["GIT_STATE"].ok)
        self.assertEqual("GIT_STATE_MISMATCH", mismatch["GIT_STATE"].code)

    def test_doctor_reports_sanitized_github_auth_failure(self):
        git_env = self._install_fake_git()
        self._install_fake_gh(authenticated=False)
        self._write_machine()
        self._write_env(oauth=True)
        self._seed_db()

        checks = self._run(extra_env=git_env)
        by_name = self._by_name(checks)

        self.assertIn("GITHUB_AUTH", by_name)
        self.assertFalse(by_name["GITHUB_AUTH"].ok)
        self.assertEqual("GITHUB_AUTH_REQUIRED", by_name["GITHUB_AUTH"].code)
        self.assertNotIn(_SECRET_MARKER, repr(checks))

    def test_oauth_config_is_required_only_for_acp_active_mode(self):
        git_env = self._install_fake_git()
        self._install_fake_gh(authenticated=True)
        self._write_machine()
        self._write_env(oauth=False)
        self._seed_db(completion_mode="ACP_ACTIVE")

        active = self._by_name(self._run(extra_env=git_env))
        self.assertIn("OAUTH_CONFIG", active)
        self.assertFalse(active["OAUTH_CONFIG"].ok)
        self.assertEqual("OAUTH_CONFIG_MISSING", active["OAUTH_CONFIG"].code)

        db_path = self.var / "acp-live.db"
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.execute("UPDATE factory_batch SET completion_mode='SOCIAL_ONLY' WHERE id='b1'")
        conn.close()

        social_only = self._by_name(self._run(extra_env=git_env))
        self.assertTrue(social_only["OAUTH_CONFIG"].ok)
        self.assertEqual("OK", social_only["OAUTH_CONFIG"].code)


if __name__ == "__main__":
    unittest.main()
