"""Phase 4 preliminary confirmation preview tests."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class ShopeePreviewTests(unittest.TestCase):
    def test_preliminary_preview_is_factual_bounded_and_preserves_links(self):
        from acp.web.shopee_polish import build_preliminary_preview

        affiliate = "https://s.shopee.vn/abcdef"
        preview = build_preliminary_preview(
            name="Tai nghe Bluetooth",
            current_price=199000,
            original_price=299000,
            image_url="https://down-vn.img.susercontent.com/file/abc",
            affiliate_url=affiliate,
            product_url="https://shopee.vn/product/123/456",
            channels=[{"code": "ch1", "handle": "@deal", "platform": "threads"}],
            metadata_source="helper",
        )
        self.assertTrue(preview["preliminary"])
        self.assertEqual(preview["affiliate_url"], affiliate)
        self.assertEqual(preview["product_url"], "https://shopee.vn/product/123/456")
        self.assertIn("Tai nghe Bluetooth", preview["caption"])
        self.assertIn("199.000đ", preview["caption"])
        self.assertIn(affiliate, preview["caption"])
        self.assertIn("#tiepthilienket", preview["caption"])
        self.assertLessEqual(len(preview["caption"]), 500)
        self.assertNotIn("mình đã dùng", preview["caption"].lower())
        self.assertEqual(preview["metadata_source"], "helper")
        self.assertEqual(preview["channels"][0]["code"], "ch1")

    def test_preview_rejects_invalid_price_image_product_and_affiliate_urls(self):
        from acp.web.shopee_polish import ShopeePreviewError, build_preliminary_preview

        base = dict(
            name="X", current_price=100000, original_price=None,
            image_url="https://example.com/x.jpg",
            affiliate_url="https://s.shopee.vn/abc",
            product_url="https://shopee.vn/product/1/2",
            channels=[{"code": "ch1", "handle": "@x", "platform": "threads"}],
            metadata_source="manual",
        )
        bad_cases = (
            {"current_price": 0},
            {"image_url": "javascript:alert(1)"},
            {"product_url": "https://example.com/product/1/2"},
            {"affiliate_url": "https://evil.example/x"},
            {"channels": []},
        )
        for change in bad_cases:
            values = dict(base)
            values.update(change)
            with self.subTest(change=change), self.assertRaises(ShopeePreviewError):
                build_preliminary_preview(**values)


if __name__ == "__main__":
    unittest.main()
