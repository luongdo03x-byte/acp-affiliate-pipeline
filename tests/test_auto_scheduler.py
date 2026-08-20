import json
import os
import sqlite3
import sys
import tempfile
import unittest
import importlib.util
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if "acp" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "acp",
        os.path.join(REPO_ROOT, "__init__.py"),
        submodule_search_locations=[REPO_ROOT],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["acp"] = module
    spec.loader.exec_module(module)

from acp.core import db


class ChannelAutomationMigrationTests(unittest.TestCase):
    def test_factory_v2_channel_schema_defaults_new_threads_channels_to_cap_three(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.row_factory = sqlite3.Row
            from acp.core.factory_v2.channel_schema import ensure_factory_channel_schema

            ensure_factory_channel_schema(conn)
            conn.execute("""
                INSERT INTO channel (id, code, platform, handle, status, created_at)
                VALUES (?,?,?,?,?,?)
            """, ("factory-channel-1", "factory_ch_1", "threads", "@factory", "ACTIVE", db.now()))
            row = conn.execute(
                "SELECT daily_post_cap FROM channel WHERE id='factory-channel-1'"
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(row["daily_post_cap"], 3)

    def test_legacy_channel_migration_adds_auto_scheduler_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "legacy-channel.db")
            conn = sqlite3.connect(path)
            try:
                conn.execute("""
                    CREATE TABLE channel (
                        id TEXT PRIMARY KEY,
                        code TEXT UNIQUE NOT NULL,
                        daily_post_cap INTEGER NOT NULL DEFAULT 12
                    )
                """)
                conn.execute("INSERT INTO channel (id, code, daily_post_cap) VALUES ('c1', 'legacy', 9)")
                conn.commit()
                conn.row_factory = sqlite3.Row

                applied = db.migrate(conn)
                self.assertIn("channel.auto_schedule_enabled", applied)
                self.assertIn("channel.daily_post_target", applied)
                self.assertIn("channel.posting_timezone", applied)
                self.assertIn("channel.posting_slots", applied)
                self.assertEqual(db.migrate(conn), [])

                row = conn.execute("""
                    SELECT daily_post_cap, auto_schedule_enabled, daily_post_target,
                           posting_timezone, posting_slots
                    FROM channel
                    WHERE id='c1'
                """).fetchone()
            finally:
                conn.close()

        self.assertEqual(row["daily_post_cap"], 9)
        self.assertEqual(row["auto_schedule_enabled"], 0)
        self.assertEqual(row["daily_post_target"], 2)
        self.assertEqual(row["posting_timezone"], "Asia/Bangkok")
        self.assertEqual(json.loads(row["posting_slots"]), ["09:30", "12:30", "20:30"])

    def test_fresh_channel_defaults_use_safe_auto_scheduler_values(self):
        previous_db_path = db.DB_PATH
        with tempfile.TemporaryDirectory() as directory:
            db.DB_PATH = os.path.join(directory, "fresh.db")
            try:
                db.init_db()
                conn = db.connect()
                try:
                    conn.execute("""
                        INSERT INTO channel (id, code, platform, handle, status, created_at)
                        VALUES (?,?,?,?,?,?)
                    """, ("channel-1", "threads-1", "threads", "@threads-1", "ACTIVE", db.now()))
                    row = conn.execute("""
                        SELECT daily_post_cap, auto_schedule_enabled, daily_post_target,
                               posting_timezone, posting_slots
                        FROM channel
                        WHERE id='channel-1'
                    """).fetchone()
                finally:
                    conn.close()
            finally:
                db.DB_PATH = previous_db_path

        self.assertEqual(row["daily_post_cap"], 3)
        self.assertEqual(row["auto_schedule_enabled"], 0)
        self.assertEqual(row["daily_post_target"], 2)
        self.assertEqual(row["posting_timezone"], "Asia/Bangkok")
        self.assertEqual(json.loads(row["posting_slots"]), ["09:30", "12:30", "20:30"])


class ChannelAutomationValidationTests(unittest.TestCase):
    def test_validate_channel_automation_config_normalizes_valid_payload(self):
        from acp.web.server import validate_channel_automation_config

        result = validate_channel_automation_config({
            "auto_schedule_enabled": "1",
            "daily_post_target": "2",
            "daily_post_cap": "3",
            "posting_timezone": "Asia/Bangkok",
            "posting_slots": [" 09:30 ", "12:30", "20:30"],
        })

        self.assertEqual(result, {
            "ok": True,
            "values": {
                "auto_schedule_enabled": 1,
                "daily_post_target": 2,
                "daily_post_cap": 3,
                "posting_timezone": "Asia/Bangkok",
                "posting_slots": '["09:30", "12:30", "20:30"]',
            },
        })

    def test_validate_channel_automation_config_rejects_target_cap_bounds(self):
        from acp.web.server import validate_channel_automation_config

        result = validate_channel_automation_config({
            "auto_schedule_enabled": "1",
            "daily_post_target": "3",
            "daily_post_cap": "2",
            "posting_timezone": "Asia/Bangkok",
            "posting_slots": ["09:30", "12:30"],
        })

        self.assertFalse(result["ok"])
        self.assertIn("target", result["error"].lower())

    def test_validate_channel_automation_config_preserves_existing_legacy_cap_above_auto_max(self):
        from acp.web.server import validate_channel_automation_config

        result = validate_channel_automation_config({
            "auto_schedule_enabled": "1",
            "daily_post_target": "3",
            "daily_post_cap": "12",
            "existing_daily_post_cap": "12",
            "posting_timezone": "Asia/Bangkok",
            "posting_slots": ["09:30", "12:30", "20:30"],
        })

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["values"]["daily_post_cap"], 12)

    def test_validate_channel_automation_config_rejects_invalid_timezone(self):
        from acp.web.server import validate_channel_automation_config

        result = validate_channel_automation_config({
            "auto_schedule_enabled": "1",
            "daily_post_target": "2",
            "daily_post_cap": "3",
            "posting_timezone": "Mars/Olympus",
            "posting_slots": ["09:30", "12:30"],
        })

        self.assertFalse(result["ok"])
        self.assertIn("Múi giờ", result["error"])

    def test_validate_channel_automation_config_rejects_duplicate_or_invalid_slots(self):
        from acp.web.server import validate_channel_automation_config

        duplicate = validate_channel_automation_config({
            "auto_schedule_enabled": "1",
            "daily_post_target": "2",
            "daily_post_cap": "3",
            "posting_timezone": "Asia/Bangkok",
            "posting_slots": ["09:30", "09:30"],
        })
        invalid = validate_channel_automation_config({
            "auto_schedule_enabled": "1",
            "daily_post_target": "2",
            "daily_post_cap": "3",
            "posting_timezone": "Asia/Bangkok",
            "posting_slots": ["9:30", "25:00"],
        })

        self.assertFalse(duplicate["ok"])
        self.assertIn("trùng", duplicate["error"])
        self.assertFalse(invalid["ok"])
        self.assertIn("HH:MM", invalid["error"])


