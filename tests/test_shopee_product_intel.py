"""Focused tests for Shopee Product Intelligence Phase 3.

Run from the directory containing the ``acp`` package:
    ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_product_intel -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class ShopeeProductIntelSchemaTests(unittest.TestCase):
    def setUp(self):
        from acp.core import db
        self.db = db
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "intel.db")

    def tearDown(self):
        self.db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def test_init_creates_metadata_cache_and_price_source(self):
        self.db.init_db()
        conn = self.db.connect()
        try:
            cache_cols = {row[1] for row in conn.execute(
                "PRAGMA table_info(shopee_metadata_cache)").fetchall()}
            self.assertTrue({
                "shop_id", "item_id", "product_id", "name", "current_price",
                "original_price", "image_url", "shop_name", "source",
                "observed_at", "updated_at",
            } <= cache_cols)

            price_cols = {row[1] for row in conn.execute(
                "PRAGMA table_info(product_price_history)").fetchall()}
            self.assertIn("source", price_cols)

            indexes = {row[1] for row in conn.execute(
                "PRAGMA index_list(shopee_metadata_cache)").fetchall()}
            self.assertIn("idx_shopee_metadata_cache_product", indexes)
        finally:
            conn.close()

    def test_migrate_is_idempotent_for_existing_database(self):
        self.db.init_db()
        conn = self.db.connect()
        try:
            first = self.db.migrate(conn)
            second = self.db.migrate(conn)
            self.assertEqual(first, [])
            self.assertEqual(second, [])
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='shopee_metadata_cache'"
            ).fetchone()[0], 1)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
