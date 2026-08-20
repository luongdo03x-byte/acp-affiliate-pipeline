"""Shopee observability tests: evidence-based events without secret URLs/tokens."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class ShopeeObservabilityTests(unittest.TestCase):
    def setUp(self):
        from acp.core import db
        self.db = db
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "observability.db")
        db.init_db()
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()
        self.db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def test_event_uses_canonical_identity_and_drops_sensitive_detail(self):
        from acp.core.shopee_observability import record_shopee_event

        record_shopee_event(
            self.conn,
            "https://shopee.vn/Tai-nghe-i.123.456?credential_token=secret",
            "helper_metadata_success",
            detail={
                "source": "helper",
                "state": "ready",
                "metadata_fields": ["name", "current_price"],
                "affiliate_url": "https://s.shopee.vn/secret-tracking",
                "token": "secret-token",
                "cookie": "secret-cookie",
                "raw_response": "secret-body",
            },
            actor="operator",
        )
        row = self.conn.execute(
            "SELECT entity, entity_id, action, actor, detail FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(row["entity"], "shopee_product")
        self.assertEqual(row["entity_id"], "123:456")
        self.assertEqual(row["action"], "helper_metadata_success")
        self.assertEqual(row["actor"], "operator")
        detail = json.loads(row["detail"])
        self.assertEqual(detail, {
            "source": "helper",
            "state": "ready",
            "metadata_fields": ["name", "current_price"],
        })
        serialized = row["detail"]
        self.assertNotIn("secret", serialized)
        self.assertNotIn("affiliate_url", serialized)
        self.assertNotIn("token", serialized)
        self.assertNotIn("cookie", serialized)

    def test_invalid_action_and_invalid_product_are_rejected(self):
        from acp.core.shopee_observability import ShopeeObservabilityError, record_shopee_event

        with self.assertRaises(ShopeeObservabilityError):
            record_shopee_event(self.conn, "https://shopee.vn/product/1/2", "made_up_event")
        with self.assertRaises(ShopeeObservabilityError):
            record_shopee_event(self.conn, "https://example.com/product/1/2", "resolve_success")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], 0)

    def test_detail_values_are_bounded_and_normalized(self):
        from acp.core.shopee_observability import record_shopee_event

        record_shopee_event(
            self.conn,
            "https://shopee.vn/product/1/2",
            "price_refresh_failed",
            detail={
                "source": "server" * 100,
                "error_category": "network" * 100,
                "http_status": 403,
                "price_changed": False,
                "metadata_fields": ["name", "current_price", "cookie", 123],
            },
        )
        detail = json.loads(self.conn.execute(
            "SELECT detail FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertLessEqual(len(detail["source"]), 64)
        self.assertLessEqual(len(detail["error_category"]), 64)
        self.assertEqual(detail["http_status"], 403)
        self.assertFalse(detail["price_changed"])
        self.assertEqual(detail["metadata_fields"], ["name", "current_price"])


if __name__ == "__main__":
    unittest.main()
