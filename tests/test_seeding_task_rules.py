"""Task intake/parser contracts for Facebook Seeding Assistant."""
from __future__ import annotations

import unittest

from acp.core import seeding_tasks


class SeedingTaskRuleTests(unittest.TestCase):
    def test_parses_common_vietnamese_job_instruction(self) -> None:
        rules = seeding_tasks.parse_task_instruction(
            "Dạ em khóa slot. Em mở slot 1k KHÔNG NHẮC SỮA. "
            "Yêu cầu LIKE BÀI ĐĂNG + mỗi acc FB/ TT bình luận 3 CMT "
            "(1 cmt chính + 2 cmt reply) (mỗi mom tối đa 3 acc)"
        )
        self.assertTrue(rules["like_required"])
        self.assertEqual(1, rules["main_comments_per_account"])
        self.assertEqual(2, rules["replies_per_account"])
        self.assertEqual(3, rules["comments_per_account"])
        self.assertEqual(3, rules["max_accounts"])
        self.assertEqual(["sữa"], rules["forbidden_words"])
        self.assertIn("facebook", rules["platforms"])

    def test_defaults_to_one_account_and_one_main_comment(self) -> None:
        rules = seeding_tasks.parse_task_instruction("Bình luận phù hợp nội dung bài")
        self.assertFalse(rules["like_required"])
        self.assertEqual(1, rules["main_comments_per_account"])
        self.assertEqual(0, rules["replies_per_account"])
        self.assertEqual(1, rules["comments_per_account"])
        self.assertEqual(1, rules["max_accounts"])
        self.assertEqual([], rules["forbidden_words"])

    def test_slot_blueprint_expands_each_account(self) -> None:
        rules = {
            "main_comments_per_account": 1,
            "replies_per_account": 2,
            "max_accounts": 3,
        }
        slots = seeding_tasks.build_slot_blueprint(rules)
        self.assertEqual(9, len(slots))
        self.assertEqual(
            [
                (1, "MAIN", 1), (1, "REPLY", 1), (1, "REPLY", 2),
                (2, "MAIN", 1), (2, "REPLY", 1), (2, "REPLY", 2),
                (3, "MAIN", 1), (3, "REPLY", 1), (3, "REPLY", 2),
            ],
            [(s["account_slot"], s["comment_type"], s["item_index"]) for s in slots],
        )

    def test_comment_plan_rejects_forbidden_or_near_duplicate_content(self) -> None:
        rules = {
            "main_comments_per_account": 1,
            "replies_per_account": 2,
            "max_accounts": 2,
            "forbidden_words": ["sữa"],
        }
        bad_plan = {
            "accounts": [
                {
                    "slot": 1,
                    "main_comments": ["Mình thấy cách này khá hợp lý."],
                    "replies": ["Chuẩn đó, nên làm từ từ.", "Có thể thử cách này trước."],
                },
                {
                    "slot": 2,
                    "main_comments": ["Mình thấy cách này khá hợp lý!"],
                    "replies": ["Không nên nhắc tới sữa ở đây.", "Mình đồng ý với ý này."],
                },
            ]
        }
        with self.assertRaises(ValueError):
            seeding_tasks.validate_comment_plan(bad_plan, rules)

        good_plan = {
            "accounts": [
                {
                    "slot": 1,
                    "main_comments": ["Theo mình nên cho bé làm quen từng bước để đỡ bị ngợp."],
                    "replies": ["Chuẩn, giai đoạn đầu cứ từ từ sẽ dễ thích nghi hơn.", "Mình cũng ưu tiên giữ lịch sinh hoạt ổn định trước."],
                },
                {
                    "slot": 2,
                    "main_comments": ["Có thể cho bé ghé làm quen môi trường trước vài buổi rồi mới bắt đầu chính thức."],
                    "replies": ["Ý này hợp lý, chuẩn bị tâm lý trước thường sẽ nhẹ nhàng hơn.", "Mình nghĩ quan sát phản ứng của bé từng ngày rồi điều chỉnh sẽ ổn."],
                },
            ]
        }
        normalized = seeding_tasks.validate_comment_plan(good_plan, rules)
        self.assertEqual(6, len(normalized))
        self.assertEqual(6, len({row["text"] for row in normalized}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
