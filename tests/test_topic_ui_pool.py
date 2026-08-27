"""Trang /kenh: pool count theo topic phải ĐÚNG NGHIÃA với cách đếm cũ.

Bản cũ gọi channel_accepts_product() cho từng cặp (kênh x sản phẩm); bản mới
tính batch trong web/topic_ui.py để trang mở nhanh. Test này khoá hai kết quả
lại với nhau trên dữ liệu có đủ INCLUDE/EXCLUDE/legacy-niches/san-pham-thieu-topic.
"""
import os
import tempfile
import unittest

from acp.core import db, topic_engine


def _stamp():
    return db.now()


def _insert_channel(conn, cid, niches="[]"):
    conn.execute(
        """INSERT INTO channel (id, code, platform, handle, status, enabled, niches, created_at)
           VALUES (?, ?, 'threads', ?, 'ACTIVE', 1, ?, ?)""",
        (cid, f"code_{cid}", f"@{cid}", niches, _stamp()),
    )


def _insert_shopee_product(conn, pid, name, category_code):
    stamp = _stamp()
    conn.execute(
        """INSERT INTO product (
             id, source, merchant, external_product_id, name, current_price,
             commission_value, category_code, sold_count, product_url,
             is_available, last_seen_at, created_at, updated_at, provider)
           VALUES (?, 'shopee', 'shopee.vn', ?, ?, 100000, 10000, ?, 0,
                   'https://shopee.vn/product/1/1', 1, ?, ?, ?, 'SHOPEE_AFFILIATE')""",
        (pid, f"ext_{pid}", name, category_code, stamp, stamp, stamp),
    )


class TopicUiPoolEquivalenceTests(unittest.TestCase):
    def setUp(self):
        self.previous_db_path = db.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "topic-ui.db")
        db.init_db()
        self.conn = db.connect()
        try:
            _insert_channel(self.conn, "ch-legacy", '["gia-dung"]')
            _insert_channel(self.conn, "ch-empty")
            _insert_channel(self.conn, "ch-rules")
            _insert_shopee_product(self.conn, "p-gd", "Bo noi gia dung", "gia-dung")
            _insert_shopee_product(self.conn, "p-mb", "Binh sua me be", "me-va-be")
            _insert_shopee_product(self.conn, "p-tt", "Vay thoi trang nu", "thoi-trang")
            topic_engine.set_channel_rules(self.conn, "ch-rules",
                                           includes=["me-va-be"], excludes=[])
            row = self.conn.execute("SELECT id FROM topic WHERE code='me-va-be'").fetchone()
            self.conn.execute(
                """INSERT INTO channel_topic_rule
                   (channel_id, topic_id, rule_mode, created_at, updated_at)
                   VALUES ('ch-rules', ?, 'EXCLUDE', ?, ?)""",
                (row["id"], _stamp(), _stamp()),
            )
            self.conn.commit()
        except Exception:
            self.conn.close()
            raise

    def tearDown(self):
        try:
            self.conn.close()
        except Exception:
            pass
        db.DB_PATH = self.previous_db_path
        self.tmp.cleanup()

    def test_batched_pool_counts_match_reference_semantics(self):
        from acp.web.topic_ui import _channel_state

        states, pools = _channel_state(self.conn)

        channels = [r["id"] for r in self.conn.execute("SELECT id FROM channel ORDER BY id")]
        self.assertEqual(set(pools), set(channels))
        for cid in channels:
            reference = sum(
                1 for row in self.conn.execute(
                    "SELECT id FROM product WHERE provider='SHOPEE_AFFILIATE' AND is_available=1"
                )
                if topic_engine.channel_accepts_product(self.conn, cid, row["id"])
            )
            self.assertEqual(
                pools[cid], reference,
                f"pool count khac nghia tren kênh {cid}: batch={pools[cid]} ref={reference}",
            )
            self.assertIn("includes", states[cid])
            self.assertIn("excludes", states[cid])

    def test_exclude_rule_blocks_topic_branch_for_legacy_include_channel(self):
        from acp.web.topic_ui import _channel_state

        _, pools = _channel_state(self.conn)

        # ch-rules: INCLUDE me-va-be nhưng đồng thời EXCLUDE chính nhánh đó
        # -> không còn sản phẩm nào thoả.
        self.assertEqual(pools["ch-rules"], 0)


if __name__ == "__main__":
    unittest.main()
