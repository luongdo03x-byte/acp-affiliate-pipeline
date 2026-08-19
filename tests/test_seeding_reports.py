from __future__ import annotations

import sqlite3
import unittest

from acp.core import seeding_accounts, seeding_execution, seeding_reports


class SeedingReportTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE seeding_campaign (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                brief TEXT NOT NULL DEFAULT '',
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
            INSERT INTO seeding_campaign(id,name,task_rules)
            VALUES ('TASK1','A2GR-64','{"like_required":true,"max_accounts":3,"main_comments_per_account":1,"replies_per_account":2}');
            INSERT INTO seeding_target(id,campaign_id,url)
            VALUES ('T1','TASK1','https://www.facebook.com/groups/demo/permalink/123/?rdid=abc');
            """
        )
        for slot in (1, 2):
            self.conn.execute(
                "INSERT INTO seeding_comment_slot VALUES (?,?,?,?,?,?,?,?,'DONE','2026','2026')",
                (f"M{slot}", "TASK1", "T1", slot, "MAIN", 1, f"Main {slot}", f"Main final {slot}"),
            )
            for rep in (1, 2):
                self.conn.execute(
                    "INSERT INTO seeding_comment_slot VALUES (?,?,?,?,?,?,?,?,'DONE','2026','2026')",
                    (f"R{slot}{rep}", "TASK1", "T1", slot, "REPLY", rep, f"Reply {slot}.{rep}", f"Reply final {slot}.{rep}"),
                )
        # Unassigned slot must not block completion or appear in report.
        self.conn.execute(
            "INSERT INTO seeding_comment_slot VALUES ('M3','TASK1','T1',3,'MAIN',1,NULL,NULL,'EMPTY','2026','2026')"
        )
        seeding_accounts.ensure_account_schema(self.conn)
        seeding_execution.ensure_execution_schema(self.conn)
        accounts = [
            seeding_accounts.register_account(self.conn, instance_id=f"p{i}", label=f"FB0{i}")
            for i in (1, 2)
        ]
        seeding_accounts.assign_task_accounts(self.conn, "TASK1", [a["id"] for a in accounts])
        seeding_execution.record_like(self.conn, "p1", "TASK1", done=True)
        seeding_execution.record_like(self.conn, "p2", "TASK1", done=True)
        seeding_reports.ensure_report_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_completion_ignores_unassigned_slots(self):
        state = seeding_reports.task_completion(self.conn, "TASK1")
        self.assertTrue(state["complete"])
        self.assertEqual(2, state["account_count"])
        self.assertEqual(6, state["comment_total"])
        self.assertEqual(6, state["comment_done"])
        self.assertEqual(2, state["like_done"])

    def test_sheet_rows_match_b_c_d_block(self):
        rows = seeding_reports.build_sheet_rows(self.conn, "TASK1")
        self.assertEqual(
            [
                ["A2GR-64", "Main final 1", "Reply final 1.1"],
                ["https://www.facebook.com/groups/demo/permalink/123/?rdid=abc", "Main final 2", "Reply final 1.2"],
                ["", "", "Reply final 2.1"],
                ["", "", "Reply final 2.2"],
            ],
            rows,
        )

    def test_push_is_idempotent_after_success(self):
        calls = []
        def sender(url, payload):
            calls.append((url, payload))
            return {"ok": True, "sheet_ref": "sheet:123"}

        first = seeding_reports.push_to_sheet(
            self.conn,
            "TASK1",
            webhook_url="https://script.google.com/macros/s/test/exec",
            secret="secret",
            sender=sender,
        )
        second = seeding_reports.push_to_sheet(
            self.conn,
            "TASK1",
            webhook_url="https://script.google.com/macros/s/test/exec",
            secret="secret",
            sender=sender,
        )
        self.assertEqual("PUSHED", first["status"])
        self.assertEqual("PUSHED", second["status"])
        self.assertEqual(1, len(calls))
        self.assertEqual("A2GR-64", calls[0][1]["task_name"])
        self.assertEqual(4, len(calls[0][1]["rows"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
