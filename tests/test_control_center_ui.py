import os
import tempfile
import unittest

from acp.core import db, topic_engine


class ControlCenterUiTests(unittest.TestCase):
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
        db.DB_PATH = os.path.join(self.tmp.name, "ui.db")
        db.init_db()
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["uid"] = "operator"
            session["csrf"] = "csrf-test"

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def test_sidebar_hides_legacy_product_entries_and_exposes_auto_posting(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.data.decode("utf-8")
        self.assertIn('href="/auto-posting"', body)
        self.assertIn(">Auto Posting<", body)
        self.assertNotIn('href="/sanpham" class="nav-item', body)
        self.assertNotIn('href="/sanpham/shopee-bulk" class="nav-item', body)

    def test_layout_loads_favicon_and_control_center_styles(self):
        response = self.client.get("/")
        body = response.data.decode("utf-8")
        self.assertIn("favicon.svg", body)
        self.assertIn("control_center.css", body)
        icon = self.client.get("/static/favicon.svg")
        self.assertEqual(icon.status_code, 200)
        self.assertIn(b"<svg", icon.data)

    def test_product_pool_shows_background_enrich_all_controls_and_progress(self):
        response = self.client.get("/sanpham/shopee")
        self.assertEqual(response.status_code, 200)
        body = response.data.decode("utf-8")
        self.assertIn("Enrich toàn bộ", body)
        self.assertIn("Tiến độ enrich ảnh", body)
        self.assertIn("/sanpham/shopee/enrichment/all/start", body)
        self.assertIn("Retry lỗi", body)
        self.assertIn("data-enrich-all-progress", body)

    def test_enrich_all_status_endpoint_returns_progress_payload(self):
        response = self.client.get("/sanpham/shopee/enrichment/all/status")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["state"], "IDLE")
        self.assertIn("percent", payload)
        self.assertIn("processed", payload)
        self.assertIn("pending", payload)

    def test_product_pool_topic_filter_renders_hierarchy_labels(self):
        response = self.client.get("/sanpham/shopee")
        self.assertEqual(response.status_code, 200)
        body = response.data.decode("utf-8")
        self.assertIn("Chủ đề", body)
        self.assertIn("Thời trang nữ", body)

    def test_product_pool_exposes_dynamic_topic_management(self):
        conn = db.connect()
        try:
            parent = topic_engine.topic_by_code(conn, "thoi-trang-nu")
            topic_engine.create_topic(
                conn, code="do-mac-nha", name="Đồ mặc nhà", topic_type="AUTO",
                parent_id=parent["id"], confidence=0.9,
            )
        finally:
            conn.close()
        response = self.client.get("/sanpham/shopee")
        self.assertEqual(response.status_code, 200)
        body = response.data.decode("utf-8")
        self.assertIn("Quản lý chủ đề tự động", body)
        self.assertIn("Đổi tên", body)
        self.assertIn("Merge vào", body)
        self.assertIn("Xóa", body)


if __name__ == "__main__":
    unittest.main()
