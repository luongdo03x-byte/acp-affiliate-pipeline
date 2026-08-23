import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from acp.core import db, niche
from acp.core import topic_engine


class DynamicTopicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "topics.db")
        db.init_db()
        self.conn = db.connect()
        topic_engine.ensure_system_topics(self.conn)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            """INSERT INTO channel (
                 id, code, platform, handle, status, enabled, niches, created_at)
               VALUES ('ch','threads-fashion','threads','@fashion','ACTIVE',1,'[]',?)""",
            (stamp,),
        )

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def _insert_shopee(self, product_id, name):
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            """INSERT INTO product (
                 id, source, merchant, external_product_id, name, description,
                 current_price, commission_value, category_code, sold_count,
                 product_url, is_available, last_seen_at, created_at, updated_at,
                 provider, shop_name, affiliate_url, affiliate_link_status, last_synced_at)
               VALUES (?,?,?,?,?,'',100000,10000,'thoi-trang',0,?,1,?,?,?,
                       'SHOPEE_AFFILIATE','Shop',?,'READY',?)""",
            (
                product_id, "manual_shopee", "shopee.vn", product_id, name,
                f"https://shopee.vn/product/1/{product_id}", stamp, stamp, stamp,
                f"https://s.shopee.vn/{product_id}", stamp,
            ),
        )

    def test_system_topics_are_mirrored_idempotently(self):
        topic_engine.ensure_system_topics(self.conn)
        topic_engine.ensure_system_topics(self.conn)
        rows = self.conn.execute(
            "SELECT code, topic_type FROM topic WHERE topic_type='SYSTEM' ORDER BY code"
        ).fetchall()
        self.assertEqual([row["code"] for row in rows], sorted(niche.NICHES))
        self.assertTrue(all(row["topic_type"] == "SYSTEM" for row in rows))

    def test_parent_include_inherits_future_child_and_child_exclude_wins(self):
        parent = topic_engine.topic_by_code(self.conn, "thoi-trang-nu")
        self.assertIsNotNone(parent)
        topic_engine.set_channel_rules(self.conn, "ch", ["thoi-trang-nu"], [])

        child = topic_engine.create_topic(
            self.conn,
            code="do-mac-nha",
            name="Đồ mặc nhà",
            topic_type="AUTO",
            parent_id=parent["id"],
            confidence=0.91,
        )
        self._insert_shopee("p1", "Set bộ nữ đồ mặc nhà thun tăm")
        topic_engine.attach_product_topic(self.conn, "p1", child["id"], 0.95, "AUTO")
        self.assertTrue(topic_engine.channel_accepts_product(self.conn, "ch", "p1"))

        topic_engine.set_channel_rules(
            self.conn,
            "ch",
            ["thoi-trang-nu"],
            ["do-mac-nha"],
        )
        self.assertFalse(topic_engine.channel_accepts_product(self.conn, "ch", "p1"))

    def test_no_include_rule_means_channel_receives_all_topics(self):
        parent = topic_engine.topic_by_code(self.conn, "gia-dung")
        child = topic_engine.create_topic(
            self.conn,
            code="nha-bep",
            name="Nhà bếp",
            topic_type="AUTO",
            parent_id=parent["id"],
            confidence=0.9,
        )
        self._insert_shopee("p2", "Hộp đựng thực phẩm nhà bếp")
        topic_engine.attach_product_topic(self.conn, "p2", child["id"], 0.9, "AUTO")
        self.assertTrue(topic_engine.channel_accepts_product(self.conn, "ch", "p2"))

    def test_cluster_requires_five_products_and_confidence_point_eight(self):
        for idx in range(4):
            self._insert_shopee(f"h{idx}", f"Set đồ mặc nhà nữ thun tăm mẫu {idx}")
        first = topic_engine.discover_dynamic_topics(self.conn)
        self.assertFalse(any(item["name"] == "Đồ mặc nhà" for item in first["created"]))

        self._insert_shopee("h4", "Set đồ mặc nhà nữ thun tăm mẫu 4")
        second = topic_engine.discover_dynamic_topics(self.conn)
        created_names = [item["name"] for item in second["created"]]
        existing_names = [item["name"] for item in second["merged"]]
        self.assertIn("Đồ mặc nhà", created_names + existing_names)
        topic = self.conn.execute("SELECT * FROM topic WHERE name='Đồ mặc nhà'").fetchone()
        self.assertIsNotNone(topic)
        self.assertGreaterEqual(topic["confidence"], 0.80)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM product_topic WHERE topic_id=?", (topic["id"],)
        ).fetchone()[0]
        self.assertGreaterEqual(count, 5)

    def test_similarity_merge_reuses_canonical_and_saves_alias(self):
        parent = topic_engine.topic_by_code(self.conn, "thoi-trang-nu")
        canonical = topic_engine.create_topic(
            self.conn,
            code="do-mac-nha",
            name="Đồ mặc nhà",
            topic_type="AUTO",
            parent_id=parent["id"],
            confidence=0.9,
        )
        merged = topic_engine.find_or_create_dynamic_topic(
            self.conn,
            parent_id=parent["id"],
            candidate_name="Đồ mặc ở nhà",
            confidence=0.92,
            product_count=5,
        )
        self.assertEqual(merged["topic_id"], canonical["id"])
        self.assertEqual(merged["action"], "merged")
        alias = self.conn.execute(
            "SELECT topic_id FROM topic_alias WHERE alias_normalized=?",
            (topic_engine.normalize_text("Đồ mặc ở nhà"),),
        ).fetchone()
        self.assertIsNotNone(alias)
        self.assertEqual(alias["topic_id"], canonical["id"])


if __name__ == "__main__":
    unittest.main()
