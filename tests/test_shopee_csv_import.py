import os
import tempfile
import unittest

from acp.core.shopee_csv_import import (
    ShopeeAffiliateCsvRow,
    ShopeeCsvRowResult,
    classify_row_against_db,
    import_rows,
    preview_rows_against_db,
)


PROVIDER = "SHOPEE_AFFILIATE"


def valid_result(
    *,
    item_id="123",
    shop_id="1",
    name="Sản phẩm CSV",
    price=100_000,
    sold=10_000,
    shop="Shop CSV",
    rate=42.5,
    commission=42_500,
    affiliate="https://s.shopee.vn/abc",
    status="VALID",
):
    return ShopeeCsvRowResult(
        row=ShopeeAffiliateCsvRow(
            item_id=item_id,
            shop_id=shop_id,
            name=name,
            current_price=price,
            sold_count=sold,
            shop_name=shop,
            commission_rate_percent=rate,
            commission_amount=commission,
            product_url=f"https://shopee.vn/product/{shop_id}/{item_id}",
            affiliate_url=affiliate,
            source_filename="batch.csv",
            source_row_number=2,
        ),
        error=None,
        status=status,
    )


class ShopeeCsvImportDbTests(unittest.TestCase):
    def setUp(self):
        from acp.core import db

        self.db = db
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "csv-import.db")
        db.init_db()
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()
        self.db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def _product(self, item_id="123"):
        return self.conn.execute(
            "SELECT * FROM product WHERE source='manual_shopee' AND merchant='shopee.vn' "
            "AND external_product_id=?",
            (item_id,),
        ).fetchone()

    def _history(self, product_id):
        return self.conn.execute(
            "SELECT price, source FROM product_price_history WHERE product_id=? ORDER BY id",
            (product_id,),
        ).fetchall()

    def test_new_row_inserts_manual_shopee_product_with_ready_official_link(self):
        result = import_rows(self.conn, [valid_result()])
        self.assertEqual(result["new"], 1)
        row = self._product()
        self.assertIsNotNone(row)
        self.assertEqual(row["provider"], PROVIDER)
        self.assertEqual(row["affiliate_url"], "https://s.shopee.vn/abc")
        self.assertEqual(row["affiliate_short_url"], "https://s.shopee.vn/abc")
        self.assertEqual(row["affiliate_link_status"], "READY")
        self.assertIsNone(row["affiliate_link_error"])
        self.assertEqual(row["current_price"], 100_000)
        self.assertEqual(row["price_min"], 100_000)
        self.assertEqual(row["price_max"], 100_000)
        self.assertEqual(row["commission_rate"], 42.5)
        self.assertEqual(row["commission_rate_percent"], 42.5)
        self.assertEqual(row["commission_value"], 42_500)
        self.assertEqual(row["commission_amount"], 42_500)
        self.assertEqual(row["sold_count"], 10_000)
        self.assertEqual(row["units_sold"], 10_000)

    def test_new_row_records_first_affiliate_csv_price_observation(self):
        import_rows(self.conn, [valid_result(price=100_000)])
        row = self._product()
        history = self._history(row["id"])
        self.assertEqual(
            [(item["price"], item["source"]) for item in history],
            [(100_000, "affiliate_csv")],
        )

    def test_reimport_same_row_is_unchanged_and_keeps_one_product_and_history_row(self):
        import_rows(self.conn, [valid_result()])
        result = import_rows(self.conn, [valid_result()])
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM product WHERE source='manual_shopee' AND merchant='shopee.vn' "
                "AND external_product_id='123'"
            ).fetchone()[0],
            1,
        )
        row = self._product()
        self.assertEqual(len(self._history(row["id"])), 1)

    def test_reimport_unchanged_row_refreshes_sync_freshness_stamps(self):
        """An unchanged re-import still proves the row is current right now.

        Auto publishing hard-blocks on `last_synced_at` age (72h). If a stable
        product never changes price, the UNCHANGED branch must still refresh
        the stamp or the product silently expires out of Auto forever.
        """
        import_rows(self.conn, [valid_result()])
        product_id = self._product()["id"]
        self.conn.execute(
            "UPDATE product SET last_synced_at=?, last_seen_at=? WHERE id=?",
            ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00", product_id),
        )

        result = import_rows(self.conn, [valid_result()])

        self.assertEqual(result["unchanged"], 1)
        row = self._product()
        self.assertGreater(row["last_synced_at"], "2020-01-01T00:00:00+00:00")
        self.assertGreater(row["last_seen_at"], "2020-01-01T00:00:00+00:00")

    def test_reimport_unchanged_row_does_not_add_price_history(self):
        import_rows(self.conn, [valid_result()])
        product_id = self._product()["id"]
        self.conn.execute(
            "UPDATE product SET last_synced_at=? WHERE id=?",
            ("2020-01-01T00:00:00+00:00", product_id),
        )
        import_rows(self.conn, [valid_result()])
        self.assertEqual(len(self._history(product_id)), 1)

    def test_changed_price_adds_exactly_one_new_sourced_history_row(self):
        import_rows(self.conn, [valid_result(price=100_000)])
        result = import_rows(self.conn, [valid_result(price=120_000)])
        self.assertEqual(result["updated"], 1)
        row = self._product()
        history = self._history(row["id"])
        self.assertEqual(
            [(item["price"], item["source"]) for item in history],
            [(100_000, "affiliate_csv"), (120_000, "affiliate_csv")],
        )

    def test_existing_richer_metadata_survives_csv_update(self):
        import_rows(self.conn, [valid_result()])
        row = self._product()
        self.conn.execute(
            "UPDATE product SET description=?, original_price=?, rating=?, review_count=?, category_code=?, "
            "image_url_original=?, main_image_url=?, category_data=? WHERE id=?",
            (
                "Mô tả giàu dữ liệu",
                150_000,
                4.9,
                321,
                "pet",
                "https://img.example/source.jpg",
                "https://img.example/main.jpg",
                '{"code":"pet"}',
                row["id"],
            ),
        )

        import_rows(
            self.conn,
            [valid_result(name="Tên mới", price=90_000, sold=20_000, rate=10.5, commission=9_450)],
        )
        updated = self._product()
        self.assertEqual(updated["name"], "Tên mới")
        self.assertEqual(updated["current_price"], 90_000)
        self.assertEqual(updated["description"], "Mô tả giàu dữ liệu")
        self.assertEqual(updated["original_price"], 150_000)
        self.assertEqual(updated["rating"], 4.9)
        self.assertEqual(updated["review_count"], 321)
        self.assertEqual(updated["category_code"], "pet")
        self.assertEqual(updated["image_url_original"], "https://img.example/source.jpg")
        self.assertEqual(updated["main_image_url"], "https://img.example/main.jpg")
        self.assertEqual(updated["category_data"], '{"code":"pet"}')

    def test_blank_optional_csv_fields_do_not_erase_existing_values(self):
        import_rows(self.conn, [valid_result(shop="Known Shop", sold=5, rate=7.5, commission=7_500)])
        import_rows(
            self.conn,
            [valid_result(shop=None, sold=None, rate=None, commission=None, affiliate="https://s.shopee.vn/new")],
        )
        row = self._product()
        self.assertEqual(row["shop_name"], "Known Shop")
        self.assertEqual(row["sold_count"], 5)
        self.assertEqual(row["units_sold"], 5)
        self.assertEqual(row["commission_rate_percent"], 7.5)
        self.assertEqual(row["commission_amount"], 7_500)
        self.assertEqual(row["affiliate_url"], "https://s.shopee.vn/new")

    def test_latest_official_affiliate_url_replaces_previous_url(self):
        import_rows(self.conn, [valid_result(affiliate="https://s.shopee.vn/old")])
        result = import_rows(self.conn, [valid_result(affiliate="https://s.shopee.vn/new")])
        self.assertEqual(result["updated"], 1)
        row = self._product()
        self.assertEqual(row["affiliate_url"], "https://s.shopee.vn/new")
        self.assertEqual(row["affiliate_short_url"], "https://s.shopee.vn/new")

    def test_duplicate_and_error_rows_do_not_mutate_database(self):
        duplicate = valid_result(item_id="123", status="DUPLICATE_IN_UPLOAD")
        error = ShopeeCsvRowResult(row=None, error="bad row", status="ERROR")
        summary = import_rows(self.conn, [duplicate, error])
        self.assertEqual(summary["duplicate"], 1)
        self.assertEqual(summary["error"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM product").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM product_price_history").fetchone()[0], 0)

    def test_preview_classifies_without_mutating_database(self):
        before_products = self.conn.execute("SELECT COUNT(*) FROM product").fetchone()[0]
        before_history = self.conn.execute("SELECT COUNT(*) FROM product_price_history").fetchone()[0]
        preview = preview_rows_against_db(self.conn, [valid_result()])
        self.assertEqual(preview[0].status, "NEW")
        self.assertEqual(classify_row_against_db(self.conn, valid_result().row), "NEW")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM product").fetchone()[0], before_products)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM product_price_history").fetchone()[0],
            before_history,
        )

    def test_preview_detects_updated_then_unchanged(self):
        import_rows(self.conn, [valid_result()])
        unchanged = preview_rows_against_db(self.conn, [valid_result()])
        changed = preview_rows_against_db(self.conn, [valid_result(price=99_000)])
        self.assertEqual(unchanged[0].status, "UNCHANGED")
        self.assertEqual(changed[0].status, "UPDATED")


if __name__ == "__main__":
    unittest.main()
