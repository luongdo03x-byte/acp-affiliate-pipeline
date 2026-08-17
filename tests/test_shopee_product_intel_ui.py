"""Static UI contracts for Shopee Product Intelligence Phase 3."""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(relative):
    with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
        return handle.read()


class ShopeeProductIntelUiTests(unittest.TestCase):
    def test_ui_tracks_metadata_source_and_manual_edits(self):
        body = read("web/static/shopee_product_intel.js")
        self.assertIn("metadata_source", body)
        self.assertIn('sourceInput.value = "manual"', body)
        self.assertIn('sourceInput.value = "helper"', body)
        self.assertIn("event.isTrusted", body)

    def test_ui_exposes_refresh_without_automatic_helper_click(self):
        body = read("web/static/shopee_product_intel.js")
        self.assertIn("Làm mới giá", body)
        self.assertIn("/sanpham/affiliate/refresh-price", body)
        self.assertIn("/sanpham/affiliate/cache", body)
        self.assertIn("helper_required", body)
        self.assertNotIn("helperButton.click()", body)

    def test_ui_labels_cached_data_as_not_realtime(self):
        body = read("web/static/shopee_product_intel.js")
        self.assertIn("không phải realtime", body)
        self.assertIn("observed_at", body)

    def test_base_loads_phase3_script_after_phase2_helper_state(self):
        base = read("web/templates/base.html")
        phase2 = base.index("shopee_helper_state.js")
        phase3 = base.index("shopee_product_intel.js")
        self.assertLess(phase2, phase3)


if __name__ == "__main__":
    unittest.main()
