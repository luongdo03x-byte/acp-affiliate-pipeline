"""Manual task intake persistence contracts."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

_tmp = tempfile.mkdtemp(prefix="acp-seeding-task-intake-")
os.environ["ACP_DB"] = os.path.join(_tmp, "task-intake.db")

from acp.core import db, seeding, seeding_tasks  # noqa: E402

db.DB_PATH = os.environ["ACP_DB"]


INSTRUCTION = (
    "Dạ em khóa slot. Em mở slot 1k KHÔNG NHẮC SỮA. "
    "Yêu cầu LIKE BÀI ĐĂNG + mỗi acc FB/ TT bình luận 3 CMT "
    "(1 cmt chính + 2 cmt reply) (mỗi mom tối đa 3 acc)"
)
POST_URL = "https://www.facebook.com/groups/467062964514242/permalink/1737872480766611/?rdid=test"


class TaskIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        db.DB_PATH = os.path.join(_tmp, f"{self._testMethodName}.db")
        if os.path.exists(db.DB_PATH):
            os.unlink(db.DB_PATH)
        db.init_db()
        self.conn = db.connect()
        seeding.set_llm(None)
        seeding_tasks.ensure_task_schema(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_schema_has_task_rules_and_comment_slots(self) -> None:
        campaign_cols = {
            row[1] for row in self.conn.execute("PRAGMA table_info(seeding_campaign)").fetchall()
        }
        self.assertIn("task_rules", campaign_cols)
        tables = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertIn("seeding_comment_slot", tables)

    def test_create_task_allows_duplicate_names_and_precreates_slots(self) -> None:
        first = seeding_tasks.create_task(
            self.conn,
            name="A2GR-64",
            instruction=INSTRUCTION,
            post_url=POST_URL,
        )
        second = seeding_tasks.create_task(
            self.conn,
            name="A2GR-64",
            instruction=INSTRUCTION,
            post_url=POST_URL,
        )
        self.assertNotEqual(first["campaign"]["id"], second["campaign"]["id"])
        self.assertEqual(INSTRUCTION, first["campaign"]["brief"])
        rules = json.loads(first["campaign"]["task_rules"])
        self.assertEqual(3, rules["max_accounts"])
        self.assertEqual(["sữa"], rules["forbidden_words"])
        self.assertTrue(rules["like_required"])
        self.assertEqual(1, first["target_count"])
        self.assertEqual(9, len(first["slots"]))
        self.assertEqual(
            {1, 2, 3},
            {row["account_slot"] for row in first["slots"]},
        )
        target = self.conn.execute(
            "SELECT url FROM seeding_target WHERE campaign_id=?",
            (first["campaign"]["id"],),
        ).fetchone()
        self.assertIsNotNone(target)
        self.assertEqual(
            "https://www.facebook.com/groups/467062964514242/permalink/1737872480766611/",
            target["url"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
