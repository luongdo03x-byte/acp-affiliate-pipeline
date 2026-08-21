import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from acp.core import auto_scheduler, db, pipeline, scoring
from acp.core.shopee_image_enrichment import READY, enqueue_product


class _FakeStorage:
    def put(self, path):
        return "https://cdn.example/composed.jpg"


class ShopeeAutoPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "auto.db")
        db.init_db()
        self.conn = db.connect()
        self.now = datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc)
        self._insert_campaign_template_channel()

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def _insert_campaign_template_channel(self):
        stamp = self.now.isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO campaign (id, code, name, niche, is_active, created_at) VALUES (?,?,?,?,1,?)",
            ("camp", "gd2026", "Campaign", "cong-nghe", stamp),
        )
        self.conn.execute(
            "INSERT INTO caption_template (id, code, name, body, is_active) VALUES (?,?,?,?,1)",
            ("tpl", "price_drop", "Price", "price_drop"),
        )
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
        *,
        product_id="sp1",
        provider="SHOPEE_AFFILIATE",
        name="Tai nghe bluetooth sạc nhanh",
        commission_value=12000,
        affiliate_url="https://s.shopee.vn/example",
        affiliate_status="READY",
        last_synced_at=None,
        has_inventory=None,
        main_image_url="https://cdn.example/shopee.jpg",
        image_path_local="/tmp/shopee-ready.jpg",
        category_code="khac",
        rating=None,
        review_count=0,
    ):
        stamp = (last_synced_at or self.now).isoformat(timespec="seconds")
        created = self.now.isoformat(timespec="seconds")
        self.conn.execute(
            """INSERT INTO product (
                 id, source, merchant, external_product_id, name, description,
                 current_price, original_price, commission_value, commission_rate,
                 category_code, rating, review_count, sold_count,
                 image_url_original, image_path_local, product_url, is_available,
                 last_seen_at, created_at, updated_at,
                 provider, main_image_url, has_inventory, affiliate_url,
                 affiliate_link_status, last_synced_at, score)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?)""",
            (
                product_id, "manual_shopee", "shopee.vn", product_id,
                name, "", 100000, None, commission_value, 0.12,
                category_code, rating, review_count, 1000,
                main_image_url, image_path_local,
                f"https://shopee.vn/product/10/{product_id[-1] if product_id[-1].isdigit() else '1'}",
                stamp, created, created,
                provider, main_image_url, has_inventory, affiliate_url,
                affiliate_status, stamp, None,
            ),
        )
        if provider == "SHOPEE_AFFILIATE":
            enqueue_product(self.conn, product_id)
            self.conn.execute(
                "UPDATE shopee_image_enrichment_job SET status=? WHERE product_id=?",
                (READY, product_id),
            )
        return self.conn.execute("SELECT * FROM product WHERE id=?", (product_id,)).fetchone()

    def _channel(self):
        return self.conn.execute("SELECT * FROM channel WHERE id='ch'").fetchone()

    def test_ready_shopee_unknown_inventory_rating_review_is_candidate(self):
        self._insert_product()
        rows = pipeline._shopee_auto_candidates(self.conn, self._channel(), 20, self.now)
        self.assertEqual([row["product"]["id"] for row in rows], ["sp1"])

    def test_shopee_candidate_requires_ready_image(self):
        self._insert_product()
        self.conn.execute(
            "UPDATE shopee_image_enrichment_job SET status='NEEDS_HELPER' WHERE product_id='sp1'"
        )
        self.assertEqual(pipeline._shopee_auto_candidates(self.conn, self._channel(), 20, self.now), [])

    def test_shopee_candidate_rejects_stale_csv(self):
        self._insert_product(last_synced_at=self.now - timedelta(hours=73))
        self.assertEqual(pipeline._shopee_auto_candidates(self.conn, self._channel(), 20, self.now), [])

    def test_shopee_candidate_rejects_invalid_affiliate_link(self):
        self._insert_product(affiliate_url="not-a-url")
        self.assertEqual(pipeline._shopee_auto_candidates(self.conn, self._channel(), 20, self.now), [])

    def test_shopee_candidate_ignores_missing_legacy_rating_filters(self):
        _, filters = scoring.active_config(self.conn)
        self.assertGreater(filters["min_rating"], 0)
        self._insert_product(rating=None, review_count=0, has_inventory=None)
        eligible, reason = pipeline._shopee_product_auto_eligibility(
            self.conn, self.conn.execute("SELECT * FROM product WHERE id='sp1'").fetchone(),
            self._channel(), self.now,
        )
        self.assertEqual((eligible, reason), (True, "ok"))

    def test_shopee_artifacts_use_exact_imported_affiliate_url(self):
        product = self._insert_product()
        source = mock.Mock()
        with mock.patch.object(pipeline.imaging, "compose", return_value="/tmp/composed.jpg"), \
             mock.patch.object(pipeline.content, "generate", return_value="Deal tai nghe #affiliate"), \
             mock.patch.object(pipeline.content, "validate", return_value=[]):
            prepared = pipeline._prepare_auto_sales_post_artifacts(
                self.conn,
                {"source": source, "storage": _FakeStorage()},
                product,
                self.conn.execute("SELECT * FROM campaign WHERE id='camp'").fetchone(),
                self._channel(),
                self.conn.execute("SELECT * FROM caption_template WHERE id='tpl'").fetchone(),
                "price_drop",
                score=0.7,
            )
        self.assertTrue(prepared["ok"])
        self.assertEqual(prepared["affiliate_link"], "https://s.shopee.vn/example")
        source.create_tracking_link.assert_not_called()

    def test_shopee_publish_preflight_allows_unknown_inventory_when_fresh(self):
        self._insert_product(has_inventory=None)
        target = {"status": "SCHEDULED", "scheduled_at": (self.now + timedelta(hours=1)).isoformat()}
        post = {"id": "post1", "product_id": "sp1", "affiliate_link": "https://s.shopee.vn/example"}
        self.assertEqual(
            auto_scheduler.preflight_auto_target(self.conn, target, post, self._channel(), now_utc=self.now),
            (True, "ok"),
        )

    def test_shopee_publish_preflight_rejects_csv_older_than_72_hours(self):
        self._insert_product(last_synced_at=self.now - timedelta(hours=73))
        target = {"status": "SCHEDULED", "scheduled_at": (self.now + timedelta(hours=1)).isoformat()}
        post = {"id": "post1", "product_id": "sp1", "affiliate_link": "https://s.shopee.vn/example"}
        self.assertEqual(
            auto_scheduler.preflight_auto_target(self.conn, target, post, self._channel(), now_utc=self.now),
            (False, "product_sync_stale"),
        )

    def test_non_shopee_preflight_keeps_inventory_guard(self):
        self._insert_product(product_id="legacy1", provider="LEGACY", has_inventory=0)
        target = {"status": "SCHEDULED", "scheduled_at": (self.now + timedelta(hours=1)).isoformat()}
        post = {"id": "post2", "product_id": "legacy1", "affiliate_link": "https://example.com/a"}
        self.assertEqual(
            auto_scheduler.preflight_auto_target(self.conn, target, post, self._channel(), now_utc=self.now),
            (False, "product_inventory_empty"),
        )


