"""Công tắc "bỏ rào chắn nội dung" (system_settings.content_guards_disabled).

Mặc định: rào BẬT (chặn công dụng/cụm cấm/bịa trải nghiệm...). Khi bật:
content.validate() chỉ còn rào kỹ thuật độ dài; reviewer _safe_rewrite chỉ còn
link affiliate bắt buộc. Tắt công tắc -> khôi phục toàn bộ.
"""
import os
import tempfile
import unittest

from acp.core import content, db, reviewer_caption
from acp.core.system_settings import (
    CONTENT_GUARDS_DISABLED,
    content_guards_disabled,
    set_system_setting,
)

BAD_CAPTION = ("Kem trị mụn chữa khỏi mụn sau 3 ngày, số 1 thị trường\n"
               "mình đã dùng và hiệu quả tuyệt vời\n"
               "https://s.shopee.vn/abc")


class GuardsSwitchTests(unittest.TestCase):
    def setUp(self):
        self.previous_db_path = db.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "guards.db")
        db.init_db()
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.previous_db_path
        self.tmp.cleanup()

    def _set(self, value):
        set_system_setting(self.conn, CONTENT_GUARDS_DISABLED, value, actor="test")

    def test_default_guards_block_efficacy_and_banned_phrases(self):
        problems = content.validate(BAD_CAPTION)
        self.assertTrue(problems, "mặc định phải chặn")

    def test_disabled_guards_only_enforce_length(self):
        self._set("1")
        self.assertTrue(content_guards_disabled(self.conn))
        self.assertEqual(content.validate(BAD_CAPTION), [])
        too_long = "x" * 600
        problems = content.validate(too_long)
        self.assertEqual(len(problems), 1)
        self.assertIn("Dài", problems[0])

    def test_switch_back_on_restores_blocks(self):
        self._set("1")
        self._set("0")
        self.assertFalse(content_guards_disabled(self.conn))
        self.assertTrue(content.validate(BAD_CAPTION))


class ReviewerGuardsOffTests(unittest.TestCase):
    def setUp(self):
        self.previous_db_path = db.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "guards-reviewer.db")
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.previous_db_path
        self.tmp.cleanup()

    def test_guards_off_accepts_fabricated_but_requires_link(self):
        self.conn = db.connect()
        try:
            set_system_setting(self.conn, CONTENT_GUARDS_DISABLED, "1", actor="test")
            fabricated = ("mình đã dùng kem này chữa khỏi mụn sau 3 ngày #review\n"
                          "không cần link cũng được nhé")
            product = {"id": "p", "provider": "SHOPEE_AFFILIATE",
                       "name": "Kem trị mụn", "current_price": 100_000,
                       "original_price": None, "category_code": "my-pham",
                       "description": "", "rating": None, "review_count": 0,
                       "sold_count": 0, "shop_name": "S"}
            out = reviewer_caption.generate(
                product, "https://s.shopee.vn/abc",
                llm_fn=lambda _p: fabricated.replace("#review", "").replace(
                    "không cần link cũng được nhé", "https://s.shopee.vn/abc"))
            # không còn bị bẻ về nháp an toàn -- operator tự chịu trách nhiệm
            self.assertIn("chữa khỏi mụn", out)
            self.assertIn("https://s.shopee.vn/abc", out)
        finally:
            self.conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
