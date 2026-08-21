import csv
import io
import os
import re
import tempfile
import time
import unittest


HEADER = (
    "Mã sản phẩm,Tên sản phẩm,Giá,Doanh thu,Tên cửa hàng,"
    "Tỉ lệ hoa hồng,Hoa hồng,Link sản phẩm,Link ưu đãi\n"
)


def csv_bytes(item_id="123", *, shop_id="1", name="CSV Product", price="100,0k", affiliate="abc"):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow([
        "Mã sản phẩm",
        "Tên sản phẩm",
        "Giá",
        "Doanh thu",
        "Tên cửa hàng",
        "Tỉ lệ hoa hồng",
        "Hoa hồng",
        "Link sản phẩm",
        "Link ưu đãi",
    ])
    writer.writerow([
        item_id,
        name,
        price,
        "10k+",
        "Shop CSV",
        "5%",
        "₫5.000",
        f"https://shopee.vn/product/{shop_id}/{item_id}",
        f"https://s.shopee.vn/{affiliate}",
    ])
    return buffer.getvalue().encode("utf-8")


class ShopeeCsvWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_password = os.environ.get("ACP_ADMIN_PASSWORD")
        os.environ["ACP_ADMIN_PASSWORD"] = "test-password"

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
        self.batches = shopee_csv_batches
        self.batches.reset_previews()
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "csv-web.db")
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

    def _count(self, table):
        conn = self.db.connect()
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()

    @staticmethod
    def _preview_token(body: bytes) -> str:
        match = re.search(rb'name="preview_token"\s+value="([^"]+)"', body)
        if not match:
            raise AssertionError("preview token not rendered")
        return match.group(1).decode("utf-8")

    def _preview(self, files=None, csrf=None):
        files = files if files is not None else [(io.BytesIO(csv_bytes()), "batch.csv")]
        return self.client.post(
            "/sanpham/shopee-import/preview",
            data={"_csrf": self.csrf if csrf is None else csrf, "files": files},
            content_type="multipart/form-data",
        )

    def test_page_requires_login_when_dashboard_auth_enabled(self):
        anonymous = self.app.test_client()
        response = anonymous.get("/sanpham/shopee-import")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dangnhap", response.headers["Location"])

    def test_page_renders_upload_workspace(self):
        response = self.client.get("/sanpham/shopee-import")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Shopee Affiliate CSV Import", response.data)
        self.assertIn(b'multiple', response.data)

    def test_preview_requires_csrf(self):
        response = self._preview(csrf="wrong")
        self.assertEqual(response.status_code, 400)

    def test_preview_does_not_mutate_product_post_job_or_history(self):
        before = {
            table: self._count(table)
            for table in ("product", "product_price_history", "post", "job_queue")
        }
        response = self._preview()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Import v\xc3\xa0o Product Pool", response.data)
        after = {
            table: self._count(table)
            for table in ("product", "product_price_history", "post", "job_queue")
        }
        self.assertEqual(after, before)

    def test_confirm_imports_server_side_preview_and_ignores_tampered_row_fields(self):
        preview = self._preview()
        token = self._preview_token(preview.data)
        response = self.client.post(
            "/sanpham/shopee-import/confirm",
            data={
                "_csrf": self.csrf,
                "preview_token": token,
                "affiliate_url": "https://evil.example/tampered",
                "current_price": "1",
            },
        )
        self.assertEqual(response.status_code, 200)
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT * FROM product WHERE source='manual_shopee' AND external_product_id='123'"
            ).fetchone()
            self.assertEqual(row["affiliate_url"], "https://s.shopee.vn/abc")
            self.assertEqual(row["current_price"], 100_000)
        finally:
            conn.close()
        self.assertEqual(self._count("post"), 0)
        self.assertEqual(self._count("job_queue"), 0)

    def test_confirm_requires_csrf(self):
        preview = self._preview()
        token = self._preview_token(preview.data)
        response = self.client.post(
            "/sanpham/shopee-import/confirm",
            data={"_csrf": "wrong", "preview_token": token},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._count("product"), 0)

    def test_replayed_preview_token_is_rejected(self):
        preview = self._preview()
        token = self._preview_token(preview.data)
        first = self.client.post(
            "/sanpham/shopee-import/confirm",
            data={"_csrf": self.csrf, "preview_token": token},
        )
        second = self.client.post(
            "/sanpham/shopee-import/confirm",
            data={"_csrf": self.csrf, "preview_token": token},
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 410)
        self.assertEqual(self._count("product"), 1)

    def test_expired_preview_token_is_rejected(self):
        issued = self.batches.issue_preview(
            [], {"rows": 0}, now_ts=time.monotonic() - 901
        )
        response = self.client.post(
            "/sanpham/shopee-import/confirm",
            data={"_csrf": self.csrf, "preview_token": issued["token"]},
        )
        self.assertEqual(response.status_code, 410)

    def test_multi_file_preview_dedupes_same_product_with_last_valid_occurrence(self):
        response = self._preview(
            files=[
                (io.BytesIO(csv_bytes(name="Old", affiliate="old")), "a.csv"),
                (io.BytesIO(csv_bytes(name="New", affiliate="new")), "b.csv"),
            ]
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DUPLICATE_IN_UPLOAD", response.data)
        self.assertIn(b"New", response.data)
        token = self._preview_token(response.data)
        self.client.post(
            "/sanpham/shopee-import/confirm",
            data={"_csrf": self.csrf, "preview_token": token},
        )
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT name, affiliate_url FROM product WHERE source='manual_shopee' AND external_product_id='123'"
            ).fetchone()
            self.assertEqual(row["name"], "New")
            self.assertEqual(row["affiliate_url"], "https://s.shopee.vn/new")
        finally:
            conn.close()

    def test_mixed_valid_and_invalid_rows_preview_together(self):
        body = (
            HEADER
            + "999,Bad,100,0,Shop,5%,₫5,https://shopee.vn/product/1/123,https://s.shopee.vn/bad\n"
            + "124,Good,200,1,Shop,5%,₫10,https://shopee.vn/product/1/124,https://s.shopee.vn/good\n"
        ).encode("utf-8")
        response = self._preview(files=[(io.BytesIO(body), "mixed.csv")])
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"ERROR", response.data)
        self.assertIn(b"Good", response.data)

    def test_no_file_and_non_csv_extension_are_rejected(self):
        empty = self._preview(files=[])
        wrong = self._preview(files=[(io.BytesIO(csv_bytes()), "batch.txt")])
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(wrong.status_code, 400)

    def test_more_than_20_files_are_rejected(self):
        files = [(io.BytesIO(csv_bytes(str(index + 1))), f"{index}.csv") for index in range(21)]
        response = self._preview(files=files)
        self.assertEqual(response.status_code, 400)

    def test_oversized_file_is_rejected(self):
        from acp.core.shopee_csv_import import MAX_FILE_BYTES

        response = self._preview(
            files=[(io.BytesIO(b"x" * (MAX_FILE_BYTES + 1)), "large.csv")]
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_required_headers_are_rejected(self):
        response = self._preview(
            files=[(io.BytesIO(b"M\xc3\xa3 s\xe1\xba\xa3n ph\xe1\xba\xa9m,T\xc3\xaan s\xe1\xba\xa3n ph\xe1\xba\xa9m\n123,X\n"), "bad.csv")]
        )
        self.assertEqual(response.status_code, 400)

    def test_more_than_20000_rows_are_rejected(self):
        rows = [
            f"{index},P{index},100,0,Shop,5%,₫5,https://shopee.vn/product/1/{index},https://s.shopee.vn/x{index}"
            for index in range(1, 20_002)
        ]
        response = self._preview(
            files=[(io.BytesIO((HEADER + "\n".join(rows) + "\n").encode("utf-8")), "many.csv")]
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
