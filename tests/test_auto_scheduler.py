import json
import os
import sqlite3
import sys
import tempfile
import unittest
import importlib.util
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
        self.assertIn("1 <= target <= cap <= 3", result["error"])

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
    ):
        ts = updated_at or scheduled_at
        self.conn.execute(
            """
            INSERT INTO publish_target (
                id, post_id, channel_id, status, scheduled_at, external_post_id,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (target_id, post_id, channel_id, status, scheduled_at, external_post_id, ts, ts),
        )

    def _insert_post_metrics(self, post_id, *, clicks):
        self.conn.execute(
            "INSERT INTO post_metrics (post_id, clicks, updated_at) VALUES (?,?,?)",
            (post_id, clicks, db.now()),
        )

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

        candidates = candidate_channels(self.conn, product, now_utc)

        self.assertEqual([row["id"] for row in candidates], ["winner"])

    def test_candidate_channels_uses_now_utc_for_local_day_quota_checks(self):
        from acp.core.auto_scheduler import candidate_channels

        self._insert_channel("winner", "winner", niches=["my-pham"], posting_timezone="Asia/Bangkok", daily_post_cap=1)
        product = self._insert_product("product-2b", name="Kem dưỡng phục hồi", category_code="my-pham")
        self._insert_post("post-quota", "product-2b", "winner", status="SCHEDULED")
        self._insert_publish_target(
            "target-quota",
            "post-quota",
            "winner",
            status="SCHEDULED",
            scheduled_at="2026-08-21T09:30:00+07:00",
        )

        before_local_midnight = datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc)
        after_local_midnight = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)

        before = candidate_channels(self.conn, product, before_local_midnight)
        after = candidate_channels(self.conn, product, after_local_midnight)

        self.assertEqual([row["id"] for row in before], ["winner"])
        self.assertEqual(after, [])

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
                    image_path_local, product_url, is_available, last_seen_at,
                    created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    ts,
                    ts,
                    ts,
                ),
            )

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
            "2026-08-20T09:30:00+07:00",
            "2026-08-20T12:30:00+07:00",
            "2026-08-21T09:30:00+07:00",
            "2026-08-21T12:30:00+07:00",
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
                "existing-target",
                "existing-post",
                "channel-1",
                "SCHEDULED",
                "2026-08-20T09:30:00+07:00",
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
        self.assertIn("2026-08-21T20:30:00+07:00", slots)

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


if __name__ == "__main__":
    unittest.main()
