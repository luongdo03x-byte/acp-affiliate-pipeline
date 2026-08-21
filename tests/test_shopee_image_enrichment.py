import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO

from PIL import Image

from acp.adapters.safe_http import SafeHttpResponse
from acp.adapters.shopee_affiliate import ProductMetadata
from acp.core.shopee_image_enrichment import (
    DOWNLOADING,
    PENDING,
    PUBLIC_FETCH,
    READY,
    ShopeeImageEnrichmentError,
    backfill_missing,
    enqueue_product,
    get_job,
    materialize_product_image,
    merge_metadata_into_product,
    recover_stale_jobs,
)


class FakeImageHttp:
    def __init__(self, content: bytes, content_type="image/jpeg"):
        self.content = content
        self.content_type = content_type
        self.calls = []

    def get(self, url, allowed_hosts=None, expected_content_prefix=None):
        self.calls.append(url)
        return SafeHttpResponse(
            final_url=url,
            content=self.content,
            content_type=self.content_type,
        )


class FakeStorage:
    def __init__(self):
        self.paths = []

    def put(self, local_path):
        self.paths.append(local_path)
        return "https://media.example/" + os.path.basename(local_path)


class FailingStorage:
    def put(self, local_path):
        raise RuntimeError("storage unavailable")


def image_bytes(fmt="JPEG"):
    output = BytesIO()
    Image.new("RGB", (8, 8), (255, 255, 255)).save(output, format=fmt)
    return output.getvalue()


