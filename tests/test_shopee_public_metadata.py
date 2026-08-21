import unittest

from acp.adapters.safe_http import SafeHttpError, SafeHttpResponse
from acp.adapters.shopee_affiliate import (
    AffiliateImportError,
    ProductMetadataResolver,
)


PRODUCT_URL = "https://shopee.vn/product/123/456"


class FakeHttp:
    def __init__(self, html=None, error=None):
        self.html = html or b"<html></html>"
        self.error = error
        self.urls = []

    def get(self, url, allowed_hosts=None, expected_content_prefix=None):
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return SafeHttpResponse(
            final_url=url,
            content=self.html,
            content_type="text/html",
        )


class ShopeePublicMetadataTests(unittest.TestCase):
    def test_resolve_public_reads_jsonld_product_image(self):
        html = b"""
        <html><head>
          <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": "San pham JSON-LD",
            "image": ["https://down-vn.img.susercontent.com/file/jsonld-image"],
            "offers": {"price": "125000"},
            "brand": {"name": "Shop JSON"}
          }
          </script>
        </head></html>
        """
        http = FakeHttp(html=html)

        metadata = ProductMetadataResolver(http=http).resolve_public(PRODUCT_URL)

        self.assertEqual(metadata.name, "San pham JSON-LD")
        self.assertEqual(metadata.current_price, 125_000)
        self.assertEqual(
            metadata.image_url,
            "https://down-vn.img.susercontent.com/file/jsonld-image",
        )
        self.assertEqual(metadata.shop, "Shop JSON")
        self.assertEqual(http.urls, [PRODUCT_URL])

    def test_resolve_public_reads_open_graph_image_without_private_fallback(self):
        html = b"""
        <html><head>
          <meta property="og:title" content="San pham OG">
          <meta property="og:image" content="https://down-vn.img.susercontent.com/file/og-image">
          <meta property="product:price:amount" content="99000">
        </head></html>
        """
        http = FakeHttp(html=html)

        metadata = ProductMetadataResolver(http=http).resolve(PRODUCT_URL)

        self.assertEqual(metadata.name, "San pham OG")
        self.assertEqual(metadata.current_price, 99_000)
        self.assertEqual(
            metadata.image_url,
            "https://down-vn.img.susercontent.com/file/og-image",
        )
        self.assertEqual(http.urls, [PRODUCT_URL])
        self.assertTrue(all("/api/v4/" not in url for url in http.urls))

    def test_resolve_with_no_image_returns_public_metadata_without_api_v4_requests(self):
        html = b"""
        <html><head>
          <meta property="og:title" content="Chi co ten">
        </head></html>
        """
        http = FakeHttp(html=html)

        metadata = ProductMetadataResolver(http=http).resolve(PRODUCT_URL)

        self.assertEqual(metadata.name, "Chi co ten")
        self.assertIsNone(metadata.image_url)
        self.assertEqual(http.urls, [PRODUCT_URL])
        self.assertTrue(all("/api/v4/" not in url for url in http.urls))

    def test_resolve_public_wraps_safe_http_failure(self):
        http = FakeHttp(error=SafeHttpError("Upstream HTTP 403"))

        with self.assertRaises(AffiliateImportError):
            ProductMetadataResolver(http=http).resolve_public(PRODUCT_URL)

        self.assertEqual(http.urls, [PRODUCT_URL])


if __name__ == "__main__":
    unittest.main()
