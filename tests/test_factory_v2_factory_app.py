import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from account_factory_server import build_app


class FactoryOnlyAppTests(unittest.TestCase):
    def test_factory_root_does_not_require_post_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "factory.db")
            with patch.dict(
                os.environ,
                {"ACP_DB": db_path, "ACP_FACTORY_API_KEY": "test-key"},
                clear=False,
            ):
                app = build_app(start_controller=False)
                res = app.test_client().get("/")
                self.assertEqual(200, res.status_code)
                body = res.get_json()
                self.assertEqual("account-factory", body["service"])

                conn = sqlite3.connect(db_path)
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                conn.close()
                self.assertNotIn("post", tables)
                self.assertNotIn("product", tables)

    def test_factory_app_does_not_register_publish_routes(self):
        app = build_app(start_controller=False)
        rules = {rule.rule for rule in app.url_map.iter_rules()}
        self.assertNotIn("/sanpham", rules)
        self.assertNotIn("/duyet", rules)
        self.assertIn("/api/factory/v2/dashboard", rules)


if __name__ == "__main__":
    unittest.main()
