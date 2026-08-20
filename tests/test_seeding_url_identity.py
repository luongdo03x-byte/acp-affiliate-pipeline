"""URL identity contract for explicit Facebook post targets."""
from __future__ import annotations

import os
import tempfile
import unittest

_tmp = tempfile.mkdtemp(prefix="acp-seeding-url-")
os.environ["ACP_DB"] = os.path.join(_tmp, "url.db")

from acp.core import db, seeding  # noqa: E402


class SeedingUrlIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        db.DB_PATH = os.path.join(_tmp, f"{self._testMethodName}.db")
        if os.path.exists(db.DB_PATH):
            os.unlink(db.DB_PATH)
        db.init_db()
        self.conn = db.connect()
        self.campaign = seeding.create_campaign(
            self.conn,
            name="URL identity",
            brand="Brand",
            brief="brief",
            allowed_claims=[],
            prohibited_topics=[],
            disclosure_policy="promotional",
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_post_tracking_query_is_removed_before_unique_check(self) -> None:
        result = seeding.import_targets(
            self.conn,
            self.campaign["id"],
            [
                "https://www.facebook.com/groups/demo/posts/123/",
                "https://www.facebook.com/groups/demo/posts/123/?__cft__=volatile",
            ],
        )
        self.assertEqual({"created": 1, "duplicates": 1, "invalid": 0}, result)
        url = self.conn.execute(
            "SELECT url FROM seeding_target WHERE campaign_id=?",
            (self.campaign["id"],),
        ).fetchone()[0]
        self.assertEqual("https://www.facebook.com/groups/demo/posts/123/", url)


if __name__ == "__main__":
    unittest.main(verbosity=2)
