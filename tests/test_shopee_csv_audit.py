import io
import json
import os
import re
import tempfile
import unittest


HEADER = (
    "Mã sản phẩm,Tên sản phẩm,Giá,Doanh thu,Tên cửa hàng,"
    "Tỉ lệ hoa hồng,Hoa hồng,Link sản phẩm,Link ưu đãi\n"
)
CSV = (
    HEADER
    + "123,Secret Product Name,100,10k+,Secret Shop,5%,₫5,"
      "https://shopee.vn/product/1/123,https://s.shopee.vn/private-short-code\n"
).encode("utf-8")


class ShopeeCsvAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_password = os.environ.get("ACP_ADMIN_PASSWORD")
        os.environ["ACP_ADMIN_PASSWORD"] = "audit-test"

    @classmethod
    def tearDownClass(cls):
        if cls.old_password is None:
            os.environ.pop("ACP_ADMIN_PASSWORD", None)
        else:
            os.environ["ACP_ADMIN_PASSWORD"] = cls.old_password

    def setUp(self):
        from acp.core import db, shopee_csv_batches
        from acp.web import create_app

        self.db = db
        shopee_csv_batches.reset_previews()
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "audit.db")
        db.init_db()
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["uid"] = "operator"
            session["csrf"] = "csrf-audit"

    def tearDown(self):
        self.db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def _audit_rows(self):
        conn = self.db.connect()
        try:
            return conn.execute(
                "SELECT entity_id, action, detail FROM audit_log "
                "WHERE action IN ('shopee_csv_preview','shopee_csv_import_completed') ORDER BY id"
            ).fetchall()
        finally:
            conn.close()

    def test_preview_and_import_audit_only_aggregate_counts(self):
        preview = self.client.post(
            "/sanpham/shopee-import/preview",
            data={
                "_csrf": "csrf-audit",
                "files": (io.BytesIO(CSV), "batch.csv"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(preview.status_code, 200)
        token_match = re.search(rb'name="preview_token"\s+value="([^"]+)"', preview.data)
        self.assertIsNotNone(token_match)
        token = token_match.group(1).decode()

        confirm = self.client.post(
            "/sanpham/shopee-import/confirm",
            data={"_csrf": "csrf-audit", "preview_token": token},
        )
        self.assertEqual(confirm.status_code, 200)

        rows = self._audit_rows()
        self.assertEqual([row["action"] for row in rows], [
            "shopee_csv_preview", "shopee_csv_import_completed"
        ])
        for row in rows:
            detail = json.loads(row["detail"] or "{}")
            self.assertTrue(set(detail).issubset({
                "files", "rows", "new", "updated", "unchanged", "duplicate", "error"
            }))
            serialized = json.dumps(detail, ensure_ascii=False)
            self.assertNotIn("s.shopee.vn", serialized)
            self.assertNotIn("Secret Product Name", serialized)
            self.assertNotIn("Secret Shop", serialized)
            self.assertNotIn(token, serialized)
            self.assertNotEqual(row["entity_id"], token)

    def test_importer_source_contains_no_post_job_or_publish_primitives(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for relative in ("core/shopee_csv_import.py", "web/shopee_csv_import.py"):
            body = open(os.path.join(root, relative), encoding="utf-8").read()
            self.assertNotIn("INSERT INTO post", body)
            self.assertNotIn("enqueue(", body)
            self.assertNotIn("approve_post", body)
            self.assertNotIn("publish_post", body)
            self.assertNotIn("PUBLISH_POST", body)


if __name__ == "__main__":
    unittest.main()
