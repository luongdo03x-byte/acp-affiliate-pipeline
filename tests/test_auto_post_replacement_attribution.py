import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from acp.core import auto_post_plans, db, pipeline


class AutoPostReplacementAttributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "replacement.db")
        db.init_db()
        self.conn = db.connect()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        stamp = now.isoformat(timespec="seconds")
        slot = (now + timedelta(hours=3)).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO campaign (id,code,name,is_active,created_at) VALUES ('camp','c','Campaign',1,?)",
            (stamp,),
        )
        self.conn.execute(
            "INSERT INTO caption_template (id,code,name,body,is_active) VALUES ('tpl','review','Review','x',1)"
        )
        self.conn.execute(
            """INSERT INTO channel
               (id,code,platform,handle,status,enabled,niches,auto_schedule_enabled,posting_timezone,created_at)
               VALUES ('ch','threads','threads','@acc','ACTIVE',1,'[]',1,'Asia/Bangkok',?)""",
            (stamp,),
        )
        for product_id, external_id, name in (
            ('p1', '1', 'Sản phẩm cũ'),
            ('p2', '2', 'Sản phẩm mới'),
        ):
            self.conn.execute(
                """INSERT INTO product (
                     id,source,merchant,external_product_id,name,description,current_price,
                     commission_value,category_code,sold_count,product_url,is_available,last_seen_at,
                     created_at,updated_at,provider,shop_name,main_image_url,image_path_local,
                     affiliate_url,affiliate_link_status,last_synced_at,score)
                   VALUES (?,?,?,?,?,'',118700,20000,'khac',0,?,1,?,?,?,
                     'SHOPEE_AFFILIATE','Shop','https://cdn.example/p.jpg','/tmp/p.jpg',
                     ?,'READY',?,80)""",
                (
                    product_id, 'manual_shopee', 'shopee.vn', external_id, name,
                    f'https://shopee.vn/product/1/{external_id}', stamp, stamp, stamp,
                    f'https://s.shopee.vn/{external_id}', stamp,
                ),
            )
        self.conn.execute(
            """INSERT INTO post (
                 id,product_id,channel_id,campaign_id,caption_template_id,variant_code,
                 caption_body,disclosure_text,caption_final,image_url_composited,
                 affiliate_link,sub_id_payload,post_type,status,scheduled_at,created_at,updated_at)
               VALUES ('post','p1','ch','camp','tpl','H1','old','disclosure','old',
                 'https://cdn.example/old.jpg','https://s.shopee.vn/1',?,
                 'SALES','SCHEDULED',?,?,?)""",
            (json.dumps({'post_id': 'post', 'product_id': 'p1'}), slot, stamp, stamp),
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

    def test_replacing_product_keeps_attribution_bound_to_existing_post_id(self):
        prepared = {
            'ok': True,
            'caption': 'new caption',
            'image_url': 'https://cdn.example/new.jpg',
            'affiliate_link': 'https://s.shopee.vn/2',
            'sub_id_payload': json.dumps({'post_id': 'generated-other-id', 'product_id': 'p2'}),
            'score': 0.8,
            'problems': [],
        }
        with mock.patch.object(pipeline, 'current_auto_product_eligibility', return_value=(True, 'ok')), \
             mock.patch.object(pipeline, '_prepare_auto_sales_post_artifacts', return_value=prepared):
            auto_post_plans.replace_product(self.conn, self.plan['id'], 'p2')

        post = self.conn.execute("SELECT product_id,sub_id_payload FROM post WHERE id='post'").fetchone()
        payload = json.loads(post['sub_id_payload'])
        self.assertEqual(post['product_id'], 'p2')
        self.assertEqual(payload['post_id'], 'post')
        self.assertEqual(payload['product_id'], 'p2')


if __name__ == '__main__':
    unittest.main()
