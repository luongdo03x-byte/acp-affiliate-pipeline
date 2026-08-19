from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SeedingSheetWebhookContracts(unittest.TestCase):
    def test_apps_script_webhook_exists_and_appends_exact_b_c_d_block(self):
        path = ROOT / "integrations" / "google_sheets_seeding_webhook.gs"
        self.assertTrue(path.is_file())
        source = path.read_text(encoding="utf-8")
        self.assertIn("ACP_SEEDING_SECRET", source)
        self.assertIn("SPREADSHEET_ID", source)
        self.assertIn("SHEET_NAME", source)
        self.assertIn("body.secret", source)
        self.assertIn("sheet.getRange(startRow, 2, rows.length, 3).setValues(rows)", source)
        self.assertIn("body.campaign_id", source)

    def test_sheet_integration_does_not_contain_facebook_credentials(self):
        source = (ROOT / "docs" / "SEEDING_SHEET_SETUP.md").read_text(encoding="utf-8").lower()
        if (ROOT / "integrations" / "google_sheets_seeding_webhook.gs").exists():
            source += (ROOT / "integrations" / "google_sheets_seeding_webhook.gs").read_text(encoding="utf-8").lower()
        self.assertNotIn("facebook_password", source)
        self.assertNotIn("facebook_cookie", source)

    def test_env_template_contains_optional_sheet_settings(self):
        source = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("ACP_SEEDING_SHEET_WEBHOOK_URL=", source)
        self.assertIn("ACP_SEEDING_SHEET_SECRET=", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
