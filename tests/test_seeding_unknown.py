from __future__ import annotations

import sqlite3
import unittest

from acp.core import seeding_accounts, seeding_execution, seeding_reports


class SeedingUnknownTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE seeding_campaign (
              id TEXT PRIMARY KEY,name TEXT NOT NULL,brief TEXT NOT NULL DEFAULT '',
              task_rules TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'ACTIVE',
              created_at TEXT NOT NULL DEFAULT '2026-08-20T00:00:00+00:00'
            );
            CREATE TABLE seeding_target (
              id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,url TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'READY'
            );
            CREATE TABLE seeding_comment_slot (
              id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,target_id TEXT NOT NULL,
              account_slot INTEGER NOT NULL,comment_type TEXT NOT NULL,item_index INTEGER NOT NULL,
              generated_text TEXT,final_text TEXT,status TEXT NOT NULL DEFAULT 'EMPTY',
              created_at TEXT NOT NULL DEFAULT '2026',updated_at TEXT NOT NULL DEFAULT '2026'
            );
            INSERT INTO seeding_campaign(id,name,task_rules)
            VALUES ('TASK1','A2GR-64','{"like_required":false,"max_accounts":1,"main_comments_per_account":1,"replies_per_account":0}');
            INSERT INTO seeding_target(id,campaign_id,url)
            VALUES ('T1','TASK1','https://www.facebook.com/groups/demo/permalink/123/');
            INSERT INTO seeding_comment_slot
              (id,campaign_id,target_id,account_slot,comment_type,item_index,generated_text,status)
            VALUES ('S1','TASK1','T1',1,'MAIN',1,'Nội dung đã điền','GENERATED');
            """
        )
        seeding_accounts.ensure_account_schema(self.conn)
        seeding_execution.ensure_execution_schema(self.conn)
        account = seeding_accounts.register_account(
            self.conn, instance_id="profile-1", label="FB01"
        )
        seeding_accounts.assign_task_accounts(self.conn, "TASK1", [account["id"]])
        seeding_reports.ensure_report_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_unknown_is_terminal_for_dispatch_but_blocks_sheet_completion(self):
        row = seeding_execution.record_comment_result(
            self.conn,
            instance_id="profile-1",
            slot_id="S1",
            result="UNKNOWN",
            final_text="Nội dung đã điền",
            proof_ref="clicked:unverified",
        )
        self.assertEqual("UNKNOWN", row["status"])
        self.assertTrue(seeding_execution.next_account_work(self.conn, "profile-1")["done"])
        state = seeding_reports.task_completion(self.conn, "TASK1")
        self.assertFalse(state["complete"])
        self.assertEqual(0, state["comment_done"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
