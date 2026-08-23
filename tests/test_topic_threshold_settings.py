import os
import tempfile
import unittest
from datetime import datetime, timezone

from acp.core import db, topic_engine


class TopicThresholdSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "topic-thresholds.db")
        db.init_db()
        self.conn = db.connect()
        topic_engine.ensure_system_topics(self.conn)

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def _insert_product(self, product_id: str, name: str):
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            """INSERT INTO product (
                 id,source,merchant,external_product_id,name,description,current_price,
                 commission_value,category_code,sold_count,product_url,is_available,last_seen_at,
                 created_at,updated_at,provider,shop_name,affiliate_url,affiliate_link_status,last_synced_at)
               VALUES (?,?,?,?,?,'',100000,10000,'thoi-trang',0,?,1,?,?,?,
                 'SHOPEE_AFFILIATE','Shop',?,'READY',?)""",
            (
                product_id, 'manual_shopee', 'shopee.vn', product_id, name,
                f'https://shopee.vn/product/1/{product_id}', stamp, stamp, stamp,
                f'https://s.shopee.vn/{product_id}', stamp,
            ),
        )

    def test_discovery_thresholds_can_be_changed_through_system_settings(self):
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO system_setting (key,value,updated_at,updated_by) VALUES (?,?,?,'test')",
            ('topic.auto_cluster_min', '3', stamp),
        )
        self.conn.execute(
            "INSERT INTO system_setting (key,value,updated_at,updated_by) VALUES (?,?,?,'test')",
            ('topic.auto_confidence_min', '0.75', stamp),
        )
        for index in range(3):
            self._insert_product(f'p{index}', f'Set đồ mặc nhà nữ mẫu {index}')

        result = topic_engine.discover_dynamic_topics(self.conn)
        names = [item['name'] for item in result['created'] + result['merged']]
        self.assertIn('Đồ mặc nhà', names)


if __name__ == '__main__':
    unittest.main()
