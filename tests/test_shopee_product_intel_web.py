"""Cache-aware Shopee source and web-composition contracts for Phase 3."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class _ServerSource:
    name = "manual_shopee"

    def __init__(self, metadata=None, error=None):
        self._metadata = metadata
        self._error = error

    def metadata(self, product_url):
        if self._error:
            raise self._error
        return self._metadata

    def resolve(self, value):
        return value


class CacheAwareSourceTests(unittest.TestCase):
    def setUp(self):
        from acp.core import db
        self.db = db
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "web-intel.db")
        db.init_db()
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()
        self.db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def test_successful_server_metadata_is_cached_as_server(self):
        from acp.adapters.shopee_affiliate import ProductMetadata
        from acp.core.shopee_products import get_metadata_cache
        from acp.web.shopee_product_intel import CacheAwareShopeeSource

        source = CacheAwareShopeeSource(
            _ServerSource(ProductMetadata(name="Server", current_price=123000)),
            lambda: self.db.connect(),
        )
        metadata = source.metadata("https://shopee.vn/product/1/2")
        self.assertEqual(metadata.name, "Server")
        cached = get_metadata_cache(self.conn, "https://shopee.vn/product/1/2")
        self.assertEqual(cached.source, "server")
        self.assertEqual(cached.current_price, 123000)

    def test_server_failure_uses_fresh_cache(self):
        from acp.adapters.shopee_affiliate import AffiliateImportError, ProductMetadata
        from acp.core.shopee_products import put_metadata_cache
        from acp.web.shopee_product_intel import CacheAwareShopeeSource

        put_metadata_cache(self.conn, "https://shopee.vn/product/1/2",
                           ProductMetadata(name="Cached", current_price=99000), "helper")
        source = CacheAwareShopeeSource(
            _ServerSource(error=AffiliateImportError("blocked")),
            lambda: self.db.connect(),
        )
        metadata = source.metadata("https://shopee.vn/product/1/2")
        self.assertEqual(metadata.name, "Cached")
        self.assertEqual(metadata.current_price, 99000)

    def test_stale_cache_does_not_mask_server_failure(self):
        from acp.adapters.shopee_affiliate import AffiliateImportError, ProductMetadata
        from acp.core.shopee_products import put_metadata_cache
        from acp.web.shopee_product_intel import CacheAwareShopeeSource

        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        put_metadata_cache(self.conn, "https://shopee.vn/product/1/2",
                           ProductMetadata(name="Stale"), "manual", observed_at=old)
        source = CacheAwareShopeeSource(
            _ServerSource(error=AffiliateImportError("blocked")),
            lambda: self.db.connect(),
        )
        with self.assertRaises(AffiliateImportError):
            source.metadata("https://shopee.vn/product/1/2")

    def test_resolve_cache_layer_does_not_create_product_or_post(self):
        from acp.adapters.shopee_affiliate import AffiliateImportError, ProductMetadata
        from acp.core.shopee_products import put_metadata_cache
        from acp.web.shopee_product_intel import CacheAwareShopeeSource

        put_metadata_cache(self.conn, "https://shopee.vn/product/1/2",
                           ProductMetadata(name="Cached", current_price=99000), "server")
        source = CacheAwareShopeeSource(
            _ServerSource(error=AffiliateImportError("blocked")),
            lambda: self.db.connect(),
        )
        source.metadata("https://shopee.vn/product/1/2")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM product").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM post").fetchone()[0], 0)


class ShopeeProductIntelStaticWebTests(unittest.TestCase):
    def test_feature_module_declares_cache_refresh_and_confirmation_finalize(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        body = open(os.path.join(root, "web", "shopee_product_intel.py"), encoding="utf-8").read()
        self.assertIn('/sanpham/affiliate/cache', body)
        self.assertIn('/sanpham/affiliate/refresh-price', body)
        self.assertIn("finalize_confirmed_product", body)
        self.assertNotIn("PUBLISH_POST", body)
        self.assertNotIn("INSERT INTO post", body)


if __name__ == "__main__":
    unittest.main()
