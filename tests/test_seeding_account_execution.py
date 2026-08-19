from __future__ import annotations

import json
import sqlite3
import unittest

from acp.core import seeding_accounts, seeding_execution


INSTRUCTION = "LIKE BÀI; mỗi acc 3 CMT (1 cmt chính + 2 reply); tối đa 3 acc; KHÔNG NHẮC SỮA"


class SeedingAccountExecutionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(
            """
            CREATE TABLE seeding_campaign (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                brief TEXT NOT NULL,
                task_rules TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_at TEXT NOT NULL DEFAULT '2026-08-20T00:00:00+00:00'
            );
            CREATE TABLE seeding_target (
                id TEXT PRIMARY KEY,
                campaign_id TEXT NOT NULL,
                url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'READY'
            );
            CREATE TABLE seeding_comment_slot (
                id TEXT PRIMARY KEY,
                campaign_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                account_slot INTEGER NOT NULL,
                comment_type TEXT NOT NULL,
                item_index INTEGER NOT NULL,
                generated_text TEXT,
                final_text TEXT,
                status TEXT NOT NULL DEFAULT 'EMPTY',
                created_at TEXT NOT NULL DEFAULT '2026-08-20T00:00:00+00:00',
                updated_at TEXT NOT NULL DEFAULT '2026-08-20T00:00:00+00:00'
            );
            INSERT INTO seeding_campaign(id,name,brief,task_rules)
            VALUES (
              'TASK1','A2GR-64','LIKE BÀI; 1 main + 2 reply; max 3; KHÔNG NHẮC SỮA',
              '{"like_required": true, "main_comments_per_account": 1, "replies_per_account": 2, "comments_per_account": 3, "max_accounts": 3, "forbidden_words": ["sữa"]}'
            );
            INSERT INTO seeding_target(id,campaign_id,url)
            VALUES ('TARGET1','TASK1','https://www.facebook.com/groups/demo/permalink/123/?rdid=x');
            """
        )
        for account_slot in range(1, 4):
            self.conn.execute(
                "INSERT INTO seeding_comment_slot(id,campaign_id,target_id,account_slot,comment_type,item_index) VALUES (?,?,?,?,?,?)",
                (f"S{account_slot}M", "TASK1", "TARGET1", account_slot, "MAIN", 1),
            )
            for reply in range(1, 3):
                self.conn.execute(
                    "INSERT INTO seeding_comment_slot(id,campaign_id,target_id,account_slot,comment_type,item_index) VALUES (?,?,?,?,?,?)",
                    (f"S{account_slot}R{reply}", "TASK1", "TARGET1", account_slot, "REPLY", reply),
                )
        seeding_accounts.ensure_account_schema(self.conn)
        seeding_execution.ensure_execution_schema(self.conn)
        self.accounts = [
            seeding_accounts.register_account(self.conn, instance_id=f"profile-{i}", label=f"FB0{i}")
            for i in range(1, 4)
        ]
        seeding_accounts.assign_task_accounts(
            self.conn, "TASK1", [self.accounts[0]["id"], self.accounts[1]["id"]]
        )

    def tearDown(self):
        self.conn.close()

    def test_unmapped_profile_gets_no_work_and_mapped_profile_gets_like_first(self):
        idle = seeding_execution.next_account_work(self.conn, "profile-3")
        self.assertTrue(idle["done"])

        work = seeding_execution.next_account_work(self.conn, "profile-1")
        self.assertFalse(work["done"])
        self.assertEqual("LIKE", work["action"])
        self.assertEqual("TASK1", work["campaign_id"])
        self.assertEqual("TARGET1", work["target"]["id"])
        self.assertEqual(1, work["account_slot"])

    def test_prepare_generates_only_selected_account_slots_and_keeps_texts_distinct(self):
        response = {
            "accounts": [
                {
                    "slot": 1,
                    "main_comments": ["Mình nghĩ nên tìm hiểu kỹ thông tin trước khi quyết định."],
                    "replies": ["Ý này khá hợp lý, hỏi thêm chi tiết sẽ dễ cân nhắc hơn.", "Mình cũng ưu tiên xem kỹ điều kiện trước rồi mới chọn."],
                },
                {
                    "slot": 2,
                    "main_comments": ["Có thể tham khảo thêm vài chia sẻ thực tế để có góc nhìn rộng hơn."],
                    "replies": ["Chuẩn, mỗi trường hợp sẽ khác nên xem nhu cầu cụ thể trước.", "Nếu còn phân vân thì hỏi trực tiếp bên hỗ trợ cho chắc nhé."],
                },
            ]
        }
        rows = seeding_execution.prepare_account_task(
            self.conn,
            instance_id="profile-1",
            campaign_id="TASK1",
            target_id="TARGET1",
            post_text="Nội dung bài Facebook cần phản hồi",
            llm_fn=lambda _prompt: json.dumps(response, ensure_ascii=False),
        )
        self.assertEqual(6, len(rows))
        self.assertEqual({1, 2}, {row["account_slot"] for row in rows})
        untouched = self.conn.execute(
            "SELECT COUNT(*) FROM seeding_comment_slot WHERE account_slot=3 AND status='EMPTY'"
        ).fetchone()[0]
        self.assertEqual(3, untouched)
        generated = [row["generated_text"] for row in rows]
        self.assertEqual(6, len(set(generated)))

    def test_like_done_then_returns_only_own_first_comment(self):
        self.conn.execute(
            "UPDATE seeding_comment_slot SET generated_text='Main A', status='GENERATED' WHERE id='S1M'"
        )
        self.conn.execute(
            "UPDATE seeding_comment_slot SET generated_text='Reply A1', status='GENERATED' WHERE id='S1R1'"
        )
        self.conn.execute(
            "UPDATE seeding_comment_slot SET generated_text='Reply A2', status='GENERATED' WHERE id='S1R2'"
        )
        seeding_execution.record_like(self.conn, "profile-1", "TASK1", done=True)
        work = seeding_execution.next_account_work(self.conn, "profile-1")
        self.assertEqual("COMMENT", work["action"])
        self.assertEqual("S1M", work["slot"]["id"])
        self.assertEqual("MAIN", work["slot"]["comment_type"])
        self.assertEqual("Main A", work["slot"]["generated_text"])

    def test_profile_cannot_complete_another_accounts_slot(self):
        self.conn.execute(
            "UPDATE seeding_comment_slot SET generated_text='Main B', status='GENERATED' WHERE id='S2M'"
        )
        with self.assertRaises(ValueError):
            seeding_execution.record_comment_result(
                self.conn,
                instance_id="profile-1",
                slot_id="S2M",
                result="DONE",
                final_text="Main B đã đăng",
                proof_ref="observed:1",
            )

    def test_mapping_cannot_change_after_generation_started(self):
        self.conn.execute(
            "UPDATE seeding_comment_slot SET generated_text='Main A', status='GENERATED' WHERE id='S1M'"
        )
        with self.assertRaises(ValueError):
            seeding_accounts.assign_task_accounts(
                self.conn,
                "TASK1",
                [self.accounts[1]["id"], self.accounts[2]["id"]],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
