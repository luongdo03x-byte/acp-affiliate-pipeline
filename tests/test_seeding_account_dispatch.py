"""Account-aware work dispatch contracts."""
from __future__ import annotations

import os
import tempfile
import unittest

_tmp = tempfile.mkdtemp(prefix="acp-seed-dispatch-")
os.environ["ACP_DB"] = os.path.join(_tmp, "dispatch.db")

from acp.core import db, seeding_accounts, seeding_tasks  # noqa: E402

INSTRUCTION = "LIKE BÀI; mỗi acc 3 CMT (1 cmt chính + 2 reply); tối đa 3 acc"
URL = "https://www.facebook.com/groups/demo/permalink/123/"


class AccountDispatchTests(unittest.TestCase):
    def setUp(self):
        db.DB_PATH = os.path.join(_tmp, f"{self._testMethodName}.db")
        if os.path.exists(db.DB_PATH):
            os.unlink(db.DB_PATH)
        db.init_db()
        self.conn = db.connect()
        task = seeding_tasks.create_task(
            self.conn, name="A2GR-64", instruction=INSTRUCTION, post_url=URL
        )
        self.campaign_id = task["campaign"]["id"]
        self.target_id = task["target_id"]
        self.fb1 = seeding_accounts.register_account(
            self.conn, instance_id="profile-1", label="FB01"
        )
        self.fb2 = seeding_accounts.register_account(
            self.conn, instance_id="profile-2", label="FB02"
        )
        seeding_accounts.assign_task_accounts(
            self.conn, self.campaign_id, [self.fb1["id"], self.fb2["id"]]
        )

    def tearDown(self):
        self.conn.close()

    def _mark_generated(self):
        for slot in (1, 2):
            for kind, idx, text in (
                ("MAIN", 1, f"main-{slot}"),
                ("REPLY", 1, f"reply-{slot}-1"),
                ("REPLY", 2, f"reply-{slot}-2"),
            ):
                self.conn.execute(
                    """UPDATE seeding_comment_slot
                       SET generated_text=?, status='GENERATED'
                       WHERE campaign_id=? AND account_slot=? AND comment_type=? AND item_index=?""",
                    (text, self.campaign_id, slot, kind, idx),
                )

    def test_resolve_instance_account_is_stable(self):
        account = seeding_accounts.resolve_instance_account(self.conn, "profile-1")
        self.assertEqual(self.fb1["id"], account["id"])
        with self.assertRaises(ValueError):
            seeding_accounts.resolve_instance_account(self.conn, "missing-profile")

    def test_next_work_starts_with_required_like_for_that_profile(self):
        work = seeding_accounts.next_account_work(
            self.conn, instance_id="profile-1", campaign_id=self.campaign_id
        )
        self.assertEqual("LIKE", work["work_type"])
        self.assertEqual(1, work["account_slot"])
        self.assertEqual("FB01", work["account_label"])
        self.assertEqual(URL, work["target_url"])

    def test_after_like_each_profile_only_receives_its_own_generated_slots(self):
        self._mark_generated()
        seeding_accounts.record_account_like_result(
            self.conn,
            campaign_id=self.campaign_id,
            instance_id="profile-1",
            result="DONE",
        )
        seeding_accounts.record_account_like_result(
            self.conn,
            campaign_id=self.campaign_id,
            instance_id="profile-2",
            result="DONE",
        )
        one = seeding_accounts.next_account_work(
            self.conn, instance_id="profile-1", campaign_id=self.campaign_id
        )
        two = seeding_accounts.next_account_work(
            self.conn, instance_id="profile-2", campaign_id=self.campaign_id
        )
        self.assertEqual("COMMENT", one["work_type"])
        self.assertEqual(1, one["account_slot"])
        self.assertEqual("main-1", one["text"])
        self.assertEqual(2, two["account_slot"])
        self.assertEqual("main-2", two["text"])

    def test_unmapped_profile_has_no_work_and_unused_slot_is_not_dispatched(self):
        other = seeding_accounts.register_account(
            self.conn, instance_id="profile-3", label="FB03"
        )
        self.assertIsNone(
            seeding_accounts.next_account_work(
                self.conn, instance_id=other["extension_instance_id"], campaign_id=self.campaign_id
            )
        )
        self.conn.execute(
            """UPDATE seeding_comment_slot SET generated_text='unused', status='GENERATED'
               WHERE campaign_id=? AND account_slot=3""",
            (self.campaign_id,),
        )
        self.assertIsNone(
            seeding_accounts.next_account_work(
                self.conn, instance_id="profile-3", campaign_id=self.campaign_id
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
