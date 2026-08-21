import os
import tempfile
import unittest

from acp.core.shopee_csv_import import (
    ShopeeAffiliateCsvRow,
    ShopeeCsvRowResult,
    import_rows,
)
from acp.core.shopee_image_enrichment import PENDING, READY, get_job


def result(item_id="123", shop_id="1"):
    return ShopeeCsvRowResult(
        row=ShopeeAffiliateCsvRow(
            item_id=item_id,
            shop_id=shop_id,
            name="Sản phẩm CSV",
            current_price=100_000,
            sold_count=10,
            shop_name="Shop CSV",
            commission_rate_percent=10.0,
            commission_amount=10_000,
            product_url=f"https://shopee.vn/product/{shop_id}/{item_id}",
            affiliate_url="https://s.shopee.vn/abc",
            source_filename="batch.csv",
            source_row_number=2,
        ),
        error=None,
        status="VALID",
    )


class ShopeeCsvEnrichmentTests(unittest.TestCase):
    def setUp(self):
        from acp.core import db

        self.db = db
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "csv-enrichment.db")
        db.init_db()
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()
        self.db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def _product(self):
        return self.conn.execute(
            "SELECT * FROM product WHERE provider='SHOPEE_AFFILIATE' AND external_product_id='123'"
        ).fetchone()

    def test_new_import_queues_missing_image_product(self):
        import_rows(self.conn, [result()])

        product = self._product()
        self.assertIsNotNone(product)
        self.assertEqual(get_job(self.conn, product["id"])["status"], PENDING)

    def test_reimport_does_not_duplicate_enrichment_job(self):
        import_rows(self.conn, [result()])
        import_rows(self.conn, [result()])
        product = self._product()

        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM shopee_image_enrichment_job WHERE product_id=?",
                (product["id"],),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(get_job(self.conn, product["id"])["status"], PENDING)

    def test_reimport_product_with_existing_main_image_marks_job_ready(self):
        import_rows(self.conn, [result()])
        product = self._product()
        self.conn.execute(
            "UPDATE product SET main_image_url=? WHERE id=?",
            ("https://media.example/product.jpg", product["id"]),
        )

        import_rows(self.conn, [result()])

        self.assertEqual(get_job(self.conn, product["id"])["status"], READY)


if __name__ == "__main__":
    unittest.main()
