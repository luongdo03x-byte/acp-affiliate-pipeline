"""Focused tests for the Facebook Seeding Assistant.

Run from the parent directory that contains the ``acp`` package:
    ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= python3 -m acp.tests.test_seeding
"""
from __future__ import annotations

import os
import tempfile
import unittest

_tmp = tempfile.mkdtemp(prefix="acp-seeding-test-")
os.environ["ACP_DB"] = os.path.join(_tmp, "seeding.db")

from acp.core import db, system_settings  # noqa: E402

db.DB_PATH = os.environ["ACP_DB"]


class SeedingSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        db.init_db()
        self.conn = db.connect()

    def tearDown(self) -> None:
        self.conn.close()

    def test_seeding_tables_exist(self) -> None:
        names = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertTrue(
            {
                "seeding_campaign",
                "seeding_template",
                "seeding_target",
                "seeding_shift",
                "seeding_activity",
            }.issubset(names)
        )

    def test_global_pause_defaults_false_and_is_audited(self) -> None:
        self.assertFalse(system_settings.seeding_global_paused(self.conn))
        system_settings.set_seeding_global_paused(self.conn, True, actor="test")
        self.assertTrue(system_settings.seeding_global_paused(self.conn))
        row = self.conn.execute(
            "SELECT action, actor FROM audit_log "
            "WHERE entity='system_setting' AND entity_id=? "
            "ORDER BY id DESC LIMIT 1",
            (system_settings.SEEDING_GLOBAL_PAUSED,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(("set", "test"), (row["action"], row["actor"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
