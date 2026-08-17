"""Static UI contracts for Shopee Affiliate Phase 4 polish."""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(relative):
    with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
        return handle.read()


class ShopeePolishUiTests(unittest.TestCase):
    def test_confirmation_preview_is_explicit_and_never_auto_submits(self):
        body = read("web/static/shopee_polish.js")
        self.assertIn("Xem trước bài", body)
        self.assertIn("/sanpham/affiliate/preview", body)
        self.assertIn("Preview sơ bộ", body)
        self.assertNotIn("form.submit()", body)
        self.assertNotIn("requestSubmit", body)

    def test_review_polish_adds_live_count_context_badges_and_copy_links(self):
        body = read("web/static/shopee_polish.js")
        self.assertIn("/api/review/shopee-context", body)
        self.assertIn("caption.length", body)
        self.assertIn("Shopee Direct", body)
        self.assertIn("navigator.clipboard", body)
        self.assertIn("affiliate_url", body)
        self.assertIn("product_url", body)

    def test_polish_css_has_responsive_focus_and_preview_rules(self):
        css = read("web/static/shopee_polish.css")
        self.assertIn(".shopee-preview", css)
        self.assertIn(".review-card--polished", css)
        self.assertIn(":focus-visible", css)
        self.assertIn("@media", css)

    def test_base_loads_phase4_assets_after_existing_shopee_assets(self):
        base = read("web/templates/base.html")
        self.assertLess(base.index("shopee_product_intel.js"), base.index("shopee_polish.js"))
        self.assertIn("shopee_polish.css", base)

    def test_web_composition_registers_polish_after_product_intel(self):
        init = read("web/__init__.py")
        self.assertIn("register_shopee_polish", init)
        self.assertLess(init.index("register_shopee_product_intel(app)"),
                        init.index("register_shopee_polish(app)"))


if __name__ == "__main__":
    unittest.main()
