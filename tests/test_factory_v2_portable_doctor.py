import base64
import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.factory_v2.account_credentials import store_account_password
from core.factory_v2.portable_state import MachineState, write_machine_state
from core.factory_v2.schema import ensure_schema


_MODULE = "core.factory_v2.portable_doctor"
_MODULE_AVAILABLE = importlib.util.find_spec(_MODULE) is not None
_KEY_A = base64.b64encode(b"\x01" * 32).decode()
_KEY_B = base64.b64encode(b"\x02" * 32).decode()
_PASSWORD = "doctor-test-password"


class PortableDoctorModuleContractTests(unittest.TestCase):
    def test_portable_doctor_module_exists(self):
        self.assertTrue(_MODULE_AVAILABLE, "portable_doctor module missing")


class FakeAvd:
    def __init__(self, *, avds=None, online=None, booted=None):
        self.avds = list(avds if avds is not None else ["acp-worker-01"])
        self.online = list(online if online is not None else ["emulator-5554"])
        self.booted = set(booted if booted is not None else ["emulator-5554"])

    def list_avds(self):
        return list(self.avds)

    def list_online_devices(self):
        return list(self.online)

    def is_boot_completed(self, serial):
        return serial in self.booted


@unittest.skipUnless(_MODULE_AVAILABLE, "portable_doctor module not implemented yet")
class PortableDoctorTests(unittest.TestCase):
    def setUp(self):
        from core.factory_v2.portable_doctor import require_portable_doctor, run_portable_doctor

        self.run_portable_doctor = run_portable_doctor
        self.require_portable_doctor = require_portable_doctor
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / "ACP"
        self.shared = self.base / "shared"
        self.var = self.shared / "var"
        self.var.mkdir(parents=True)
        self.repo_root = self.root / "repo"
        self.repo_root.mkdir()
        self.machine_file = self.shared / "machine.json"
        write_machine_state(self.machine_file, MachineState("machine-1", 3, "ACTIVE"))

    def _env_text(self, *, key=_KEY_A, public_base_url="https://factory.example.com"):
        lines = [
            f"ACP_DB={self.var / 'acp-live.db'}",
            f"ACP_AVATAR_DIR={self.shared / 'avatars'}",
        ]
        if key is not None:
            lines.append(f"ACP_MASTER_KEY={key}")
        if public_base_url is not None:
            lines.append(f"ACP_PUBLIC_BASE_URL={public_base_url}")
        lines.append("THREADS_APP_ID=test-app-id")
        lines.append("THREADS_APP_SECRET=test-app-secret")
        return "\n".join(lines) + "\n"

    def _write_env(self, *, key=_KEY_A, mode=0o600, public_base_url="https://factory.example.com"):
        env_path = self.shared / ".env.local"
        env_path.write_text(
            self._env_text(key=key, public_base_url=public_base_url),
            encoding="utf-8",
        )
        env_path.chmod(mode)
        return env_path

    def _seed_db(self, *, stage="PROFILE_READY", completion_mode="ACP_ACTIVE", credential=True):
        db_path = self.var / "acp-live.db"
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        with patch.dict(os.environ, {"ACP_MASTER_KEY": _KEY_A}):
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
                    'a1','b1',1,1,'doctor_user','Doctor User',?,?,
                    '2026-08-22T00:00:00+00:00','2026-08-22T00:00:00+00:00'
                )""",
                (stage, stage if stage in {"PROFILE_READY", "IG_CREATED", "THREADS_CREATED"} else "PROFILE_READY"),
            )
            if credential:
                store_account_password(conn, "a1", _PASSWORD)
        conn.close()
        return db_path

    @staticmethod
    def _checks_by_name(checks):
        return {check.name: check for check in checks}

    def test_matching_credential_decrypt_is_ok_without_secret_output(self):
        self._write_env(key=_KEY_A)
        self._seed_db()

        checks = self.run_portable_doctor(
            self.base,
            self.repo_root,
            avd=FakeAvd(),
            http_probe=lambda url: True,
        )
        by_name = self._checks_by_name(checks)

        self.assertTrue(by_name["CREDENTIAL_DECRYPT"].ok)
        self.assertEqual("OK", by_name["CREDENTIAL_DECRYPT"].code)
        rendered = repr(checks)
        self.assertNotIn(_KEY_A, rendered)
        self.assertNotIn(_PASSWORD, rendered)
        self.assertNotIn("test-app-secret", rendered)

    def test_wrong_master_key_returns_safe_credential_failure(self):
        self._write_env(key=_KEY_B)
        self._seed_db()

        checks = self.run_portable_doctor(
            self.base,
            self.repo_root,
            avd=FakeAvd(),
            http_probe=lambda url: True,
        )
        check = self._checks_by_name(checks)["CREDENTIAL_DECRYPT"]

        self.assertFalse(check.ok)
        self.assertEqual("CREDENTIAL_DECRYPT_FAILED", check.code)
        rendered = repr(checks)
        self.assertNotIn(_KEY_A, rendered)
        self.assertNotIn(_KEY_B, rendered)
        self.assertNotIn(_PASSWORD, rendered)

    def test_env_permissions_master_key_and_ownership_fail_closed(self):
        self._write_env(key=None, mode=0o644)
        self._seed_db(credential=False)
        write_machine_state(self.machine_file, MachineState("machine-1", 3, "HANDED_OFF"))

        checks = self._checks_by_name(self.run_portable_doctor(
            self.base,
            self.repo_root,
            avd=FakeAvd(),
            http_probe=lambda url: True,
        ))

        self.assertEqual("ENV_PERMISSIONS_INVALID", checks["ENV_PERMISSIONS"].code)
        self.assertEqual("ACP_MASTER_KEY_MISSING", checks["ACP_MASTER_KEY"].code)
        self.assertEqual("MACHINE_HANDED_OFF", checks["OWNERSHIP"].code)

    def test_invalid_sqlite_is_reported_without_schema_mutation(self):
        self._write_env()
        db_path = self.var / "acp-live.db"
        db_path.write_bytes(b"not sqlite")

        checks = self._checks_by_name(self.run_portable_doctor(
            self.base,
            self.repo_root,
            avd=FakeAvd(),
            http_probe=lambda url: True,
        ))

        self.assertFalse(checks["SQLITE"].ok)
        self.assertEqual("SQLITE_INTEGRITY_FAILED", checks["SQLITE"].code)

    def test_missing_or_unbooted_factory_avd_is_reported(self):
        self._write_env()
        self._seed_db(credential=False)

        missing = self._checks_by_name(self.run_portable_doctor(
            self.base,
            self.repo_root,
            avd=FakeAvd(avds=[], online=[], booted=[]),
            http_probe=lambda url: True,
        ))
        self.assertEqual("AVD_MISSING", missing["AVD"].code)

        unbooted = self._checks_by_name(self.run_portable_doctor(
            self.base,
            self.repo_root,
            avd=FakeAvd(avds=["acp-worker-01"], online=["emulator-5554"], booted=[]),
            http_probe=lambda url: True,
        ))
        self.assertEqual("AVD_NOT_BOOTED", unbooted["AVD"].code)

    def test_callback_probe_is_required_for_threads_created_acp_active_account(self):
        self._write_env(public_base_url="https://factory.example.com")
        self._seed_db(stage="THREADS_CREATED", completion_mode="ACP_ACTIVE", credential=False)
        probed = []

        checks = self._checks_by_name(self.run_portable_doctor(
            self.base,
            self.repo_root,
            avd=FakeAvd(),
            http_probe=lambda url: probed.append(url) or False,
        ))

        self.assertEqual(["https://factory.example.com"], probed)
        self.assertFalse(checks["CALLBACK"].ok)
        self.assertEqual("CALLBACK_UNREACHABLE", checks["CALLBACK"].code)

    def test_require_portable_doctor_raises_first_safe_failure_code(self):
        self._write_env(key=None)
        self._seed_db(credential=False)

        with self.assertRaisesRegex(
            RuntimeError,
            r"^PORTABLE_DOCTOR_FAILED:ACP_MASTER_KEY_MISSING$",
        ):
            self.require_portable_doctor(
                self.base,
                self.repo_root,
                avd=FakeAvd(),
                http_probe=lambda url: True,
            )


if __name__ == "__main__":
    unittest.main()
