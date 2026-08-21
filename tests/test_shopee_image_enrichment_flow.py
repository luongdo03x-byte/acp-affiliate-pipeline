import os
import tempfile
import unittest
from io import BytesIO

from PIL import Image

from acp.adapters.safe_http import SafeHttpResponse
from acp.adapters.shopee_affiliate import AffiliateImportError, ProductMetadata
from acp.core.shopee_image_enrichment import (
    FAILED,
    NEEDS_HELPER,
    PENDING,
    READY,
    complete_from_helper,
    enqueue_product,
    enrich_product,
    get_job,
    reset_for_retry,
    run_batch,
)


class FakeMetadataResolver:
    def __init__(self, metadata=None, error=None):
        self.metadata = metadata or ProductMetadata()
        self.error = error
        self.calls = []

    def resolve_public(self, product_url):
        self.calls.append(product_url)
        if self.error is not None:
            raise self.error
        return self.metadata


class FakeImageHttp:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def get(self, url, allowed_hosts=None, expected_content_prefix=None):
        self.calls.append(url)
        return SafeHttpResponse(url, self.content, "image/jpeg")


class FakeStorage:
    def put(self, local_path):
        return "https://media.example/" + os.path.basename(local_path)


def jpeg_bytes():
    output = BytesIO()
    Image.new("RGB", (8, 8), (255, 255, 255)).save(output, format="JPEG")
    return output.getvalue()


