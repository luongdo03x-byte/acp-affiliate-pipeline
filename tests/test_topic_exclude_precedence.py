import os
import tempfile
import unittest
from datetime import datetime, timezone

from acp.core import db, topic_engine


class TopicExcludePrecedenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "exclude.db")
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

    def test_explicit_exclude_wins_if_same_topic_is_also_included(self):
        parent = topic_engine.topic_by_code(self.conn, "thoi-trang-nu")
        child = topic_engine.create_topic(
            self.conn,
            code="bigsize",
            name="Bigsize",
            topic_type="AUTO",
            parent_id=parent["id"],
            confidence=0.95,
        )
        topic_engine.set_channel_rules(
            self.conn,
            "ch",
            [child["code"]],
            [child["code"]],
        )
        rules = topic_engine.channel_rules(self.conn, "ch")
        self.assertNotIn(child["code"], [item["code"] for item in rules["includes"]])
        self.assertIn(child["code"], [item["code"] for item in rules["excludes"]])


if __name__ == "__main__":
    unittest.main()
