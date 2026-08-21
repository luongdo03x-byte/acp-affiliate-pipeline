import os
import tempfile
import unittest
from io import BytesIO

from PIL import Image

from acp.adapters.safe_http import SafeHttpError, SafeHttpResponse
from acp.adapters.shopee_affiliate import AffiliateImportError, ProductMetadata
from acp.core.shopee_image_enrichment import (
    FAILED,
    NEEDS_HELPER,
    ShopeeImageEnrichmentError,
    enqueue_product,
    enrich_product,
    get_job,
    materialize_product_image,
    run_batch,
)


class FakeStorage:
    def put(self, local_path):
        return "https://media.example/" + os.path.basename(local_path)


class FakeImageHttp:
    def __init__(self, content, content_type="image/jpeg"):
        self.content = content
        self.content_type = content_type
        self.calls = []

    def get(self, url, allowed_hosts=None, expected_content_prefix=None):
        self.calls.append(url)
        return SafeHttpResponse(url, self.content, self.content_type)


def image_bytes(fmt="JPEG"):
    out = BytesIO()
    Image.new("RGB", (8, 8), (255, 255, 255)).save(out, format=fmt)
    return out.getvalue()


class BlockingResolver:
    def __init__(self):
        self.calls = 0

    def resolve_public(self, product_url):
        self.calls += 1
        raise AffiliateImportError("blocked")


class FirstUnexpectedThenNoImageResolver:
    def __init__(self):
        self.calls = 0

    def resolve_public(self, product_url):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("unexpected provider bug")
        return ProductMetadata(name="Public metadata without image")


class ContentTypeFailingHttp:
    def get(self, url, allowed_hosts=None, expected_content_prefix=None):
        raise SafeHttpError("Content-Type không phù hợp")


class OversizedFailingHttp:
    def get(self, url, allowed_hosts=None, expected_content_prefix=None):
        raise SafeHttpError("Response vượt giới hạn kích thước")


class ShopeeImageEnrichmentReviewTests(unittest.TestCase):
    def setUp(self):
        from acp.core import db

        self.db = db
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "review.db")
        db.init_db()
        self.conn = db.connect()
        self.media_dir = os.path.join(self.tmp.name, "media")

    def tearDown(self):
        self.conn.close()
        self.db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def _insert_product(self, product_id, item_id):
        timestamp = self.db.now()
        self.conn.execute(
            """INSERT INTO product (
                 id, source, merchant, external_product_id, name, current_price,
                 commission_value, category_code, product_url, is_available,
                 created_at, updated_at, provider)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                product_id, "manual_shopee", "shopee.vn", item_id,
                f"Product {item_id}", 100_000, 10_000, "khac",
                f"https://shopee.vn/product/99/{item_id}", 1,
                timestamp, timestamp, "SHOPEE_AFFILIATE",
            ),
        )
        enqueue_product(self.conn, product_id)

    def test_stale_valid_tmp_file_is_not_reused_as_deterministic_cache(self):
        os.makedirs(self.media_dir, exist_ok=True)
        stale_tmp = os.path.join(self.media_dir, "shopee_123_456.crash.tmp")
        with open(stale_tmp, "wb") as handle:
            handle.write(image_bytes("JPEG"))
        http = FakeImageHttp(image_bytes("PNG"), "image/png")

        result = materialize_product_image(
            "https://shopee.vn/product/123/456",
            "https://down-vn.img.susercontent.com/file/new",
            self.media_dir,
            FakeStorage(),
            http_client=http,
        )

        self.assertEqual(len(http.calls), 1)
        self.assertEqual(os.path.basename(result["image_path_local"]), "shopee_123_456.png")

    def test_wrong_content_type_is_classified_as_invalid_content(self):
        with self.assertRaises(ShopeeImageEnrichmentError) as captured:
            materialize_product_image(
                "https://shopee.vn/product/123/456",
                "https://down-vn.img.susercontent.com/file/not-image",
                self.media_dir,
                FakeStorage(),
                http_client=ContentTypeFailingHttp(),
            )

        self.assertEqual(captured.exception.code, "IMAGE_INVALID_CONTENT")

    def test_oversized_response_is_classified_separately(self):
        with self.assertRaises(ShopeeImageEnrichmentError) as captured:
            materialize_product_image(
                "https://shopee.vn/product/123/456",
                "https://down-vn.img.susercontent.com/file/too-large",
                self.media_dir,
                FakeStorage(),
                http_client=OversizedFailingHttp(),
            )

        self.assertEqual(captured.exception.code, "IMAGE_TOO_LARGE")

    def test_public_retry_waits_between_attempts(self):
        self._insert_product("p1", "1")
        resolver = BlockingResolver()
        sleeps = []

        result = enrich_product(
            self.conn,
            "p1",
            metadata_resolver=resolver,
            media_dir=self.media_dir,
            storage_backend=FakeStorage(),
            image_http=FakeImageHttp(image_bytes()),
            retry_delay_seconds=0.25,
            sleep_fn=lambda seconds: sleeps.append(seconds),
        )

        self.assertEqual(result["status"], NEEDS_HELPER)
        self.assertEqual(resolver.calls, 2)
        self.assertEqual(sleeps, [0.25])

    def test_unexpected_product_failure_does_not_abort_remaining_batch(self):
        self._insert_product("p1", "1")
        self._insert_product("p2", "2")
        resolver = FirstUnexpectedThenNoImageResolver()
        self.conn.close()

        summary = run_batch(
            self.db.connect,
            limit=2,
            delay_seconds=0,
            metadata_resolver_factory=lambda: resolver,
            image_http_factory=lambda: FakeImageHttp(image_bytes()),
            media_dir=self.media_dir,
            storage_backend=FakeStorage(),
            sleep_fn=lambda _seconds: None,
        )

        self.conn = self.db.connect()
        self.assertEqual(summary["processed"], 2)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["needs_helper"], 1)
        self.assertEqual(get_job(self.conn, "p1")["status"], FAILED)
        self.assertEqual(get_job(self.conn, "p1")["last_error_code"], "ENRICHMENT_FAILED")
        self.assertEqual(get_job(self.conn, "p2")["status"], NEEDS_HELPER)


if __name__ == "__main__":
    unittest.main()
