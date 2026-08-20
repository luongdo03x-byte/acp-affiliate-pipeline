import json
import os
import sqlite3
import sys
import tempfile
import unittest
import importlib.util

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


if __name__ == "__main__":
    unittest.main()
