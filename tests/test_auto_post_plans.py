import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from acp.core import db, pipeline
from acp.core import auto_post_plans


class AutoPostPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "plans.db")
        db.init_db()
        self.conn = db.connect()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        stamp = self.now.isoformat(timespec="seconds")
        self.slot = (self.now + timedelta(hours=3)).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO campaign (id,code,name,is_active,created_at) VALUES ('camp','c','Campaign',1,?)",
            (stamp,),
        )
        self.conn.execute(
            "INSERT INTO channel (id,code,platform,handle,status,enabled,niches,auto_schedule_enabled,created_at) VALUES ('ch','threads','threads','@acc','ACTIVE',1,'[]',1,?)",
            (stamp,),
        )
        self.conn.execute(
            """INSERT INTO product (
                 id,source,merchant,external_product_id,name,description,current_price,
                 commission_value,category_code,sold_count,product_url,is_available,last_seen_at,
                 created_at,updated_at,provider,shop_name,main_image_url,image_path_local,
                 affiliate_url,affiliate_link_status,last_synced_at)
               VALUES ('p','manual_shopee','shopee.vn','1','Quần ống rộng nữ','',118700,
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
               VALUES ('post','p','ch','camp','H1','caption','disclosure','caption',
                 'https://cdn.example/p.jpg','https://s.shopee.vn/p','SALES','SCHEDULED',?,?,?)""",
            (self.slot, stamp, stamp),
        )
        self.conn.execute(
            """INSERT INTO publish_target (
                 id,post_id,channel_id,status,scheduled_at,auto_scheduled,created_at,updated_at)
               VALUES ('target','post','ch','SCHEDULED',?,1,?,?)""",
            (self.slot, stamp, stamp),
        )
        self.conn.execute(
            """INSERT INTO job_queue (job_type,payload,status,priority,run_after,idempotency_key,created_at,updated_at)
               VALUES ('PUBLISH_POST',?,'READY',50,?,'pub:target',?,?)""",
            (json.dumps({'publish_target_id':'target','post_id':'post','channel_id':'ch'}), self.slot, stamp, stamp),
        )

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def _mark_image_ready(self):
        stamp = self.now.isoformat(timespec='seconds')
        self.conn.execute(
            """INSERT INTO shopee_image_enrichment_job
               (product_id,status,created_at,updated_at)
               VALUES ('p','READY',?,?)
               ON CONFLICT(product_id) DO UPDATE SET status='READY', updated_at=excluded.updated_at""",
            (stamp, stamp),
        )

    def test_upsert_from_target_and_list_48h(self):
        plan = auto_post_plans.upsert_from_target(self.conn, 'post', 'target')
        self.assertEqual(plan['state'], 'READY')
        rows = auto_post_plans.list_window(self.conn, self.now, hours=48)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['channel_handle'], '@acc')
        self.assertEqual(rows[0]['caption_final'], 'caption')
        self.assertEqual(rows[0]['product_name'], 'Quần ống rộng nữ')

    def test_edit_caption_increments_revision(self):
        plan = auto_post_plans.upsert_from_target(self.conn, 'post', 'target')
        updated = auto_post_plans.edit_caption(self.conn, plan['id'], 'caption mới', actor='operator')
        self.assertEqual(updated['content_revision'], 2)
        post = self.conn.execute("SELECT caption_final FROM post WHERE id='post'").fetchone()
        self.assertEqual(post['caption_final'], 'caption mới')

    def test_move_slot_updates_target_post_and_ready_publish_job(self):
        plan = auto_post_plans.upsert_from_target(self.conn, 'post', 'target')
        new_slot = (self.now + timedelta(hours=5)).isoformat(timespec='seconds')
        auto_post_plans.move_slot(self.conn, plan['id'], new_slot, actor='operator')
        target = self.conn.execute("SELECT scheduled_at FROM publish_target WHERE id='target'").fetchone()
        job = self.conn.execute("SELECT run_after FROM job_queue WHERE idempotency_key='pub:target'").fetchone()
        self.assertEqual(target['scheduled_at'], new_slot)
        self.assertEqual(job['run_after'], new_slot)

    def test_cancel_marks_target_and_neutralizes_ready_publish_job(self):
        plan = auto_post_plans.upsert_from_target(self.conn, 'post', 'target')
        auto_post_plans.cancel_plan(self.conn, plan['id'], actor='operator')
        target = self.conn.execute("SELECT status FROM publish_target WHERE id='target'").fetchone()
        job = self.conn.execute("SELECT status FROM job_queue WHERE idempotency_key='pub:target'").fetchone()
        saved = self.conn.execute("SELECT state FROM auto_post_plan WHERE id=?", (plan['id'],)).fetchone()
        self.assertEqual(target['status'], 'CANCELLED')
        self.assertEqual(job['status'], 'DONE')
        self.assertEqual(saved['state'], 'CANCELLED')

    def test_reconcile_price_change_updates_caption_price_without_regenerating_product(self):
        self._mark_image_ready()
        self.conn.execute("UPDATE post SET caption_body='giá 118,7k', caption_final='giá 118,7k' WHERE id='post'")
        plan = auto_post_plans.upsert_from_target(self.conn, 'post', 'target')
        self.conn.execute("UPDATE product SET current_price=129000 WHERE id='p'")
        result = auto_post_plans.reconcile_plan(self.conn, plan['id'])
        saved = self.conn.execute("SELECT caption_final, product_id FROM post WHERE id='post'").fetchone()
        self.assertTrue(result['ok'])
        self.assertEqual(saved['product_id'], 'p')
        self.assertIn('129', saved['caption_final'])
        self.assertNotIn('118,7k', saved['caption_final'])

    def test_reconcile_image_change_refreshes_composited_image_only(self):
        self._mark_image_ready()
        plan = auto_post_plans.upsert_from_target(self.conn, 'post', 'target')
        self.conn.execute("UPDATE product SET main_image_url='https://cdn.example/new-source.jpg' WHERE id='p'")

        storage = mock.Mock()
        storage.put.return_value = 'https://cdn.example/new-composite.jpg'
        with mock.patch.object(pipeline.imaging, 'compose', return_value='/tmp/new-composite.jpg') as compose, \
             mock.patch.object(pipeline.storage, 'get_storage', return_value=storage), \
             mock.patch.object(pipeline.content, 'generate', side_effect=AssertionError('caption must not regenerate')):
            result = auto_post_plans.reconcile_plan(self.conn, plan['id'])

        saved = self.conn.execute("SELECT caption_final,image_url_composited FROM post WHERE id='post'").fetchone()
        self.assertTrue(result['ok'])
        self.assertEqual(saved['caption_final'], 'caption')
        self.assertEqual(saved['image_url_composited'], 'https://cdn.example/new-composite.jpg')
        compose.assert_called_once()

    def test_reconcile_invalid_caption_regenerates_caption_for_same_product(self):
        self._mark_image_ready()
        plan = auto_post_plans.upsert_from_target(self.conn, 'post', 'target')

        def validate(text, **kwargs):
            return ['new rule'] if text == 'caption' else []

        with mock.patch.object(pipeline.content, 'validate', side_effect=validate), \
             mock.patch.object(pipeline.content, 'generate', return_value='caption mới\nhttps://s.shopee.vn/p'):
            result = auto_post_plans.reconcile_plan(self.conn, plan['id'])

        saved = self.conn.execute("SELECT caption_final,product_id FROM post WHERE id='post'").fetchone()
        updated_plan = self.conn.execute("SELECT content_revision,last_change_reason FROM auto_post_plan WHERE id=?", (plan['id'],)).fetchone()
        self.assertTrue(result['ok'])
        self.assertEqual(saved['product_id'], 'p')
        self.assertEqual(saved['caption_final'], 'caption mới\nhttps://s.shopee.vn/p')
        self.assertEqual(updated_plan['last_change_reason'], 'caption_regenerated')
        self.assertGreater(updated_plan['content_revision'], plan['content_revision'])

    def test_auto_approve_creates_plan_via_runtime_wrapper(self):
        # A second draft post exercises the installed pipeline.approve_post wrapper.
        stamp = self.now.isoformat(timespec='seconds')
        self.conn.execute(
            """INSERT INTO post (
                 id,product_id,channel_id,campaign_id,variant_code,caption_body,
                 disclosure_text,caption_final,image_url_composited,affiliate_link,
                 post_type,status,created_at,updated_at)
               VALUES ('post2','p','ch','camp','H1','x','disclosure','x',
                 'https://cdn.example/p.jpg','https://s.shopee.vn/p','SALES','PENDING_REVIEW',?,?)""",
            (stamp, stamp),
        )
        # Keep validation seam deterministic for this lifecycle test.
        original = pipeline.content.validate
        pipeline.content.validate = lambda *args, **kwargs: []
        try:
            result = pipeline.approve_post(
                self.conn, 'post2', actor='auto_scheduler', scheduled_at=self.slot, auto_scheduled=True
            )
        finally:
            pipeline.content.validate = original
        self.assertTrue(result['ok'])
        target_id = result['publish_target_id']
        plan = self.conn.execute(
            "SELECT * FROM auto_post_plan WHERE publish_target_id=?", (target_id,)
        ).fetchone()
        self.assertIsNotNone(plan)
        self.assertEqual(plan['state'], 'READY')


if __name__ == '__main__':
    unittest.main()