class ShopeeImageEnrichmentJobTests(unittest.TestCase):
    def setUp(self):
        from acp.core import db

        self.db = db
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "enrichment.db")
        db.init_db()
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()
        self.db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def _insert_product(
        self,
        *,
        product_id="p1",
        provider="SHOPEE_AFFILIATE",
        item_id="456",
        shop_id="123",
        image_path_local=None,
        main_image_url=None,
    ):
        timestamp = self.db.now()
        self.conn.execute(
            """INSERT INTO product (
                 id, source, merchant, external_product_id, name, current_price,
                 commission_value, category_code, product_url, is_available,
                 created_at, updated_at, provider, image_path_local, main_image_url)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                product_id,
                "manual_shopee" if provider == "SHOPEE_AFFILIATE" else "feed",
                "shopee.vn" if provider == "SHOPEE_AFFILIATE" else "example",
                item_id,
                "Sản phẩm test",
                100_000,
                10_000,
                "khac",
                f"https://shopee.vn/product/{shop_id}/{item_id}",
                1,
                timestamp,
                timestamp,
                provider,
                image_path_local,
                main_image_url,
            ),
        )

    def test_schema_registers_enrichment_job_table(self):
        columns = {
            row[1]
            for row in self.conn.execute(
                "PRAGMA table_info(shopee_image_enrichment_job)"
            ).fetchall()
        }
        self.assertIn("product_id", columns)
        self.assertIn("status", columns)
        self.assertIn("attempt_count", columns)
        self.assertIn("download_attempt_count", columns)
        self.assertIn("last_error_code", columns)

    def test_enqueue_missing_shopee_product_is_idempotent(self):
        self._insert_product()

        first = enqueue_product(self.conn, "p1")
        second = enqueue_product(self.conn, "p1")

        self.assertEqual(first, PENDING)
        self.assertEqual(second, PENDING)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM shopee_image_enrichment_job WHERE product_id='p1'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(get_job(self.conn, "p1")["status"], PENDING)

    def test_non_shopee_product_is_not_enqueued(self):
        self._insert_product(product_id="other", provider="ACCESSTRADE_TIKTOK")

        result = enqueue_product(self.conn, "other")

        self.assertIsNone(result)
        self.assertIsNone(get_job(self.conn, "other"))

    def test_existing_main_image_is_ready(self):
        self._insert_product(
            product_id="ready",
            main_image_url="https://media.example/shopee_123_456.jpg",
        )

        status = enqueue_product(self.conn, "ready")

        self.assertEqual(status, READY)
        self.assertEqual(get_job(self.conn, "ready")["status"], READY)

    def test_backfill_enqueues_preexisting_missing_image_products(self):
        self._insert_product(product_id="p1", item_id="1")
        self._insert_product(product_id="p2", item_id="2")
        self._insert_product(
            product_id="p3",
            item_id="3",
            main_image_url="https://media.example/ready.jpg",
        )

        count = backfill_missing(self.conn)

        self.assertEqual(count, 2)
        self.assertEqual(get_job(self.conn, "p1")["status"], PENDING)
        self.assertEqual(get_job(self.conn, "p2")["status"], PENDING)
        self.assertIsNone(get_job(self.conn, "p3"))

    def test_stale_transient_jobs_return_to_pending(self):
        self._insert_product(product_id="fetch", item_id="10")
        self._insert_product(product_id="download", item_id="11")
        enqueue_product(self.conn, "fetch")
        enqueue_product(self.conn, "download")
        old = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat(timespec="seconds")
        self.conn.execute(
            "UPDATE shopee_image_enrichment_job SET status=?, updated_at=? WHERE product_id='fetch'",
            (PUBLIC_FETCH, old),
        )
        self.conn.execute(
            "UPDATE shopee_image_enrichment_job SET status=?, updated_at=? WHERE product_id='download'",
            (DOWNLOADING, old),
        )

        recovered = recover_stale_jobs(self.conn, now_dt=datetime.now(timezone.utc))

        self.assertEqual(recovered, 2)
        self.assertEqual(get_job(self.conn, "fetch")["status"], PENDING)
        self.assertEqual(get_job(self.conn, "download")["status"], PENDING)

    def test_materialize_product_image_writes_deterministic_verified_file(self):
        http = FakeImageHttp(image_bytes("JPEG"))
        storage = FakeStorage()
        media_dir = os.path.join(self.tmp.name, "media")

        result = materialize_product_image(
            "https://shopee.vn/product/123/456",
            "https://down-vn.img.susercontent.com/file/example",
            media_dir,
            storage,
            http_client=http,
        )

        self.assertEqual(os.path.basename(result["image_path_local"]), "shopee_123_456.jpg")
        self.assertTrue(os.path.isfile(result["image_path_local"]))
        self.assertEqual(
            result["image_url_original"],
            "https://down-vn.img.susercontent.com/file/example",
        )
        self.assertEqual(result["main_image_url"], "https://media.example/shopee_123_456.jpg")
        self.assertEqual(len(http.calls), 1)

    def test_existing_valid_deterministic_file_is_reused_without_download(self):
        media_dir = os.path.join(self.tmp.name, "media")
        os.makedirs(media_dir, exist_ok=True)
        existing = os.path.join(media_dir, "shopee_123_456.jpg")
        with open(existing, "wb") as fh:
            fh.write(image_bytes("JPEG"))
        http = FakeImageHttp(b"not-used")
        storage = FakeStorage()

        result = materialize_product_image(
            "https://shopee.vn/product/123/456",
            "https://down-vn.img.susercontent.com/file/example",
            media_dir,
            storage,
            http_client=http,
        )

        self.assertEqual(result["image_path_local"], existing)
        self.assertEqual(http.calls, [])
        self.assertEqual(storage.paths, [existing])

    def test_corrupt_image_never_creates_final_target(self):
        media_dir = os.path.join(self.tmp.name, "media")
        http = FakeImageHttp(b"this-is-not-an-image")

        with self.assertRaises(ShopeeImageEnrichmentError) as captured:
            materialize_product_image(
                "https://shopee.vn/product/123/456",
                "https://down-vn.img.susercontent.com/file/bad",
                media_dir,
                FakeStorage(),
                http_client=http,
            )

        self.assertEqual(captured.exception.code, "IMAGE_DECODE_FAILED")
        self.assertFalse(os.path.exists(os.path.join(media_dir, "shopee_123_456.jpg")))

    def test_storage_failure_keeps_verified_local_file_but_returns_error(self):
        media_dir = os.path.join(self.tmp.name, "media")

        with self.assertRaises(ShopeeImageEnrichmentError) as captured:
            materialize_product_image(
                "https://shopee.vn/product/123/456",
                "https://down-vn.img.susercontent.com/file/example",
                media_dir,
                FailingStorage(),
                http_client=FakeImageHttp(image_bytes("PNG"), "image/png"),
            )

        self.assertEqual(captured.exception.code, "STORAGE_FAILED")
        self.assertTrue(os.path.isfile(os.path.join(media_dir, "shopee_123_456.png")))

    def test_metadata_merge_is_additive_and_preserves_csv_owned_values(self):
        self._insert_product()
        materialized = {
            "image_url_original": "https://down-vn.img.susercontent.com/file/example",
            "image_path_local": os.path.join(self.tmp.name, "shopee_123_456.jpg"),
            "main_image_url": "https://media.example/shopee_123_456.jpg",
        }
        metadata = ProductMetadata(
            name="Tên HTML không được ghi đè",
            current_price=1,
            original_price=150_000,
            image_url=materialized["image_url_original"],
            shop="Shop từ HTML",
        )

        merge_metadata_into_product(self.conn, "p1", metadata, materialized)

        row = self.conn.execute("SELECT * FROM product WHERE id='p1'").fetchone()
        self.assertEqual(row["name"], "Sản phẩm test")
        self.assertEqual(row["current_price"], 100_000)
        self.assertEqual(row["original_price"], 150_000)
        self.assertEqual(row["shop_name"], "Shop từ HTML")
        self.assertEqual(row["image_url_original"], materialized["image_url_original"])
        self.assertEqual(row["image_path_local"], materialized["image_path_local"])
        self.assertEqual(row["main_image_url"], materialized["main_image_url"])


if __name__ == "__main__":
    unittest.main()
