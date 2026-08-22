import random
import unittest

from acp.core import content


AFFILIATE = "https://s.shopee.vn/reviewer-test"


def _product(**overrides):
    row = {
        "id": "p1",
        "provider": "SHOPEE_AFFILIATE",
        "name": "Quần Bom Nữ Form Rộng Cạp Chun Phong Cách Hàn Quốc Dễ Phối Đồ",
        "description": "",
        "current_price": 118_700,
        "original_price": None,
        "commission_value": 12_000,
        "commission_rate": None,
        "category_code": "thoi-trang",
        "rating": None,
        "review_count": 0,
        "sold_count": 40_000,
        "shop_name": "Fashion House",
    }
    row.update(overrides)
    return row


class ReviewerCaptionV2Tests(unittest.TestCase):
    def tearDown(self):
        content.set_llm(None)

    def test_shopee_social_proof_caption_is_short_and_does_not_read_like_listing_title(self):
        product = _product()

        caption = content.generate(
            product,
            "spec_highlight",
            AFFILIATE,
            hook_code="H5_XAHOI",
            rng=random.Random(1),
        )

        nonempty = [line.strip() for line in caption.splitlines() if line.strip()]
        self.assertLessEqual(len(nonempty), 6)
        self.assertLessEqual(len(nonempty[0].split()), 12)
        self.assertIn("40", caption)
        self.assertIn(AFFILIATE, caption)
        self.assertNotIn(product["name"], caption)
        self.assertLessEqual(len(caption), 500)
        for phrase in (
            "sản phẩm này",
            "sự lựa chọn lý tưởng",
            "không thể bỏ lỡ",
            "mình đã dùng",
            "mình dùng thử",
        ):
            self.assertNotIn(phrase, caption.lower())

    def test_bigsize_product_leads_with_the_audience_pain_point(self):
        product = _product(
            name="Áo Yếm Bigsize Nữ 55-90kg Lưng Nhún Chun Dễ Phối",
            current_price=129_000,
            sold_count=7_000,
        )

        caption = content.generate(
            product,
            "comparison",
            AFFILIATE,
            hook_code="H4_CAUHOI",
            rng=random.Random(2),
        )

        first_line = next(line.strip() for line in caption.splitlines() if line.strip())
        self.assertTrue("55-90kg" in first_line.lower() or "55–90kg" in first_line.lower())
        self.assertLessEqual(len(first_line.split()), 12)
        self.assertIn("129.000đ", caption)
        self.assertIn(AFFILIATE, caption)

    def test_non_shopee_product_keeps_legacy_generation_path(self):
        product = _product(
            provider="LEGACY",
            name="Tai nghe kiểm thử legacy",
            sold_count=0,
            current_price=300_000,
        )

        caption = content.generate(
            product,
            "spec_highlight",
            AFFILIATE,
            hook_code="H9_TRUCTIEP",
            rng=random.Random(3),
        )

        self.assertIn("Tai nghe kiểm thử legacy", caption)
        self.assertIn(AFFILIATE, caption)


if __name__ == "__main__":
    unittest.main()
