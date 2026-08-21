import unittest

from acp.core import helper_pairing


class ShopeeHelperEnrichmentConsumeTests(unittest.TestCase):
    def setUp(self):
        helper_pairing.reset()

    def tearDown(self):
        helper_pairing.reset()

    def _ready_token(self, product_url="https://shopee.vn/product/123/456"):
        issued = helper_pairing.issue(product_url)
        self.assertTrue(helper_pairing.submit(
            issued["token"],
            product_url,
            {
                "name": "Tai nghe",
                "current_price": 199000,
                "image_url": "https://down-vn.img.susercontent.com/file/helper-image",
            },
        ))
        return issued["token"]

    def test_consume_ready_for_product_returns_metadata_only_for_bound_product(self):
        token = self._ready_token()

        metadata = helper_pairing.consume_ready_for_product(
            token,
            "https://shopee.vn/product/123/456",
        )

        self.assertEqual(metadata["name"], "Tai nghe")
        self.assertEqual(
            metadata["image_url"],
            "https://down-vn.img.susercontent.com/file/helper-image",
        )
        self.assertIsNone(helper_pairing.poll(token))

    def test_wrong_product_cannot_consume_ready_token(self):
        token = self._ready_token()

        self.assertIsNone(helper_pairing.consume_ready_for_product(
            token,
            "https://shopee.vn/product/123/999",
        ))
        self.assertEqual(helper_pairing.poll(token)["status"], "ready")

    def test_pending_token_cannot_be_consumed(self):
        issued = helper_pairing.issue("https://shopee.vn/product/123/456")

        self.assertIsNone(helper_pairing.consume_ready_for_product(
            issued["token"],
            "https://shopee.vn/product/123/456",
        ))
        self.assertEqual(helper_pairing.poll(issued["token"]), {"status": "pending"})


if __name__ == "__main__":
    unittest.main()
