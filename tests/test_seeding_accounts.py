from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from acp.core import seeding_accounts


class SeedingAccountTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(
            """
            CREATE TABLE seeding_campaign (
                id TEXT PRIMARY KEY,
                task_rules TEXT NOT NULL DEFAULT '{}'
            );
            INSERT INTO seeding_campaign(id,task_rules)
            VALUES ('TASK1', '{"max_accounts": 3}');
            """
        )

    def tearDown(self):
        self.conn.close()

    def test_register_is_idempotent_by_extension_instance(self):
        seeding_accounts.ensure_account_schema(self.conn)
        first = seeding_accounts.register_account(
            self.conn, instance_id="profile-1", label="FB01"
        )
        second = seeding_accounts.register_account(
            self.conn, instance_id="profile-1", label="Facebook Chính"
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual("Facebook Chính", second["label"])
        self.assertEqual(
            1,
            self.conn.execute("SELECT COUNT(*) FROM seeding_account").fetchone()[0],
        )

    def test_online_status_comes_from_recent_heartbeat(self):
        seeding_accounts.ensure_account_schema(self.conn)
        account = seeding_accounts.register_account(
            self.conn, instance_id="p1", label="FB01"
        )
        reference = datetime(2026, 8, 19, 16, 0, tzinfo=timezone.utc)
        self.conn.execute(
            "UPDATE seeding_account SET last_seen_at=? WHERE id=?",
            (
                (reference - timedelta(seconds=30)).isoformat(timespec="seconds"),
                account["id"],
            ),
        )
        row = seeding_accounts.list_accounts(
            self.conn, reference_time=reference
        )[0]
        self.assertTrue(row["online"])
        self.conn.execute(
            "UPDATE seeding_account SET last_seen_at=? WHERE id=?",
            (
                (reference - timedelta(minutes=5)).isoformat(timespec="seconds"),
                account["id"],
            ),
        )
        row = seeding_accounts.list_accounts(
            self.conn, reference_time=reference
        )[0]
        self.assertFalse(row["online"])

    def test_assign_maps_selected_accounts_to_slots_in_order(self):
        seeding_accounts.ensure_account_schema(self.conn)
        accounts = [
            seeding_accounts.register_account(
                self.conn, instance_id=f"p{i}", label=f"FB0{i}"
            )
            for i in range(1, 4)
        ]
        mapped = seeding_accounts.assign_task_accounts(
            self.conn, "TASK1", [row["id"] for row in accounts]
        )
        self.assertEqual([1, 2, 3], [row["account_slot"] for row in mapped])
        self.assertEqual(
            ["FB01", "FB02", "FB03"], [row["label"] for row in mapped]
        )

    def test_assign_rejects_more_than_task_max_or_duplicates(self):
        seeding_accounts.ensure_account_schema(self.conn)
        accounts = [
            seeding_accounts.register_account(
                self.conn, instance_id=f"p{i}", label=f"FB0{i}"
            )
            for i in range(1, 5)
        ]
        with self.assertRaises(ValueError):
            seeding_accounts.assign_task_accounts(
                self.conn, "TASK1", [row["id"] for row in accounts]
            )
        with self.assertRaises(ValueError):
            seeding_accounts.assign_task_accounts(
                self.conn, "TASK1", [accounts[0]["id"], accounts[0]["id"]]
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
