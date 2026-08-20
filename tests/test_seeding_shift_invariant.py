"""Cross-campaign shift invariant for the Facebook Seeding Assistant."""
from __future__ import annotations

import os
import tempfile
import unittest

_tmp = tempfile.mkdtemp(prefix="acp-seeding-shift-")
os.environ["ACP_DB"] = os.path.join(_tmp, "shift.db")

from acp.core import db, seeding  # noqa: E402


class SeedingShiftInvariantTests(unittest.TestCase):
    def setUp(self) -> None:
        db.DB_PATH = os.path.join(_tmp, f"{self._testMethodName}.db")
        if os.path.exists(db.DB_PATH):
            os.unlink(db.DB_PATH)
        db.init_db()
        self.conn = db.connect()

    def tearDown(self) -> None:
        self.conn.close()

    def _campaign(self, name: str):
        return seeding.create_campaign(
            self.conn,
            name=name,
            brand=name,
            brief="brief",
            allowed_claims=[],
            prohibited_topics=[],
            disclosure_policy="promotional",
        )

    def test_only_one_active_shift_is_allowed_across_campaigns(self) -> None:
        first = self._campaign("A")
        second = self._campaign("B")
        seeding.start_shift(self.conn, first["id"])
        with self.assertRaises(ValueError):
            seeding.start_shift(self.conn, second["id"])

    def test_paused_shift_can_coexist_but_cannot_resume_over_another_active_shift(self) -> None:
        first = self._campaign("A")
        second = self._campaign("B")
        shift_a = seeding.start_shift(self.conn, first["id"])
        seeding.pause_shift(self.conn, shift_a["id"])
        seeding.start_shift(self.conn, second["id"])
        with self.assertRaises(ValueError):
            seeding.start_shift(self.conn, first["id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