class ShopeeImageEnrichmentFlowTests(unittest.TestCase):
    def setUp(self):
        from acp.core import db

        self.db = db
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "flow.db")
        db.init_db()
        self.conn = db.connect()
        self.media_dir = os.path.join(self.tmp.name, "media")

    def tearDown(self):
        self.conn.close()
        self.db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def _insert_product(self, product_id="p1", item_id="1", shop_id="10"):
        timestamp = self.db.now()
        self.conn.execute(
            """INSERT INTO product (
                 id, source, merchant, external_product_id, name, current_price,
                 commission_value, category_code, product_url, is_available,
                 created_at, updated_at, provider)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                product_id,
                "manual_shopee",
                "shopee.vn",
                item_id,
                f"Sản phẩm {item_id}",
                100_000,
                10_000,
                "khac",
                f"https://shopee.vn/product/{shop_id}/{item_id}",
                1,
                timestamp,
                timestamp,
                "SHOPEE_AFFILIATE",
            ),
        )
        enqueue_product(self.conn, product_id)

    def test_public_enrichment_materializes_image_and_marks_ready(self):
        self._insert_product()
        resolver = FakeMetadataResolver(ProductMetadata(
            image_url="https://down-vn.img.susercontent.com/file/example",
            original_price=150_000,
            shop="Shop HTML",
        ))
        image_http = FakeImageHttp(jpeg_bytes())

        result = enrich_product(
            self.conn,
            "p1",
            metadata_resolver=resolver,
            media_dir=self.media_dir,
            storage_backend=FakeStorage(),
            image_http=image_http,
        )

        self.assertEqual(result["status"], READY)
        job = get_job(self.conn, "p1")
        self.assertEqual(job["status"], READY)
        self.assertEqual(job["attempt_count"], 1)
        self.assertEqual(job["download_attempt_count"], 1)
        row = self.conn.execute("SELECT * FROM product WHERE id='p1'").fetchone()
        self.assertTrue(row["main_image_url"].endswith("shopee_10_1.jpg"))
        self.assertEqual(row["original_price"], 150_000)
        self.assertEqual(row["shop_name"], "Shop HTML")

    def test_public_metadata_without_image_moves_to_needs_helper(self):
        self._insert_product()
        resolver = FakeMetadataResolver(ProductMetadata(name="Tên HTML"))

        result = enrich_product(
            self.conn,
            "p1",
            metadata_resolver=resolver,
            media_dir=self.media_dir,
            storage_backend=FakeStorage(),
            image_http=FakeImageHttp(jpeg_bytes()),
        )

        self.assertEqual(result["status"], NEEDS_HELPER)
        self.assertEqual(get_job(self.conn, "p1")["last_error_code"], "PUBLIC_NO_IMAGE")
        self.assertEqual(len(resolver.calls), 1)

    def test_public_failure_uses_two_attempts_then_needs_helper(self):
        self._insert_product()
        resolver = FakeMetadataResolver(error=AffiliateImportError("blocked"))

        result = enrich_product(
            self.conn,
            "p1",
            metadata_resolver=resolver,
            media_dir=self.media_dir,
            storage_backend=FakeStorage(),
            image_http=FakeImageHttp(jpeg_bytes()),
        )

        self.assertEqual(result["status"], NEEDS_HELPER)
        self.assertEqual(len(resolver.calls), 2)
        self.assertEqual(get_job(self.conn, "p1")["attempt_count"], 2)

    def test_bad_image_retries_twice_then_failed(self):
        self._insert_product()
        resolver = FakeMetadataResolver(ProductMetadata(
            image_url="https://down-vn.img.susercontent.com/file/bad"
        ))
        image_http = FakeImageHttp(b"not-an-image")

        result = enrich_product(
            self.conn,
            "p1",
            metadata_resolver=resolver,
            media_dir=self.media_dir,
            storage_backend=FakeStorage(),
            image_http=image_http,
        )

        self.assertEqual(result["status"], FAILED)
        self.assertEqual(len(image_http.calls), 2)
        job = get_job(self.conn, "p1")
        self.assertEqual(job["download_attempt_count"], 2)
        self.assertEqual(job["last_error_code"], "IMAGE_DECODE_FAILED")

    def test_helper_completion_uses_same_materialization_path(self):
        self._insert_product()
        self.conn.execute(
            "UPDATE shopee_image_enrichment_job SET status=? WHERE product_id='p1'",
            (NEEDS_HELPER,),
        )

        result = complete_from_helper(
            self.conn,
            "p1",
            {
                "name": "Tên helper",
                "current_price": 1,
                "original_price": 130_000,
                "image_url": "https://down-vn.img.susercontent.com/file/helper",
                "shop": "Shop helper",
            },
            media_dir=self.media_dir,
            storage_backend=FakeStorage(),
            image_http=FakeImageHttp(jpeg_bytes()),
        )

        self.assertEqual(result["status"], READY)
        self.assertEqual(get_job(self.conn, "p1")["status"], READY)
        row = self.conn.execute("SELECT * FROM product WHERE id='p1'").fetchone()
        self.assertEqual(row["current_price"], 100_000)
        self.assertEqual(row["shop_name"], "Shop helper")

    def test_retry_resets_failed_attempt_budget(self):
        self._insert_product()
        self.conn.execute(
            """UPDATE shopee_image_enrichment_job
               SET status=?, attempt_count=2, download_attempt_count=2,
                   last_error_code='IMAGE_DECODE_FAILED', last_error='bad'
               WHERE product_id='p1'""",
            (FAILED,),
        )

        status = reset_for_retry(self.conn, "p1")

        self.assertEqual(status, PENDING)
        job = get_job(self.conn, "p1")
        self.assertEqual(job["attempt_count"], 0)
        self.assertEqual(job["download_attempt_count"], 0)
        self.assertIsNone(job["last_error_code"])

    def test_run_batch_processes_at_most_twenty_products(self):
        for index in range(21):
            self._insert_product(
                product_id=f"p{index}",
                item_id=str(index + 1),
                shop_id="99",
            )
        resolver = FakeMetadataResolver(ProductMetadata(name="Không có ảnh"))
        sleeps = []

        summary = run_batch(
            self.db.connect,
            metadata_resolver_factory=lambda: resolver,
            media_dir=self.media_dir,
            storage_backend=FakeStorage(),
            image_http_factory=lambda: FakeImageHttp(jpeg_bytes()),
            sleep_fn=lambda seconds: sleeps.append(seconds),
            delay_seconds=0.01,
            limit=100,
        )

        self.assertEqual(summary["processed"], 20)
        remaining = self.conn.execute(
            "SELECT COUNT(*) FROM shopee_image_enrichment_job WHERE status=?",
            (PENDING,),
        ).fetchone()[0]
        self.assertEqual(remaining, 1)
        self.assertEqual(summary["needs_helper"], 20)
        self.assertEqual(len(sleeps), 19)


if __name__ == "__main__":
    unittest.main()
