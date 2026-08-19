from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SeedingExecutionWebContracts(unittest.TestCase):
    def test_account_routes_expose_profile_scoped_execution_api(self):
        source = (ROOT / "web" / "seeding_account_routes.py").read_text(encoding="utf-8")
        for route in (
            '/api/seeding/account/next-work',
            '/api/seeding/account/prepare',
            '/api/seeding/account/like-result',
            '/api/seeding/account/work-result',
        ):
            self.assertIn(route, source)
        self.assertIn("_require_extension_token()", source)
        self.assertIn("seeding_execution.next_account_work", source)
        self.assertIn("seeding_reports.maybe_auto_push", source)

    def test_content_script_uses_profile_scoped_workflow_not_global_target_queue(self):
        source = (ROOT / "extensions" / "facebook-seeding-assistant" / "content.js").read_text(encoding="utf-8")
        self.assertIn("/api/seeding/account/next-work", source)
        self.assertIn("/api/seeding/account/prepare", source)
        self.assertIn("/api/seeding/account/like-result", source)
        self.assertIn("/api/seeding/account/work-result", source)
        self.assertIn("extensionInstanceId", source)
        self.assertNotIn("/api/seeding/next-target", source)

    def test_reply_work_requires_operator_selected_reply_composer(self):
        source = (ROOT / "extensions" / "facebook-seeding-assistant" / "content.js").read_text(encoding="utf-8")
        self.assertIn("REPLY", source)
        self.assertIn("findFocusedComposer", source)
        self.assertNotIn("submitControl.click()", source)

    def test_manual_done_requires_cleared_composer_and_idle_profile_polls_for_new_work(self):
        source = (ROOT / "extensions" / "facebook-seeding-assistant" / "content.js").read_text(encoding="utf-8")
        self.assertIn("verifyManualSubmission", source)
        self.assertIn("lastFilledComposer", source)
        self.assertIn("scheduleIdlePoll", source)
        self.assertIn("setTimeout", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
