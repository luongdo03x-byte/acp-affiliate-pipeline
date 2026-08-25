import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from acp.core import db
from acp.core.system_settings import publish_worker_enabled


class AutoPostingJobControlsTests(unittest.TestCase):
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
        from acp.web import create_app

        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "controls.db")
        db.init_db()
        self.conn = db.connect()
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO campaign (id,code,name,is_active,created_at) VALUES ('camp','gd2026','Campaign',1,?)",
            (stamp,),
        )
        self.conn.execute(
            "INSERT INTO caption_template (id,code,name,body,is_active) VALUES ('tpl','price_drop','Price','price_drop',1)"
        )
        self.conn.execute(
            """INSERT INTO channel (
                 id,code,platform,handle,status,enabled,niches,auto_schedule_enabled,
                 daily_post_target,daily_post_cap,posting_timezone,posting_slots,created_at)
               VALUES ('ch','threads-main','threads','@account','ACTIVE',1,'[\"thoi-trang-nu\"]',0,
                       2,3,'Asia/Bangkok','[\"09:30\",\"20:30\"]',?)""",
            (stamp,),
        )
        self.conn.execute(
            """INSERT INTO channel (
                 id,code,platform,handle,status,enabled,niches,auto_schedule_enabled,
                 daily_post_target,daily_post_cap,posting_timezone,posting_slots,created_at)
               VALUES ('fb','facebook-main','facebook','fb-page','ACTIVE',1,'[]',0,
                       2,3,'Asia/Bangkok','[\"09:30\",\"20:30\"]',?)""",
            (stamp,),
        )

        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["uid"] = "operator"
            session["csrf"] = "csrf-test"
        self.csrf = "csrf-test"

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def test_page_shows_job_controls_and_threads_account_even_without_plans(self):
        response = self.client.get("/auto-posting")
        self.assertEqual(response.status_code, 200)
        body = response.data.decode("utf-8")
        self.assertIn("Auto System", body)
        self.assertIn("Publish Worker", body)
        self.assertIn("Lấp lịch hôm nay + ngày mai", body)
        self.assertIn("Hôm nay + ngày mai", body)
        self.assertNotIn("Tạo lịch 48h ngay", body)
        self.assertNotIn("48-hour control center", body)
        self.assertIn("@account", body)
        self.assertIn("Bật Auto", body)
        self.assertNotIn("fb-page", body)

    def test_channel_auto_toggle_updates_only_threads_channel(self):
        response = self.client.post(
            "/auto-posting/channel/ch/auto-toggle",
            data={"_csrf": self.csrf, "enabled": "1"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        row = self.conn.execute("SELECT auto_schedule_enabled FROM channel WHERE id='ch'").fetchone()
        self.assertEqual(row["auto_schedule_enabled"], 1)

        rejected = self.client.post(
            "/auto-posting/channel/fb/auto-toggle",
            data={"_csrf": self.csrf, "enabled": "1"},
            follow_redirects=True,
        )
        self.assertEqual(rejected.status_code, 200)
        fb = self.conn.execute("SELECT auto_schedule_enabled FROM channel WHERE id='fb'").fetchone()
        self.assertEqual(fb["auto_schedule_enabled"], 0)
        self.assertIn("Threads", rejected.data.decode("utf-8"))

    def test_publish_worker_toggle_reuses_existing_durable_setting(self):
        self.assertFalse(publish_worker_enabled(self.conn))
        response = self.client.post(
            "/auto-posting/worker-toggle",
            data={"_csrf": self.csrf, "enabled": "1"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(publish_worker_enabled(self.conn))

    def test_run_scheduler_calls_existing_fill_only_and_never_drains_worker(self):
        with mock.patch(
            "acp.web.auto_posting.pipeline.fill_auto_schedule",
            return_value={"scheduled": 2, "review": 0, "skipped": 0, "cancelled": 0},
        ) as fill, mock.patch("acp.core.jobs.drain") as drain:
            response = self.client.post(
                "/auto-posting/run-scheduler",
                data={"_csrf": self.csrf},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 302)
        fill.assert_called_once()
        drain.assert_not_called()


if __name__ == "__main__":
    unittest.main()
