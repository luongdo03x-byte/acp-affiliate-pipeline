import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from acp.core import auto_post_plans, db


class AutoPostTimeConflictTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "time-conflict.db")
        db.init_db()
        self.conn = db.connect()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        stamp = now.isoformat(timespec="seconds")
        self.slot = (now + timedelta(hours=2)).isoformat(timespec="seconds")
        self.blocked_slot = (now + timedelta(hours=4)).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO campaign (id,code,name,is_active,created_at) VALUES ('camp','c','Campaign',1,?)",
            (stamp,),
        )
        self.conn.execute(
            """INSERT INTO channel
               (id,code,platform,handle,status,enabled,niches,auto_schedule_enabled,created_at)
               VALUES ('ch','threads','threads','@acc','ACTIVE',1,'[]',1,?)""",
            (stamp,),
        )
        self.conn.execute(
            """INSERT INTO product
               (id,source,merchant,external_product_id,name,description,current_price,
                commission_value,category_code,product_url,is_available,created_at,updated_at)
               VALUES ('p','s','m','1','P','',100000,10000,'khac','https://example.com/p',1,?,?)""",
            (stamp, stamp),
        )
        self.conn.execute(
            """INSERT INTO post
               (id,product_id,channel_id,campaign_id,variant_code,caption_body,disclosure_text,
                caption_final,post_type,status,scheduled_at,created_at,updated_at)
               VALUES ('post','p','ch','camp','H1','x','d','x','SALES','SCHEDULED',?,?,?)""",
            (self.slot, stamp, stamp),
        )
        self.conn.execute(
            """INSERT INTO publish_target
               (id,post_id,channel_id,status,scheduled_at,auto_scheduled,created_at,updated_at)
               VALUES ('target','post','ch','SCHEDULED',?,1,?,?)""",
            (self.slot, stamp, stamp),
        )
        self.plan = auto_post_plans.upsert_from_target(self.conn, 'post', 'target')
        self.conn.execute(
            """INSERT INTO post
               (id,product_id,channel_id,campaign_id,variant_code,caption_body,disclosure_text,
                caption_final,post_type,status,scheduled_at,created_at,updated_at)
               VALUES ('manual','p','ch','camp','H2','m','d','m','SALES','SCHEDULED',?,?,?)""",
            (self.blocked_slot, stamp, stamp),
        )
        self.conn.execute(
            """INSERT INTO publish_target
               (id,post_id,channel_id,status,scheduled_at,auto_scheduled,created_at,updated_at)
               VALUES ('manual-target','manual','ch','SCHEDULED',?,0,?,?)""",
            (self.blocked_slot, stamp, stamp),
        )

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def test_move_slot_rejects_other_live_publish_target(self):
        with self.assertRaisesRegex(ValueError, "Slot này đã có bài khác"):
            auto_post_plans.move_slot(self.conn, self.plan['id'], self.blocked_slot)
        target = self.conn.execute("SELECT scheduled_at FROM publish_target WHERE id='target'").fetchone()
        self.assertEqual(target['scheduled_at'], self.slot)


if __name__ == '__main__':
    unittest.main()
