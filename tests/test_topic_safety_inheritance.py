import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from acp.core import db, topic_engine


class TopicSafetyInheritanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "topic-safety.db")
        db.init_db()
        self.conn = db.connect()
        topic_engine.ensure_system_topics(self.conn)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            """INSERT INTO channel
               (id,code,platform,handle,status,enabled,niches,created_at)
               VALUES ('ch','threads-fashion','threads','@fashion','ACTIVE',1,'[]',?)""",
            (stamp,),
        )

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def test_dynamic_child_include_mirrors_system_ancestor_for_content_safety(self):
        parent = topic_engine.topic_by_code(self.conn, "thoi-trang-nu")
        child = topic_engine.create_topic(
            self.conn,
            code="do-mac-nha",
            name="Đồ mặc nhà",
            topic_type="AUTO",
            parent_id=parent["id"],
            confidence=0.95,
        )
        topic_engine.set_channel_rules(self.conn, "ch", [child["code"]], [])
        row = self.conn.execute("SELECT niches FROM channel WHERE id='ch'").fetchone()
        self.assertEqual(json.loads(row["niches"]), ["thoi-trang-nu"])


if __name__ == "__main__":
    unittest.main()
