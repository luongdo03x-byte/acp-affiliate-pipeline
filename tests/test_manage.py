#!/usr/bin/env python3
"""Integration tests for manage.sh using an isolated fake ACP installation."""

from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANAGE = REPO / "manage.sh"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def fake_run_py() -> str:
    return textwrap.dedent(
        """\
        #!/usr/bin/env python3
        import os
        import sys
        from http.server import BaseHTTPRequestHandler, HTTPServer

        cmd = sys.argv[1] if len(sys.argv) > 1 else ""
        if cmd == "serve":
            port = int(os.environ.get("PORT", "5000"))
            class H(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")
                def log_message(self, *args):
                    pass
            HTTPServer(("127.0.0.1", port), H).serve_forever()
        elif cmd == "doctor":
            print("Sẵn sàng.")
        else:
            raise SystemExit(0)
        """
    )


def create_fake_release(root: Path, version: str, port: int) -> Path:
    release_root = root / "releases" / version
    app = release_root / "acp"
    (app / "tests").mkdir(parents=True, exist_ok=True)
    (app / "core").mkdir(parents=True, exist_ok=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (app / "tests" / "test_pipeline.py").write_text(
        "print('37 đạt, 0 hỏng')\n", encoding="utf-8"
    )
    (app / "tests" / "test_pilot.py").write_text(
        "print('101 đạt, 0 hỏng')\n", encoding="utf-8"
    )
    (app / "tests" / "test_seeding.py").write_text(
        "print('SEEDING_TEST_OK')\n", encoding="utf-8"
    )
    (app / "tests" / "test_seeding_web.py").write_text(
        "import os\n"
        "assert os.environ.get('ACP_ENV') == 'test', os.environ.get('ACP_ENV')\n"
        "print('SEEDING_WEB_TEST_OK')\n", encoding="utf-8"
    )
    (app / "core" / "__init__.py").write_text("", encoding="utf-8")
    (app / "core" / "db.py").write_text(
        "def init_db():\n    print('SCHEMA_OK')\n", encoding="utf-8"
    )
    (app / "run.py").write_text(fake_run_py(), encoding="utf-8")
    (app / "requirements.txt").write_text("", encoding="utf-8")
    (app / ".env.local").write_text(
        f"export ACP_DB='{root / 'shared' / 'var' / 'acp-live.db'}'\n"
        f"export PORT='{port}'\n"
        "export ACP_ADAPTER='mock'\n"
        "export ACP_SOURCE='mock'\n",
        encoding="utf-8",
    )
    (app / "var").mkdir(exist_ok=True)
    (app / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    os.symlink(sys.executable, app / ".venv" / "bin" / "python")
    return app


class ManageScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="acp-manage-test-"))
        self.base = self.tmp / "ACP"
        (self.base / "shared" / "var").mkdir(parents=True)
        (self.base / "shared" / "run").mkdir(parents=True)
        (self.base / "logs").mkdir(parents=True)
        self.port = free_port()
        self.v1 = create_fake_release(self.base, "1.0", self.port)
        db = self.base / "shared" / "var" / "acp-live.db"
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE marker (value TEXT)")
            conn.execute("INSERT INTO marker VALUES ('keep-me')")
        shared_env = self.base / "shared" / ".env.local"
        shared_env.write_text((self.v1 / ".env.local").read_text(encoding="utf-8"), encoding="utf-8")
        shutil.rmtree(self.v1 / "var")
        os.symlink(self.base / "shared" / "var", self.v1 / "var")
        (self.v1 / ".env.local").unlink()
        os.symlink(shared_env, self.v1 / ".env.local")
        os.symlink(self.v1, self.base / "acp")

    def tearDown(self) -> None:
        if MANAGE.exists():
            subprocess.run(
                [str(MANAGE), "stop"],
                env={**os.environ, "ACP_BASE": str(self.base)},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_manage(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(MANAGE), *args],
            env={**os.environ, "ACP_BASE": str(self.base)},
            text=True,
            capture_output=True,
            check=check,
            timeout=120,
        )

    def test_status_reports_stopped_release(self) -> None:
        r = self.run_manage("status")
        self.assertIn("1.0", r.stdout)
        self.assertIn("STOPPED", r.stdout)

    def test_start_then_stop_manages_app_process(self) -> None:
        started = self.run_manage("start")
        self.assertIn("ACP_STARTED", started.stdout)
        status = self.run_manage("status")
        self.assertIn("RUNNING", status.stdout)
        stopped = self.run_manage("stop")
        self.assertIn("ACP_STOPPED", stopped.stdout)
        status2 = self.run_manage("status")
        self.assertIn("STOPPED", status2.stdout)

    def test_invalid_command_is_rejected(self) -> None:
        r = self.run_manage("wat", check=False)
        self.assertNotEqual(0, r.returncode)
        self.assertIn("Cách dùng", r.stdout + r.stderr)

    def test_test_command_runs_seeding_suites(self) -> None:
        result = self.run_manage("test")
        self.assertIn("SEEDING_TEST_OK", result.stdout)
        self.assertIn("SEEDING_WEB_TEST_OK", result.stdout)
        self.assertIn("TEST_OK", result.stdout)

    def make_upgrade_zip(self, version: str) -> Path:
        src_root = self.tmp / f"zip-{version}"
        app = create_fake_release(src_root, version, free_port())
        shutil.rmtree(app / ".venv")
        zip_path = self.tmp / f"acp_{version}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in app.rglob("*"):
                if path.is_file():
                    zf.write(path, Path("acp") / path.relative_to(app))
        return zip_path

    def test_upgrade_switches_release_preserves_shared_db_and_rollback_restores_previous(self) -> None:
        upgrade_zip = self.make_upgrade_zip("2.0")
        upgraded = self.run_manage("upgrade", str(upgrade_zip), "2.0")
        self.assertIn("UPGRADE_OK", upgraded.stdout)
        self.assertEqual((self.base / "releases" / "2.0" / "acp").resolve(), (self.base / "acp").resolve())
        self.assertEqual((self.base / "shared" / "var").resolve(), (self.base / "acp" / "var").resolve())
        with sqlite3.connect(self.base / "shared" / "var" / "acp-live.db") as conn:
            self.assertEqual("keep-me", conn.execute("SELECT value FROM marker").fetchone()[0])
        rolled = self.run_manage("rollback")
        self.assertIn("ROLLBACK_OK", rolled.stdout)
        self.assertEqual(self.v1.resolve(), (self.base / "acp").resolve())


if __name__ == "__main__":
    unittest.main(verbosity=2)
