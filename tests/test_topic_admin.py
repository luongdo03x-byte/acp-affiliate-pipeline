import os
import tempfile
import unittest
from datetime import datetime, timezone

from acp.core import db, topic_engine


class TopicAdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "topic-admin.db")
        db.init_db()
        self.conn = db.connect()
        topic_engine.ensure_system_topics(self.conn)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO channel (id,code,platform,handle,status,enabled,niches,created_at) VALUES ('ch','threads','threads','@acc','ACTIVE',1,'[]',?)",
            (stamp,),
        )
        parent = topic_engine.topic_by_code(self.conn, "thoi-trang-nu")
        self.parent_id = parent["id"]
        self.source = topic_engine.create_topic(
            self.conn, code="do-mac-nha", name="Đồ mặc nhà", topic_type="AUTO",
            parent_id=self.parent_id, confidence=0.9,
        )
        self.target = topic_engine.create_topic(
            self.conn, code="thoi-trang-mac-nha", name="Thời trang mặc nhà", topic_type="AUTO",
            parent_id=self.parent_id, confidence=0.88,
        )

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def _insert_product(self, product_id="p"):
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            """INSERT INTO product (
                 id,source,merchant,external_product_id,name,description,current_price,
                 commission_value,category_code,sold_count,product_url,is_available,
                 last_seen_at,created_at,updated_at,provider)
               VALUES (?,?,?,?,?,'',100000,10000,'thoi-trang',0,?,1,?,?,?,'SHOPEE_AFFILIATE')""",
            (product_id, "manual_shopee", "shopee.vn", product_id, "Set đồ mặc nhà nữ",
             f"https://shopee.vn/product/1/{product_id}", stamp, stamp, stamp),
        )

    def test_rename_keeps_code_and_records_old_name_as_alias(self):
        from acp.core import topic_admin
        updated = topic_admin.rename_topic(self.conn, self.source["id"], "Homewear nữ")
        self.assertEqual(updated["code"], "do-mac-nha")
        self.assertEqual(updated["name"], "Homewear nữ")
        alias = self.conn.execute(
            "SELECT topic_id FROM topic_alias WHERE alias_normalized=?",
            (topic_engine.normalize_text("Đồ mặc nhà"),),
        ).fetchone()
        self.assertEqual(alias["topic_id"], self.source["id"])

    def test_merge_moves_product_and_channel_rules_to_target(self):
        from acp.core import topic_admin
        self._insert_product()
        topic_engine.attach_product_topic(self.conn, "p", self.source["id"], 0.95, "AUTO")
        topic_engine.set_channel_rules(self.conn, "ch", [self.source["code"]], [])

        result = topic_admin.merge_topic(self.conn, self.source["id"], self.target["id"])
        self.assertEqual(result["target_id"], self.target["id"])
        source = self.conn.execute("SELECT status FROM topic WHERE id=?", (self.source["id"],)).fetchone()
        self.assertEqual(source["status"], "MERGED")
        attached = self.conn.execute(
            "SELECT 1 FROM product_topic WHERE product_id='p' AND topic_id=?", (self.target["id"],)
        ).fetchone()
        self.assertIsNotNone(attached)
        rules = topic_engine.channel_rules(self.conn, "ch")
        self.assertEqual([row["code"] for row in rules["includes"]], [self.target["code"]])

    def test_delete_is_soft_and_system_topics_cannot_be_deleted(self):
        from acp.core import topic_admin
        topic_admin.delete_topic(self.conn, self.source["id"])
        row = self.conn.execute("SELECT status FROM topic WHERE id=?", (self.source["id"],)).fetchone()
        self.assertEqual(row["status"], "DISABLED")
        system_topic = topic_engine.topic_by_code(self.conn, "thoi-trang-nu")
        with self.assertRaises(ValueError):
            topic_admin.delete_topic(self.conn, system_topic["id"])

    def test_merge_rejects_topics_from_different_parents(self):
        from acp.core import topic_admin
        other_parent = topic_engine.topic_by_code(self.conn, "gia-dung")
        other = topic_engine.create_topic(
            self.conn, code="nha-bep", name="Nhà bếp", topic_type="AUTO",
            parent_id=other_parent["id"], confidence=0.9,
        )
        with self.assertRaises(ValueError):
            topic_admin.merge_topic(self.conn, self.source["id"], other["id"])


if __name__ == "__main__":
    unittest.main()
