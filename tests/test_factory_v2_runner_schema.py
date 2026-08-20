import sqlite3
import unittest

from core.factory_v2.schema import ensure_schema


class RunnerSchemaTests(unittest.TestCase):
    def test_local_worker_does_not_require_avd_name(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        conn.execute(
            """INSERT INTO factory_worker
               (id, runner_type, device_id, device_name, state)
               VALUES ('phone-1','LOCAL_DEVICE','android-id-1','Pixel','READY')"""
        )
        row = conn.execute(
            "SELECT * FROM factory_worker WHERE id='phone-1'"
        ).fetchone()
        self.assertIsNone(row["avd_name"])
        self.assertEqual("LOCAL_DEVICE", row["runner_type"])

    def test_existing_avd_worker_is_backfilled_remote_avd(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE factory_worker (
                id TEXT PRIMARY KEY,
                avd_name TEXT NOT NULL UNIQUE,
                adb_serial TEXT UNIQUE,
                state TEXT NOT NULL,
                current_account_id TEXT,
                current_job_id TEXT,
                pid INTEGER,
                started_at TEXT,
                last_heartbeat_at TEXT,
                last_progress_at TEXT,
                processed_count INTEGER NOT NULL DEFAULT 0,
                recovery_count INTEGER NOT NULL DEFAULT 0,
                estimated_ram_mb INTEGER,
                current_ram_mb INTEGER,
                current_cpu_percent REAL,
                draining INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO factory_worker(id,avd_name,state) VALUES('w1','acp-worker-01','READY')"
        )

        ensure_schema(conn)

        row = conn.execute("SELECT * FROM factory_worker WHERE id='w1'").fetchone()
        self.assertEqual("REMOTE_AVD", row["runner_type"])
        self.assertEqual("acp-worker-01", row["avd_name"])

    def test_factory_job_has_runner_type_and_account_has_execution_target(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)

        job_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(factory_job)").fetchall()
        }
        account_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(factory_account)").fetchall()
        }
        self.assertIn("runner_type", job_cols)
        self.assertIn("execution_target", account_cols)


if __name__ == "__main__":
    unittest.main()
