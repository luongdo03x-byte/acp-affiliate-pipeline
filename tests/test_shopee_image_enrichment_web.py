import os
import tempfile
import unittest

from acp.core import helper_pairing
from acp.core.shopee_image_enrichment import (
    FAILED,
    NEEDS_HELPER,
    PENDING,
    READY,
    enqueue_product,
)


class ShopeeImageEnrichmentWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_password = os.environ.get("ACP_ADMIN_PASSWORD")
        os.environ["ACP_ADMIN_PASSWORD"] = "test-password"
        os.environ["ACP_ADAPTER"] = "mock"
        os.environ["ACP_SOURCE"] = "mock"

    @classmethod
    def tearDownClass(cls):
        if cls.old_password is None:
            os.environ.pop("ACP_ADMIN_PASSWORD", None)
        else:
            os.environ["ACP_ADMIN_PASSWORD"] = cls.old_password

    def setUp(self):
        from acp.core import db
        from acp.web import create_app

        helper_pairing.reset()
        self.db = db
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "shopee-enrichment-web.db")
        db.init_db()

        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["uid"] = "operator"
            session["csrf"] = "csrf-test"
        self.csrf = "csrf-test"

    def tearDown(self):
        helper_pairing.reset()
        self.db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def _insert_product(
        self,
        *,
        product_id,
        item_id,
        provider="SHOPEE_AFFILIATE",
        shop_id="10",
        name=None,
        main_image_url=None,
    ):
        conn = self.db.connect()
        try:
            timestamp = self.db.now()
            conn.execute(
                """INSERT INTO product (
                     id, source, merchant, external_product_id, name, current_price,
                     commission_value, category_code, product_url, is_available,
                     created_at, updated_at, provider, sold_count,
                     commission_rate_percent, commission_amount, main_image_url)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    product_id,
                    "manual_shopee" if provider == "SHOPEE_AFFILIATE" else "feed",
                    "shopee.vn" if provider == "SHOPEE_AFFILIATE" else "example",
                    item_id,
                    name or f"Shopee {item_id}",
                    100_000,
                    10_000,
                    "khac",
                    f"https://shopee.vn/product/{shop_id}/{item_id}",
                    1,
                    timestamp,
                    timestamp,
                    provider,
                    1234,
                    10.0,
                    10_000,
                    main_image_url,
                ),
            )
            if provider == "SHOPEE_AFFILIATE":
                enqueue_product(conn, product_id)
        finally:
            conn.close()

    def _set_status(self, product_id, status):
        conn = self.db.connect()
        try:
            conn.execute(
                "UPDATE shopee_image_enrichment_job SET status=? WHERE product_id=?",
                (status, product_id),
            )
        finally:
            conn.close()

    def test_workspace_requires_login(self):
        anonymous = self.app.test_client()
        response = anonymous.get("/sanpham/shopee")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dangnhap", response.headers["Location"])

    def test_workspace_lists_only_shopee_affiliate_products(self):
        self._insert_product(product_id="s1", item_id="1", name="Shopee Visible")
        self._insert_product(
            product_id="a1",
            item_id="2",
            provider="ACCESSTRADE_TIKTOK",
            name="AccessTrade Hidden",
        )

        response = self.client.get("/sanpham/shopee")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Shopee Product Pool", response.data)
        self.assertIn(b"Shopee Visible", response.data)
        self.assertNotIn(b"AccessTrade Hidden", response.data)

    def test_status_filter_shows_only_requested_state(self):
        self._insert_product(product_id="ready", item_id="1", name="Ready Product")
        self._insert_product(product_id="helper", item_id="2", name="Helper Product")
        self._set_status("ready", READY)
        self._set_status("helper", NEEDS_HELPER)

        response = self.client.get("/sanpham/shopee?status=needs_helper")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Helper Product", response.data)
        self.assertNotIn(b"Ready Product", response.data)

    def test_post_actions_require_csrf(self):
        response = self.client.post(
            "/sanpham/shopee/enrichment/backfill",
            data={"_csrf": "wrong"},
        )
        self.assertEqual(response.status_code, 400)

    def test_backfill_enrolls_preexisting_missing_products(self):
        self._insert_product(product_id="s1", item_id="1")
        conn = self.db.connect()
        try:
            conn.execute("DELETE FROM shopee_image_enrichment_job WHERE product_id='s1'")
        finally:
            conn.close()

        response = self.client.post(
            "/sanpham/shopee/enrichment/backfill",
            data={"_csrf": self.csrf},
        )

        self.assertEqual(response.status_code, 302)
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT status FROM shopee_image_enrichment_job WHERE product_id='s1'"
            ).fetchone()
            self.assertEqual(row["status"], PENDING)
        finally:
            conn.close()

    def test_batch_route_always_caps_runner_at_twenty(self):
        calls = []

        def fake_runner(connection_factory, **kwargs):
            calls.append(kwargs)
            return {"processed": 20, "ready": 10, "needs_helper": 8, "failed": 2, "pending": 0}

        self.app.config["SHOPEE_ENRICHMENT_BATCH_RUNNER"] = fake_runner

        response = self.client.post(
            "/sanpham/shopee/enrichment/run",
            data={"_csrf": self.csrf, "limit": "999"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["limit"], 20)

    def test_single_enrich_rejects_non_shopee_product(self):
        self._insert_product(
            product_id="a1",
            item_id="1",
            provider="ACCESSTRADE_TIKTOK",
        )
        calls = []
        self.app.config["SHOPEE_ENRICHMENT_SINGLE_RUNNER"] = (
            lambda *args, **kwargs: calls.append((args, kwargs))
        )

        response = self.client.post(
            "/sanpham/shopee/a1/enrich",
            data={"_csrf": self.csrf},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(calls, [])

    def test_single_enrich_delegates_to_configured_runner(self):
        self._insert_product(product_id="s1", item_id="1")
        calls = []

        def fake_single(conn, product_id, **kwargs):
            calls.append(product_id)
            return {"product_id": product_id, "status": NEEDS_HELPER}

        self.app.config["SHOPEE_ENRICHMENT_SINGLE_RUNNER"] = fake_single

        response = self.client.post(
            "/sanpham/shopee/s1/enrich",
            data={"_csrf": self.csrf},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(calls, ["s1"])

    def test_retry_resets_failed_job(self):
        self._insert_product(product_id="s1", item_id="1")
        conn = self.db.connect()
        try:
            conn.execute(
                """UPDATE shopee_image_enrichment_job
                   SET status=?, attempt_count=2, download_attempt_count=2,
                       last_error_code='IMAGE_DECODE_FAILED', last_error='bad'
                   WHERE product_id='s1'""",
                (FAILED,),
            )
        finally:
            conn.close()

        response = self.client.post(
            "/sanpham/shopee/s1/retry",
            data={"_csrf": self.csrf},
        )

        self.assertEqual(response.status_code, 302)
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT * FROM shopee_image_enrichment_job WHERE product_id='s1'"
            ).fetchone()
            self.assertEqual(row["status"], PENDING)
            self.assertEqual(row["attempt_count"], 0)
            self.assertEqual(row["download_attempt_count"], 0)
        finally:
            conn.close()

    def test_helper_token_route_is_server_bound_to_product_url(self):
        self._insert_product(product_id="s1", item_id="1", shop_id="10")

        response = self.client.post(
            "/sanpham/shopee/s1/helper/token",
            data={"_csrf": self.csrf},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["product_url"], "https://shopee.vn/product/10/1")
        self.assertTrue(payload["token"])
        self.assertEqual(payload["expires_in"], 300)

    def test_helper_complete_consumes_metadata_for_same_product_only(self):
        self._insert_product(product_id="s1", item_id="1", shop_id="10")
        self._insert_product(product_id="s2", item_id="2", shop_id="10")
        token_response = self.client.post(
            "/sanpham/shopee/s1/helper/token",
            data={"_csrf": self.csrf},
        ).get_json()
        token = token_response["token"]
        self.assertTrue(helper_pairing.submit(
            token,
            "https://shopee.vn/product/10/1",
            {
                "name": "Helper",
                "current_price": 100_000,
                "image_url": "https://down-vn.img.susercontent.com/file/helper",
            },
        ))
        completed = []

        def fake_complete(conn, product_id, metadata, **kwargs):
            completed.append((product_id, metadata["image_url"]))
            return {"product_id": product_id, "status": READY}

        self.app.config["SHOPEE_ENRICHMENT_HELPER_COMPLETER"] = fake_complete

        wrong = self.client.post(
            "/sanpham/shopee/s2/helper/complete",
            data={"_csrf": self.csrf, "token": token},
        )
        right = self.client.post(
            "/sanpham/shopee/s1/helper/complete",
            data={"_csrf": self.csrf, "token": token},
        )

        self.assertEqual(wrong.status_code, 410)
        self.assertEqual(right.status_code, 302)
        self.assertEqual(completed, [(
            "s1", "https://down-vn.img.susercontent.com/file/helper"
        )])
        self.assertIsNone(helper_pairing.poll(token))


if __name__ == "__main__":
    unittest.main()
