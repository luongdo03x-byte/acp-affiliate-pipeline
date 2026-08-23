import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from acp.core import auto_post_plans, db


class AutoPostingWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_password = os.environ.get('ACP_ADMIN_PASSWORD')
        cls.old_adapter = os.environ.get('ACP_ADAPTER')
        cls.old_source = os.environ.get('ACP_SOURCE')
        os.environ['ACP_ADMIN_PASSWORD'] = 'test-password'
        os.environ['ACP_ADAPTER'] = 'mock'
        os.environ['ACP_SOURCE'] = 'mock'

    @classmethod
    def tearDownClass(cls):
        for key, value in (
            ('ACP_ADMIN_PASSWORD', cls.old_password),
            ('ACP_ADAPTER', cls.old_adapter),
            ('ACP_SOURCE', cls.old_source),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def setUp(self):
        from acp.web import create_app
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, 'web.db')
        db.init_db()
        self.conn = db.connect()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        stamp = self.now.isoformat(timespec='seconds')
        slot = (self.now + timedelta(hours=2)).isoformat(timespec='seconds')
        self.conn.execute("INSERT INTO campaign (id,code,name,is_active,created_at) VALUES ('camp','c','Campaign',1,?)", (stamp,))
        self.conn.execute(
            """INSERT INTO channel (
                 id,code,platform,handle,status,enabled,niches,auto_schedule_enabled,
                 posting_timezone,created_at)
               VALUES ('ch','threads','threads','@account','ACTIVE',1,'[]',1,'Asia/Bangkok',?)""",
            (stamp,),
        )
        self.conn.execute(
            """INSERT INTO product (
                 id,source,merchant,external_product_id,name,description,current_price,
                 commission_value,category_code,sold_count,product_url,is_available,last_seen_at,
                 created_at,updated_at,provider,shop_name,main_image_url,image_path_local,
                 affiliate_url,affiliate_link_status,last_synced_at)
               VALUES ('p','manual_shopee','shopee.vn','1','Quần nữ ống rộng','',118700,
                 20000,'thoi-trang',0,'https://shopee.vn/product/1/1',1,?,?,?,
                 'SHOPEE_AFFILIATE','Shop','https://cdn.example/p.jpg','/tmp/p.jpg',
                 'https://s.shopee.vn/p','READY',?)""",
            (stamp, stamp, stamp, stamp),
        )
        self.conn.execute(
            """INSERT INTO post (
                 id,product_id,channel_id,campaign_id,variant_code,caption_body,
                 disclosure_text,caption_final,image_url_composited,affiliate_link,
                 post_type,status,scheduled_at,created_at,updated_at)
               VALUES ('post','p','ch','camp','H1','caption','disclosure',
                 'caption\nhttps://s.shopee.vn/p','https://cdn.example/p.jpg',
                 'https://s.shopee.vn/p','SALES','SCHEDULED',?,?,?)""",
            (slot, stamp, stamp),
        )
        self.conn.execute(
            """INSERT INTO publish_target (
                 id,post_id,channel_id,status,scheduled_at,auto_scheduled,created_at,updated_at)
               VALUES ('target','post','ch','SCHEDULED',?,1,?,?)""",
            (slot, stamp, stamp),
        )
        self.conn.execute(
            """INSERT INTO job_queue (
                 job_type,payload,status,priority,run_after,idempotency_key,created_at,updated_at)
               VALUES ('PUBLISH_POST',?,'READY',50,?,'pub:target',?,?)""",
            (json.dumps({'publish_target_id':'target','post_id':'post','channel_id':'ch'}), slot, stamp, stamp),
        )
        self.plan = auto_post_plans.upsert_from_target(self.conn, 'post', 'target')
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['uid'] = 'operator'
            session['csrf'] = 'csrf-test'
        self.csrf = 'csrf-test'

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def test_page_shows_account_product_caption_image_and_actions(self):
        response = self.client.get('/auto-posting')
        self.assertEqual(response.status_code, 200)
        body = response.data.decode('utf-8')
        self.assertIn('@account', body)
        self.assertIn('Quần nữ ống rộng', body)
        self.assertIn('caption', body)
        self.assertIn('https://cdn.example/p.jpg', body)
        self.assertIn('Sửa caption', body)
        self.assertIn('Đổi giờ', body)
        self.assertIn('Hủy', body)

    def test_caption_action_updates_plan_without_publishing(self):
        response = self.client.post(
            f"/auto-posting/{self.plan['id']}/caption",
            data={'_csrf': self.csrf, 'caption': 'caption mới\nhttps://s.shopee.vn/p'},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        saved = self.conn.execute("SELECT caption_final,status FROM post WHERE id='post'").fetchone()
        self.assertEqual(saved['caption_final'], 'caption mới\nhttps://s.shopee.vn/p')
        self.assertEqual(saved['status'], 'SCHEDULED')

    def test_cancel_action_does_not_call_publisher(self):
        response = self.client.post(
            f"/auto-posting/{self.plan['id']}/cancel",
            data={'_csrf': self.csrf},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        target = self.conn.execute("SELECT status FROM publish_target WHERE id='target'").fetchone()
        self.assertEqual(target['status'], 'CANCELLED')


if __name__ == '__main__':
    unittest.main()
