"""Phase 4 Shopee preview/review polish and evidence-based observability.

This module composes around existing routes. It never owns approve/publish state
transitions and never logs raw affiliate URLs, tokens, cookies or provider bodies.
"""
import re

from flask import request

from ..adapters.safe_http import SafeHttpError
from ..core.db import connect
from ..core.shopee_observability import ShopeeObservabilityError, record_shopee_event


_CAPTCHA_MARKERS = (b"captcha", b"shopee captcha")
_PRODUCT_HIDDEN_RE = re.compile(r'name="product_url"\s+value="([^"]+)"')


class ObservedShopeeHttpClient:
    """Transparent SafeHttpClient proxy that emits only evidence-backed events."""

    def __init__(self, delegate, event_callback):
        self.delegate = delegate
        self.event_callback = event_callback

    def validate_url(self, url, allowed_hosts=None):
        return self.delegate.validate_url(url, allowed_hosts)

    def get(self, url, allowed_hosts=None, expected_content_prefix=None):
        try:
            response = self.delegate.get(
                url, allowed_hosts=allowed_hosts,
                expected_content_prefix=expected_content_prefix)
        except SafeHttpError as exc:
            if "/api/v4/" in str(url) and "Upstream HTTP 403" in str(exc):
                self.event_callback(url, "json_api_403", {"http_status": 403})
            raise

        if ((response.content_type or "").startswith("text/html") and
                any(marker in (response.content or b"").lower() for marker in _CAPTCHA_MARKERS)):
            self.event_callback(url, "html_captcha", {"state": "captcha"})
        return response


def _metadata_fields(metadata):
    fields = []
    for field in ("name", "current_price", "original_price", "image_url", "shop"):
        if getattr(metadata, field, None) not in (None, ""):
            fields.append(field)
    return fields


def _record_event(product_url, action, detail=None, actor="system"):
    conn = connect()
    try:
        record_shopee_event(conn, product_url, action, detail=detail, actor=actor)
    except ShopeeObservabilityError:
        pass
    finally:
        conn.close()


def _instrument_source(source):
    raw = getattr(source, "_base", source)
    if getattr(raw, "_acp_shopee_observed", False):
        return source
    if not all(hasattr(raw, name) for name in ("http", "url_resolver", "metadata_resolver")):
        return source

    observed_http = ObservedShopeeHttpClient(raw.http, _record_event)
    raw.http = observed_http
    raw.url_resolver.http = observed_http
    raw.metadata_resolver.http = observed_http

    original_html = raw.metadata_resolver._html_metadata

    def observed_html(product_url):
        metadata = original_html(product_url)
        fields = _metadata_fields(metadata)
        if fields:
            _record_event(product_url, "html_metadata_success", {
                "source": "server", "metadata_fields": fields,
            })
        return metadata

    raw.metadata_resolver._html_metadata = observed_html
    raw._acp_shopee_observed = True
    return source


def _response_json(response):
    try:
        return response.get_json(silent=True) or {}
    except Exception:
        return {}


def _audit_after_request(response):
    path = request.path
    if path == "/api/helper/shopee-product" and request.method == "POST" and response.status_code == 200:
        payload = request.get_json(silent=True) or {}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        _record_event(payload.get("product_url", ""), "helper_metadata_success", {
            "source": "helper",
            "state": "ready",
            "metadata_fields": [key for key in metadata if key in
                                ("name", "current_price", "original_price", "image_url", "shop")],
        }, actor="operator")

    elif path == "/sanpham/affiliate/cache" and request.method == "GET" and response.status_code == 200:
        data = _response_json(response)
        if data.get("status") in ("fresh", "stale"):
            _record_event(request.args.get("product_url", ""),
                          "cache_hit" if data["status"] == "fresh" else "cache_stale",
                          {"source": data.get("source"), "state": data.get("status")})

    elif path == "/sanpham/affiliate/refresh-price" and request.method == "POST":
        data = _response_json(response)
        success = response.status_code == 200 and data.get("status") in ("server", "cache")
        _record_event(request.form.get("product_url", ""),
                      "price_refresh_success" if success else "price_refresh_failed",
                      {"source": data.get("source"), "state": data.get("status"),
                       "error_category": None if success else "metadata_unavailable"},
                      actor="operator")

    elif path == "/sanpham/affiliate/create" and request.method == "POST":
        location = response.headers.get("Location", "")
        if response.status_code in (301, 302, 303, 307, 308) and location.endswith("/duyet"):
            if request.form.get("metadata_source", "manual") == "manual":
                _record_event(request.form.get("product_url", ""), "manual_fallback",
                              {"source": "manual", "state": "confirmed"}, actor="operator")

    elif path == "/sanpham/affiliate/resolve" and request.method == "POST" and response.status_code == 200:
        if (response.content_type or "").startswith("text/html"):
            text = response.get_data(as_text=True)
            match = _PRODUCT_HIDDEN_RE.search(text)
            if match:
                product_url = match.group(1)
                _record_event(product_url, "resolve_success", {"state": "resolved"}, actor="operator")
                _record_event(product_url, "canonicalized", {"state": "canonical"}, actor="operator")
    return response


def register_shopee_polish(app) -> None:
    if app.config.get("SHOPEE_POLISH_REGISTERED"):
        return
    app.config["SHOPEE_POLISH_REGISTERED"] = True

    base_factory = app.config["SHOPEE_SOURCE_FACTORY"]

    def observed_source_factory():
        return _instrument_source(base_factory())

    app.config["SHOPEE_SOURCE_FACTORY"] = observed_source_factory
    app.after_request(_audit_after_request)
