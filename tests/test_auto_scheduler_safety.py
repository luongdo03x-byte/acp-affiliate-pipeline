import sqlite3
import unittest

from acp.core.auto_scheduler import live_slot_occupied


class AutoSchedulerSlotSafetyTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE publish_target (
                id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                status TEXT NOT NULL,
                scheduled_at TEXT
            )
            """
        )

    def tearDown(self):
        self.conn.close()

    def test_missing_slot_is_not_safe_for_auto_approval(self):
        self.assertTrue(live_slot_occupied(self.conn, "channel-1", None))

    def test_invalid_slot_is_not_safe_for_auto_approval(self):
        self.assertTrue(live_slot_occupied(self.conn, "channel-1", "not-an-iso-time"))

    def test_valid_empty_slot_is_available(self):
        self.assertFalse(
            live_slot_occupied(
                self.conn,
                "channel-1",
                "2026-08-20T09:30:00+07:00",
            )
        )


if __name__ == "__main__":
    unittest.main()
