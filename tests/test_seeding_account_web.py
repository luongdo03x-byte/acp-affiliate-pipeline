from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SeedingAccountWebStaticTests(unittest.TestCase):
    def test_account_routes_are_loaded_before_shared_blueprint_registration(self):
        package_source = (ROOT / "web" / "__init__.py").read_text(encoding="utf-8")
        route_source = (ROOT / "web" / "seeding_account_routes.py").read_text(encoding="utf-8")
        self.assertIn("seeding_account_routes", package_source)
        self.assertIn('@bp.get("/seeding/accounts")', route_source)
        self.assertIn('@bp.post("/seeding/campaign/<campaign_id>/accounts")', route_source)

    def test_pairing_api_is_token_protected(self):
        source = (ROOT / "web" / "seeding_account_routes.py").read_text(encoding="utf-8")
        for route in (
            '/api/seeding/account/register',
            '/api/seeding/account/heartbeat',
            '/api/seeding/accounts',
        ):
            self.assertIn(route, source)
        self.assertGreaterEqual(source.count("_require_extension_token()"), 3)

    def test_account_manager_template_has_mapping_controls(self):
        source = (ROOT / "web" / "templates" / "seeding_accounts.html").read_text(encoding="utf-8")
        self.assertIn("Facebook Account Manager", source)
        self.assertIn('name="account_ids"', source)
        self.assertIn("ONLINE", source)
        self.assertIn("OFFLINE", source)

    def test_account_manager_does_not_store_facebook_credentials(self):
        account_source = (ROOT / "core" / "seeding_accounts.py").read_text(encoding="utf-8").lower()
        background_source = (ROOT / "extensions" / "facebook-seeding-assistant" / "background.js").read_text(encoding="utf-8").lower()
        self.assertNotIn("facebook_password", account_source + background_source)
        self.assertNotIn("facebook_cookie", account_source + background_source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
