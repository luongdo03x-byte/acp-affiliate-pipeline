"""Recovery contract for browser reload/lost extension assignment."""
from __future__ import annotations

import os
import tempfile
import unittest

_tmp = tempfile.mkdtemp(prefix="acp-seeding-recovery-")
os.environ["ACP_DB"] = os.path.join(_tmp, "recovery.db")

from acp.core import db, seeding  # noqa: E402

db.DB_PATH = os.environ["ACP_DB"]


class SeedingRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        db.DB_PATH = os.path.join(_tmp, f"{self._testMethodName}.db")
        if os.path.exists(db.DB_PATH):
            os.unlink(db.DB_PATH)
        db.init_db()
        self.conn = db.connect()
        self.campaign = seeding.create_campaign(
            self.conn,
            name="Recovery",
            brand="Brand",
            brief="brief",
            allowed_claims=[],
            prohibited_topics=[],
            disclosure_policy="promotional",
        )
        seeding.import_targets(
            self.conn,
            self.campaign["id"],
            [
                "https://www.facebook.com/groups/demo/posts/1/",
                "https://www.facebook.com/groups/demo/posts/2/",
            ],
        )
        self.shift = seeding.start_shift(self.conn, self.campaign["id"])

    def tearDown(self) -> None:
        self.conn.close()

    def test_next_target_returns_existing_inflight_before_advancing_queue(self) -> None:
        first = seeding.next_target(self.conn, self.shift["id"])
        self.assertEqual("OPENING", first["status"])
        recovered = seeding.next_target(self.conn, self.shift["id"])
        self.assertEqual(first["id"], recovered["id"])
        second_status = self.conn.execute(
            "SELECT status FROM seeding_target WHERE campaign_id=? AND position=1",
            (self.campaign["id"],),
        ).fetchone()[0]
        self.assertEqual("READY", second_status)


if __name__ == "__main__":
    unittest.main(verbosity=2)
