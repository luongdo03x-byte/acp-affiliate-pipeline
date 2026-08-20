from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SeedingUnknownWebContracts(unittest.TestCase):
    def test_dashboard_exposes_explicit_unknown_reset_only(self):
        routes = (ROOT / "web" / "seeding_account_routes.py").read_text(encoding="utf-8")
        template = (ROOT / "web" / "templates" / "seeding_accounts.html").read_text(encoding="utf-8")
        self.assertIn('/seeding/comment/<slot_id>/reset-unknown', routes)
        self.assertIn('reset_unknown_comment', routes)
        self.assertIn('unknown_slots', template)
        self.assertIn('Reset để thử lại', template)


if __name__ == "__main__":
    unittest.main(verbosity=2)
