import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from acp.core import auto_post_plans, db, pipeline


class AutoPostSchedulerReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "scheduler-reconcile.db")
        db.init_db()
        self.conn = db.connect()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.now = now
        stamp = now.isoformat(timespec="seconds")
        slot = (now + timedelta(hours=6)).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO campaign (id,code,name,is_active,created_at) VALUES ('camp','c','Campaign',1,?)",
            (stamp,),
        )
        self.conn.execute(
            """INSERT INTO channel
               (id,code,platform,handle,status,enabled,niches,auto_schedule_enabled,posting_timezone,created_at)
               VALUES ('ch','threads','threads','@acc','ACTIVE',1,'[]',1,'Asia/Bangkok',?)""",
            (stamp,),
        )
        self.conn.execute(
            """INSERT INTO product (
                 id,source,merchant,external_product_id,name,description,current_price,
                 commission_value,category_code,sold_count,product_url,is_available,last_seen_at,
                 created_at,updated_at,provider,shop_name,main_image_url,image_path_local,
                 affiliate_url,affiliate_link_status,last_synced_at)
               VALUES ('p','manual_shopee','shopee.vn','1','Quần nữ','',118700,20000,
                 'thoi-trang',0,'https://shopee.vn/product/1/1',1,?,?,?,
                 'SHOPEE_AFFILIATE','Shop','https://cdn.example/p.jpg','/tmp/p.jpg',
                 'https://s.shopee.vn/p','READY',?)""",
            (stamp, stamp, stamp, stamp),
        )
        self.conn.execute(
            """INSERT INTO post (
                 id,product_id,channel_id,campaign_id,variant_code,caption_body,
                 disclosure_text,caption_final,image_url_composited,affiliate_link,
                 post_type,status,scheduled_at,created_at,updated_at)
               VALUES ('post','p','ch','camp','H1','caption','d','caption',
                 'https://cdn.example/p.jpg','https://s.shopee.vn/p','SALES','SCHEDULED',?,?,?)""",
            (slot, stamp, stamp),
        )
        self.conn.execute(
            """INSERT INTO publish_target
               (id,post_id,channel_id,status,scheduled_at,auto_scheduled,created_at,updated_at)
               VALUES ('target','post','ch','SCHEDULED',?,1,?,?)""",
            (slot, stamp, stamp),
        )
        self.plan = auto_post_plans.upsert_from_target(self.conn, 'post', 'target')

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def test_existing_auto_schedule_pass_reconciles_live_48h_plans(self):
        with mock.patch.object(
            auto_post_plans,
            'reconcile_plan',
            return_value={'ok': True, 'action': 'kept'},
        ) as reconcile:
            stats = pipeline.fill_auto_schedule(
                self.conn,
                'missing-campaign-code',
                now_utc=self.now,
                ctx={},
            )
        reconcile.assert_called_once_with(self.conn, self.plan['id'])
        self.assertEqual(stats['reconciled'], 1)
        self.assertEqual(stats['reconcile_deferred'], 0)


if __name__ == '__main__':
    unittest.main()
