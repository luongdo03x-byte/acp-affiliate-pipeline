import csv
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from acp.core import db, jobs, pipeline
from acp.core.shopee_csv_import import (
    ShopeeAffiliateCsvRow,
    ShopeeCsvRowResult,
    import_rows,
)
from acp.core.shopee_image_enrichment import READY, enqueue_product


def _csv_bytes(item_id="123", *, shop_id="1", name="CSV Product", affiliate="abc"):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow([
        "Mã sản phẩm", "Tên sản phẩm", "Giá", "Doanh thu", "Tên cửa hàng",
        "Tỉ lệ hoa hồng", "Hoa hồng", "Link sản phẩm", "Link ưu đãi",
    ])
    writer.writerow([
        item_id, name, "100,0k", "10k+", "Shop CSV", "5%", "₫5.000",
        f"https://shopee.vn/product/{shop_id}/{item_id}",
        f"https://s.shopee.vn/{affiliate}",
    ])
    return buffer.getvalue().encode("utf-8")


def _row_result(item_id="123", *, shop_id="1", name="Tai nghe bluetooth sạc nhanh"):
    return ShopeeCsvRowResult(
        row=ShopeeAffiliateCsvRow(
            item_id=item_id,
            shop_id=shop_id,
            name=name,
            current_price=100_000,
            sold_count=1000,
            shop_name="Shop CSV",
            commission_rate_percent=10.0,
            commission_amount=10_000,
            product_url=f"https://shopee.vn/product/{shop_id}/{item_id}",
            affiliate_url=f"https://s.shopee.vn/{item_id}",
            source_filename="batch.csv",
            source_row_number=2,
        ),
        error=None,
        status="VALID",
    )


