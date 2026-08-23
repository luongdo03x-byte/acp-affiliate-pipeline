import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

from acp.core import db


class AutoPostPlanSchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "plan-schema.db")
        db.init_db()
        self.conn = db.connect()
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            """INSERT INTO channel (id,code,platform,handle,status,enabled,niches,created_at)
               VALUES ('ch','threads','threads','@acc','ACTIVE',1,'[]',?)""",
            (stamp,),
        )
        self.stamp = stamp

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def _insert(self, plan_id: str, state: str):
        self.conn.execute(
            """INSERT INTO auto_post_plan
               (id,channel_id,scheduled_at,state,content_revision,generated_at,
                replacement_count,created_at,updated_at)
               VALUES (?,?,? ,?,1,?,0,?,?)""",
            (plan_id, 'ch', '2030-01-01T10:00:00+00:00', state, self.stamp, self.stamp, self.stamp),
        )

    def test_only_one_live_plan_can_occupy_channel_slot(self):
        self._insert('p1', 'READY')
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert('p2', 'PLANNED')

    def test_terminal_plan_does_not_block_future_reuse_of_same_slot(self):
        self._insert('p1', 'CANCELLED')
        self._insert('p2', 'READY')
        count = self.conn.execute("SELECT COUNT(*) FROM auto_post_plan").fetchone()[0]
        self.assertEqual(count, 2)


if __name__ == '__main__':
    unittest.main()
