"""Prompt caption tùy biến của operator (core/prompt_template.py).

Phủ: render token, ưu tiên DB > env-file > builtin, luồng v1 + luồng Shopee
đều dùng prompt chung khi đặt, rào cấu trúc được nới nhưng rào nội dung giữ
nguyên, và UI lưu/xoá tại /chamdiem.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from acp.core import content, db, prompt_template, reviewer_caption


class DefaultPromptTests(unittest.TestCase):
    def test_default_prompt_requests_half_length_caption(self):
        path = Path(__file__).resolve().parents[1] / "docs" / "PROMPT_CAPTION_MAC_DINH.txt"
        text = path.read_text(encoding="utf-8")
        self.assertIn("3–5 dòng TỔNG CỘNG, đã tính dòng link cuối", text)
        self.assertNotIn("Caption dài 5–9 dòng", text)


def _product(**over):
    row = {"id": "p1", "provider": "SHOPEE_AFFILIATE",
           "name": "Quần Bom Nữ Form Rộng Cạp Chun _ShopX",
           "current_price": 118_700, "original_price": None,
           "category_code": "thoi-trang", "description": "Chất nỉ mềm",
           "rating": None, "review_count": 0, "sold_count": 0,
           "shop_name": "ShopX"}
    row.update(over)
    return row


class RenderTests(unittest.TestCase):
    def test_tokens_replaced_with_real_data(self):
        tpl = "Tên: {{TEN}} | Giá: {{GIA}} | Giảm {{GIAM}}% | Mục {{DANH_MUC}} | Link {{LINK}}"
        out = prompt_template.render(tpl, _product(), "nháp\nhttps://s.shopee.vn/abc")
        self.assertIn("Quần Bom Nữ Form Rộng Cạp Chun", out)   # hậu tố shop đã cắt
        self.assertNotIn("ShopX", out)
        self.assertIn("118.700đ", out)
        self.assertIn("Giảm 0%", out)
        self.assertIn("thoi-trang", out)
        self.assertIn("https://s.shopee.vn/abc", out)

    def test_discount_token_uses_real_original_price(self):
        out = prompt_template.render("{{GIAM}}%", _product(original_price=237_400), "")
        self.assertEqual(out, "50%")

    def test_draft_passed_verbatim(self):
        out = prompt_template.render("X{{DRAFT}}Y", _product(), "A\nB")
        self.assertEqual(out, "XA\nBY")


class V1UsesCustomPromptTests(unittest.TestCase):
    def setUp(self):
        self.previous_db_path = db.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "prompt-v1.db")
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.previous_db_path
        self.tmp.cleanup()
        os.environ.pop("ACP_CAPTION_PROMPT_FILE", None)

    def test_custom_template_replaces_builtin_prompt(self):
        captured = {}
        conn = db.connect()
        try:
            from acp.core.system_settings import set_system_setting
            set_system_setting(conn, prompt_template.CAPTION_PROMPT_KEY,
                               "PROMPT_CUA_TUI cho {{TEN}}, draft là: {{DRAFT}}")
        finally:
            conn.close()

        def fake_llm(prompt):
            captured["prompt"] = prompt
            return "ok\nhttps://x.test/l"

        content.set_llm(fake_llm)
        try:
            cap = content.generate(_product(), "spec_highlight", "https://x.test/l",
                                   hook_code="H9_TRUCTIEP", rng=None)
        finally:
            content.set_llm(None)
        self.assertIn("PROMPT_CUA_TUI", captured["prompt"])
        self.assertIn("Quần Bom Nữ", captured["prompt"])
        self.assertTrue(cap.startswith("ok"))

    def test_env_file_used_when_db_empty(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as fh:
            fh.write("PROMPT_TU_FILE {{GIA}}")
            path = fh.name
        os.environ["ACP_CAPTION_PROMPT_FILE"] = path
        try:
            captured = {}

            def fake_llm(prompt):
                captured["prompt"] = prompt
                return "ok\nhttps://x.test/l"

            content.set_llm(fake_llm)
            try:
                content.generate(_product(), "spec_highlight", "https://x.test/l",
                                 rng=random_rng())
            finally:
                content.set_llm(None)
            self.assertIn("PROMPT_TU_FILE 118.700đ", captured["prompt"])
        finally:
            os.unlink(path)


def random_rng():
    import random as _r
    return _r.Random(1)


class ReviewerCustomPromptTests(unittest.TestCase):
    def setUp(self):
        self.previous_db_path = db.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "prompt-reviewer.db")
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.previous_db_path
        self.tmp.cleanup()

    def test_custom_prompt_allows_longer_caption_but_keeps_content_guards(self):
        conn = db.connect()
        try:
            from acp.core.system_settings import set_system_setting
            set_system_setting(conn, prompt_template.CAPTION_PROMPT_KEY,
                               "Viết dài chi tiết cho {{TEN}} từ {{DRAFT}}")
        finally:
            conn.close()
        long_ok = (
            "lướt thấy quần bom này form rộng dễ mặc ghê\n"
            "chất nỉ cạp chun, phối áo oversized hay đồ bộ đều được\n"
            "giá 118.700đ thì quá ổn cho một chiếc quần đi chơi\n"
            "https://s.shopee.vn/abc"
        )
        content.set_llm(lambda _p: long_ok)
        try:
            cap = reviewer_caption.generate(_product(), "https://s.shopee.vn/abc",
                                            llm_fn=content._llm_fn)
        finally:
            content.set_llm(None)
        # structural gates đã nới: caption >5 dòng ngắn kiểu cũ vẫn được nhận
        self.assertIn("quá ổn", cap)

        fabricated = ("mình đã dùng quần này 2 tuần cực thích\n"
                      f"https://s.shopee.vn/abc")
        content.set_llm(lambda _p: fabricated)
        try:
            cap2 = reviewer_caption.generate(_product(), "https://s.shopee.vn/abc",
                                             llm_fn=content._llm_fn)
        finally:
            content.set_llm(None)
        self.assertNotIn("mình đã dùng", cap2)   # rơi về nháp an toàn

    def test_no_custom_template_keeps_builtin_behaviour(self):
        sent = {}
        content.set_llm(lambda p: sent.setdefault("p", p) or ("ngắn thôi\nhttps://s.shopee.vn/abc"))
        try:
            reviewer_caption.generate(_product(), "https://s.shopee.vn/abc",
                                      llm_fn=content._llm_fn)
        finally:
            content.set_llm(None)
        self.assertNotIn("PROMPT_CUA_TUI", sent["p"])
        self.assertIn("Threads cá nhân Việt Nam", sent["p"])  # prompt mặc định luồng Shopee

    def test_custom_template_does_not_fall_back_when_llm_fails(self):
        conn = db.connect()
        try:
            from acp.core.system_settings import set_system_setting
            set_system_setting(conn, prompt_template.CAPTION_PROMPT_KEY,
                               "Viết caption mới cho {{TEN}} từ {{DRAFT}}")
        finally:
            conn.close()

        def failed_llm(_prompt):
            raise RuntimeError("OpenAI HTTP 429")

        with self.assertRaises(reviewer_caption.CaptionRewriteError):
            reviewer_caption.generate(_product(), "https://s.shopee.vn/abc",
                                      llm_fn=failed_llm)


if __name__ == "__main__":
    unittest.main(verbosity=2)
