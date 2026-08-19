"""Multi-account comment plan generation contracts."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

_tmp = tempfile.mkdtemp(prefix="acp-seeding-task-plan-")
os.environ["ACP_DB"] = os.path.join(_tmp, "task-plan.db")

from acp.core import db, seeding_tasks  # noqa: E402

db.DB_PATH = os.environ["ACP_DB"]

INSTRUCTION = (
    "LIKE BÀI ĐĂNG; mỗi acc 3 CMT (1 cmt chính + 2 cmt reply); "
    "tối đa 3 acc; KHÔNG NHẮC SỮA."
)
POST_URL = "https://www.facebook.com/groups/demo/permalink/123/"
POST_TEXT = "Mọi người chia sẻ kinh nghiệm giúp bé làm quen môi trường mới với ạ."


def _good_plan_json() -> str:
    return json.dumps(
        {
            "accounts": [
                {
                    "slot": 1,
                    "main_comments": ["Có thể cho bé làm quen từng bước để đỡ bị ngợp."],
                    "replies": [
                        "Chuẩn, giai đoạn đầu cứ từ từ sẽ dễ thích nghi hơn.",
                        "Mình cũng ưu tiên giữ lịch sinh hoạt ổn định trước.",
                    ],
                },
                {
                    "slot": 2,
                    "main_comments": ["Thử cho bé ghé làm quen môi trường trước vài buổi cũng là một cách."],
                    "replies": [
                        "Ý này hợp lý, chuẩn bị tâm lý trước sẽ nhẹ nhàng hơn.",
                        "Theo dõi phản ứng của bé từng ngày rồi điều chỉnh sẽ dễ hơn.",
                    ],
                },
                {
                    "slot": 3,
                    "main_comments": ["Mình nghĩ nên bắt đầu bằng thời gian ngắn rồi tăng dần khi bé quen."],
                    "replies": [
                        "Đúng rồi, đừng thay đổi quá nhiều thứ cùng một lúc.",
                        "Có lịch cố định và báo trước cho bé thường sẽ dễ phối hợp hơn.",
                    ],
                },
            ]
        },
        ensure_ascii=False,
    )


class TaskCommentPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        db.DB_PATH = os.path.join(_tmp, f"{self._testMethodName}.db")
        if os.path.exists(db.DB_PATH):
            os.unlink(db.DB_PATH)
        db.init_db()
        self.conn = db.connect()
        seeding_tasks.ensure_task_schema(self.conn)
        task = seeding_tasks.create_task(
            self.conn,
            name="A2GR-64",
            instruction=INSTRUCTION,
            post_url=POST_URL,
        )
        self.campaign_id = task["campaign"]["id"]
        self.target_id = task["target_id"]

    def tearDown(self) -> None:
        self.conn.close()

    def test_generate_comment_plan_persists_all_distinct_slots(self) -> None:
        prompts = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            return _good_plan_json()

        rows = seeding_tasks.generate_comment_plan(
            self.conn,
            campaign_id=self.campaign_id,
            target_id=self.target_id,
            post_text=POST_TEXT,
            llm_fn=fake_llm,
        )
        self.assertEqual(9, len(rows))
        self.assertEqual(9, len({row["generated_text"] for row in rows}))
        self.assertTrue(all(row["status"] == "GENERATED" for row in rows))
        self.assertEqual(1, len(prompts))
        self.assertIn(POST_TEXT, prompts[0])
        self.assertIn(INSTRUCTION, prompts[0])

    def test_invalid_plan_is_rejected_without_partial_persistence(self) -> None:
        bad = json.loads(_good_plan_json())
        bad["accounts"][1]["main_comments"][0] = bad["accounts"][0]["main_comments"][0]

        with self.assertRaises(ValueError):
            seeding_tasks.generate_comment_plan(
                self.conn,
                campaign_id=self.campaign_id,
                target_id=self.target_id,
                post_text=POST_TEXT,
                llm_fn=lambda _prompt: json.dumps(bad, ensure_ascii=False),
            )

        rows = seeding_tasks.list_comment_slots(self.conn, self.campaign_id)
        self.assertEqual(9, len(rows))
        self.assertTrue(all(row["status"] == "EMPTY" for row in rows))
        self.assertTrue(all(row["generated_text"] is None for row in rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
