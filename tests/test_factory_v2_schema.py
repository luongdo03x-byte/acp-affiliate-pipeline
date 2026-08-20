import os
import sqlite3
import tempfile
import unittest

from core.factory_v2.schema import ensure_schema


class FactoryV2SchemaTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def tearDown(self):
        self.conn.close()

    def test_schema_creates_required_tables(self):
        ensure_schema(self.conn)
        names = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        self.assertTrue({
            "factory_batch", "factory_account", "factory_worker",
            "factory_job", "factory_checkpoint", "factory_resource_sample",
        } <= names)

    def test_one_active_job_per_account(self):
        ensure_schema(self.conn)
        indexes = {r[1] for r in self.conn.execute("PRAGMA index_list(factory_job)")}
        self.assertIn("uq_factory_job_active_account", indexes)

    def test_schema_has_threads_tester_milestone_columns(self):
        ensure_schema(self.conn)
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(factory_account)")}
        self.assertIn("tester_invited_at", columns)
        self.assertIn("tester_accepted_at", columns)

    def test_schema_migrates_existing_factory_account_without_losing_row(self):
        self.conn.executescript("""
            CREATE TABLE factory_batch (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                target_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE factory_account (
                id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                group_no INTEGER NOT NULL,
                username TEXT NOT NULL,
                display_name TEXT NOT NULL,
                stage TEXT NOT NULL,
                last_safe_stage TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO factory_batch (id,name,target_count,status,created_at)
            VALUES ('b1','Legacy batch',1,'READY','2026-08-19T00:00:00+00:00');
            INSERT INTO factory_account
                (id,batch_id,sequence,group_no,username,display_name,stage,last_safe_stage,created_at,updated_at)
            VALUES
                ('a1','b1',1,1,'legacy.user','Legacy','THREADS_CREATED','THREADS_CREATED',
                 '2026-08-19T00:00:00+00:00','2026-08-19T00:00:00+00:00');
        """)

        ensure_schema(self.conn)

        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(factory_account)")}
        row = self.conn.execute(
            "SELECT username,tester_invited_at,tester_accepted_at FROM factory_account WHERE id='a1'"
        ).fetchone()
        self.assertIn("tester_invited_at", columns)
        self.assertIn("tester_accepted_at", columns)
        self.assertEqual("legacy.user", row["username"])
        self.assertIsNone(row["tester_invited_at"])
        self.assertIsNone(row["tester_accepted_at"])

    def test_init_db_is_idempotent_and_preserves_factory_rows(self):
        from core import db

        with tempfile.TemporaryDirectory() as tmp:
            old_path = db.DB_PATH
            db.DB_PATH = os.path.join(tmp, "acp.db")
            try:
                db.init_db()
                conn = db.connect()
                conn.execute(
                    "INSERT INTO factory_batch (id,name,target_count,status,created_at) VALUES (?,?,?,?,?)",
                    ("b1", "Batch", 1, "READY", "2026-08-17T00:00:00+00:00"),
                )
                conn.close()
                db.init_db()
                conn = db.connect()
                self.assertEqual(
                    1,
                    conn.execute("SELECT COUNT(*) FROM factory_batch WHERE id='b1'").fetchone()[0],
                )
                conn.close()
            finally:
                db.DB_PATH = old_path


if __name__ == "__main__":
    unittest.main()
