"""Focused tests for Shopee Metadata Helper Phase 2.

Run from the directory containing the ``acp`` package:
    ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_helper -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class ShopeeHelperValidationTests(unittest.TestCase):
    def test_same_product_different_shopee_url_shapes_match(self):
        from acp.core.shopee_helper import validate_helper_submission

        got = validate_helper_submission(
            "https://shopee.vn/product/123/456",
            "https://shopee.vn/Ten-san-pham-i.123.456?sp_atk=x",
            {
                "name": "Tai nghe",
                "current_price": 199000,
                "image_url": "https://down-vn.img.susercontent.com/file/abc",
            },
        )
        self.assertEqual(got.product_id, "456")
        self.assertEqual(got.expected_product_url, "https://shopee.vn/product/123/456")
        self.assertEqual(got.observed_product_url, "https://shopee.vn/product/123/456")

    def test_opa_product_shape_canonicalizes(self):
        from acp.core.shopee_helper import canonical_helper_product

        canonical, item_id = canonical_helper_product(
            "https://shopee.vn/opaapi/lp/123/456?credential_token=must-not-survive"
        )
        self.assertEqual(canonical, "https://shopee.vn/product/123/456")
        self.assertEqual(item_id, "456")
        self.assertNotIn("credential_token", canonical)

    def test_different_product_is_rejected(self):
        from acp.core.shopee_helper import ShopeeHelperError, validate_helper_submission

        with self.assertRaises(ShopeeHelperError):
            validate_helper_submission(
                "https://shopee.vn/product/123/456",
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


if __name__ == "__main__":
    unittest.main()
