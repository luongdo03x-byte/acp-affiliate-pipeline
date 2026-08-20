"""Focused tests for Shopee bulk affiliate Phase 1."""
import os
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_tmp = tempfile.mkdtemp()
os.environ["ACP_DB"] = os.path.join(_tmp, "shopee_bulk.db")
os.environ.pop("ACP_ADMIN_PASSWORD", None)
os.environ.pop("ACP_ENV", None)

from acp.core import db  # noqa: E402
db.DB_PATH = os.environ["ACP_DB"]
from acp.core.db import connect, init_db, now  # noqa: E402
from acp.core.shopee_bulk_affiliate import (  # noqa: E402
    BulkAffiliateError,
    MAX_BULK_URLS,
    generate_bulk_links,
)


class ShopeeBulkAffiliateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        os.environ["SHOPEE_AFFILIATE_ID"] = "14354840000"

    def test_builds_official_redirect_shape(self):
        result = generate_bulk_links(
            "https://shopee.vn/Tai-nghe-i.12345.67890?sp_atk=tracking",
            affiliate_id="14354840000",
            sub_tag="threads",
        )[0]
        self.assertEqual(result.status, "CREATED")
        self.assertEqual(result.product_url, "https://shopee.vn/product/12345/67890")
        parsed = urlsplit(result.affiliate_url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "s.shopee.vn")
        self.assertEqual(parsed.path, "/an_redir")
        query = parse_qs(parsed.query)
        self.assertEqual(query["origin_link"], ["https://shopee.vn/product/12345/67890"])
        self.assertEqual(query["affiliate_id"], ["14354840000"])
        self.assertEqual(len(query["sub_id"][0].split("-")), 5)
        self.assertEqual(query["sub_id"][0].split("-")[-1], "threads")

    def test_rejects_short_affiliate_and_external_urls_per_row(self):
        results = generate_bulk_links(
            "https://s.shopee.vn/abc\nhttps://example.com/product/1/2",
            affiliate_id="14354840000",
        )
        self.assertEqual([row.status for row in results], ["ERROR", "ERROR"])
        self.assertTrue(all(row.error for row in results))

    def test_rejects_missing_affiliate_id(self):
        with self.assertRaises(BulkAffiliateError):
            generate_bulk_links("https://shopee.vn/product/1/2", affiliate_id="")

    def test_enforces_500_url_limit_before_dedup(self):
        body = "\n".join(["https://shopee.vn/product/1/2"] * (MAX_BULK_URLS + 1))
        with self.assertRaises(BulkAffiliateError):
            generate_bulk_links(body, affiliate_id="14354840000")

    def test_deduplicates_same_product_in_one_batch(self):
        results = generate_bulk_links(
            "https://shopee.vn/A-i.123.456\nhttps://shopee.vn/product/123/456",
            affiliate_id="14354840000",
        )
        self.assertEqual(results[0].status, "CREATED")
        self.assertEqual(results[1].status, "DUPLICATE")
        self.assertEqual(results[0].affiliate_url, results[1].affiliate_url)

    def test_links_matching_existing_product_row(self):
        conn = connect()
        conn.execute("DELETE FROM product WHERE id='bulk-p1'")
        ts = now()
        conn.execute(
            """INSERT INTO product (
                   id, source, merchant, external_product_id, name, description,
                   current_price, original_price, commission_value, commission_rate,
                   category_code, rating, review_count, sold_count, image_url_original,
                   image_path_local, product_url, is_available, last_seen_at, created_at, updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)""",
            ("bulk-p1", "manual_shopee", "shopee.vn", "998877", "Test product", "",
             100000, None, 0, None, "khac", None, 0, 0, None, None,
             "https://shopee.vn/product/123/998877", ts, ts, ts),
        )
        result = generate_bulk_links(
            "https://shopee.vn/product/123/998877",
            affiliate_id="14354840000",
            conn=conn,
        )[0]
        row = conn.execute(
            "SELECT affiliate_url, affiliate_link_status, affiliate_link_created_at FROM product WHERE id='bulk-p1'"
        ).fetchone()
        conn.close()
        self.assertEqual(result.status, "LINKED")
        self.assertEqual(result.product_id, "bulk-p1")
        self.assertEqual(row["affiliate_url"], result.affiliate_url)
        self.assertEqual(row["affiliate_link_status"], "READY")
        self.assertTrue(row["affiliate_link_created_at"])

    def test_bulk_page_and_post_are_registered(self):
        from acp.web.server import create_app
        app = create_app()
        app.config["TESTING"] = True
        client = app.test_client()
        page = client.get("/sanpham/shopee-bulk")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Tạo link Shopee hàng loạt", page.get_data(as_text=True))
        response = client.post(
            "/sanpham/shopee-bulk/generate",
            data={"product_urls": "https://shopee.vn/product/123/456", "sub_tag": "web"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("s.shopee.vn/an_redir", body)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ShopeeBulkAffiliateTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
