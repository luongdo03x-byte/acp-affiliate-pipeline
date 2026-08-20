"""Confirmed Shopee Product upsert and price-observation tests."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class _ManualShopeeSource:
    name = "manual_shopee"


class ShopeeConfirmedProductTests(unittest.TestCase):
    def setUp(self):
        from acp.core import db
        self.db = db
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "intel-upsert.db")
        db.init_db()
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()
        self.db.DB_PATH = self.old_path
        self.tmp.cleanup()

    @staticmethod
    def _raw(price=100000, name="Sản phẩm test"):
        from acp.adapters.base import RawProduct
        return RawProduct(
            external_product_id="456",
            name=name,
            current_price=price,
            original_price=150000,
            commission_value=0,
            commission_rate=None,
            category_code="khac",
            product_url="https://shopee.vn/product/123/456",
            merchant="shopee.vn",
            image_url_original="https://down-vn.img.susercontent.com/file/test-image",
        )

    def test_same_item_reuses_product_and_unchanged_price_does_not_add_history(self):
        from acp.core.shopee_products import upsert_confirmed_product

        source = _ManualShopeeSource()
        first = upsert_confirmed_product(
            self.conn, source, self._raw(100000), metadata_source="helper")
        second = upsert_confirmed_product(
            self.conn, source, self._raw(100000, "Tên đã xác nhận lại"), metadata_source="manual")

        self.assertEqual(first, second)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM product WHERE source='manual_shopee' AND merchant='shopee.vn' AND external_product_id='456'"
        ).fetchone()[0], 1)
        history = self.conn.execute(
            "SELECT price, source FROM product_price_history WHERE product_id=? ORDER BY id", (first,)
        ).fetchall()
        self.assertEqual([(row["price"], row["source"]) for row in history], [(100000, "helper")])

    def test_changed_price_adds_one_sourced_history_row(self):
        from acp.core.shopee_products import upsert_confirmed_product

        source = _ManualShopeeSource()
        product_id = upsert_confirmed_product(
            self.conn, source, self._raw(100000), metadata_source="manual")
        same = upsert_confirmed_product(
            self.conn, source, self._raw(90000), metadata_source="server")

        self.assertEqual(product_id, same)
        history = self.conn.execute(
            "SELECT price, source FROM product_price_history WHERE product_id=? ORDER BY id", (product_id,)
        ).fetchall()
        self.assertEqual(
            [(row["price"], row["source"]) for row in history],
            [(100000, "manual"), (90000, "server")],
        )
        self.assertEqual(self.conn.execute(
            "SELECT current_price FROM product WHERE id=?", (product_id,)
        ).fetchone()[0], 90000)

    def test_confirmed_upsert_links_cache_to_product(self):
        from acp.core.shopee_products import get_metadata_cache, upsert_confirmed_product

        product_id = upsert_confirmed_product(
            self.conn, _ManualShopeeSource(), self._raw(), metadata_source="helper")
        cached = get_metadata_cache(self.conn, "https://shopee.vn/product/123/456")
        self.assertEqual(cached.product_id, product_id)
        self.assertEqual(cached.source, "helper")
        self.assertEqual(cached.current_price, 100000)
        self.assertEqual(cached.name, "Sản phẩm test")

    def test_invalid_metadata_source_is_rejected_before_upsert(self):
        from acp.core.shopee_products import ShopeeProductError, upsert_confirmed_product

        with self.assertRaises(ShopeeProductError):
            upsert_confirmed_product(
                self.conn, _ManualShopeeSource(), self._raw(), metadata_source="cookie")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM product").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
