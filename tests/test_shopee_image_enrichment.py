import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from acp.core.shopee_image_enrichment import (
    DOWNLOADING,
    PENDING,
    PUBLIC_FETCH,
    READY,
    backfill_missing,
    enqueue_product,
    get_job,
    recover_stale_jobs,
)


class ShopeeImageEnrichmentJobTests(unittest.TestCase):
    def setUp(self):
        from acp.core import db

        self.db = db
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "enrichment.db")
        db.init_db()
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()
        self.db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def _insert_product(
        self,
        *,
        product_id="p1",
        provider="SHOPEE_AFFILIATE",
        item_id="456",
        shop_id="123",
        image_path_local=None,
        main_image_url=None,
    ):
        timestamp = self.db.now()
        self.conn.execute(
            """INSERT INTO product (
                 id, source, merchant, external_product_id, name, current_price,
                 commission_value, category_code, product_url, is_available,
                 created_at, updated_at, provider, image_path_local, main_image_url)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                product_id,
                "manual_shopee" if provider == "SHOPEE_AFFILIATE" else "feed",
                "shopee.vn" if provider == "SHOPEE_AFFILIATE" else "example",
                item_id,
                "Sản phẩm test",
                100_000,
                10_000,
                "khac",
                f"https://shopee.vn/product/{shop_id}/{item_id}",
                1,
                timestamp,
                timestamp,
                provider,
                image_path_local,
                main_image_url,
            ),
        )

    def test_schema_registers_enrichment_job_table(self):
        columns = {
            row[1]
            for row in self.conn.execute(
                "PRAGMA table_info(shopee_image_enrichment_job)"
            ).fetchall()
        }
        self.assertIn("product_id", columns)
        self.assertIn("status", columns)
        self.assertIn("attempt_count", columns)
        self.assertIn("download_attempt_count", columns)
        self.assertIn("last_error_code", columns)

    def test_enqueue_missing_shopee_product_is_idempotent(self):
        self._insert_product()

        first = enqueue_product(self.conn, "p1")
        second = enqueue_product(self.conn, "p1")

        self.assertEqual(first, PENDING)
        self.assertEqual(second, PENDING)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM shopee_image_enrichment_job WHERE product_id='p1'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(get_job(self.conn, "p1")["status"], PENDING)

    def test_non_shopee_product_is_not_enqueued(self):
        self._insert_product(product_id="other", provider="ACCESSTRADE_TIKTOK")

        result = enqueue_product(self.conn, "other")

        self.assertIsNone(result)
        self.assertIsNone(get_job(self.conn, "other"))

    def test_existing_main_image_is_ready(self):
        self._insert_product(
            product_id="ready",
            main_image_url="https://media.example/shopee_123_456.jpg",
        )

        status = enqueue_product(self.conn, "ready")

        self.assertEqual(status, READY)
        self.assertEqual(get_job(self.conn, "ready")["status"], READY)

    def test_backfill_enqueues_preexisting_missing_image_products(self):
        self._insert_product(product_id="p1", item_id="1")
        self._insert_product(product_id="p2", item_id="2")
        self._insert_product(
            product_id="p3",
            item_id="3",
            main_image_url="https://media.example/ready.jpg",
        )

        count = backfill_missing(self.conn)

        self.assertEqual(count, 2)
        self.assertEqual(get_job(self.conn, "p1")["status"], PENDING)
        self.assertEqual(get_job(self.conn, "p2")["status"], PENDING)
        self.assertIsNone(get_job(self.conn, "p3"))

    def test_stale_transient_jobs_return_to_pending(self):
        self._insert_product(product_id="fetch", item_id="10")
        self._insert_product(product_id="download", item_id="11")
        enqueue_product(self.conn, "fetch")
        enqueue_product(self.conn, "download")
        old = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat(timespec="seconds")
        self.conn.execute(
            "UPDATE shopee_image_enrichment_job SET status=?, updated_at=? WHERE product_id='fetch'",
            (PUBLIC_FETCH, old),
        )
        self.conn.execute(
            "UPDATE shopee_image_enrichment_job SET status=?, updated_at=? WHERE product_id='download'",
            (DOWNLOADING, old),
        )

        recovered = recover_stale_jobs(self.conn, now_dt=datetime.now(timezone.utc))

        self.assertEqual(recovered, 2)
        self.assertEqual(get_job(self.conn, "fetch")["status"], PENDING)
        self.assertEqual(get_job(self.conn, "download")["status"], PENDING)


if __name__ == "__main__":
    unittest.main()
