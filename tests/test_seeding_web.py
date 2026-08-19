"""Web/API contract tests for the Facebook Seeding Assistant."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    import flask  # noqa: F401
except ImportError:  # Local minimal harness can still run static contracts.
    HAS_FLASK = False
else:
    HAS_FLASK = True


class SeedingWebStaticTests(unittest.TestCase):
    def test_route_module_and_template_exist(self) -> None:
        self.assertTrue((ROOT / "web" / "seeding_routes.py").is_file())
        self.assertTrue((ROOT / "web" / "templates" / "seeding.html").is_file())

    def test_server_registers_seeding_and_exempts_only_token_api_prefix(self) -> None:
        source = (ROOT / "web" / "server.py").read_text(encoding="utf-8")
        self.assertIn('"/api/seeding/"', source)
        self.assertIn("register_seeding(app)", source)

    def test_sidebar_contains_seeding_link(self) -> None:
        source = (ROOT / "web" / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn('href="/seeding"', source)
        self.assertIn("page=='seeding'", source)

    def test_extension_has_token_protected_pause_shift_endpoint(self) -> None:
        source = (ROOT / "web" / "seeding_routes.py").read_text(encoding="utf-8")
        self.assertIn('@bp.post("/api/seeding/pause-shift")', source)
        self.assertIn("_require_extension_token()", source)
        self.assertIn("seeding.pause_shift", source)

    def test_result_endpoint_uses_exact_shift_even_after_pause(self) -> None:
        source = (ROOT / "web" / "seeding_routes.py").read_text(encoding="utf-8")
        start = source.index("def _record_api_result")
        end = source.index('@bp.post("/api/seeding/result")', start)
        body = source[start:end]
        self.assertIn("_find_shift(conn, shift_id)", body)
        self.assertNotIn("_find_active_shift(conn", body)

    def test_manual_task_form_and_route_contract_exist(self) -> None:
        route_source = (ROOT / "web" / "seeding_routes.py").read_text(encoding="utf-8")
        template_source = (ROOT / "web" / "templates" / "seeding.html").read_text(encoding="utf-8")
        self.assertIn('@bp.post("/seeding/task")', route_source)
        self.assertIn('action="/seeding/task"', template_source)
        self.assertIn('name="task_name"', template_source)
        self.assertIn('name="instruction"', template_source)
        self.assertIn('name="post_url"', template_source)


@unittest.skipUnless(HAS_FLASK, "Flask is not installed in the minimal local harness")
class SeedingWebFunctionalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp(prefix="acp-seeding-web-")
        os.environ["ACP_DB"] = os.path.join(cls.tmp, "web.db")
        os.environ["ACP_ADMIN_PASSWORD"] = ""
        os.environ["ACP_SEEDING_EXTENSION_TOKEN"] = "test-seeding-token"
        os.environ["ACP_ADAPTER"] = "mock"
        os.environ["ACP_SOURCE"] = "mock"
        os.environ["ACP_CAPTION_LLM"] = ""

        from acp.core import db
        db.DB_PATH = os.environ["ACP_DB"]
        db.init_db()

        from acp.web.server import create_app
        cls.app = create_app()
        cls.app.config.update(TESTING=True)

    def setUp(self) -> None:
        self.client = self.app.test_client()

    def test_extension_api_requires_dedicated_token(self) -> None:
        self.assertEqual(401, self.client.get("/api/seeding/status").status_code)
        self.assertEqual(
            401,
            self.client.get(
                "/api/seeding/status",
                headers={"X-ACP-Seeding-Token": "wrong"},
            ).status_code,
        )
        response = self.client.get(
            "/api/seeding/status",
            headers={"X-ACP-Seeding-Token": "test-seeding-token"},
        )
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["ok"])

    def test_dashboard_renders_manual_task_controls(self) -> None:
        response = self.client.get("/seeding")
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("Seeding", body)
        self.assertIn("Global pause", body)
        self.assertIn("Tạo nhiệm vụ", body)
        self.assertIn('name="task_name"', body)
        self.assertIn('name="instruction"', body)
        self.assertIn('name="post_url"', body)

    def test_manual_task_create_redirects_to_new_task_and_shows_parsed_rules(self) -> None:
        response = self.client.post(
            "/seeding/task",
            data={
                "task_name": "A2GR-64",
                "instruction": (
                    "LIKE BÀI ĐĂNG; mỗi acc 3 CMT (1 cmt chính + 2 cmt reply); "
                    "tối đa 3 acc; KHÔNG NHẮC SỮA."
                ),
                "post_url": "https://www.facebook.com/groups/demo/permalink/123/",
            },
            follow_redirects=True,
        )
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("A2GR-64", body)
        self.assertIn("3 account", body)
        self.assertIn("1 main", body)
        self.assertIn("2 reply", body)
        self.assertIn("sữa", body.lower())
        self.assertGreaterEqual(body.count("Account "), 3)


def load_tests(loader, tests, pattern):
    """Keep seeding regressions inside the existing manage.sh seeding gate."""
    from acp.tests import (
        test_seeding_account_execution,
        test_seeding_account_web,
        test_seeding_accounts,
        test_seeding_execution_web,
        test_seeding_reports,
        test_seeding_sheet_webhook,
        test_seeding_task_comment_plan,
        test_seeding_task_intake,
        test_seeding_task_rules,
    )

    suite = unittest.TestSuite()
    suite.addTests(tests)
    for module in (
        test_seeding_task_rules,
        test_seeding_task_intake,
        test_seeding_task_comment_plan,
        test_seeding_accounts,
        test_seeding_account_execution,
        test_seeding_account_web,
        test_seeding_execution_web,
        test_seeding_reports,
        test_seeding_sheet_webhook,
    ):
        suite.addTests(loader.loadTestsFromModule(module))
    return suite


if __name__ == "__main__":
    unittest.main(verbosity=2)
