import os
import tempfile
import unittest
from datetime import datetime, timezone

from acp.core import db
from acp.core.shopee_image_enrichment import FAILED, PENDING, READY, enqueue_product
from acp.core import shopee_bulk_enrichment


class ShopeeBulkEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "bulk.db")
        db.init_db()
        self.conn = db.connect()
        self.stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def _product(self, pid, image=None):
        self.conn.execute(
            """INSERT INTO product (
                 id, source, merchant, external_product_id, name, description,
                 current_price, commission_value, category_code, sold_count,
                 image_url_original, image_path_local, product_url, is_available,
                 last_seen_at, created_at, updated_at, provider, affiliate_url,
                 affiliate_link_status, last_synced_at)
               VALUES (?,?,?,?,?,'',100000,10000,'khac',0,?,?,?,1,?,?,?,
                       'SHOPEE_AFFILIATE',?,'READY',?)""",
            (pid, 'manual_shopee', 'shopee.vn', pid, f'Product {pid}', image, None,
             f'https://shopee.vn/product/1/{pid}', self.stamp, self.stamp, self.stamp,
             f'https://s.shopee.vn/{pid}', self.stamp),
        )
        enqueue_product(self.conn, pid)

    def test_start_backfills_and_queues_bounded_batch(self):
        for index in range(25):
            self._product(str(100 + index))
        result = shopee_bulk_enrichment.start(self.conn)
        self.assertEqual(result['state'], 'RUNNING')
        queued = self.conn.execute(
            "SELECT COUNT(*) FROM job_queue WHERE job_type='SHOPEE_ENRICH_PRODUCT'"
        ).fetchone()[0]
        self.assertLessEqual(queued, 20)
        self.assertGreater(queued, 0)

    def test_pause_blocks_pump_and_resume_continues(self):
        for index in range(3):
            self._product(str(200 + index))
        shopee_bulk_enrichment.start(self.conn)
        shopee_bulk_enrichment.pause(self.conn)
        before = self.conn.execute("SELECT COUNT(*) FROM job_queue").fetchone()[0]
        pumped = shopee_bulk_enrichment.pump(self.conn)
        after = self.conn.execute("SELECT COUNT(*) FROM job_queue").fetchone()[0]
        self.assertEqual(pumped['queued'], 0)
        self.assertEqual(before, after)
        self.assertEqual(shopee_bulk_enrichment.resume(self.conn)['state'], 'RUNNING')

    def test_status_reports_progress_and_retry_failed_resets(self):
        self._product('301')
        self._product('302')
        self.conn.execute("UPDATE shopee_image_enrichment_job SET status=? WHERE product_id='301'", (READY,))
        self.conn.execute("UPDATE shopee_image_enrichment_job SET status=?, attempt_count=2 WHERE product_id='302'", (FAILED,))
        status = shopee_bulk_enrichment.status(self.conn)
        self.assertEqual(status['ready'], 1)
        self.assertEqual(status['failed'], 1)
        retried = shopee_bulk_enrichment.retry_failed(self.conn)
        self.assertEqual(retried['reset'], 1)
        state = self.conn.execute(
            "SELECT status, attempt_count FROM shopee_image_enrichment_job WHERE product_id='302'"
        ).fetchone()
        self.assertEqual(state['status'], PENDING)
        self.assertEqual(state['attempt_count'], 0)


if __name__ == '__main__':
    unittest.main()
