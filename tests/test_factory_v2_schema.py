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
