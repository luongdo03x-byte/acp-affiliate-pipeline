"""Focused tests for Shopee Product Intelligence Phase 3.

Run from the directory containing the ``acp`` package:
    ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_product_intel -v
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class _DbCase(unittest.TestCase):
    def setUp(self):
        from acp.core import db
        self.db = db
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "intel.db")
        db.init_db()
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()
        self.db.DB_PATH = self.old_path
        self.tmp.cleanup()


class ShopeeProductIntelSchemaTests(_DbCase):
    def test_init_creates_metadata_cache_and_price_source(self):
        cache_cols = {row[1] for row in self.conn.execute(
            "PRAGMA table_info(shopee_metadata_cache)").fetchall()}
        self.assertTrue({
            "shop_id", "item_id", "product_id", "name", "current_price",
            "original_price", "image_url", "shop_name", "source",
            "observed_at", "updated_at",
        } <= cache_cols)

        price_cols = {row[1] for row in self.conn.execute(
            "PRAGMA table_info(product_price_history)").fetchall()}
        self.assertIn("source", price_cols)

        indexes = {row[1] for row in self.conn.execute(
            "PRAGMA index_list(shopee_metadata_cache)").fetchall()}
        self.assertIn("idx_shopee_metadata_cache_product", indexes)

    def test_migrate_is_idempotent_for_existing_database(self):
        first = self.db.migrate(self.conn)
        second = self.db.migrate(self.conn)
        self.assertEqual(first, [])
        self.assertEqual(second, [])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='shopee_metadata_cache'"
        ).fetchone()[0], 1)


class ShopeeMetadataCacheTests(_DbCase):
    def test_slug_and_canonical_urls_share_one_cache_row(self):
        from acp.adapters.shopee_affiliate import ProductMetadata
        from acp.core.shopee_products import get_metadata_cache, put_metadata_cache

        put_metadata_cache(
            self.conn,
            "https://shopee.vn/Tai-nghe-i.123.456?sp_atk=x",
            ProductMetadata(name="Tai nghe", current_price=199000),
            "server",
        )
        cached = get_metadata_cache(self.conn, "https://shopee.vn/product/123/456")
        self.assertIsNotNone(cached)
        self.assertEqual((cached.shop_id, cached.item_id), ("123", "456"))
        self.assertEqual(cached.name, "Tai nghe")
        self.assertEqual(cached.current_price, 199000)
        self.assertEqual(cached.source, "server")
        self.assertTrue(cached.is_fresh)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM shopee_metadata_cache").fetchone()[0], 1)

    def test_cache_upsert_preserves_latest_source_and_values(self):
        from acp.adapters.shopee_affiliate import ProductMetadata
        from acp.core.shopee_products import get_metadata_cache, put_metadata_cache

        put_metadata_cache(self.conn, "https://shopee.vn/product/1/2",
                           ProductMetadata(name="Cũ", current_price=100000), "server")
        put_metadata_cache(self.conn, "https://shopee.vn/product/1/2",
                           ProductMetadata(name="Mới", current_price=90000, shop="Shop A"), "helper")
        cached = get_metadata_cache(self.conn, "https://shopee.vn/product/1/2")
        self.assertEqual(cached.name, "Mới")
        self.assertEqual(cached.current_price, 90000)
        self.assertEqual(cached.shop, "Shop A")
        self.assertEqual(cached.source, "helper")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM shopee_metadata_cache").fetchone()[0], 1)

    def test_partial_metadata_is_cached_without_fabricating_fields(self):
        from acp.adapters.shopee_affiliate import ProductMetadata
        from acp.core.shopee_products import get_metadata_cache, put_metadata_cache

        put_metadata_cache(self.conn, "https://shopee.vn/product/10/20",
                           ProductMetadata(name="Chỉ có tên"), "manual")
        cached = get_metadata_cache(self.conn, "https://shopee.vn/product/10/20")
        self.assertEqual(cached.name, "Chỉ có tên")
        self.assertIsNone(cached.current_price)
        self.assertIsNone(cached.original_price)
        self.assertIsNone(cached.image_url)
        self.assertIsNone(cached.shop)

    def test_freshness_is_explicit_at_24_hours(self):
        from acp.adapters.shopee_affiliate import ProductMetadata
        from acp.core.shopee_products import get_metadata_cache, put_metadata_cache

        observed = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
        put_metadata_cache(self.conn, "https://shopee.vn/product/7/8",
                           ProductMetadata(name="X"), "server", observed_at=observed.isoformat())

        fresh = get_metadata_cache(
            self.conn, "https://shopee.vn/product/7/8",
            now_dt=observed + timedelta(hours=23, minutes=59))
        stale = get_metadata_cache(
            self.conn, "https://shopee.vn/product/7/8",
            now_dt=observed + timedelta(hours=24, seconds=1))
        self.assertTrue(fresh.is_fresh)
        self.assertFalse(stale.is_fresh)

    def test_invalid_source_and_empty_metadata_are_rejected(self):
        from acp.adapters.shopee_affiliate import ProductMetadata
        from acp.core.shopee_products import ShopeeProductError, put_metadata_cache

        with self.assertRaises(ShopeeProductError):
            put_metadata_cache(self.conn, "https://shopee.vn/product/1/2",
                               ProductMetadata(name="X"), "cookie")
        with self.assertRaises(ShopeeProductError):
            put_metadata_cache(self.conn, "https://shopee.vn/product/1/2",
                               ProductMetadata(), "server")


if __name__ == "__main__":
    unittest.main()
