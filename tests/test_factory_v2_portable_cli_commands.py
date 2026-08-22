import importlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


_MODULE = "core.factory_v2.portable_cli"


class PortableCliCommandTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module(_MODULE)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _remaining_api(self):
        for name in ("doctor", "resume", "main"):
            self.assertTrue(hasattr(self.module, name), f"{name} missing")
        return self.module.doctor, self.module.resume, self.module.main

    def test_remaining_cli_api_exists(self):
        self._remaining_api()

    def test_doctor_emits_only_stable_success_line(self):
        doctor, _, _ = self._remaining_api()
        output = io.StringIO()
        calls = []

        def checker(base, repo_root):
            calls.append((Path(base), Path(repo_root)))

        doctor(
            base=self.root / "ACP",
            repo_root=self.root / "repo",
            checker=checker,
            out=output,
        )

        self.assertEqual(
            [(self.root / "ACP", self.root / "repo")],
            calls,
        )
        self.assertEqual("DOCTOR_OK\n", output.getvalue())

    def test_doctor_failure_does_not_emit_success_or_secret(self):
        doctor, _, _ = self._remaining_api()
        output = io.StringIO()
        secret = "must-not-appear"

        def checker(_base, _repo_root):
            raise RuntimeError("PORTABLE_DOCTOR_FAILED:CREDENTIAL_DECRYPT_FAILED")

        with self.assertRaisesRegex(
            RuntimeError,
            r"^PORTABLE_DOCTOR_FAILED:CREDENTIAL_DECRYPT_FAILED$",
        ):
            doctor(
                base=self.root / "ACP",
                repo_root=self.root / "repo",
                checker=checker,
                out=output,
            )

        self.assertEqual("", output.getvalue())
        self.assertNotIn(secret, output.getvalue())

    def test_resume_opens_live_db_once_and_emits_sanitized_counts(self):
        _, resume, _ = self._remaining_api()
        base = self.root / "ACP"
        db_path = base / "shared" / "var" / "acp-live.db"
        db_path.parent.mkdir(parents=True)
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        conn.execute("INSERT INTO marker(value) VALUES ('live')")
        conn.commit()
        conn.close()

        output = io.StringIO()
        calls = []

        def reconciler(conn, now_iso):
            calls.append((conn.execute("SELECT value FROM marker").fetchone()[0], now_iso))
            return {
                "leases_reconciled": 2,
                "oauth_reconciled": 1,
                "oauth_gated": 3,
                "secret": "must-not-appear",
            }

        result = resume(
            base=base,
            now_iso="2026-08-22T12:00:00+00:00",
            reconciler=reconciler,
            out=output,
        )

        self.assertEqual([("live", "2026-08-22T12:00:00+00:00")], calls)
        self.assertEqual(
            {"leases_reconciled": 2, "oauth_reconciled": 1, "oauth_gated": 3, "secret": "must-not-appear"},
            result,
        )
        self.assertEqual(
            "RESUME_RECONCILED leases=2 oauth=1 gated=3\n",
            output.getvalue(),
        )
        self.assertNotIn("must-not-appear", output.getvalue())

    def test_main_dispatches_doctor_and_resume_without_extra_output(self):
        _, _, main = self._remaining_api()
        base = self.root / "ACP"
        repo_root = self.root / "repo"

        with patch.object(self.module, "doctor") as doctor_mock:
            rc = main([
                "doctor",
                "--base", str(base),
                "--repo-root", str(repo_root),
            ])
        self.assertEqual(0, rc)
        doctor_mock.assert_called_once_with(base=base, repo_root=repo_root)

        with patch.object(self.module, "resume") as resume_mock:
            rc = main(["resume", "--base", str(base)])
        self.assertEqual(0, rc)
        resume_mock.assert_called_once_with(base=base)

    def test_main_dispatches_handoff_out_and_handoff_in(self):
        _, _, main = self._remaining_api()
        base = self.root / "ACP"

        with patch.object(self.module, "handoff_out") as handoff_out_mock:
            rc = main([
                "handoff-out",
                "--base", str(base),
                "--repo", "o/r",
                "--git-commit", "abc123",
                "--git-branch", "feat/account-factory-android",
            ])
        self.assertEqual(0, rc)
        handoff_out_mock.assert_called_once_with(
            base=base,
            repo="o/r",
            git_commit="abc123",
            git_branch="feat/account-factory-android",
        )

        with patch("socket.gethostname", return_value="weekend-machine"):
            with patch.object(self.module, "handoff_in") as handoff_in_mock:
                rc = main([
                    "handoff-in",
                    "--base", str(base),
                    "--repo", "o/r",
                ])
        self.assertEqual(0, rc)
        handoff_in_mock.assert_called_once_with(
            base=base,
            repo="o/r",
            machine_id="weekend-machine",
        )


if __name__ == "__main__":
    unittest.main()
