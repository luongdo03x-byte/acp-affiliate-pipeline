import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from acp.core import db, jobs
from acp.core.shopee_csv_import import import_rows
from acp.core.shopee_enrichment_jobs import queue_pending_products
from acp.core.shopee_image_enrichment import READY, reset_for_retry
from acp.core.shopee_product_pool import build_product_pool
from acp.tests.test_shopee_product_pool_v2 import _row_result


class ShopeeImmediateEnrichmentEdgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "queue-edges.db")
        db.init_db()
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def _import_pending(self, item_id="123"):
        summary = import_rows(self.conn, [_row_result(item_id=item_id)])
        product_id = summary["touched_product_ids"][0]
        queued = queue_pending_products(self.conn, [product_id])
        self.assertEqual(queued["queued"], 1)
        return product_id

    def test_new_pending_generation_can_queue_after_retry_reset(self):
        product_id = self._import_pending()
        first_key = self.conn.execute(
            "SELECT idempotency_key FROM job_queue WHERE job_type='SHOPEE_ENRICH_PRODUCT'"
        ).fetchone()["idempotency_key"]
        self.conn.execute(
            "UPDATE job_queue SET status='FAILED' WHERE job_type='SHOPEE_ENRICH_PRODUCT'"
        )
        self.conn.execute(
            """UPDATE shopee_image_enrichment_job
               SET status='FAILED', attempt_count=2, download_attempt_count=2
               WHERE product_id=?""",
            (product_id,),
        )
        self.assertEqual(reset_for_retry(self.conn, product_id), "PENDING")

        second = queue_pending_products(self.conn, [product_id])

        self.assertEqual(second["queued"], 1)
        keys = [
            row["idempotency_key"]
            for row in self.conn.execute(
                "SELECT idempotency_key FROM job_queue WHERE job_type='SHOPEE_ENRICH_PRODUCT' ORDER BY id"
            ).fetchall()
        ]
        self.assertEqual(len(keys), 2)
        self.assertNotEqual(keys[0], keys[1])
        self.assertEqual(keys[0], first_key)

    def test_publish_disabled_still_executes_non_publish_enrichment_job(self):
        product_id = self._import_pending("124")
        with mock.patch(
            "acp.core.system_settings.publish_worker_enabled", return_value=False
        ), mock.patch(
            "acp.core.shopee_enrichment_jobs.enrichment.enrich_product",
            return_value={"product_id": product_id, "status": "NEEDS_HELPER"},
        ) as enrich:
            stats = jobs.run_once(self.conn, limit=10, ctx={})

        self.assertEqual(stats["done"], 1)
        enrich.assert_called_once()


class ShopeeProductPoolServiceEdgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "pool-edges.db")
        db.init_db()
        self.conn = db.connect()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def _import_ready(self, item_id, name):
        summary = import_rows(
            self.conn,
            [_row_result(item_id=str(item_id), shop_id="1", name=name)],
        )
        product_id = summary["touched_product_ids"][0]
        self.conn.execute(
            "UPDATE product SET main_image_url=? WHERE id=?",
            (f"https://cdn.example/{item_id}.jpg", product_id),
        )
        self.conn.execute(
            "UPDATE shopee_image_enrichment_job SET status=? WHERE product_id=?",
            (READY, product_id),
        )
        return product_id

    def _insert_auto_channel(self):
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

    def test_allowed_page_sizes_20_50_100_and_clamping(self):
        import_rows(
            self.conn,
            [
                _row_result(item_id=str(index), shop_id="1", name=f"Tai nghe {index}")
                for index in range(1, 56)
            ],
        )
        self.conn.execute(
            "UPDATE product SET main_image_url='https://cdn.example/ready.jpg' WHERE provider='SHOPEE_AFFILIATE'"
        )
        self.conn.execute("UPDATE shopee_image_enrichment_job SET status='READY'")

        default_page = build_product_pool(self.conn, {}, now_utc=self.now)
        fifty = build_product_pool(self.conn, {"per_page": "50"}, now_utc=self.now)
        fifty_page_two = build_product_pool(
            self.conn, {"per_page": "50", "page": "2"}, now_utc=self.now
        )
        hundred = build_product_pool(self.conn, {"per_page": "100"}, now_utc=self.now)
        invalid = build_product_pool(
            self.conn, {"per_page": "999", "page": "999"}, now_utc=self.now
        )

        self.assertEqual(len(default_page["items"]), 20)
        self.assertEqual(len(fifty["items"]), 50)
        self.assertEqual(len(fifty_page_two["items"]), 5)
        self.assertEqual(len(hundred["items"]), 55)
        self.assertEqual(invalid["filters"]["per_page"], 20)
        self.assertEqual(invalid["pagination"]["page"], 3)

    def test_auto_eligible_uses_real_active_channel_eligibility(self):
        product_id = self._import_ready("901", "Tai nghe bluetooth sạc nhanh")
        self._insert_auto_channel()

        pool = build_product_pool(
            self.conn, {"auto": "eligible"}, now_utc=self.now
        )

        self.assertEqual([item["id"] for item in pool["items"]], [product_id])
        self.assertEqual(pool["summary"]["auto_eligible"], 1)
        tech = next(stat for stat in pool["niche_stats"] if stat["code"] == "cong-nghe")
        self.assertEqual(tech["total"], 1)
        self.assertEqual(tech["unused"], 1)
        self.assertEqual(tech["scheduled"], 0)
        self.assertEqual(tech["published"], 0)


if __name__ == "__main__":
    unittest.main()
