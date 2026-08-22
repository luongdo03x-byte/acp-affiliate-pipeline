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


def _first_line(caption):
    return next(line.strip() for line in caption.splitlines() if line.strip())


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
        self.assertIn(content.DISCLOSURE_DEFAULT, caption)
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

    def test_variant_code_changes_reviewer_hook_but_keeps_same_real_signal(self):
        product = _product()

        social = content.generate(
            product, "spec_highlight", AFFILIATE,
            hook_code="H5_XAHOI", rng=random.Random(11),
        )
        question = content.generate(
            product, "spec_highlight", AFFILIATE,
            hook_code="H4_CAUHOI", rng=random.Random(11),
        )

        social_hook = _first_line(social)
        question_hook = _first_line(question)
        self.assertNotEqual(social_hook, question_hook)
        self.assertIn("40k+", social_hook)
        self.assertIn("40k+", question_hook)
        self.assertIn("?", question_hook)
        self.assertLessEqual(len(social_hook.split()), 12)
        self.assertLessEqual(len(question_hook.split()), 12)

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

        first_line = _first_line(caption)
        self.assertTrue("55-90kg" in first_line.lower() or "55–90kg" in first_line.lower())
        self.assertLessEqual(len(first_line.split()), 12)
        self.assertIn("129.000đ", caption)
        self.assertIn(AFFILIATE, caption)

    def test_real_csv_set_title_uses_a_concrete_listing_detail_not_generic_filler(self):
        product = _product(
            name="Set Bộ Cộc Sát Nách In Cún SỌC Chất Thun Tăm Hàn Mỏng Nhẹ Mặc Nhà Phong Cách Trẻ Trung Dễ Mặc Top",
            current_price=85_000,
            sold_count=1_000,
        )

        caption = content.generate(
            product,
            "spec_highlight",
            AFFILIATE,
            hook_code="H5_XAHOI",
            rng=random.Random(7),
        )

        self.assertNotIn("chỉ note lại đúng thông tin nổi bật", caption.lower())
        self.assertTrue("thun tăm" in caption.lower() or "mặc nhà" in caption.lower())

    def test_llm_rewrite_is_used_when_it_keeps_real_facts_and_short_structure(self):
        rewritten = (
            "118,7k mà 40k+ lượt mua, mình dừng lại xem.\n"
            "Form rộng + cạp chun là điểm mình để ý.\n"
            "Ai đang tìm kiểu này xem thêm ↓\n"
            f"{AFFILIATE}"
        )
        content.set_llm(lambda _prompt: rewritten)

        caption = content.generate(
            _product(),
            "spec_highlight",
            AFFILIATE,
            hook_code="H5_XAHOI",
            rng=random.Random(4),
        )

        self.assertTrue(caption.startswith("118,7k mà 40k+ lượt mua"))
        self.assertIn(AFFILIATE, caption)
        self.assertIn(content.DISCLOSURE_DEFAULT, caption)

    def test_llm_rewrite_falls_back_when_it_invents_first_hand_experience(self):
        unsafe = (
            "Mình đã dùng quần này 2 tuần và cực thích.\n"
            "Form rộng dễ mặc.\n"
            "Xem ở đây ↓\n"
            f"{AFFILIATE}"
        )
        content.set_llm(lambda _prompt: unsafe)

        caption = content.generate(
            _product(),
            "spec_highlight",
            AFFILIATE,
            hook_code="H5_XAHOI",
            rng=random.Random(5),
        )

        self.assertNotIn("mình đã dùng", caption.lower())
        self.assertNotIn("2 tuần", caption.lower())
        self.assertIn("40k+", caption)

    def test_llm_rewrite_falls_back_when_it_invents_a_new_number(self):
        unsafe = (
            "99k cho mẫu này thì quá hời.\n"
            "Form rộng + cạp chun là điểm mình để ý.\n"
            "Xem thêm ↓\n"
            f"{AFFILIATE}"
        )
        content.set_llm(lambda _prompt: unsafe)

        caption = content.generate(
            _product(),
            "spec_highlight",
            AFFILIATE,
            hook_code="H5_XAHOI",
            rng=random.Random(6),
        )

        self.assertNotIn("99k", caption.lower())
        self.assertIn("118,7k", caption)

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
