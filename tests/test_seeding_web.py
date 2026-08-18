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

    def test_dashboard_renders_seeding_controls(self) -> None:
        response = self.client.get("/seeding")
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("Seeding", body)
        self.assertIn("Global pause", body)
        self.assertIn("confidence_threshold", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
