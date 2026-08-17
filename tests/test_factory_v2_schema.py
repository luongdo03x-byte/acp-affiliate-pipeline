import sqlite3
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


if __name__ == "__main__":
    unittest.main()