class ChannelAutomationWebTests(unittest.TestCase):
    def setUp(self):
        self.previous_db_path = db.DB_PATH
        self.previous_password = os.environ.get("ACP_ADMIN_PASSWORD")
        self.previous_secret = os.environ.get("ACP_SECRET_KEY")
        self.tempdir = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tempdir.name, "channels-web.db")
        os.environ["ACP_ADMIN_PASSWORD"] = "test-password"
        os.environ["ACP_SECRET_KEY"] = "test-secret"

        db.init_db()
        conn = db.connect()
        try:
            conn.execute("""
                INSERT INTO channel (
                    id, code, platform, handle, status, enabled, daily_post_cap,
                    min_gap_minutes, niches, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """, ("threads-1", "threads_1", "threads", "@threads-1", "ACTIVE", 1, 3, 90,
                  '["my-pham"]', db.now()))
            conn.execute("""
                INSERT INTO channel (
                    id, code, platform, handle, status, enabled, daily_post_cap,
                    min_gap_minutes, niches, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """, ("facebook-1", "facebook_1", "facebook", "FB Page", "ACTIVE", 1, 3, 90,
                  "[]", db.now()))
        finally:
            conn.close()

        from acp.web.server import create_app

        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.client.post("/dangnhap", data={"password": "test-password"})
        with self.client.session_transaction() as session_data:
            self.csrf = session_data["csrf"]

    def tearDown(self):
        db.DB_PATH = self.previous_db_path
        self.tempdir.cleanup()
        if self.previous_password is None:
            os.environ.pop("ACP_ADMIN_PASSWORD", None)
        else:
            os.environ["ACP_ADMIN_PASSWORD"] = self.previous_password
        if self.previous_secret is None:
            os.environ.pop("ACP_SECRET_KEY", None)
        else:
            os.environ["ACP_SECRET_KEY"] = self.previous_secret

    def test_threads_channel_form_renders_automation_controls_only_for_threads(self):
        response = self.client.get("/kenh")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Tự duyệt + lên lịch", response.text)
        self.assertIn("worker toàn hệ thống", response.text)
        self.assertEqual(response.text.count('name="daily_post_target"'), 1)
        self.assertEqual(response.text.count('name="posting_slots"'), 1)

    def test_threads_channel_form_persists_automation_config_and_audits(self):
        response = self.client.post("/kenh", data={
            "_csrf": self.csrf,
            "channel_id": "threads-1",
            "niches": ["my-pham", "gia-dung"],
            "auto_schedule_enabled": "1",
            "daily_post_target": "2",
            "daily_post_cap": "3",
            "posting_timezone": "Asia/Bangkok",
            "posting_slots": ["09:30", "20:30"],
        })
        self.assertEqual(response.status_code, 200)

        conn = db.connect()
        try:
            row = conn.execute("""
                SELECT niches, auto_schedule_enabled, daily_post_target,
                       daily_post_cap, posting_timezone, posting_slots
                FROM channel
                WHERE id='threads-1'
            """).fetchone()
            audit_row = conn.execute("""
                SELECT action, actor, detail
                FROM audit_log
                WHERE entity='channel' AND entity_id='threads-1'
                ORDER BY id DESC LIMIT 1
            """).fetchone()
        finally:
            conn.close()

        self.assertEqual(json.loads(row["niches"]), ["my-pham", "gia-dung"])
        self.assertEqual(row["auto_schedule_enabled"], 1)
        self.assertEqual(row["daily_post_target"], 2)
        self.assertEqual(row["daily_post_cap"], 3)
        self.assertEqual(row["posting_timezone"], "Asia/Bangkok")
        self.assertEqual(json.loads(row["posting_slots"]), ["09:30", "20:30"])
        self.assertEqual(audit_row["action"], "updated_automation")
        self.assertEqual(audit_row["actor"], "operator")
        self.assertIn('"auto_schedule_enabled": 1', audit_row["detail"])
        self.assertIn('"posting_slots": ["09:30", "20:30"]', audit_row["detail"])

    def test_threads_channel_niche_only_save_does_not_emit_updated_automation_audit(self):
        response = self.client.post("/kenh", data={
            "_csrf": self.csrf,
            "channel_id": "threads-1",
            "niches": ["my-pham", "gia-dung"],
            "auto_schedule_enabled": "",
            "daily_post_target": "2",
            "daily_post_cap": "3",
            "posting_timezone": "Asia/Bangkok",
            "posting_slots": ["09:30", "12:30", "20:30"],
        })
        self.assertEqual(response.status_code, 200)

        conn = db.connect()
        try:
            actions = [row["action"] for row in conn.execute("""
                SELECT action
                FROM audit_log
                WHERE entity='channel' AND entity_id='threads-1'
                ORDER BY id
            """).fetchall()]
        finally:
            conn.close()

        self.assertEqual(actions, ["set_niches"])

    def test_ops_page_shows_auto_schedule_summary_with_sanitized_reasons(self):
        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        conn = db.connect()
        try:
            conn.execute("""
                UPDATE channel
                SET auto_schedule_enabled=1,
                    daily_post_target=2,
                    daily_post_cap=3,
                    posting_timezone='Asia/Bangkok',
                    posting_slots='["09:30", "12:30", "20:30"]'
                WHERE id='threads-1'
            """)
            conn.execute("""
                INSERT INTO campaign (id, code, name, created_at)
                VALUES (?,?,?,?)
            """, ("camp-1", "camp", "Campaign", db.now()))
            for product_id, name in (("product-1", "Serum dưỡng ẩm"), ("product-2", "Kem chống nắng")):
                conn.execute("""
                    INSERT INTO product (
                        id, source, merchant, external_product_id, name, description,
                        current_price, original_price, commission_value, commission_rate,
                        category_code, rating, review_count, sold_count, image_url_original,
                        image_path_local, product_url, is_available, has_inventory, last_seen_at,
                        created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    product_id,
                    "mock",
                    "Shop",
                    product_id,
                    name,
                    "",
                    100000,
                    150000,
                    20000,
                    0.1,
                    "my-pham",
                    4.8,
                    20,
                    100,
                    "https://img.test/product.jpg",
                    None,
                    f"https://example.test/{product_id}",
                    1,
                    1,
                    db.now(),
                    db.now(),
                    db.now(),
                ))
            conn.execute("""
                INSERT INTO post (
                    id, product_id, channel_id, campaign_id, variant_code, caption_body,
                    disclosure_text, caption_final, affiliate_link, status, scheduled_at,
                    created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                "post-upcoming",
                "product-1",
                "threads-1",
                "camp-1",
                "A",
                "caption",
                "Ad",
                "caption",
                "https://example.test/aff-1",
                "SCHEDULED",
                "2026-08-20T09:30:00+07:00",
                db.now(),
                db.now(),
            ))
            conn.execute("""
                INSERT INTO publish_target (
                    id, post_id, channel_id, status, scheduled_at, auto_scheduled, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?)
            """, (
                "target-upcoming",
                "post-upcoming",
                "threads-1",
                "SCHEDULED",
                "2026-08-20T09:30:00+07:00",
                1,
                db.now(),
                db.now(),
            ))
            conn.execute("""
                INSERT INTO post (
                    id, product_id, channel_id, campaign_id, variant_code, caption_body,
                    disclosure_text, caption_final, affiliate_link, status, scheduled_at,
                    created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                "post-cancelled",
                "product-2",
                "threads-1",
                "camp-1",
                "A",
                "caption",
                "Ad",
                "caption",
                "https://example.test/aff-2",
                "PENDING_REVIEW",
                "2026-08-20T12:30:00+07:00",
                db.now(),
                db.now(),
            ))
            conn.execute("""
                INSERT INTO publish_target (
                    id, post_id, channel_id, status, scheduled_at, auto_scheduled, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?)
            """, (
                "target-cancelled",
                "post-cancelled",
                "threads-1",
                "CANCELLED",
                "2026-08-20T12:30:00+07:00",
                1,
                db.now(),
                db.now(),
            ))
            conn.execute("""
                INSERT INTO audit_log (
                    entity, entity_id, action, actor, detail, created_at
                ) VALUES (?,?,?,?,?,?)
            """, (
                "publish_target",
                "target-cancelled",
                "auto_stale_cancelled",
                "system",
                json.dumps({
                    "target_id": "target-cancelled",
                    "post_id": "post-cancelled",
                    "reason": "product_sync_stale",
                    "affiliate_link": "https://secret.example/token-123",
                }),
                db.now(),
            ))
            conn.commit()
        finally:
            conn.close()

        response = self.client.get("/vanhanh?now=2026-08-20T01:00:00+00:00")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Lịch Auto 48 giờ tới", response.text)
        self.assertIn("Auto kênh chỉ tạo, duyệt và xếp lịch", response.text)
        self.assertIn("Worker global vẫn phải bật riêng", response.text)
        self.assertIn("1 target Auto sắp tới", response.text)
        self.assertIn("09:30", response.text)
        self.assertIn("product_sync_stale", response.text)
        self.assertNotIn("https://secret.example/token-123", response.text)
        self.assertNotIn("affiliate_link", response.text)


class AutoSchedulerRoutingTests(unittest.TestCase):
    def setUp(self):
        self.previous_db_path = db.DB_PATH
        self.tempdir = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tempdir.name, "auto-scheduler.db")
        db.init_db()
        self.conn = db.connect()
        self.conn.execute(
            "INSERT INTO campaign (id, code, name, created_at) VALUES (?,?,?,?)",
            ("camp-1", "camp", "Campaign", db.now()),
        )

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.previous_db_path
        self.tempdir.cleanup()

    def _insert_channel(
        self,
        channel_id,
        code,
        *,
        niches,
        auto_schedule_enabled=1,
        enabled=1,
        status="ACTIVE",
        daily_post_target=2,
        daily_post_cap=3,
        posting_timezone="Asia/Bangkok",
        posting_slots=None,
    ):
        if posting_slots is None:
            posting_slots = ["09:30", "12:30", "20:30"]
        self.conn.execute(
            """
            INSERT INTO channel (
                id, code, platform, handle, status, enabled, auto_schedule_enabled,
                daily_post_target, daily_post_cap, posting_timezone, posting_slots,
                min_gap_minutes, niches, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                channel_id,
                code,
                "threads",
                f"@{code}",
                status,
                enabled,
                auto_schedule_enabled,
                daily_post_target,
                daily_post_cap,
                posting_timezone,
                json.dumps(posting_slots),
                90,
                json.dumps(niches, ensure_ascii=False),
                db.now(),
            ),
        )

    def _insert_product(
        self,
        product_id,
        *,
        name,
        category_code="my-pham",
        merchant="Shop",
        commission_value=20000,
        is_available=1,
        has_inventory=1,
    ):
        ts = db.now()
        self.conn.execute(
            """
            INSERT INTO product (
                id, source, merchant, external_product_id, name, description,
                current_price, original_price, commission_value, commission_rate,
                category_code, rating, review_count, sold_count, image_url_original,
                image_path_local, product_url, is_available, last_seen_at,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                product_id,
                "mock",
                merchant,
                product_id,
                name,
                "",
                100000,
                150000,
                commission_value,
                0.1,
                category_code,
                4.8,
                120,
                500,
                "https://img.test/product.jpg",
                None,
                f"https://example.test/{product_id}",
                is_available,
                ts,
                ts,
                ts,
            ),
        )
        self.conn.execute("UPDATE product SET has_inventory=? WHERE id=?", (has_inventory, product_id))
        return self.conn.execute("SELECT * FROM product WHERE id=?", (product_id,)).fetchone()

    def _insert_post(
        self,
        post_id,
        product_id,
        channel_id,
        *,
        status="PENDING_REVIEW",
        created_at=None,
        published_at=None,
        scheduled_at=None,
    ):
        ts = created_at or db.now()
        self.conn.execute(
            """
            INSERT INTO post (
                id, product_id, channel_id, campaign_id, caption_template_id,
                variant_code, caption_body, disclosure_text, caption_final,
                affiliate_link, status, scheduled_at, published_at, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                post_id,
                product_id,
                channel_id,
                "camp-1",
                None,
                "A",
                "caption",
                "Ad",
                "caption",
                "https://example.test/aff",
                status,
                scheduled_at,
                published_at,
                ts,
                ts,
            ),
        )

    def _insert_publish_target(
        self,
        target_id,
        post_id,
        channel_id,
        *,
        status,
        scheduled_at,
        updated_at=None,
        external_post_id=None,
        auto_scheduled=0,
    ):
        ts = updated_at or scheduled_at
        self.conn.execute(
            """
            INSERT INTO publish_target (
                id, post_id, channel_id, status, scheduled_at, auto_scheduled, external_post_id,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (target_id, post_id, channel_id, status, scheduled_at, auto_scheduled, external_post_id, ts, ts),
        )

    def _insert_post_metrics(self, post_id, *, clicks):
        self.conn.execute(
            "INSERT INTO post_metrics (post_id, clicks, updated_at) VALUES (?,?,?)",
            (post_id, clicks, db.now()),
        )

    def _preflight_auto_target(self):
        from acp.core import auto_scheduler

        self.assertTrue(
            hasattr(auto_scheduler, "preflight_auto_target"),
            "preflight_auto_target(conn, target, post, channel, now_utc=None) is missing",
        )
        return auto_scheduler.preflight_auto_target

    def test_candidate_channels_requires_exact_niche_match_and_no_empty_niche_fallback(self):
        from acp.core.auto_scheduler import candidate_channels

        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        self._insert_channel("channel-beauty", "beauty", niches=["my-pham"])
        self._insert_channel("channel-general", "general", niches=[])
        product = self._insert_product("product-1", name="Serum dưỡng ẩm phục hồi", category_code="my-pham")

        candidates = candidate_channels(self.conn, product, now_utc)

        self.assertEqual([row["id"] for row in candidates], ["channel-beauty"])
        self.assertEqual(candidates[0]["matched_niches"], ["my-pham"])

    def test_candidate_channels_excludes_inactive_disabled_auto_off_and_full_quota_channels(self):
        from acp.core.auto_scheduler import candidate_channels

        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        self._insert_channel("winner", "winner", niches=["my-pham"])
        self._insert_channel("inactive", "inactive", niches=["my-pham"], status="PAUSED")
        self._insert_channel("disabled", "disabled", niches=["my-pham"], enabled=0)
        self._insert_channel("manual", "manual", niches=["my-pham"], auto_schedule_enabled=0)
        self._insert_channel("full", "full", niches=["my-pham"], daily_post_cap=1)
        product = self._insert_product("product-2", name="Kem chống nắng dịu da", category_code="my-pham")
        self._insert_post("post-full", "product-2", "full", status="SCHEDULED")
        self._insert_publish_target(
            "target-full",
            "post-full",
            "full",
            status="SCHEDULED",
            scheduled_at="2026-08-20T09:30:00+07:00",
        )
        product_other_day = self._insert_product(
            "product-2-full-other-day",
            name="Serum full ngày mai",
            category_code="my-pham",
        )
        self._insert_post("post-full-other-day", product_other_day["id"], "full", status="SCHEDULED")
        self._insert_publish_target(
            "target-full-other-day",
            "post-full-other-day",
            "full",
            status="SCHEDULED",
            scheduled_at="2026-08-21T09:30:00+07:00",
        )

        candidates = candidate_channels(self.conn, product, now_utc)

        self.assertEqual([row["id"] for row in candidates], ["winner"])

    def test_route_product_uses_slot_local_day_for_quota_checks(self):
        from acp.core.auto_scheduler import route_product

        self._insert_channel("winner", "winner", niches=["my-pham"], posting_timezone="Asia/Bangkok", daily_post_cap=1)
        product = self._insert_product("product-2b", name="Kem dưỡng phục hồi", category_code="my-pham")
        existing = self._insert_product("product-2b-existing", name="Kem đã xếp lịch", category_code="my-pham")
        self._insert_post("post-quota", existing["id"], "winner", status="SCHEDULED")
        self._insert_publish_target(
            "target-quota",
            "post-quota",
            "winner",
            status="SCHEDULED",
            scheduled_at="2026-08-21T09:30:00+07:00",
        )

        after_local_midnight = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)

        routed = route_product(self.conn, product, after_local_midnight)

        self.assertEqual(routed["channel_id"], "winner")
        self.assertEqual(routed["slot"], "2026-08-22T09:30:00+07:00")

    def test_route_product_keeps_channel_when_today_full_but_future_slot_open(self):
        from acp.core.auto_scheduler import candidate_channels, route_product

        self._insert_channel(
            "rolling",
            "rolling",
            niches=["my-pham"],
            posting_timezone="Asia/Bangkok",
            daily_post_target=1,
            daily_post_cap=1,
            posting_slots=["09:30"],
        )
        existing = self._insert_product("already-scheduled", name="Serum đã lên lịch", category_code="my-pham")
        product = self._insert_product("candidate-product", name="Kem dưỡng phục hồi", category_code="my-pham")
        self._insert_post("today-post", existing["id"], "rolling", status="SCHEDULED")
        self._insert_publish_target(
            "today-target",
            "today-post",
            "rolling",
            status="SCHEDULED",
            scheduled_at="2026-08-20T09:30:00+07:00",
        )
        now_after_today_slot = datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc)

        candidates = candidate_channels(self.conn, product, now_after_today_slot)
        routed = route_product(self.conn, product, now_after_today_slot)

        self.assertEqual([row["id"] for row in candidates], ["rolling"])
        self.assertEqual(routed["channel_id"], "rolling")
        self.assertEqual(routed["slot"], "2026-08-21T09:30:00+07:00")

    def test_route_product_skips_products_already_active_on_a_channel(self):
        from acp.core.auto_scheduler import route_product

        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        self._insert_channel("channel-beauty", "beauty", niches=["my-pham"])
        product = self._insert_product("product-3", name="Son dưỡng ẩm", category_code="my-pham")
        self._insert_post("existing-post", "product-3", "channel-beauty", status="PENDING_REVIEW")

        routed = route_product(self.conn, product, now_utc)

        self.assertEqual(routed, {"reason": "product_already_routed"})

    def test_route_product_blocks_recent_published_product_but_allows_after_cooldown(self):
        from acp.core.auto_scheduler import route_product

        self._insert_channel("channel-beauty", "beauty", niches=["my-pham"])
        product = self._insert_product("product-3b", name="Serum cấp ẩm", category_code="my-pham")

        recent_now = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        old_now = datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc)

        self._insert_post(
            "published-post",
            "product-3b",
            "channel-beauty",
            status="PUBLISHED",
            created_at="2026-08-01T08:00:00+00:00",
            published_at="2026-08-01T08:00:00+00:00",
        )

        recent = route_product(self.conn, product, recent_now)
        after_cooldown = route_product(self.conn, product, old_now)

        self.assertEqual(recent, {"reason": "product_already_routed"})
        self.assertEqual(after_cooldown["channel_id"], "channel-beauty")

    def test_route_product_prefers_more_specific_match_then_code_tie_break(self):
        from acp.core.auto_scheduler import route_product

        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        self._insert_channel("channel-broad", "broad", niches=["my-pham"])
        self._insert_channel("channel-specific", "specific", niches=["my-pham", "gia-dung"])
        product = self._insert_product(
            "product-4",
            name="Set serum dưỡng ẩm kèm hộp đựng mỹ phẩm",
            category_code="my-pham gia-dung",
        )

        first = route_product(self.conn, product, now_utc)
        self.conn.execute("DELETE FROM channel WHERE id='channel-specific'")
        self._insert_channel("channel-alpha", "alpha", niches=["my-pham"])
        self._insert_channel("channel-zeta", "zeta", niches=["my-pham"])

        second = route_product(self.conn, product, now_utc)

        self.assertEqual(first["channel_id"], "channel-specific")
        self.assertEqual(second["channel_code"], "alpha")

    def test_route_product_reports_only_actually_matched_niches(self):
        from acp.core.auto_scheduler import route_product

        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        self._insert_channel("channel-mixed", "mixed", niches=["my-pham", "gia-dung"])
        product = self._insert_product("product-4b", name="Serum phục hồi da", category_code="my-pham")

        routed = route_product(self.conn, product, now_utc)

        self.assertEqual(routed["matched_niches"], ["my-pham"])
        self.assertEqual(routed["match_count"], 1)

    def test_rank_slots_uses_same_account_hour_history_and_falls_back_to_configured_order(self):
        from acp.core.auto_scheduler import rank_slots

        local_date = datetime(2026, 8, 20).date()
        slots = ["09:30", "12:30", "20:30"]
        self._insert_channel("channel-history", "history", niches=["my-pham"])
        self._insert_channel("channel-fallback", "fallback", niches=["my-pham"])
        self._insert_channel("other-account", "other", niches=["my-pham"])
        product = self._insert_product("metric-product", name="Sữa rửa mặt dịu nhẹ", category_code="my-pham")

        for index, clicks in enumerate((120, 110, 100, 90, 80), start=1):
            post_id = f"hist-post-{index}"
            target_id = f"hist-target-{index}"
            published_at = datetime(2026, 8, 19, 13, 30, tzinfo=timezone.utc) + timedelta(days=index)
            self._insert_post(post_id, product["id"], "channel-history", status="PUBLISHED", created_at=published_at.isoformat(timespec="seconds"))
            self._insert_publish_target(
                target_id,
                post_id,
                "channel-history",
                status="SUCCESS",
                scheduled_at=published_at.isoformat(timespec="seconds"),
                updated_at=published_at.isoformat(timespec="seconds"),
                external_post_id=f"threads-{index}",
            )
            self._insert_post_metrics(post_id, clicks=clicks)

        for index, clicks in enumerate((10, 10, 10, 10, 10), start=1):
            post_id = f"other-post-{index}"
            target_id = f"other-target-{index}"
            published_at = datetime(2026, 8, 19, 2, 30, tzinfo=timezone.utc) + timedelta(days=index)
            self._insert_post(post_id, product["id"], "other-account", status="PUBLISHED", created_at=published_at.isoformat(timespec="seconds"))
            self._insert_publish_target(
                target_id,
                post_id,
                "other-account",
                status="SUCCESS",
                scheduled_at=published_at.isoformat(timespec="seconds"),
                updated_at=published_at.isoformat(timespec="seconds"),
                external_post_id=f"other-{index}",
            )
            self._insert_post_metrics(post_id, clicks=clicks)

        ranked = rank_slots(self.conn, "channel-history", local_date, slots)
        fallback = rank_slots(self.conn, "channel-fallback", local_date, slots)

        self.assertEqual([row["slot"] for row in ranked], ["20:30", "09:30", "12:30"])
        self.assertEqual(ranked[0]["sample_size"], 5)
        self.assertGreater(ranked[0]["hour_score"], ranked[1]["hour_score"])
        self.assertEqual([row["slot"] for row in fallback], slots)
        self.assertEqual([row["sample_size"] for row in fallback], [0, 0, 0])

    def test_preflight_auto_target_rejects_unavailable_product(self):
        preflight_auto_target = self._preflight_auto_target()

        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        self._insert_channel("channel-beauty", "beauty", niches=["my-pham"])
        product = self._insert_product("product-stale-1", name="Serum dưỡng ẩm", is_available=0)
        self._insert_post("post-stale-1", product["id"], "channel-beauty", status="SCHEDULED")
        self._insert_publish_target(
            "target-stale-1", "post-stale-1", "channel-beauty",
            status="SCHEDULED", scheduled_at=now_utc.isoformat(timespec="seconds"), auto_scheduled=1)

        target = self.conn.execute("SELECT * FROM publish_target WHERE id='target-stale-1'").fetchone()
        post = self.conn.execute("SELECT * FROM post WHERE id='post-stale-1'").fetchone()
        channel = self.conn.execute("SELECT * FROM channel WHERE id='channel-beauty'").fetchone()

        self.assertEqual(preflight_auto_target(self.conn, target, post, channel, now_utc), (False, "product_unavailable"))

    def test_preflight_auto_target_rejects_stale_product_sync(self):
        preflight_auto_target = self._preflight_auto_target()

        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        old_seen = (now_utc - timedelta(hours=73)).isoformat(timespec="seconds")
        self._insert_channel("channel-beauty", "beauty", niches=["my-pham"])
        product = self._insert_product("product-stale-2", name="Kem dưỡng phục hồi")
        self.conn.execute("UPDATE product SET last_seen_at=? WHERE id=?", (old_seen, product["id"]))
        self._insert_post("post-stale-2", product["id"], "channel-beauty", status="SCHEDULED")
        self._insert_publish_target(
            "target-stale-2", "post-stale-2", "channel-beauty",
            status="SCHEDULED", scheduled_at=now_utc.isoformat(timespec="seconds"), auto_scheduled=1)

        target = self.conn.execute("SELECT * FROM publish_target WHERE id='target-stale-2'").fetchone()
        post = self.conn.execute("SELECT * FROM post WHERE id='post-stale-2'").fetchone()
        channel = self.conn.execute("SELECT * FROM channel WHERE id='channel-beauty'").fetchone()

        self.assertEqual(preflight_auto_target(self.conn, target, post, channel, now_utc), (False, "product_sync_stale"))

    def test_preflight_auto_target_accepts_product_synced_at_120_minute_boundary(self):
        preflight_auto_target = self._preflight_auto_target()

        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        boundary_seen = (now_utc - timedelta(minutes=120)).isoformat(timespec="seconds")
        self._insert_channel("channel-beauty", "beauty", niches=["my-pham"])
        product = self._insert_product("product-sync-boundary-ok", name="Kem dưỡng phục hồi")
        self.conn.execute("UPDATE product SET last_seen_at=? WHERE id=?", (boundary_seen, product["id"]))
        self._insert_post("post-sync-boundary-ok", product["id"], "channel-beauty", status="SCHEDULED")
        self._insert_publish_target(
            "target-sync-boundary-ok", "post-sync-boundary-ok", "channel-beauty",
            status="SCHEDULED", scheduled_at=now_utc.isoformat(timespec="seconds"), auto_scheduled=1)

        target = self.conn.execute("SELECT * FROM publish_target WHERE id='target-sync-boundary-ok'").fetchone()
        post = self.conn.execute("SELECT * FROM post WHERE id='post-sync-boundary-ok'").fetchone()
        channel = self.conn.execute("SELECT * FROM channel WHERE id='channel-beauty'").fetchone()

        self.assertEqual(preflight_auto_target(self.conn, target, post, channel, now_utc), (True, "ok"))

    def test_preflight_auto_target_rejects_product_synced_after_120_minutes(self):
        preflight_auto_target = self._preflight_auto_target()

        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        stale_seen = (now_utc - timedelta(minutes=121)).isoformat(timespec="seconds")
        self._insert_channel("channel-beauty", "beauty", niches=["my-pham"])
        product = self._insert_product("product-sync-boundary-stale", name="Kem dưỡng phục hồi")
        self.conn.execute("UPDATE product SET last_seen_at=? WHERE id=?", (stale_seen, product["id"]))
        self._insert_post("post-sync-boundary-stale", product["id"], "channel-beauty", status="SCHEDULED")
        self._insert_publish_target(
            "target-sync-boundary-stale", "post-sync-boundary-stale", "channel-beauty",
            status="SCHEDULED", scheduled_at=now_utc.isoformat(timespec="seconds"), auto_scheduled=1)

        target = self.conn.execute("SELECT * FROM publish_target WHERE id='target-sync-boundary-stale'").fetchone()
        post = self.conn.execute("SELECT * FROM post WHERE id='post-sync-boundary-stale'").fetchone()
        channel = self.conn.execute("SELECT * FROM channel WHERE id='channel-beauty'").fetchone()

        self.assertEqual(preflight_auto_target(self.conn, target, post, channel, now_utc), (False, "product_sync_stale"))

    def test_preflight_auto_target_rejects_empty_inventory(self):
        preflight_auto_target = self._preflight_auto_target()

        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        self._insert_channel("channel-beauty", "beauty", niches=["my-pham"])
        product = self._insert_product("product-stale-inventory", name="Kem dưỡng phục hồi")
        self.conn.execute("UPDATE product SET has_inventory=0 WHERE id=?", (product["id"],))
        self._insert_post("post-stale-inventory", product["id"], "channel-beauty", status="SCHEDULED")
        self._insert_publish_target(
            "target-stale-inventory", "post-stale-inventory", "channel-beauty",
            status="SCHEDULED", scheduled_at=now_utc.isoformat(timespec="seconds"), auto_scheduled=1)

        target = self.conn.execute("SELECT * FROM publish_target WHERE id='target-stale-inventory'").fetchone()
        post = self.conn.execute("SELECT * FROM post WHERE id='post-stale-inventory'").fetchone()
        channel = self.conn.execute("SELECT * FROM channel WHERE id='channel-beauty'").fetchone()

        self.assertEqual(preflight_auto_target(self.conn, target, post, channel, now_utc), (False, "product_inventory_empty"))

    def test_preflight_auto_target_rejects_unknown_inventory(self):
        preflight_auto_target = self._preflight_auto_target()

        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        self._insert_channel("channel-beauty", "beauty", niches=["my-pham"])
        product = self._insert_product("product-stale-unknown-inventory", name="Kem dưỡng phục hồi",
                                       has_inventory=None)
        self._insert_post("post-stale-unknown-inventory", product["id"], "channel-beauty", status="SCHEDULED")
        self._insert_publish_target(
            "target-stale-unknown-inventory", "post-stale-unknown-inventory", "channel-beauty",
            status="SCHEDULED", scheduled_at=now_utc.isoformat(timespec="seconds"), auto_scheduled=1)

        target = self.conn.execute("SELECT * FROM publish_target WHERE id='target-stale-unknown-inventory'").fetchone()
        post = self.conn.execute("SELECT * FROM post WHERE id='post-stale-unknown-inventory'").fetchone()
        channel = self.conn.execute("SELECT * FROM channel WHERE id='channel-beauty'").fetchone()

        self.assertEqual(preflight_auto_target(self.conn, target, post, channel, now_utc), (False, "product_inventory_empty"))

    def test_preflight_auto_target_rejects_invalid_affiliate_url(self):
        preflight_auto_target = self._preflight_auto_target()

        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        self._insert_channel("channel-beauty", "beauty", niches=["my-pham"])
        product = self._insert_product("product-stale-3", name="Kem chống nắng dịu da")
        self._insert_post("post-stale-3", product["id"], "channel-beauty", status="SCHEDULED")
        self.conn.execute("UPDATE post SET affiliate_link=? WHERE id='post-stale-3'", ("not a url with secret-token-123",))
        self._insert_publish_target(
            "target-stale-3", "post-stale-3", "channel-beauty",
            status="SCHEDULED", scheduled_at=now_utc.isoformat(timespec="seconds"), auto_scheduled=1)

        target = self.conn.execute("SELECT * FROM publish_target WHERE id='target-stale-3'").fetchone()
        post = self.conn.execute("SELECT * FROM post WHERE id='post-stale-3'").fetchone()
        channel = self.conn.execute("SELECT * FROM channel WHERE id='channel-beauty'").fetchone()

        self.assertEqual(preflight_auto_target(self.conn, target, post, channel, now_utc), (False, "affiliate_link_invalid"))

    def test_preflight_auto_target_rejects_stale_affiliate_link_status(self):
        preflight_auto_target = self._preflight_auto_target()

        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        self._insert_channel("channel-beauty", "beauty", niches=["my-pham"])
        product = self._insert_product("product-stale-link", name="Kem chống nắng dịu da")
        self.conn.execute("UPDATE product SET affiliate_link_status=? WHERE id=?", ("STALE", product["id"]))
        self._insert_post("post-stale-link", product["id"], "channel-beauty", status="SCHEDULED")
        self._insert_publish_target(
            "target-stale-link", "post-stale-link", "channel-beauty",
            status="SCHEDULED", scheduled_at=now_utc.isoformat(timespec="seconds"), auto_scheduled=1)

        target = self.conn.execute("SELECT * FROM publish_target WHERE id='target-stale-link'").fetchone()
        post = self.conn.execute("SELECT * FROM post WHERE id='post-stale-link'").fetchone()
        channel = self.conn.execute("SELECT * FROM channel WHERE id='channel-beauty'").fetchone()

        self.assertEqual(preflight_auto_target(self.conn, target, post, channel, now_utc), (False, "affiliate_link_invalid"))

    def test_preflight_auto_target_rechecks_channel_hard_filter(self):
        preflight_auto_target = self._preflight_auto_target()

        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        self._insert_channel("channel-beauty", "beauty", niches=["my-pham"])
        product = self._insert_product("product-stale-4", name="Serum dưỡng ẩm", category_code="my-pham")
        self._insert_post("post-stale-4", product["id"], "channel-beauty", status="SCHEDULED")
        self._insert_publish_target(
            "target-stale-4", "post-stale-4", "channel-beauty",
            status="SCHEDULED", scheduled_at=now_utc.isoformat(timespec="seconds"), auto_scheduled=1)
        self.conn.execute("UPDATE channel SET niches=? WHERE id='channel-beauty'", (json.dumps(["gia-dung"]),))

        target = self.conn.execute("SELECT * FROM publish_target WHERE id='target-stale-4'").fetchone()
        post = self.conn.execute("SELECT * FROM post WHERE id='post-stale-4'").fetchone()
        channel = self.conn.execute("SELECT * FROM channel WHERE id='channel-beauty'").fetchone()

        self.assertEqual(
            preflight_auto_target(self.conn, target, post, channel, now_utc),
            (False, "product_no_longer_matches_channel"),
        )

    def test_preflight_auto_target_rejects_already_published_target_for_idempotency(self):
        preflight_auto_target = self._preflight_auto_target()

        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        self._insert_channel("channel-beauty", "beauty", niches=["my-pham"])
        product = self._insert_product("product-stale-5", name="Sữa rửa mặt dịu nhẹ")
        self._insert_post("post-stale-5", product["id"], "channel-beauty", status="PUBLISHED")
        self._insert_publish_target(
            "target-stale-5", "post-stale-5", "channel-beauty",
            status="SUCCESS", scheduled_at=now_utc.isoformat(timespec="seconds"),
            external_post_id="threads-existing", auto_scheduled=1)

        target = self.conn.execute("SELECT * FROM publish_target WHERE id='target-stale-5'").fetchone()
        post = self.conn.execute("SELECT * FROM post WHERE id='post-stale-5'").fetchone()
        channel = self.conn.execute("SELECT * FROM channel WHERE id='channel-beauty'").fetchone()

        self.assertEqual(preflight_auto_target(self.conn, target, post, channel, now_utc), (False, "target_already_published"))

    def test_preflight_auto_target_accepts_fresh_matching_auto_target(self):
        preflight_auto_target = self._preflight_auto_target()

        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        self._insert_channel("channel-beauty", "beauty", niches=["my-pham"])
        product = self._insert_product("product-fresh-1", name="Kem dưỡng phục hồi")
        self.conn.execute("UPDATE product SET last_seen_at=? WHERE id=?", (now_utc.isoformat(timespec="seconds"), product["id"]))
        self._insert_post("post-fresh-1", product["id"], "channel-beauty", status="SCHEDULED")
        self._insert_publish_target(
            "target-fresh-1", "post-fresh-1", "channel-beauty",
            status="SCHEDULED", scheduled_at=now_utc.isoformat(timespec="seconds"), auto_scheduled=1)

        target = self.conn.execute("SELECT * FROM publish_target WHERE id='target-fresh-1'").fetchone()
        post = self.conn.execute("SELECT * FROM post WHERE id='post-fresh-1'").fetchone()
        channel = self.conn.execute("SELECT * FROM channel WHERE id='channel-beauty'").fetchone()

        self.assertEqual(preflight_auto_target(self.conn, target, post, channel, now_utc), (True, "ok"))


class AutoScheduleFillTests(unittest.TestCase):
    def setUp(self):
        self.previous_db_path = db.DB_PATH
        self.previous_adapter = os.environ.get("ACP_ADAPTER")
        self.previous_source = os.environ.get("ACP_SOURCE")
        self.tempdir = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tempdir.name, "auto-fill.db")
        os.environ["ACP_ADAPTER"] = "mock"
        os.environ["ACP_SOURCE"] = "mock"
        db.init_db()
        self.conn = db.connect()
        self.conn.execute(
            "INSERT INTO campaign (id, code, name, created_at) VALUES (?,?,?,?)",
            ("camp-1", "camp", "Campaign", db.now()),
        )
        self.conn.execute(
            "INSERT INTO caption_template (id, code, name, body, is_active) VALUES (?,?,?,?,1)",
            ("tpl-1", "price_drop", "Price Drop", "price_drop"),
        )
        from acp.core import pipeline, scoring

        self.previous_media_dir = pipeline.MEDIA_DIR
        pipeline.MEDIA_DIR = os.path.join(self.tempdir.name, "media")
        test_filters = dict(scoring.DEFAULT_FILTERS, max_per_category_per_day=20)
        scoring.save_config(self.conn, scoring.DEFAULT_WEIGHTS, test_filters, "auto fill tests")

    def tearDown(self):
        from acp.adapters import factory
        from acp.core import pipeline

        pipeline.MEDIA_DIR = self.previous_media_dir
        self.conn.close()
        db.DB_PATH = self.previous_db_path
        self.tempdir.cleanup()
        if self.previous_adapter is None:
            os.environ.pop("ACP_ADAPTER", None)
        else:
            os.environ["ACP_ADAPTER"] = self.previous_adapter
        if self.previous_source is None:
            os.environ.pop("ACP_SOURCE", None)
        else:
            os.environ["ACP_SOURCE"] = self.previous_source
        factory.reset_cache()

    def _insert_channel(
        self,
        channel_id="channel-1",
        *,
        auto_schedule_enabled=1,
        daily_post_target=2,
        daily_post_cap=3,
        posting_slots=None,
    ):
        if posting_slots is None:
            posting_slots = ["09:30", "12:30", "20:30"]
        self.conn.execute(
            """
            INSERT INTO channel (
                id, code, platform, handle, status, enabled, auto_schedule_enabled,
                daily_post_target, daily_post_cap, posting_timezone, posting_slots,
                min_gap_minutes, niches, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                channel_id,
                channel_id,
                "threads",
                f"@{channel_id}",
                "ACTIVE",
                1,
                auto_schedule_enabled,
                daily_post_target,
                daily_post_cap,
                "Asia/Bangkok",
                json.dumps(posting_slots),
                90,
                json.dumps(["my-pham"], ensure_ascii=False),
                db.now(),
            ),
        )

    def _insert_products(self, count):
        ts = db.now()
        for index in range(count):
            self.conn.execute(
                """
                INSERT INTO product (
                    id, source, merchant, external_product_id, name, description,
                    current_price, original_price, commission_value, commission_rate,
                    category_code, rating, review_count, sold_count, image_url_original,
                    image_path_local, product_url, is_available, has_inventory, last_seen_at,
                    created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"product-{index}",
                    "mock",
                    "Shop",
                    f"product-{index}",
                    f"Serum duong am {index}",
                    "Duong am da mat",
                    100000 + index,
                    150000 + index,
                    50000 - index,
                    0.1,
                    "my-pham",
                    4.8,
                    100 + index,
                    1000 - index,
                    "https://img.test/product.jpg",
                    None,
                    f"https://example.test/product-{index}",
                    1,
                    1,
                    ts,
                    ts,
                    ts,
                ),
            )

    def _insert_catalog_product(
        self,
        product_id="catalog-product-1",
        *,
        category_code="my-pham",
        name=None,
        rating=4.8,
        review_count=120,
        commission_value=50000,
    ):
        ts = db.now()
        self.conn.execute(
            """
            INSERT INTO product (
                id, source, merchant, external_product_id, name, description,
                current_price, original_price, commission_value, commission_rate,
                category_code, rating, review_count, sold_count, image_url_original,
                image_path_local, product_url, is_available, last_seen_at,
                created_at, updated_at, provider, shop_name, detail_link, main_image_url,
                commission_amount, commission_rate_percent, units_sold, has_inventory,
                score, affiliate_link_status, last_synced_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                product_id,
                "accesstrade_tiktok",
                "TikTok Shop",
                f"external-{product_id}",
                name or "Serum duong am catalog",
                "Duong am da mat",
                100000,
                150000,
                commission_value,
                0.1,
                category_code,
                rating,
                review_count,
                1000,
                "https://img.test/catalog.jpg",
                os.path.join(self.tempdir.name, "catalog-source.jpg"),
                f"https://example.test/catalog/{product_id}",
                1,
                ts,
                ts,
                ts,
                "ACCESSTRADE_TIKTOK",
                "TikTok Shop",
                f"https://shop.example.test/{product_id}",
                "https://img.test/catalog-main.jpg",
                commission_value,
                12.5,
                1000,
                1,
                99.0,
                "NOT_CREATED",
                ts,
            ),
        )
        return self.conn.execute("SELECT * FROM product WHERE id=?", (product_id,)).fetchone()

    def test_fill_auto_schedule_fills_default_two_slots_per_day_for_48_hours(self):
        from acp.core import pipeline

        self._insert_channel()
        self._insert_products(8)
        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)

        stats = pipeline.fill_auto_schedule(self.conn, "camp", now_utc=now_utc)

        targets = self.conn.execute(
            "SELECT scheduled_at, auto_scheduled FROM publish_target ORDER BY scheduled_at"
        ).fetchall()
        jobs = self.conn.execute(
            "SELECT COUNT(*) FROM job_queue WHERE job_type='PUBLISH_POST'"
        ).fetchone()[0]
        posts = self.conn.execute(
            "SELECT status, reviewed_by FROM post ORDER BY scheduled_at"
        ).fetchall()

        self.assertEqual(stats["scheduled"], 4)
        self.assertEqual([row["scheduled_at"] for row in targets], [
            "2026-08-20T02:30:00+00:00",
            "2026-08-20T05:30:00+00:00",
            "2026-08-21T02:30:00+00:00",
            "2026-08-21T05:30:00+00:00",
        ])
        self.assertEqual([row["auto_scheduled"] for row in targets], [1, 1, 1, 1])
        self.assertEqual(jobs, 4)
        self.assertTrue(all(row["status"] == "SCHEDULED" for row in posts))
        self.assertTrue(all(row["reviewed_by"] == "auto_scheduler" for row in posts))

    def test_fill_auto_schedule_third_target_respects_existing_slots_and_daily_cap(self):
        from acp.core import pipeline

        self._insert_channel(daily_post_target=3, daily_post_cap=3)
        self._insert_products(8)
        self.conn.execute(
            """
            INSERT INTO post (
                id, product_id, channel_id, campaign_id, variant_code, caption_body,
                disclosure_text, caption_final, affiliate_link, status, scheduled_at,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "existing-post",
                "product-0",
                "channel-1",
                "camp-1",
                "A",
                "caption",
                "Ad",
                "caption",
                "https://example.test/aff",
                "SCHEDULED",
                "2026-08-20T02:30:00+00:00",
                db.now(),
                db.now(),
            ),
        )
        self.conn.execute(
            """
            INSERT INTO publish_target (
                id, post_id, channel_id, status, scheduled_at, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                "existing-target",
                "existing-post",
                "channel-1",
                "SCHEDULED",
                "2026-08-20T02:30:00+00:00",
                db.now(),
                db.now(),
            ),
        )
        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)

        stats = pipeline.fill_auto_schedule(self.conn, "camp", now_utc=now_utc)

        targets = self.conn.execute(
            "SELECT scheduled_at FROM publish_target ORDER BY scheduled_at"
        ).fetchall()
        slots = [row["scheduled_at"] for row in targets]
        per_day = {}
        for slot in slots:
            per_day[slot[:10]] = per_day.get(slot[:10], 0) + 1

        self.assertEqual(stats["scheduled"], 5)
        self.assertEqual(len(slots), len(set(slots)))
        self.assertLessEqual(per_day["2026-08-20"], 3)
        self.assertLessEqual(per_day["2026-08-21"], 3)
        self.assertIn("2026-08-21T13:30:00+00:00", slots)

    def test_fill_auto_schedule_is_idempotent_and_never_reuses_products(self):
        from acp.core import pipeline

        self._insert_channel()
        self._insert_products(8)
        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)

        first = pipeline.fill_auto_schedule(self.conn, "camp", now_utc=now_utc)
        second = pipeline.fill_auto_schedule(self.conn, "camp", now_utc=now_utc)

        posts = self.conn.execute(
            "SELECT product_id FROM post WHERE product_id IS NOT NULL"
        ).fetchall()
        targets = self.conn.execute("SELECT COUNT(*) FROM publish_target").fetchone()[0]

        self.assertEqual(first["scheduled"], 4)
        self.assertEqual(second["scheduled"], 0)
        self.assertEqual(targets, 4)
        self.assertEqual(
            len({row["product_id"] for row in posts}),
            len(posts),
        )

    def test_fill_auto_schedule_keeps_auto_off_channels_review_only(self):
        from acp.core import pipeline

        self._insert_channel(auto_schedule_enabled=0)
        self._insert_products(4)
        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)

        stats = pipeline.fill_auto_schedule(self.conn, "camp", now_utc=now_utc)

        posts = self.conn.execute("SELECT status FROM post ORDER BY created_at").fetchall()
        targets = self.conn.execute("SELECT COUNT(*) FROM publish_target").fetchone()[0]
        jobs = self.conn.execute(
            "SELECT COUNT(*) FROM job_queue WHERE job_type='PUBLISH_POST'"
        ).fetchone()[0]

        self.assertEqual(stats["scheduled"], 0)
        self.assertEqual(stats["review"], 2)
        self.assertTrue(all(row["status"] in ("PENDING_REVIEW", "DRAFT") for row in posts))
        self.assertEqual(targets, 0)
        self.assertEqual(jobs, 0)

    def test_fill_auto_schedule_schedules_synced_tiktok_catalog_products(self):
        from acp.adapters.accesstrade_client import LinkResult
        from acp.adapters import factory
        from acp.core import pipeline

        self._insert_channel()
        self._insert_catalog_product()
        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)

        class _ProductClient:
            def create_product_link(self, detail_link, *, post_id, external_product_id):
                return LinkResult(
                    full_url=f"https://go.example.test/full/{post_id}",
                    short_url=f"https://go.example.test/s/{post_id}",
                )

        class _Storage:
            def put(self, image_path):
                return "https://cdn.example.test/catalog.jpg"

        original_build_context = factory.build_context
        original_compose = pipeline.imaging.compose
        try:
            factory.build_context = lambda: {"product_client": _ProductClient(), "storage": _Storage()}
            pipeline.imaging.compose = lambda *args, **kwargs: os.path.join(self.tempdir.name, "catalog-composed.jpg")

            stats = pipeline.fill_auto_schedule(self.conn, "camp", now_utc=now_utc)
        finally:
            factory.build_context = original_build_context
            pipeline.imaging.compose = original_compose

        post = self.conn.execute(
            "SELECT * FROM post WHERE product_id='catalog-product-1'"
        ).fetchone()
        target = self.conn.execute(
            "SELECT * FROM publish_target WHERE post_id=?",
            (post["id"] if post else None,),
        ).fetchone()
        job = self.conn.execute(
            "SELECT * FROM job_queue WHERE job_type='PUBLISH_POST'"
        ).fetchone()

        self.assertEqual(stats["scheduled"], 1)
        self.assertIsNotNone(post)
        self.assertEqual(post["status"], "SCHEDULED")
        self.assertEqual(post["reviewed_by"], "auto_scheduler")
        self.assertIn('"provider": "accesstrade_product"', post["sub_id_payload"])
        self.assertIn('"sub1": "' + post["id"] + '"', post["sub_id_payload"])
        self.assertEqual(target["scheduled_at"], "2026-08-20T02:30:00+00:00")
        self.assertEqual(target["auto_scheduled"], 1)
        self.assertIsNotNone(job)

    def test_auto_catalog_candidates_and_preflight_apply_active_quality_filters(self):
        from acp.core import auto_scheduler, pipeline

        self._insert_channel()
        bad_product = self._insert_catalog_product(
            rating=0,
            review_count=0,
            commission_value=0,
        )
        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        channel = self.conn.execute("SELECT * FROM channel WHERE id='channel-1'").fetchone()

        candidates = pipeline._candidate_products_for_channel(self.conn, channel, limit=10, now_utc=now_utc)

        self.assertEqual(candidates, [])

        self.conn.execute(
            """
            INSERT INTO post (
                id, product_id, channel_id, campaign_id, variant_code, caption_body,
                disclosure_text, caption_final, affiliate_link, status, scheduled_at,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "bad-quality-post",
                bad_product["id"],
                "channel-1",
                "camp-1",
                "A",
                "caption",
                "Ad",
                "caption",
                "https://go.example.test/bad-quality",
                "SCHEDULED",
                "2026-08-20T09:30:00+07:00",
                db.now(),
                db.now(),
            ),
        )
        self.conn.execute(
            """
            INSERT INTO publish_target (
                id, post_id, channel_id, status, scheduled_at, auto_scheduled, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                "bad-quality-target",
                "bad-quality-post",
                "channel-1",
                "SCHEDULED",
                "2026-08-20T09:30:00+07:00",
                1,
                db.now(),
                db.now(),
            ),
        )
        post = self.conn.execute("SELECT * FROM post WHERE id='bad-quality-post'").fetchone()
        target = self.conn.execute("SELECT * FROM publish_target WHERE id='bad-quality-target'").fetchone()

        self.assertEqual(
            auto_scheduler.preflight_auto_target(
                self.conn,
                target,
                post,
                channel,
                now_utc,
                eligibility_checker=pipeline.current_auto_product_eligibility,
            ),
            (False, "product_quality_filter"),
        )

    def test_auto_preflight_uses_channel_niches_not_global_scoring_niches(self):
        from acp.core import pipeline, scoring

        scoring.save_config(
            self.conn,
            scoring.DEFAULT_WEIGHTS,
            dict(scoring.DEFAULT_FILTERS, niches=["gia-dung"]),
            "global niche must not override channel checkbox",
        )
        self._insert_channel()
        self._insert_products(1)
        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        product = self.conn.execute("SELECT * FROM product WHERE id='product-0'").fetchone()
        channel = self.conn.execute("SELECT * FROM channel WHERE id='channel-1'").fetchone()

        self.assertEqual(
            pipeline.current_auto_product_eligibility(
                self.conn,
                product,
                channel,
                now_utc,
                slot_at="2026-08-20T09:30:00+07:00",
            ),
            (True, "ok"),
        )

    def test_fill_auto_schedule_keeps_external_artifact_calls_outside_write_transaction(self):
        from acp.adapters.accesstrade_client import LinkResult
        from acp.core import pipeline
        from acp.adapters import factory

        self._insert_channel()
        self._insert_products(4)
        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        state = {"in_write_tx": False}
        violations = []

        class _Source:
            def create_tracking_link(self, product_url, sub_ids):
                if state["in_write_tx"]:
                    violations.append("tracking")
                return LinkResult(full_url="https://go.example.test/full", short_url="https://go.example.test/short")

        class _Storage:
            def put(self, image_path):
                if state["in_write_tx"]:
                    violations.append("storage")
                return "https://cdn.example.test/image.jpg"

        original_build_context = factory.build_context
        original_transaction = pipeline.transaction
        original_compose = pipeline.imaging.compose

        @contextmanager
        def tracking_transaction(conn):
            with original_transaction(conn) as active:
                state["in_write_tx"] = True
                try:
                    yield active
                finally:
                    state["in_write_tx"] = False

        def compose_without_side_effects(*args, **kwargs):
            if state["in_write_tx"]:
                violations.append("image")
            return os.path.join(self.tempdir.name, "composed.jpg")

        try:
            factory.build_context = lambda: {"source": _Source(), "storage": _Storage()}
            pipeline.transaction = tracking_transaction
            pipeline.imaging.compose = compose_without_side_effects

            stats = pipeline.fill_auto_schedule(self.conn, "camp", now_utc=now_utc)
        finally:
            factory.build_context = original_build_context
            pipeline.transaction = original_transaction
            pipeline.imaging.compose = original_compose

        self.assertEqual(stats["scheduled"], 4)
        self.assertEqual(violations, [])

    def test_fill_auto_schedule_rechecks_current_catalog_eligibility_inside_transaction(self):
        from acp.adapters.accesstrade_client import LinkResult
        from acp.adapters import factory
        from acp.core import pipeline, scoring

        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)

        class _ProductClient:
            def create_product_link(self, detail_link, *, post_id, external_product_id):
                return LinkResult(
                    full_url=f"https://go.example.test/full/{post_id}",
                    short_url=f"https://go.example.test/s/{post_id}",
                )

        class _Storage:
            def put(self, image_path):
                return "https://cdn.example.test/catalog.jpg"

        def assert_no_persisted_rows(conn):
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM post WHERE product_id='catalog-product-1'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM publish_target").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM job_queue WHERE job_type='PUBLISH_POST'").fetchone()[0], 0)

        cases = {
            "affiliate_link_status_unavailable": lambda conn: conn.execute(
                "UPDATE product SET affiliate_link_status='UNAVAILABLE' WHERE id='catalog-product-1'"
            ),
            "blocked_category_config_changed": lambda conn: scoring.save_config(
                conn,
                scoring.DEFAULT_WEIGHTS,
                dict(scoring.DEFAULT_FILTERS, blocked_categories=["my-pham"], max_per_category_per_day=20),
                "block current category during auto fill",
            ),
            "channel_niche_config_changed": lambda conn: conn.execute(
                "UPDATE channel SET niches=? WHERE id='channel-1'",
                (json.dumps(["gia-dung"], ensure_ascii=False),),
            ),
            "category_day_cap_now_full": lambda conn: (
                scoring.save_config(
                    conn,
                    scoring.DEFAULT_WEIGHTS,
                    dict(scoring.DEFAULT_FILTERS, max_per_category_per_day=1),
                    "lower category cap during auto fill",
                ),
                conn.execute(
                    """
                    INSERT INTO product (
                        id, source, merchant, external_product_id, name, description,
                        current_price, original_price, commission_value, commission_rate,
                        category_code, rating, review_count, sold_count, image_url_original,
                        image_path_local, product_url, is_available, last_seen_at,
                        created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "category-cap-product",
                        "mock",
                        "Shop",
                        "category-cap-product",
                        "Serum da du len lich",
                        "",
                        100000,
                        150000,
                        50000,
                        0.1,
                        "my-pham",
                        4.8,
                        120,
                        500,
                        "https://img.test/product.jpg",
                        None,
                        "https://example.test/category-cap-product",
                        1,
                        db.now(),
                        db.now(),
                        db.now(),
                    ),
                ),
                conn.execute(
                    """
                    INSERT INTO post (
                        id, product_id, channel_id, campaign_id, variant_code, caption_body,
                        disclosure_text, caption_final, affiliate_link, status, scheduled_at,
                        created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "category-cap-post",
                        "category-cap-product",
                        "channel-1",
                        "camp-1",
                        "A",
                        "caption",
                        "Ad",
                        "caption",
                        "https://example.test/aff",
                        "PUBLISHED",
                        "2026-08-20T08:00:00+07:00",
                        db.now(),
                        db.now(),
                    ),
                ),
            ),
        }

        for name, mutate in cases.items():
            with self.subTest(name=name):
                self.conn.execute("DELETE FROM job_queue")
                self.conn.execute("DELETE FROM publish_target")
                self.conn.execute("DELETE FROM post_channel_selection")
                self.conn.execute("DELETE FROM audit_log")
                self.conn.execute("DELETE FROM post")
                self.conn.execute("DELETE FROM product")
                self.conn.execute("DELETE FROM channel")
                scoring.save_config(
                    self.conn,
                    scoring.DEFAULT_WEIGHTS,
                    dict(scoring.DEFAULT_FILTERS, max_per_category_per_day=20),
                    f"reset {name}",
                )
                self._insert_channel()
                self._insert_catalog_product()

                original_build_context = factory.build_context
                original_compose = pipeline.imaging.compose
                original_prepare = pipeline._prepare_auto_sales_post_artifacts
                mutated = {"done": False}

                def prepare_then_mutate(*args, **kwargs):
                    prepared = original_prepare(*args, **kwargs)
                    if not mutated["done"]:
                        mutated["done"] = True
                        mutate(self.conn)
                    return prepared

                try:
                    factory.build_context = lambda: {"product_client": _ProductClient(), "storage": _Storage()}
                    pipeline.imaging.compose = lambda *args, **kwargs: os.path.join(self.tempdir.name, "catalog-composed.jpg")
                    pipeline._prepare_auto_sales_post_artifacts = prepare_then_mutate

                    stats = pipeline.fill_auto_schedule(self.conn, "camp", now_utc=now_utc)
                finally:
                    factory.build_context = original_build_context
                    pipeline.imaging.compose = original_compose
                    pipeline._prepare_auto_sales_post_artifacts = original_prepare

                self.assertEqual(stats["scheduled"], 0)
                assert_no_persisted_rows(self.conn)

    def test_fill_auto_schedule_uses_fresh_channel_auto_state_after_artifact_prep(self):
        from acp.core import pipeline

        self._insert_channel()
        self._insert_products(1)
        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        original_prepare = pipeline._prepare_auto_sales_post_artifacts
        toggled = {"done": False}

        def prepare_then_disable_auto(*args, **kwargs):
            prepared = original_prepare(*args, **kwargs)
            if not toggled["done"]:
                toggled["done"] = True
                self.conn.execute("UPDATE channel SET auto_schedule_enabled=0 WHERE id='channel-1'")
            return prepared

        pipeline._prepare_auto_sales_post_artifacts = prepare_then_disable_auto
        try:
            stats = pipeline.fill_auto_schedule(self.conn, "camp", now_utc=now_utc)
        finally:
            pipeline._prepare_auto_sales_post_artifacts = original_prepare

        post = self.conn.execute("SELECT * FROM post WHERE product_id='product-0'").fetchone()
        targets = self.conn.execute("SELECT COUNT(*) FROM publish_target").fetchone()[0]
        jobs = self.conn.execute("SELECT COUNT(*) FROM job_queue WHERE job_type='PUBLISH_POST'").fetchone()[0]

        self.assertEqual(stats["scheduled"], 0)
        self.assertEqual(stats["review"], 1)
        self.assertEqual(post["status"], "PENDING_REVIEW")
        self.assertIsNone(post["scheduled_at"])
        self.assertEqual(targets, 0)
        self.assertEqual(jobs, 0)

    def test_fill_auto_schedule_skips_legacy_products_with_unknown_inventory(self):
        from acp.core import pipeline

        self._insert_channel()
        self._insert_products(1)
        self.conn.execute("UPDATE product SET has_inventory=NULL WHERE id='product-0'")
        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)

        stats = pipeline.fill_auto_schedule(self.conn, "camp", now_utc=now_utc)

        targets = self.conn.execute("SELECT COUNT(*) FROM publish_target").fetchone()[0]
        posts = self.conn.execute("SELECT COUNT(*) FROM post WHERE product_id='product-0'").fetchone()[0]
        self.assertEqual(stats["scheduled"], 0)
        self.assertEqual(targets, 0)
        self.assertEqual(posts, 0)

    def test_fill_auto_schedule_category_cap_uses_selected_slot_local_day(self):
        from acp.core import pipeline, scoring

        scoring.save_config(
            self.conn,
            scoring.DEFAULT_WEIGHTS,
            dict(scoring.DEFAULT_FILTERS, max_per_category_per_day=1),
            "slot day cap regression",
        )
        self._insert_channel(daily_post_target=1, daily_post_cap=1, posting_slots=["09:30"])
        self._insert_products(2)
        self.conn.execute(
            """
            INSERT INTO post (
                id, product_id, channel_id, campaign_id, variant_code, caption_body,
                disclosure_text, caption_final, affiliate_link, status, scheduled_at,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "today-category-post",
                "product-0",
                "channel-1",
                "camp-1",
                "A",
                "caption",
                "Ad",
                "caption",
                "https://example.test/aff",
                "SCHEDULED",
                "2026-08-20T09:30:00+07:00",
                db.now(),
                db.now(),
            ),
        )
        self.conn.execute(
            """
            INSERT INTO publish_target (
                id, post_id, channel_id, status, scheduled_at, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                "today-category-target",
                "today-category-post",
                "channel-1",
                "SCHEDULED",
                "2026-08-20T09:30:00+07:00",
                db.now(),
                db.now(),
            ),
        )
        now_after_today_slot = datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc)

        stats = pipeline.fill_auto_schedule(self.conn, "camp", now_utc=now_after_today_slot)

        slots = [
            row["scheduled_at"]
            for row in self.conn.execute(
                "SELECT scheduled_at FROM publish_target WHERE id!='today-category-target' ORDER BY scheduled_at"
            ).fetchall()
        ]
        self.assertEqual(stats["scheduled"], 1)
        self.assertEqual(slots, ["2026-08-21T02:30:00+00:00"])

    def test_fill_auto_schedule_uses_exact_48_hour_horizon_not_two_local_dates(self):
        from acp.core import pipeline

        self._insert_channel()
        self._insert_products(8)
        now_utc = datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc)

        stats = pipeline.fill_auto_schedule(self.conn, "camp", now_utc=now_utc)

        slots = [
            row["scheduled_at"]
            for row in self.conn.execute(
                "SELECT scheduled_at FROM publish_target ORDER BY scheduled_at"
            ).fetchall()
        ]

        self.assertEqual(stats["scheduled"], 4)
        self.assertEqual(slots, [
            "2026-08-21T02:30:00+00:00",
            "2026-08-21T05:30:00+00:00",
            "2026-08-22T02:30:00+00:00",
            "2026-08-22T05:30:00+00:00",
        ])

    def test_fill_auto_schedule_clamps_malformed_core_target_to_three_slots(self):
        from acp.core import pipeline

        self._insert_channel(
            daily_post_target=5,
            daily_post_cap=5,
            posting_slots=["09:30", "12:30", "15:30", "18:30", "20:30"],
        )
        self._insert_products(12)
        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)

        stats = pipeline.fill_auto_schedule(self.conn, "camp", now_utc=now_utc)

        slots = [
            row["scheduled_at"]
            for row in self.conn.execute(
                "SELECT scheduled_at FROM publish_target ORDER BY scheduled_at"
            ).fetchall()
        ]

        self.assertEqual(stats["scheduled"], 6)
        self.assertEqual(slots, [
            "2026-08-20T02:30:00+00:00",
            "2026-08-20T05:30:00+00:00",
            "2026-08-20T08:30:00+00:00",
            "2026-08-21T02:30:00+00:00",
            "2026-08-21T05:30:00+00:00",
            "2026-08-21T08:30:00+00:00",
        ])

    def test_fill_auto_schedule_rechecks_slot_inside_transaction_on_collision(self):
        from acp.core import pipeline

        self._insert_channel()
        self._insert_products(8)
        now_utc = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
        self.conn.execute(
            """
            INSERT INTO post (
                id, product_id, channel_id, campaign_id, variant_code, caption_body,
                disclosure_text, caption_final, affiliate_link, status, scheduled_at,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "collision-post",
                "product-7",
                "channel-1",
                "camp-1",
                "A",
                "caption",
                "Ad",
                "caption",
                "https://example.test/aff",
                "SCHEDULED",
                "2026-08-20T09:30:00+07:00",
                db.now(),
                db.now(),
            ),
        )
        original_prepare = pipeline._prepare_auto_sales_post_artifacts
        injected = {"done": False}

        def inject_collision(*args, **kwargs):
            prepared = original_prepare(*args, **kwargs)
            if not injected["done"]:
                injected["done"] = True
                self.conn.execute(
                    """
                    INSERT INTO publish_target (
                        id, post_id, channel_id, status, scheduled_at, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        "collision-target",
                        "collision-post",
                        "channel-1",
                        "SCHEDULED",
                        "2026-08-20T09:30:00+07:00",
                        db.now(),
                        db.now(),
                    ),
                )
            return prepared

        pipeline._prepare_auto_sales_post_artifacts = inject_collision
        try:
            stats = pipeline.fill_auto_schedule(self.conn, "camp", now_utc=now_utc)
        finally:
            pipeline._prepare_auto_sales_post_artifacts = original_prepare

        slot_counts = {
            row["scheduled_at"]: row["n"]
            for row in self.conn.execute(
                """
                SELECT scheduled_at, COUNT(*) AS n
                FROM publish_target
                WHERE channel_id='channel-1'
                GROUP BY scheduled_at
                """
            ).fetchall()
        }

        self.assertEqual(slot_counts["2026-08-20T09:30:00+07:00"], 1)
        self.assertEqual(stats["scheduled"], 3)
        leaked_review_rows = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM post
            WHERE channel_id='channel-1'
              AND status='PENDING_REVIEW'
              AND id != 'collision-post'
            """
        ).fetchone()[0]
        self.assertEqual(leaked_review_rows, 0)


if __name__ == "__main__":
    unittest.main()
