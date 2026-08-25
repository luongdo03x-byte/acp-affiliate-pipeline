import json
import os
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timezone
from unittest import mock

from acp.core import auto_post_plans, auto_scheduler, db, pipeline


class _Source:
    def create_tracking_link(self, product_url, sub_ids):
        return "https://go.example.test/tracking"


class _Storage:
    def put(self, image_path):
        return "https://cdn.example.test/composed.jpg"


class AutoPostCalendarFairnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "calendar-fairness.db")
        db.init_db()
        self.conn = db.connect()
        self.conn.execute(
            "INSERT INTO campaign (id,code,name,is_active,created_at) VALUES ('camp','gd2026','Campaign',1,?)",
            (db.now(),),
        )
        self.conn.execute(
            "INSERT INTO caption_template (id,code,name,body,is_active) VALUES ('tpl','price_drop','Price','price_drop',1)"
        )

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def _insert_channel(self, channel_id, code, *, target=2, slots=None, niches=None):
        slots = slots or ["09:30", "12:30", "20:30"]
        niches = niches or ["my-pham"]
        self.conn.execute(
            """INSERT INTO channel (
                 id,code,platform,handle,status,enabled,niches,auto_schedule_enabled,
                 daily_post_target,daily_post_cap,posting_timezone,posting_slots,
                 min_gap_minutes,created_at)
               VALUES (?,?,?,?, 'ACTIVE',1,?,1,?,?, 'Asia/Bangkok',?,90,?)""",
            (
                channel_id,
                code,
                f"@{code}",
                json.dumps(niches, ensure_ascii=False),
                target,
                3,
                json.dumps(slots),
                db.now(),
            ),
        )
        return self.conn.execute("SELECT * FROM channel WHERE id=?", (channel_id,)).fetchone()

    def _insert_product(self, product_id, *, name=None):
        stamp = db.now()
        self.conn.execute(
            """INSERT INTO product (
                 id,source,merchant,external_product_id,name,description,
                 current_price,original_price,commission_value,commission_rate,
                 category_code,rating,review_count,sold_count,image_url_original,
                 image_path_local,product_url,is_available,last_seen_at,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)""",
            (
                product_id,
                "mock",
                "Shop",
                product_id,
                name or product_id,
                "",
                100000,
                150000,
                20000,
                0.1,
                "my-pham",
                4.8,
                120,
                500,
                "https://img.example.test/product.jpg",
                None,
                f"https://example.test/{product_id}",
                stamp,
                stamp,
                stamp,
            ),
        )
        self.conn.execute("UPDATE product SET has_inventory=1 WHERE id=?", (product_id,))
        return self.conn.execute("SELECT * FROM product WHERE id=?", (product_id,)).fetchone()

    def _insert_scheduled_post(self, post_id, product_id, channel_id, scheduled_at):
        stamp = db.now()
        self.conn.execute(
            """INSERT INTO post (
                 id,product_id,channel_id,campaign_id,caption_template_id,variant_code,
                 caption_body,disclosure_text,caption_final,affiliate_link,status,
                 scheduled_at,created_at,updated_at)
               VALUES (?,?,?,?,?,'A','caption','Ad','caption','https://go.example.test/a',
                       'SCHEDULED',?,?,?)""",
            (post_id, product_id, channel_id, "camp", "tpl", scheduled_at, stamp, stamp),
        )
        target_id = f"target-{post_id}"
        self.conn.execute(
            """INSERT INTO publish_target (
                 id,post_id,channel_id,status,scheduled_at,auto_scheduled,created_at,updated_at)
               VALUES (?,?,?,'SCHEDULED',?,1,?,?)""",
            (target_id, post_id, channel_id, scheduled_at, stamp, stamp),
        )
        return target_id

    def test_afternoon_run_uses_remaining_slot_today_then_tomorrow(self):
        channel = self._insert_channel("ch-1", "channel-1", target=2)
        now_utc = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)  # 15:00 Bangkok

        slots = auto_scheduler.available_slots(self.conn, channel, now_utc)
        local_slots = [item["slot"] for item in slots]

        self.assertEqual(
            local_slots,
            [
                "2026-08-25T20:30:00+07:00",
                "2026-08-26T09:30:00+07:00",
                "2026-08-26T12:30:00+07:00",
            ],
        )
        self.assertFalse(any(slot.startswith("2026-08-27") for slot in local_slots))

    def test_product_duplicate_and_cooldown_are_scoped_to_channel(self):
        self._insert_channel("ch-a", "channel-a")
        self._insert_channel("ch-b", "channel-b")
        self._insert_product("product-1")
        self._insert_scheduled_post(
            "post-a",
            "product-1",
            "ch-a",
            "2026-08-25T20:30:00+07:00",
        )
        now_utc = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)

        self.assertTrue(
            auto_scheduler._queued_or_recently_published_product_exists(
                self.conn, "product-1", now_utc, channel_id="ch-a"
            )
        )
        self.assertFalse(
            auto_scheduler._queued_or_recently_published_product_exists(
                self.conn, "product-1", now_utc, channel_id="ch-b"
            )
        )

    def test_fill_auto_schedule_round_robins_limited_products_across_channels(self):
        channel_ids = [f"ch-{index}" for index in range(1, 5)]
        for index, channel_id in enumerate(channel_ids, start=1):
            self._insert_channel(channel_id, f"channel-{index}", target=2)
        self._insert_product("product-1", name="Serum dưỡng ẩm")
        self._insert_product("product-2", name="Kem chống nắng")
        now_utc = datetime(2026, 8, 25, 1, 0, tzinfo=timezone.utc)  # 08:00 Bangkok

        with mock.patch(
            "acp.core.pipeline.imaging.compose",
            return_value=os.path.join(self.tmp.name, "composed.jpg"),
        ):
            stats = pipeline.fill_auto_schedule(
                self.conn,
                "gd2026",
                now_utc=now_utc,
                ctx={"source": _Source(), "storage": _Storage()},
            )

        rows = self.conn.execute(
            """SELECT channel_id, COUNT(*) AS total
               FROM publish_target
               WHERE auto_scheduled=1
               GROUP BY channel_id
               ORDER BY channel_id"""
        ).fetchall()
        counts = Counter({row["channel_id"]: row["total"] for row in rows})

        self.assertEqual(stats["scheduled"], 8)
        self.assertEqual(set(counts), set(channel_ids))
        self.assertTrue(all(counts[channel_id] == 2 for channel_id in channel_ids), counts)

    def test_list_window_includes_earlier_today_and_tomorrow_but_not_day_after(self):
        self._insert_channel("ch-1", "channel-1")
        self._insert_product("product-1")
        self._insert_product("product-2")
        self._insert_product("product-3")
        self._insert_scheduled_post("post-morning", "product-1", "ch-1", "2026-08-25T09:30:00+07:00")
        self._insert_scheduled_post("post-tomorrow", "product-2", "ch-1", "2026-08-26T09:30:00+07:00")
        self._insert_scheduled_post("post-day-after", "product-3", "ch-1", "2026-08-27T09:30:00+07:00")
        now_utc = datetime(2026, 8, 25, 9, 30, tzinfo=timezone.utc)  # 16:30 Bangkok

        rows = auto_post_plans.list_window(self.conn, now_utc, hours=48)
        post_ids = {row["post_id"] for row in rows}

        self.assertIn("post-morning", post_ids)
        self.assertIn("post-tomorrow", post_ids)
        self.assertNotIn("post-day-after", post_ids)


if __name__ == "__main__":
    unittest.main()
