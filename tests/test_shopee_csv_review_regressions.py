import os
import tempfile
import unittest

from acp.core.shopee_csv_import import (
    ShopeeAffiliateCsvRow,
    ShopeeCsvError,
    ShopeeCsvRowResult,
    import_rows,
    parse_commission_percent,
    parse_price_vnd,
    parse_shopee_affiliate_csv,
    parse_sold_count,
    preview_rows_against_db,
)


HEADER = (
    "Mã sản phẩm,Tên sản phẩm,Giá,Doanh thu,Tên cửa hàng,"
    "Tỉ lệ hoa hồng,Hoa hồng,Link sản phẩm,Link ưu đãi\n"
)


def result_for(*, shop_id="1", item_id="123", affiliate="abc"):
    return ShopeeCsvRowResult(
        row=ShopeeAffiliateCsvRow(
            item_id=item_id,
            shop_id=shop_id,
            name="Product",
            current_price=100_000,
            sold_count=10,
            shop_name="Shop",
            commission_rate_percent=5.0,
            commission_amount=5_000,
            product_url=f"https://shopee.vn/product/{shop_id}/{item_id}",
            affiliate_url=f"https://s.shopee.vn/{affiliate}",
            source_filename="batch.csv",
            source_row_number=2,
        ),
        error=None,
        status="VALID",
        source_filename="batch.csv",
        source_row_number=2,
    )


class ShopeeCsvReviewRegressionTests(unittest.TestCase):
    def test_non_finite_numeric_values_are_safe_validation_errors(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(price=value):
                with self.assertRaises(ShopeeCsvError):
                    parse_price_vnd(value)
            with self.subTest(sold=value):
                with self.assertRaises(ShopeeCsvError):
                    parse_sold_count(value)
        for value in ("NaN%", "Infinity%", "-Infinity%"):
            with self.subTest(percent=value):
                with self.assertRaises(ShopeeCsvError):
                    parse_commission_percent(value)

    def test_invalid_row_keeps_source_filename_and_row_number(self):
        raw = (
            HEADER
            + "999,Bad,100,0,Shop,5%,₫5,"
              "https://shopee.vn/product/1/123,https://s.shopee.vn/abc\n"
        ).encode("utf-8")
        parsed = parse_shopee_affiliate_csv(raw, "source.csv")[0]
        self.assertEqual(parsed.status, "ERROR")
        self.assertEqual(parsed.source_filename, "source.csv")
        self.assertEqual(parsed.source_row_number, 2)

    def test_same_item_id_from_different_shop_is_rejected_not_overwritten(self):
        from acp.core import db

        temp = tempfile.TemporaryDirectory()
        old_path = db.DB_PATH
        db.DB_PATH = os.path.join(temp.name, "collision.db")
        try:
            db.init_db()
            conn = db.connect()
            try:
                first = result_for(shop_id="1", item_id="123", affiliate="first")
                imported = import_rows(conn, [first])
                self.assertEqual(imported["new"], 1)

                collision = result_for(shop_id="2", item_id="123", affiliate="second")
                preview = preview_rows_against_db(conn, [collision])
                self.assertEqual(preview[0].status, "ERROR")
                self.assertIn("khác shop", preview[0].error.lower())

                confirmed = import_rows(conn, [collision])
                self.assertEqual(confirmed["error"], 1)
                row = conn.execute(
                    "SELECT product_url, affiliate_url FROM product "
                    "WHERE source='manual_shopee' AND external_product_id='123'"
                ).fetchone()
                self.assertEqual(row["product_url"], "https://shopee.vn/product/1/123")
                self.assertEqual(row["affiliate_url"], "https://s.shopee.vn/first")
            finally:
                conn.close()
        finally:
            db.DB_PATH = old_path
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
