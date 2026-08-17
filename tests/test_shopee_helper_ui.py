"""Static UI contracts for Shopee Metadata Helper Phase 2."""
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(relative):
    with open(os.path.join(REPO_ROOT, relative), encoding="utf-8") as handle:
        return handle.read()


class ShopeeHelperUiContractTests(unittest.TestCase):
    def test_server_metadata_states_remain_explicit(self):
        products = _read("web/templates/products.html")
        self.assertIn("metadata_state == 'AUTO_COMPLETE'", products)
        self.assertIn("metadata_state == 'AUTO_PARTIAL'", products)
        self.assertIn("metadata_state == 'BROWSER_HELPER_REQUIRED'", products)
        self.assertIn('id="helper-open-btn"', products)

    def test_manual_inputs_remain_available_when_helper_fails(self):
        products = _read("web/templates/products.html")
        for field in ("name", "current_price", "image_url"):
            marker = f'id="{field}"'
            self.assertIn(marker, products)
            start = products.index(marker)
            self.assertNotIn("disabled", products[start:start + 240])

    def test_manual_required_is_presentation_state_not_resolver_state(self):
        helper_js = _read("web/static/shopee_helper_state.js")
        adapter = _read("adapters/shopee_affiliate.py")
        self.assertIn('"MANUAL_REQUIRED"', helper_js)
        self.assertIn("Hết thời gian chờ", helper_js)
        self.assertNotIn('MANUAL_REQUIRED = "MANUAL_REQUIRED"', adapter)

    def test_base_loads_helper_state_module(self):
        base = _read("web/templates/base.html")
        self.assertIn("shopee_helper_state.js", base)


if __name__ == "__main__":
    unittest.main()
