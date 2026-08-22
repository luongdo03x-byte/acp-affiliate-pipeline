import io
import os
import re
import tempfile
import unittest
from unittest import mock

from acp.tests.test_shopee_product_pool_v2 import _csv_bytes


class ShopeeImportTriggerResilienceTests(unittest.TestCase):
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
        from acp.core import db, shopee_csv_batches
        from acp.web import create_app

        self.db = db
        self.batches = shopee_csv_batches
        self.batches.reset_previews()
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "trigger-resilience.db")
        db.init_db()
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["uid"] = "operator"
            session["csrf"] = "csrf-test"
        self.csrf = "csrf-test"

    def tearDown(self):
        self.batches.reset_previews()
        self.db.DB_PATH = self.old_path
        self.tmp.cleanup()

    @staticmethod
    def _token(body: bytes) -> str:
        match = re.search(rb'name="preview_token"\s+value="([^"]+)"', body)
        if not match:
            raise AssertionError("preview token not rendered")
        return match.group(1).decode("utf-8")

    def _preview(self):
        return self.client.post(
            "/sanpham/shopee-import/preview",
            data={
                "_csrf": self.csrf,
                "files": [(io.BytesIO(_csv_bytes()), "batch.csv")],
            },
            content_type="multipart/form-data",
        )

    def test_queue_trigger_failure_does_not_rollback_or_invite_reimport(self):
        preview = self._preview()
        self.assertEqual(preview.status_code, 200)
        token = self._token(preview.data)

        with mock.patch(
            "acp.web.shopee_csv_import.queue_pending_products",
            side_effect=RuntimeError("secret upstream detail"),
        ):
            response = self.client.post(
                "/sanpham/shopee-import/confirm",
                data={"_csrf": self.csrf, "preview_token": token},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("trigger enrich tức thời gặp lỗi".encode("utf-8"), response.data)
        self.assertNotIn(b"secret upstream detail", response.data)
        conn = self.db.connect()
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM product WHERE provider='SHOPEE_AFFILIATE'").fetchone()[0],
                1,
            )
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM job_queue").fetchone()[0], 0)
        finally:
            conn.close()

        replay = self.client.post(
            "/sanpham/shopee-import/confirm",
            data={"_csrf": self.csrf, "preview_token": token},
        )
        self.assertEqual(replay.status_code, 410)


if __name__ == "__main__":
    unittest.main()