class ShopeeAutoEnrichmentCommandTests(unittest.TestCase):
    def test_bounded_enrichment_command_uses_twenty_product_limit(self):
        import shopee_auto_enrich

        summary = {"processed": 2, "ready": 2, "needs_helper": 0, "failed": 0, "pending": 0}
        with mock.patch.object(shopee_auto_enrich.db, "init_db"), \
             mock.patch.object(shopee_auto_enrich.enrichment, "run_batch", return_value=summary) as batch:
            rc = shopee_auto_enrich.main()
        self.assertEqual(rc, 0)
        self.assertEqual(batch.call_args.kwargs["limit"], 20)

    def test_bounded_enrichment_command_hides_provider_exception_details(self):
        import shopee_auto_enrich

        with mock.patch.object(shopee_auto_enrich.db, "init_db"), \
             mock.patch.object(shopee_auto_enrich.enrichment, "run_batch", side_effect=RuntimeError("secret body")), \
             mock.patch("builtins.print") as printer:
            rc = shopee_auto_enrich.main()
        self.assertEqual(rc, 1)
        self.assertNotIn("secret body", " ".join(map(str, printer.call_args_list)))

    def test_service_runs_enrichment_before_auto_schedule(self):
        text = open("ops/acp-auto-schedule.service", encoding="utf-8").read()
        self.assertLess(text.index("shopee_auto_enrich.py"), text.index('run.py" auto-schedule'))
        self.assertIn('run.py" worker-once', text)


if __name__ == "__main__":
    unittest.main()
