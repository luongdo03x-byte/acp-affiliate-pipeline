import sqlite3
import unittest
from unittest.mock import patch


class ShopeePolishObservabilityResilienceTests(unittest.TestCase):
    def test_missing_audit_table_does_not_break_request_observability_hook(self):
        from acp.web import shopee_polish

        conn = sqlite3.connect(":memory:")
        try:
            with patch.object(shopee_polish, "connect", return_value=conn):
                # Observability is best-effort. A missing/corrupt audit table must
                # never turn an otherwise successful Shopee request into HTTP 500.
                shopee_polish._record_event(
                    "https://shopee.vn/product/123/456",
                    "helper_metadata_success",
                    {"source": "helper", "state": "ready"},
                    actor="operator",
                )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
