import sqlite3
import unittest
from datetime import datetime, timedelta, timezone


class ShopeeAutoStateTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE product (
                id TEXT PRIMARY KEY,
                provider TEXT,
                last_synced_at TEXT,
                last_seen_at TEXT
            );
            CREATE TABLE shopee_image_enrichment_job (
                product_id TEXT PRIMARY KEY,
                status TEXT NOT NULL
            );
            CREATE TABLE channel (
                id TEXT PRIMARY KEY,
                handle TEXT
            );
            CREATE TABLE post (
                id TEXT PRIMARY KEY,
                product_id TEXT,
                channel_id TEXT,
                status TEXT,
                published_at TEXT
            );
            CREATE TABLE publish_target (
                id TEXT PRIMARY KEY,
                post_id TEXT,
                channel_id TEXT,
                status TEXT,
                scheduled_at TEXT,
                auto_scheduled INTEGER DEFAULT 0
            );
            """
        )
        self.now = datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.conn.close()

    def _product(self, *, image_status="READY", age_hours=1):
        synced = (self.now - timedelta(hours=age_hours)).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO product (id, provider, last_synced_at, last_seen_at) VALUES (?,?,?,?)",
            ("sp1", "SHOPEE_AFFILIATE", synced, synced),
        )
        self.conn.execute(
            "INSERT INTO shopee_image_enrichment_job (product_id, status) VALUES (?,?)",
            ("sp1", image_status),
        )
        return self.conn.execute(
            """SELECT p.*, j.status AS enrichment_status
               FROM product p JOIN shopee_image_enrichment_job j ON j.product_id=p.id
               WHERE p.id='sp1'"""
        ).fetchone()

    def test_waiting_image_precedes_every_auto_state(self):
        from acp.web.shopee_auto_state import derive_auto_state
        product = self._product(image_status="NEEDS_HELPER")
        self.assertEqual(derive_auto_state(self.conn, product, now_utc=self.now)["state"], "WAITING_IMAGE")

    def test_stale_ready_product_is_stale(self):
        from acp.web.shopee_auto_state import derive_auto_state
        product = self._product(age_hours=73)
        self.assertEqual(derive_auto_state(self.conn, product, now_utc=self.now)["state"], "STALE")

    def test_live_auto_target_is_scheduled_with_channel(self):
        from acp.web.shopee_auto_state import derive_auto_state
        product = self._product()
        self.conn.execute("INSERT INTO channel (id, handle) VALUES ('ch1','@tech')")
        self.conn.execute(
            "INSERT INTO post (id, product_id, channel_id, status) VALUES ('p1','sp1','ch1','SCHEDULED')"
        )
        scheduled_at = (self.now + timedelta(hours=2)).isoformat(timespec="seconds")
        self.conn.execute(
            """INSERT INTO publish_target
               (id, post_id, channel_id, status, scheduled_at, auto_scheduled)
               VALUES ('t1','p1','ch1','SCHEDULED',?,1)""",
            (scheduled_at,),
        )
        state = derive_auto_state(self.conn, product, now_utc=self.now)
        self.assertEqual(state["state"], "SCHEDULED")
        self.assertEqual(state["channel_handle"], "@tech")
        self.assertEqual(state["scheduled_at"], scheduled_at)

    def test_review_precedes_old_published_state(self):
        from acp.web.shopee_auto_state import derive_auto_state
        product = self._product()
        self.conn.execute(
            "INSERT INTO post (id, product_id, channel_id, status) VALUES ('review','sp1','ch','PENDING_REVIEW')"
        )
        self.conn.execute(
            "INSERT INTO post (id, product_id, channel_id, status, published_at) VALUES ('old','sp1','ch','PUBLISHED',?)",
            ((self.now - timedelta(days=1)).isoformat(timespec="seconds"),),
        )
        self.assertEqual(derive_auto_state(self.conn, product, now_utc=self.now)["state"], "REVIEW")

    def test_published_product_is_published(self):
        from acp.web.shopee_auto_state import derive_auto_state
        product = self._product()
        self.conn.execute(
            "INSERT INTO post (id, product_id, channel_id, status, published_at) VALUES ('p1','sp1','ch','PUBLISHED',?)",
            (self.now.isoformat(timespec="seconds"),),
        )
        self.assertEqual(derive_auto_state(self.conn, product, now_utc=self.now)["state"], "PUBLISHED")

    def test_ready_fresh_unused_product_is_eligible(self):
        from acp.web.shopee_auto_state import derive_auto_state
        product = self._product()
        self.assertEqual(derive_auto_state(self.conn, product, now_utc=self.now)["state"], "ELIGIBLE")


if __name__ == "__main__":
    unittest.main()