class ShopeeProductPoolV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_password = os.environ.get("ACP_ADMIN_PASSWORD")
        cls.old_adapter = os.environ.get("ACP_ADAPTER")
        cls.old_source = os.environ.get("ACP_SOURCE")
        os.environ["ACP_ADMIN_PASSWORD"] = "test-password"
        os.environ["ACP_ADAPTER"] = "mock"
        os.environ["ACP_SOURCE"] = "mock"

    @classmethod
    def tearDownClass(cls):
        for key, value in (
            ("ACP_ADMIN_PASSWORD", cls.old_password),
            ("ACP_ADAPTER", cls.old_adapter),
            ("ACP_SOURCE", cls.old_source),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def setUp(self):
        from acp.core import shopee_csv_batches
        from acp.web import create_app

        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "pool-v2.db")
        db.init_db()
        self.conn = db.connect()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["uid"] = "operator"
            session["csrf"] = "csrf-test"
        self.csrf = "csrf-test"
        shopee_csv_batches.reset_previews()
        self.batches = shopee_csv_batches

    def tearDown(self):
        self.conn.close()
        self.batches.reset_previews()
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def _insert_channel(self):
        stamp = self.now.isoformat(timespec="seconds")
        self.conn.execute(
            """INSERT INTO channel (
                 id, code, platform, handle, external_user_id, status,
                 daily_post_cap, auto_schedule_enabled, daily_post_target,
                 posting_timezone, posting_slots, min_gap_minutes, niches,
                 created_at, enabled)
               VALUES (?,?,?,?,?,'ACTIVE',?,?,?,?,?,?,?,?,1)""",
            (
                "ch", "threads_tech", "threads", "@tech", "uid-tech",
                3, 1, 2, "Asia/Bangkok", json.dumps(["15:30", "20:30"]),
                90, json.dumps(["cong-nghe"]), stamp,
            ),
        )

    def _insert_product(
        self,
        product_id,
        *,
        item_id=None,
        provider="SHOPEE_AFFILIATE",
        name="Tai nghe bluetooth sạc nhanh",
        shop_name="Shop Alpha",
        image_status=READY,
        last_synced_at=None,
        main_image_url="https://cdn.example/product.jpg",
        has_inventory=None,
        rating=None,
        review_count=0,
        commission_value=12_000,
    ):
        item_id = item_id or product_id.strip("p") or "1"
        stamp = (last_synced_at or self.now).isoformat(timespec="seconds")
        self.conn.execute(
            """INSERT INTO product (
                 id, source, merchant, external_product_id, name, description,
                 current_price, commission_value, category_code, rating, review_count,
                 sold_count, image_url_original, image_path_local, product_url,
                 is_available, last_seen_at, created_at, updated_at, provider,
                 shop_name, main_image_url, has_inventory, affiliate_url,
                 affiliate_link_status, last_synced_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                product_id,
                "manual_shopee" if provider == "SHOPEE_AFFILIATE" else "legacy",
                "shopee.vn" if provider == "SHOPEE_AFFILIATE" else "legacy",
                str(item_id), name, "", 100_000, commission_value, "khac",
                rating, review_count, 1000, main_image_url, "/tmp/product.jpg",
                f"https://shopee.vn/product/10/{item_id}", 1, stamp, stamp, stamp,
                provider, shop_name, main_image_url, has_inventory,
                "https://s.shopee.vn/example" if provider == "SHOPEE_AFFILIATE" else "https://example.com/a",
                "READY", stamp,
            ),
        )
        if provider == "SHOPEE_AFFILIATE":
            enqueue_product(self.conn, product_id)
            self.conn.execute(
                "UPDATE shopee_image_enrichment_job SET status=? WHERE product_id=?",
                (image_status, product_id),
            )

    def _preview_token(self, body):
        import re
        match = re.search(rb'name="preview_token"\s+value="([^"]+)"', body)
        self.assertIsNotNone(match)
        return match.group(1).decode("utf-8")

    def _confirm_csv(self, *, item_id="123", shop_id="1", name="CSV Product", affiliate="abc"):
        preview = self.client.post(
            "/sanpham/shopee-import/preview",
            data={
                "_csrf": self.csrf,
                "files": [(io.BytesIO(_csv_bytes(item_id, shop_id=shop_id, name=name, affiliate=affiliate)), "batch.csv")],
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(preview.status_code, 200)
        return self.client.post(
            "/sanpham/shopee-import/confirm",
            data={"_csrf": self.csrf, "preview_token": self._preview_token(preview.data)},
        )

    def test_installed_auto_candidate_source_is_shopee_only(self):
        self._insert_channel()
        self._insert_product("sp1")
        self._insert_product(
            "legacy1",
            provider="LEGACY",
            has_inventory=1,
            rating=4.9,
            review_count=500,
            commission_value=50_000,
        )
        rows = pipeline._candidate_products_for_channel(self.conn, self.conn.execute("SELECT * FROM channel WHERE id='ch'").fetchone(), 20, self.now)
        self.assertTrue(rows)
        self.assertEqual({row["product"]["provider"] for row in rows}, {"SHOPEE_AFFILIATE"})

    def test_import_rows_returns_touched_product_ids(self):
        result = import_rows(self.conn, [_row_result()])
        self.assertIn("touched_product_ids", result)
        self.assertEqual(len(result["touched_product_ids"]), 1)

    def test_confirm_queues_immediate_enrichment_for_pending_product(self):
        response = self._confirm_csv()
        self.assertEqual(response.status_code, 200)
        row = self.conn.execute(
            "SELECT job_type, status, idempotency_key FROM job_queue ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["job_type"], "SHOPEE_ENRICH_PRODUCT")
        self.assertEqual(row["status"], "READY")
        self.assertTrue(str(row["idempotency_key"]).startswith("shopee-enrich:"))

    def test_reimport_same_pending_generation_does_not_duplicate_enrichment_job(self):
        self.assertEqual(self._confirm_csv().status_code, 200)
        self.assertEqual(self._confirm_csv().status_code, 200)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM job_queue WHERE job_type='SHOPEE_ENRICH_PRODUCT'"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_ready_product_is_not_queued_for_enrichment_on_reimport(self):
        self.assertEqual(self._confirm_csv().status_code, 200)
        product = self.conn.execute("SELECT id FROM product WHERE external_product_id='123'").fetchone()
        self.conn.execute("DELETE FROM job_queue WHERE job_type='SHOPEE_ENRICH_PRODUCT'")
        self.conn.execute(
            "UPDATE product SET main_image_url='https://cdn.example/ready.jpg' WHERE id=?",
            (product["id"],),
        )
        self.conn.execute(
            "UPDATE shopee_image_enrichment_job SET status='READY' WHERE product_id=?",
            (product["id"],),
        )
        self.assertEqual(self._confirm_csv().status_code, 200)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM job_queue WHERE job_type='SHOPEE_ENRICH_PRODUCT'"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_enrichment_worker_handler_is_registered_and_uses_existing_primitive(self):
        handler = jobs._handlers.get("SHOPEE_ENRICH_PRODUCT")
        self.assertTrue(callable(handler))
        self._insert_product("sp1", main_image_url=None, image_status="PENDING")
        jobs.enqueue(
            self.conn,
            "SHOPEE_ENRICH_PRODUCT",
            {"product_id": "sp1"},
            idempotency_key="shopee-enrich:sp1:test",
        )
        with mock.patch("acp.core.shopee_enrichment_jobs.enrichment.enrich_product", return_value={"status": "NEEDS_HELPER"}) as enrich:
            stats = jobs.run_once(self.conn, limit=10, ctx={})
        self.assertEqual(stats["done"], 1)
        enrich.assert_called_once()

    def test_product_pool_default_page_is_twenty_and_has_pagination(self):
        for index in range(25):
            self._insert_product(f"p{index + 1}", item_id=str(index + 1), name=f"Tai nghe {index + 1}")
        response = self.client.get("/sanpham/shopee")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.count(b'data-product-id="'), 20)
        self.assertIn(b'data-total-pages="2"', response.data)

    def test_product_pool_search_and_niche_filter(self):
        self._insert_product("p1", item_id="1", name="Tai nghe bluetooth sạc nhanh", shop_name="Tech House")
        self._insert_product("p2", item_id="2", name="Đầm nữ dự tiệc", shop_name="Fashion House")
        response = self.client.get("/sanpham/shopee?q=Tech+House&niche=cong-nghe")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Tai nghe bluetooth", response.data)
        self.assertNotIn("Đầm nữ dự tiệc".encode("utf-8"), response.data)

    def test_published_usage_precedes_stale_health(self):
        stale = self.now - timedelta(hours=80)
        self._insert_product("p1", item_id="1", last_synced_at=stale)
        self.conn.execute(
            "INSERT INTO post (id, product_id, channel_id, status, published_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            ("post1", "p1", None, "PUBLISHED", self.now.isoformat(), self.now.isoformat(), self.now.isoformat()),
        )
        response = self.client.get("/sanpham/shopee")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-auto-state="PUBLISHED"', response.data)

    def test_ready_unused_product_without_active_auto_channel_is_ineligible(self):
        self._insert_product("p1", item_id="1")
        response = self.client.get("/sanpham/shopee?auto=ineligible")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-product-id="p1"', response.data)
        self.assertIn(b'data-auto-state="INELIGIBLE"', response.data)

    def test_global_summary_is_independent_of_current_filter(self):
        self._insert_product("p1", item_id="1", name="Tai nghe bluetooth")
        self._insert_product("p2", item_id="2", name="Đầm nữ dự tiệc")
        response = self.client.get("/sanpham/shopee?q=does-not-exist")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-summary-total="2"', response.data)
        self.assertEqual(response.data.count(b'data-product-id="'), 0)


if __name__ == "__main__":
    unittest.main()
