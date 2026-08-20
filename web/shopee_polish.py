"""Phase 4 Shopee preview/review polish and evidence-based observability.

This module composes around existing routes. It never owns approve/publish state
transitions and never logs raw affiliate URLs, tokens, cookies or provider bodies.
"""
import json
import re
from urllib.parse import urlsplit

from flask import jsonify, request

from ..adapters.safe_http import SafeHttpError
from ..core import content
from ..core.db import connect
from ..core.shopee_helper import ShopeeHelperError, sanitize_helper_metadata
from ..core.shopee_observability import ShopeeObservabilityError, record_shopee_event
from ..core.shopee_products import CACHE_SOURCES, ShopeeProductError, identity_from_url


_CAPTCHA_MARKERS = (b"captcha", b"shopee captcha")
_PRODUCT_HIDDEN_RE = re.compile(r'name="product_url"\s+value="([^"]+)"')
_AFFILIATE_HOSTS = {"shopee.vn", "www.shopee.vn", "s.shopee.vn"}


class ShopeePreviewError(ValueError):
    """Safe operator-facing validation error for non-persisted preview."""


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


def _validate_affiliate_url(value: str) -> str:
    if not isinstance(value, str):
        raise ShopeePreviewError("Affiliate link không hợp lệ.")
    text = value.strip()
    if not text or len(text) > 4096 or any(ord(ch) < 32 for ch in text):
        raise ShopeePreviewError("Affiliate link không hợp lệ.")
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as exc:
        raise ShopeePreviewError("Affiliate link không hợp lệ.") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or host not in _AFFILIATE_HOSTS:
        raise ShopeePreviewError("Chỉ chấp nhận affiliate link Shopee HTTPS.")
    if parsed.username is not None or parsed.password is not None or port not in (None, 443):
        raise ShopeePreviewError("Affiliate link không hợp lệ.")
    return text


def _fmt_vnd(value: int) -> str:
    return f"{int(value):,}đ".replace(",", ".")


def build_preliminary_preview(*, name, current_price, original_price, image_url,
                              affiliate_url, product_url, channels, metadata_source):
    """Build a factual, non-persisted preview; final pipeline caption may differ."""
    try:
        amount = int(current_price)
    except (TypeError, ValueError) as exc:
        raise ShopeePreviewError("Giá hiện tại không hợp lệ.") from exc
    if amount <= 0:
        raise ShopeePreviewError("Giá hiện tại phải lớn hơn 0.")
    original = None
    if original_price not in (None, ""):
        try:
            original = int(original_price)
        except (TypeError, ValueError) as exc:
            raise ShopeePreviewError("Giá gốc không hợp lệ.") from exc
        if original <= 0:
            original = None

    try:
        identity = identity_from_url(product_url)
        clean = sanitize_helper_metadata({
            "name": name,
            "current_price": amount,
            "original_price": original,
            "image_url": image_url,
            "shop": None,
        })
    except (ShopeeProductError, ShopeeHelperError) as exc:
        raise ShopeePreviewError(str(exc)) from exc
    if not clean["name"] or not clean["image_url"]:
        raise ShopeePreviewError("Preview cần tên và ảnh sản phẩm.")
    affiliate = _validate_affiliate_url(affiliate_url)
    if metadata_source not in CACHE_SOURCES:
        metadata_source = "manual"
    if not isinstance(channels, list) or not channels:
        raise ShopeePreviewError("Chọn ít nhất một kênh để xem preview.")

    safe_channels = []
    for channel in channels:
        if not isinstance(channel, dict) or not channel.get("code") or not channel.get("handle"):
            raise ShopeePreviewError("Kênh preview không hợp lệ.")
        safe_channels.append({
            "code": str(channel["code"])[:80],
            "handle": str(channel["handle"])[:120],
            "platform": str(channel.get("platform") or "threads")[:32],
        })

    disclosure = content.DISCLOSURE_DEFAULT
    tail = f"\n\nXem sản phẩm:\n{affiliate}\n\n{disclosure}"
    if len(tail) >= 500:
        raise ShopeePreviewError("Affiliate link quá dài để tạo caption preview an toàn.")
    body = f"{clean['name'][:120]}\n\nGiá hiện tại {_fmt_vnd(amount)}."
    budget = 500 - len(tail)
    if len(body) > budget:
        body = body[:budget].rstrip()
    caption = body + tail

    return {
        "preliminary": True,
        "caption": caption,
        "disclosure": disclosure,
        "name": clean["name"],
        "current_price": amount,
        "original_price": original,
        "image_url": clean["image_url"],
        "affiliate_url": affiliate,
        "product_url": identity.canonical_url,
        "channels": safe_channels,
        "metadata_source": metadata_source,
        "warnings": ["Preview sơ bộ; caption cuối vẫn do pipeline tạo và kiểm tra lại trước /duyet."],
    }


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

    @app.post("/sanpham/affiliate/preview")
    def shopee_affiliate_preview():
        codes = [code for code in request.form.getlist("channel_codes") if code]
        if not codes:
            return jsonify(error="Chọn ít nhất một kênh để xem preview."), 400
        placeholders = ",".join("?" for _ in codes)
        conn = connect()
        try:
            rows = conn.execute(
                f"SELECT code, handle, platform FROM channel WHERE status='ACTIVE' AND enabled=1 AND code IN ({placeholders})",
                codes).fetchall()
        finally:
            conn.close()
        by_code = {row["code"]: dict(row) for row in rows}
        if any(code not in by_code for code in codes):
            return jsonify(error="Có kênh không còn hoạt động."), 400
        channels = [by_code[code] for code in codes]
        try:
            preview = build_preliminary_preview(
                name=request.form.get("name", ""),
                current_price=request.form.get("current_price", ""),
                original_price=request.form.get("original_price", ""),
                image_url=request.form.get("image_url", ""),
                affiliate_url=request.form.get("affiliate_url", ""),
                product_url=request.form.get("product_url", ""),
                channels=channels,
                metadata_source=request.form.get("metadata_source", "manual"),
            )
        except ShopeePreviewError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(preview)

    @app.get("/api/review/shopee-context")
    def shopee_review_context():
        post_id = request.args.get("post_id", "").strip()
        if not post_id or len(post_id) > 80:
            return jsonify(error="post_id không hợp lệ"), 400
        conn = connect()
        try:
            row = conn.execute("""SELECT p.id, p.post_type, p.affiliate_link, p.sub_id_payload,
                                         pr.product_url, pr.source, pr.merchant, pr.external_product_id
                                  FROM post p LEFT JOIN product pr ON pr.id=p.product_id
                                  WHERE p.id=?""", (post_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            return jsonify(error="Không tìm thấy bài"), 404
        if row["source"] != "manual_shopee" or row["merchant"] != "shopee.vn":
            return jsonify(shopee=False)
        attribution = {}
        try:
            attribution = json.loads(row["sub_id_payload"] or "{}")
        except (TypeError, ValueError):
            pass
        try:
            canonical = identity_from_url(row["product_url"]).canonical_url
            affiliate = _validate_affiliate_url(row["affiliate_link"])
        except (ShopeeProductError, ShopeePreviewError):
            return jsonify(shopee=True, safe_links=False)
        return jsonify(
            shopee=True,
            safe_links=True,
            product_url=canonical,
            affiliate_url=affiliate,
            source="shopee_direct",
            link_mode=attribution.get("link_mode") if isinstance(attribution, dict) else None,
        )

    app.after_request(_audit_after_request)
