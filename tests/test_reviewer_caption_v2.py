import random
import unittest

from acp.core import content


AFFILIATE = "https://s.shopee.vn/reviewer-test"
HOOK_CODES = (
    "H1_GIAGIAM", "H2_SOSANH", "H3_KHANHIEM", "H4_CAUHOI", "H5_XAHOI",
    "H6_HANGMOI", "H7_TIETKIEM", "H8_CANHBAO", "H9_TRUCTIEP",
)


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

    def test_shopee_caption_is_short_natural_and_ignores_sold_count(self):
        product = _product()
        caption = content.generate(
            product, "spec_highlight", AFFILIATE,
            hook_code="H5_XAHOI", rng=random.Random(1),
        )

        nonempty = [line.strip() for line in caption.splitlines() if line.strip()]
        self.assertLessEqual(len(nonempty), 5)
        self.assertLessEqual(len(nonempty[0].split()), 12)
        self.assertIn(AFFILIATE, caption)
        self.assertTrue("118,7k" in caption or "118.700đ" in caption)
        self.assertNotIn("40k", caption.lower())
        self.assertNotIn("lượt mua", caption.lower())
        self.assertNotIn("sold", caption.lower())
        self.assertNotIn(content.DISCLOSURE_DEFAULT, caption)
        self.assertNotIn("#tiepthilienket", caption.lower())
        self.assertNotIn(product["name"], caption)
        self.assertLessEqual(len(caption), 500)
        for phrase in (
            "sản phẩm này", "sự lựa chọn lý tưởng", "không thể bỏ lỡ",
            "mình đã dùng", "mình dùng thử", "listing", "detail",
            "mình chú ý", "mình note lại", "đáng chú ý", "khoan lướt",
        ):
            self.assertNotIn(phrase, caption.lower())

    def test_changing_sold_count_does_not_change_caption(self):
        low = content.generate(
            _product(sold_count=0), "spec_highlight", AFFILIATE,
            hook_code="H5_XAHOI", rng=random.Random(2),
        )
        high = content.generate(
            _product(sold_count=987_654), "spec_highlight", AFFILIATE,
            hook_code="H5_XAHOI", rng=random.Random(2),
        )

        self.assertEqual(low, high)

    def test_shopee_reviewer_ignores_explicit_manual_disclosure(self):
        caption = content.generate(
            _product(), "spec_highlight", AFFILIATE,
            disclosure=content.DISCLOSURE_DEFAULT,
            hook_code="H5_XAHOI", rng=random.Random(13),
        )

        self.assertIn(AFFILIATE, caption)
        self.assertNotIn(content.DISCLOSURE_DEFAULT, caption)
        self.assertNotIn("#tiepthilienket", caption.lower())

    def test_variant_code_changes_reviewer_hook_without_sold_count(self):
        reaction = content.generate(
            _product(), "spec_highlight", AFFILIATE,
            hook_code="H5_XAHOI", rng=random.Random(11),
        )
        question = content.generate(
            _product(), "spec_highlight", AFFILIATE,
            hook_code="H4_CAUHOI", rng=random.Random(11),
        )

        reaction_hook = _first_line(reaction)
        question_hook = _first_line(question)
        self.assertNotEqual(reaction_hook, question_hook)
        self.assertIn("?", question_hook)
        self.assertLessEqual(len(reaction_hook.split()), 12)
        self.assertLessEqual(len(question_hook.split()), 12)
        for caption in (reaction, question):
            self.assertNotIn("40k", caption.lower())
            self.assertNotIn("lượt mua", caption.lower())

    def test_all_measured_hook_codes_remain_distinct_short_and_human(self):
        hooks = []
        for code in HOOK_CODES:
            caption = content.generate(
                _product(), "spec_highlight", AFFILIATE,
                hook_code=code, rng=random.Random(12),
            )
            hook = _first_line(caption)
            hooks.append(hook)
            self.assertLessEqual(len(hook.split()), 12, msg=f"{code}: {hook}")
            self.assertNotIn("40k", hook.lower(), msg=f"{code}: {hook}")
            self.assertNotIn("lượt mua", hook.lower(), msg=f"{code}: {hook}")
            self.assertNotIn("mình note lại", hook.lower(), msg=f"{code}: {hook}")
            self.assertNotIn("mình chú ý", hook.lower(), msg=f"{code}: {hook}")

        self.assertEqual(len(set(hooks)), len(HOOK_CODES))

    def test_bigsize_product_leads_with_the_audience_pain_point(self):
        product = _product(
            name="Áo Yếm Bigsize Nữ 55-90kg Lưng Nhún Chun Dễ Phối",
            current_price=129_000,
            sold_count=7_000,
        )
        caption = content.generate(
            product, "comparison", AFFILIATE,
            hook_code="H4_CAUHOI", rng=random.Random(2),
        )

        first_line = _first_line(caption)
        self.assertTrue("55-90kg" in first_line.lower() or "55–90kg" in first_line.lower())
        self.assertLessEqual(len(first_line.split()), 12)
        self.assertIn("129.000đ", caption)
        self.assertIn(AFFILIATE, caption)
        self.assertNotIn("7k", caption.lower())
        self.assertNotIn("lượt mua", caption.lower())

    def test_real_csv_set_title_uses_a_concrete_detail_not_generic_filler(self):
        product = _product(
            name="Set Bộ Cộc Sát Nách In Cún SỌC Chất Thun Tăm Hàn Mỏng Nhẹ Mặc Nhà Phong Cách Trẻ Trung Dễ Mặc Top",
            current_price=85_000,
            sold_count=1_000,
        )
        caption = content.generate(
            product, "spec_highlight", AFFILIATE,
            hook_code="H5_XAHOI", rng=random.Random(7),
        )

        self.assertNotIn("listing", caption.lower())
        self.assertNotIn("detail", caption.lower())
        self.assertNotIn("1k", caption.lower())
        self.assertNotIn("lượt mua", caption.lower())
        self.assertTrue("thun tăm" in caption.lower() or "mặc nhà" in caption.lower())

    def test_llm_prompt_does_not_expose_sold_count(self):
        seen = {}

        def rewrite(prompt):
            seen["prompt"] = prompt
            return (
                "ê cái form này nhìn ổn phết =))\n"
                "form rộng + cạp chun, giá 118,7k\n"
                f"{AFFILIATE}"
            )

        content.set_llm(rewrite)
        content.generate(
            _product(), "spec_highlight", AFFILIATE,
            hook_code="H5_XAHOI", rng=random.Random(4),
        )

        prompt = seen["prompt"].lower()
        self.assertNotIn("sold count", prompt)
        self.assertNotIn("40000", prompt)
        self.assertNotIn("40k", prompt)
        self.assertIn("118.700đ", prompt)

    def test_llm_rewrite_is_used_when_it_is_short_and_uses_allowed_facts(self):
        rewritten = (
            "ê cái form này nhìn ổn phết =))\n"
            "form rộng + cạp chun, giá 118,7k\n"
            f"{AFFILIATE}"
        )
        content.set_llm(lambda _prompt: rewritten)

        caption = content.generate(
            _product(), "spec_highlight", AFFILIATE,
            hook_code="H5_XAHOI", rng=random.Random(4),
        )

        self.assertTrue(caption.startswith("ê cái form này"))
        self.assertIn("118,7k", caption)
        self.assertNotIn("40k", caption.lower())
        self.assertNotIn("lượt mua", caption.lower())
        self.assertIn(AFFILIATE, caption)

    def test_llm_rewrite_falls_back_when_it_invents_first_hand_experience(self):
        unsafe = (
            "mình đã dùng quần này 2 tuần và cực thích\n"
            "form rộng dễ mặc\n"
            f"{AFFILIATE}"
        )
        content.set_llm(lambda _prompt: unsafe)

        caption = content.generate(
            _product(), "spec_highlight", AFFILIATE,
            hook_code="H5_XAHOI", rng=random.Random(5),
        )

        self.assertNotIn("mình đã dùng", caption.lower())
        self.assertNotIn("2 tuần", caption.lower())
        self.assertNotIn("40k", caption.lower())
        self.assertIn(AFFILIATE, caption)

    def test_llm_rewrite_falls_back_when_it_invents_a_new_number(self):
        unsafe = (
            "99k cho mẫu này thì cũng được đó\n"
            "form rộng + cạp chun nhìn khá dễ mặc\n"
            f"{AFFILIATE}"
        )
        content.set_llm(lambda _prompt: unsafe)

        caption = content.generate(
            _product(), "spec_highlight", AFFILIATE,
            hook_code="H5_XAHOI", rng=random.Random(6),
        )

        self.assertNotIn("99k", caption.lower())
        self.assertTrue("118,7k" in caption or "118.700đ" in caption)
        self.assertNotIn("40k", caption.lower())

    def test_non_shopee_product_keeps_legacy_generation_path(self):
        product = _product(
            provider="LEGACY", name="Tai nghe kiểm thử legacy",
            sold_count=0, current_price=300_000,
        )
        caption = content.generate(
            product, "spec_highlight", AFFILIATE,
            hook_code="H9_TRUCTIEP", rng=random.Random(3),
        )

        self.assertIn("Tai nghe kiểm thử legacy", caption)
        self.assertIn(AFFILIATE, caption)


if __name__ == "__main__":
    unittest.main()
