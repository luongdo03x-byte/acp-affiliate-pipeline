"""Evidence-based HTTP instrumentation tests for Shopee observability."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class _Delegate:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    def validate_url(self, url, allowed_hosts=None):
        return url

    def get(self, url, allowed_hosts=None, expected_content_prefix=None):
        if self.error:
            raise self.error
        return self.response


class ShopeeTransportInstrumentationTests(unittest.TestCase):
    def test_api_403_emits_json_api_403(self):
        from acp.adapters.safe_http import SafeHttpError
        from acp.web.shopee_polish import ObservedShopeeHttpClient

        events = []
        client = ObservedShopeeHttpClient(
            _Delegate(error=SafeHttpError("Upstream HTTP 403")),
            lambda url, action, detail=None: events.append((url, action, detail)),
        )
        with self.assertRaises(SafeHttpError):
            client.get(
                "https://shopee.vn/api/v4/item/get?shopid=123&itemid=456",
                allowed_hosts={"shopee.vn"}, expected_content_prefix="application/json")
        self.assertEqual(events[0][1], "json_api_403")
        self.assertEqual(events[0][2], {"http_status": 403})

    def test_html_with_explicit_captcha_marker_emits_html_captcha(self):
        from acp.adapters.safe_http import SafeHttpResponse
        from acp.web.shopee_polish import ObservedShopeeHttpClient

        events = []
        response = SafeHttpResponse(
            "https://shopee.vn/product/123/456",
            b"<html><title>Verify</title><div id='captcha'>captcha</div></html>",
            "text/html",
        )
        client = ObservedShopeeHttpClient(
            _Delegate(response=response),
            lambda url, action, detail=None: events.append((url, action, detail)),
        )
        got = client.get("https://shopee.vn/product/123/456", expected_content_prefix="text/html")
        self.assertIs(got, response)
        self.assertEqual([event[1] for event in events], ["html_captcha"])

    def test_generic_network_error_does_not_fake_captcha_or_403(self):
        from acp.adapters.safe_http import SafeHttpError
        from acp.web.shopee_polish import ObservedShopeeHttpClient

        events = []
        client = ObservedShopeeHttpClient(
            _Delegate(error=SafeHttpError("Không thể kết nối tới URL")),
            lambda url, action, detail=None: events.append((url, action, detail)),
        )
        with self.assertRaises(SafeHttpError):
            client.get("https://shopee.vn/product/123/456", expected_content_prefix="text/html")
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
