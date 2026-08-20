from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SeedingStructuredLlmContracts(unittest.TestCase):
    def test_prepare_route_uses_structured_json_llm(self):
        source = (ROOT / "web" / "seeding_account_routes.py").read_text(encoding="utf-8")
        start = source.index('def seeding_account_prepare')
        end = source.index('@bp.post("/api/seeding/account/like-result")', start)
        body = source[start:end]
        self.assertIn('factory.get_seeding_llm()', body)
        self.assertNotIn('factory.get_caption_llm()', body)

    def test_factory_maps_gemini_to_json_mode_for_seeding(self):
        source = (ROOT / "adapters" / "factory.py").read_text(encoding="utf-8")
        self.assertIn('def get_seeding_llm()', source)
        self.assertIn('llm_gemini.rewrite_json', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
