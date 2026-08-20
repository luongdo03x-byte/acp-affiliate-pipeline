"""Focused tests for Shopee Metadata Helper Phase 2.

Run from the directory containing the ``acp`` package:
    ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_helper -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

VALID_PRODUCT = "https://shopee.vn/product/123/456"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ShopeeHelperValidationTests(unittest.TestCase):
    def test_same_product_different_shopee_url_shapes_match(self):
        from acp.core.shopee_helper import validate_helper_submission

        got = validate_helper_submission(
            VALID_PRODUCT,
            "https://shopee.vn/Ten-san-pham-i.123.456?sp_atk=x",
            {
                "name": "Tai nghe",
                "current_price": 199000,
                "image_url": "https://down-vn.img.susercontent.com/file/abc",
            },
        )
        self.assertEqual(got.product_id, "456")
        self.assertEqual(got.expected_product_url, VALID_PRODUCT)
        self.assertEqual(got.observed_product_url, VALID_PRODUCT)

    def test_opa_product_shape_canonicalizes(self):
        from acp.core.shopee_helper import canonical_helper_product

        canonical, item_id = canonical_helper_product(
            "https://shopee.vn/opaapi/lp/123/456?credential_token=must-not-survive"
        )
        self.assertEqual(canonical, VALID_PRODUCT)
        self.assertEqual(item_id, "456")
        self.assertNotIn("credential_token", canonical)

    def test_different_product_is_rejected(self):
        from acp.core.shopee_helper import ShopeeHelperError, validate_helper_submission

        with self.assertRaises(ShopeeHelperError):
            validate_helper_submission(
                VALID_PRODUCT,
                "https://shopee.vn/product/123/999",
                {"name": "Sai sản phẩm"},
            )

    def test_invalid_product_urls_are_rejected(self):
        from acp.core.shopee_helper import ShopeeHelperError, canonical_helper_product

        for bad in (
            "http://shopee.vn/product/1/2",
            "https://example.com/product/1/2",
            "https://s.shopee.vn/abc",
            "https://shope.ee/abc",
            "https://user:pass@shopee.vn/product/1/2",
            "https://shopee.vn:444/product/1/2",
            "https://shopee.vn/search?keyword=test",
        ):
            with self.subTest(url=bad), self.assertRaises(ShopeeHelperError):
                canonical_helper_product(bad)

    def test_unknown_fields_are_dropped(self):
        from acp.core.shopee_helper import sanitize_helper_metadata

        meta = sanitize_helper_metadata({
            "name": "X",
            "current_price": "199000",
            "cookie": "forbidden",
            "token": "forbidden",
            "localStorage": "forbidden",
        })
        self.assertEqual(meta, {
            "name": "X",
            "current_price": 199000,
            "original_price": None,
            "image_url": None,
            "shop": None,
        })

    def test_invalid_prices_are_rejected(self):
        from acp.core.shopee_helper import ShopeeHelperError, sanitize_helper_metadata

        for value in (-1, True, "-2", 10_000_000_001):
            with self.subTest(value=value), self.assertRaises(ShopeeHelperError):
                sanitize_helper_metadata({"current_price": value})

    def test_text_and_image_limits_are_enforced(self):
        from acp.core.shopee_helper import ShopeeHelperError, sanitize_helper_metadata

        with self.assertRaises(ShopeeHelperError):
            sanitize_helper_metadata({"name": "x" * 501})
        with self.assertRaises(ShopeeHelperError):
            sanitize_helper_metadata({"shop": "x" * 201})
        with self.assertRaises(ShopeeHelperError):
            sanitize_helper_metadata({"image_url": "https://example.com/" + "x" * 2049})

        for bad in (
            "javascript:alert(1)",
            "file:///tmp/a.jpg",
            "https://user:pass@example.com/a.jpg",
            "https://example.com/a.jpg\nX-Test: bad",
        ):
            with self.subTest(url=bad), self.assertRaises(ShopeeHelperError):
                sanitize_helper_metadata({"image_url": bad})


class ShopeeHelperPairingTests(unittest.TestCase):
    def setUp(self):
        from acp.core import helper_pairing
        helper_pairing.reset()

    def tearDown(self):
        from acp.core import helper_pairing
        helper_pairing.reset()

    def test_pairing_accepts_same_product_slug_and_canonical_shapes(self):
        from acp.core import helper_pairing

        issued = helper_pairing.issue(VALID_PRODUCT)
        self.assertEqual(issued["expires_in"], 300)
        self.assertTrue(helper_pairing.submit(
            issued["token"],
            "https://shopee.vn/Tai-nghe-i.123.456?tracking=x",
            {"name": "Tai nghe", "current_price": 199000},
        ))
        self.assertEqual(
            helper_pairing.poll(issued["token"]),
            {"status": "ready", "metadata": {
                "name": "Tai nghe", "current_price": 199000,
                "original_price": None, "image_url": None, "shop": None,
            }},
        )

    def test_product_mismatch_does_not_consume_token(self):
        from acp.core import helper_pairing

        issued = helper_pairing.issue(VALID_PRODUCT)
        self.assertFalse(helper_pairing.submit(
            issued["token"], "https://shopee.vn/product/123/999", {"name": "Sai"}))
        self.assertEqual(helper_pairing.poll(issued["token"]), {"status": "pending"})
        self.assertTrue(helper_pairing.submit(
            issued["token"], VALID_PRODUCT, {"name": "Đúng"}))

    def test_valid_token_is_one_time(self):
        from acp.core import helper_pairing

        issued = helper_pairing.issue("https://shopee.vn/product/1/2")
        self.assertTrue(helper_pairing.submit(
            issued["token"], "https://shopee.vn/product/1/2", {"name": "X"}))
        self.assertFalse(helper_pairing.submit(
            issued["token"], "https://shopee.vn/product/1/2", {"name": "Y"}))

    def test_expired_token_is_removed(self):
        from acp.core import helper_pairing

        issued = helper_pairing.issue("https://shopee.vn/product/3/4")
        helper_pairing._tokens[issued["token"]]["created_at"] -= helper_pairing.TTL_SECONDS + 1
        self.assertIsNone(helper_pairing.poll(issued["token"]))

    def test_invalid_metadata_does_not_consume_token(self):
        from acp.core import helper_pairing

        issued = helper_pairing.issue("https://shopee.vn/product/7/8")
        self.assertFalse(helper_pairing.submit(
            issued["token"], "https://shopee.vn/product/7/8", {"current_price": True}))
        self.assertEqual(helper_pairing.poll(issued["token"]), {"status": "pending"})
        self.assertTrue(helper_pairing.submit(
            issued["token"], "https://shopee.vn/product/7/8", {"current_price": 1000}))


class ShopeeHelperRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old_admin = os.environ.get("ACP_ADMIN_PASSWORD")
        os.environ["ACP_ADMIN_PASSWORD"] = "helper-test-password"
        os.environ["ACP_ADAPTER"] = "mock"
        os.environ["ACP_SOURCE"] = "mock"
        from acp.web.server import create_app
        cls.app = create_app()
        cls.app.config.update(TESTING=True)

    @classmethod
    def tearDownClass(cls):
        if cls._old_admin is None:
            os.environ.pop("ACP_ADMIN_PASSWORD", None)
        else:
            os.environ["ACP_ADMIN_PASSWORD"] = cls._old_admin

    def setUp(self):
        from acp.core import helper_pairing
        helper_pairing.reset()
        self.client = self.app.test_client()

    def _login_and_csrf(self):
        response = self.client.post("/dangnhap", data={"password": "helper-test-password"})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            return session["csrf"]

    def _issue(self):
        csrf = self._login_and_csrf()
        response = self.client.post(
            "/sanpham/affiliate/helper/token",
            data={"product_url": VALID_PRODUCT, "_csrf": csrf},
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()["token"]

    def _payload(self, token, observed_url=VALID_PRODUCT, metadata=None):
        return {
            "token": token,
            "product_url": VALID_PRODUCT,
            "observed_url": observed_url,
            "metadata": metadata or {"name": "Tai nghe", "current_price": 199000},
        }

    def test_dashboard_pairing_endpoints_still_require_login(self):
        self.assertEqual(self.client.post(
            "/sanpham/affiliate/helper/token", data={"product_url": VALID_PRODUCT}).status_code, 302)
        self.assertEqual(self.client.get(
            "/sanpham/affiliate/helper/status?token=x").status_code, 302)

    def test_submit_rejects_non_loopback_even_if_forwarded_header_is_spoofed(self):
        token = self._issue()
        response = self.client.post(
            "/api/helper/shopee-product",
            json=self._payload(token),
            environ_base={"REMOTE_ADDR": "203.0.113.9"},
            headers={"X-Forwarded-For": "127.0.0.1"},
        )
        self.assertEqual(response.status_code, 403)

    def test_submit_rejects_proxy_forwarded_remote_client(self):
        token = self._issue()
        response = self.client.post(
            "/api/helper/shopee-product",
            json=self._payload(token),
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
        self.assertEqual(response.status_code, 403)

    def test_wrong_observed_product_does_not_burn_token(self):
        token = self._issue()
        wrong = self.client.post(
            "/api/helper/shopee-product",
            json=self._payload(token, "https://shopee.vn/product/123/999"),
        )
        self.assertGreaterEqual(wrong.status_code, 400)
        self.assertLess(wrong.status_code, 500)

        correct = self.client.post(
            "/api/helper/shopee-product", json=self._payload(token))
        self.assertEqual(correct.status_code, 200)

    def test_unknown_metadata_fields_are_not_polled_back(self):
        token = self._issue()
        payload = self._payload(token, metadata={
            "name": "Tai nghe", "current_price": 199000,
            "cookie": "must-not-survive", "token": "must-not-survive",
        })
        self.assertEqual(self.client.post(
            "/api/helper/shopee-product", json=payload).status_code, 200)
        status = self.client.get(
            f"/sanpham/affiliate/helper/status?token={token}").get_json()
        self.assertEqual(status["status"], "ready")
        self.assertNotIn("cookie", status["metadata"])
        self.assertNotIn("token", status["metadata"])

    def test_malformed_and_oversized_payloads_are_rejected(self):
        token = self._issue()
        malformed = self.client.post(
            "/api/helper/shopee-product", data="{", content_type="application/json")
        self.assertEqual(malformed.status_code, 400)

        oversized = self.client.post(
            "/api/helper/shopee-product",
            json=self._payload(token, metadata={"name": "x" * 17_000}),
        )
        self.assertEqual(oversized.status_code, 413)

    def test_replay_is_rejected(self):
        token = self._issue()
        payload = self._payload(token)
        self.assertEqual(self.client.post(
            "/api/helper/shopee-product", json=payload).status_code, 200)
        replay = self.client.post("/api/helper/shopee-product", json=payload)
        self.assertEqual(replay.status_code, 410)


class ChromeHelperStaticContractTests(unittest.TestCase):
    def _read(self, relative):
        with open(os.path.join(REPO_ROOT, relative), encoding="utf-8") as handle:
            return handle.read()

    def test_background_posts_observed_tab_url_separately_from_pairing_url(self):
        body = self._read("tools/chrome_helper/background.js")
        self.assertIn("observed_url: location.href", body)
        self.assertIn("observed_url: observed.observed_url", body)
        self.assertIn("product_url: pairing.productUrl", body)

    def test_extension_does_not_read_browser_or_shopee_credentials(self):
        combined = self._read("tools/chrome_helper/background.js") + self._read(
            "tools/chrome_helper/content_acp.js")
        for forbidden in (
            "document.cookie", "chrome.cookies", "localStorage.getItem",
            "sessionStorage.getItem", "chrome.webRequest",
        ):
            self.assertNotIn(forbidden, combined)

    def test_content_script_validates_local_acp_origin_before_pairing(self):
        body = self._read("tools/chrome_helper/content_acp.js")
        self.assertIn("function isAllowedAcpOrigin", body)
        self.assertIn("isAllowedAcpOrigin(location)", body)

    def test_manifest_keeps_minimal_permissions_and_local_hosts(self):
        manifest = json.loads(self._read("tools/chrome_helper/manifest.json"))
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(set(manifest["permissions"]), {"activeTab", "scripting"})
        self.assertEqual(set(manifest["host_permissions"]), {
            "https://shopee.vn/*",
            "http://127.0.0.1:5000/*",
            "http://localhost:5000/*",
        })


if __name__ == "__main__":
    unittest.main()
