from __future__ import annotations

import os
import unittest
from pathlib import Path

from acp.adapters import factory

ROOT = Path(__file__).resolve().parents[1]


class SeedingLlmContractTests(unittest.TestCase):
    def setUp(self):
        self.previous = os.environ.get("ACP_CAPTION_LLM")

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("ACP_CAPTION_LLM", None)
        else:
            os.environ["ACP_CAPTION_LLM"] = self.previous

    def test_seeding_llm_uses_structured_gemini_json_callback(self):
        os.environ["ACP_CAPTION_LLM"] = "gemini"
        callback = factory.get_seeding_llm()
        self.assertIsNotNone(callback)
        self.assertEqual("rewrite_json", callback.__name__)

    def test_seeding_llm_is_disabled_when_caption_llm_is_disabled(self):
        os.environ["ACP_CAPTION_LLM"] = ""
        self.assertIsNone(factory.get_seeding_llm())

    def test_gemini_structured_callback_enables_json_response_mode(self):
        source = (ROOT / "core" / "llm_gemini.py").read_text(encoding="utf-8")
        self.assertIn("def rewrite_json", source)
        self.assertIn('response_mime_type="application/json"', source)

    def test_prepare_route_uses_seeding_llm_not_free_form_caption_callback(self):
        source = (ROOT / "web" / "seeding_account_routes.py").read_text(encoding="utf-8")
        start = source.index("def seeding_account_prepare")
        end = source.index('@bp.post("/api/seeding/account/like-result")', start)
        body = source[start:end]
        self.assertIn("factory.get_seeding_llm()", body)
        self.assertNotIn("factory.get_caption_llm()", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
