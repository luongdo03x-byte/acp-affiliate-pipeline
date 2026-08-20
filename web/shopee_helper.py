"""Request boundary hardening for the existing ACP Shopee Helper routes.

The legacy routes remain in ``web.server`` for compatibility.  This module is
registered by ``web.__init__`` and validates helper requests before those
routes run, keeping the large legacy app factory untouched.
"""
import ipaddress

from flask import abort, request

from ..core.shopee_helper import (
    ShopeeHelperError,
    canonical_helper_product,
    sanitize_helper_metadata,
)


HELPER_SUBMIT_PATH = "/api/helper/shopee-product"
HELPER_TOKEN_PATH = "/sanpham/affiliate/helper/token"
MAX_HELPER_BODY_BYTES = 16 * 1024
MAX_HELPER_TOKEN_LEN = 256


def _is_loopback_address(value) -> bool:
    if not value:
        return False
    try:
        address = ipaddress.ip_address(str(value))
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def _request_is_strict_loopback() -> bool:
    """Require both socket peer and ProxyFix-derived client to be loopback.

    ``web.server`` uses ProxyFix for ngrok.  Checking only ``request.remote_addr``
    would let a directly connected client spoof X-Forwarded-For; checking only
    the raw socket peer would let a public request forwarded by ngrok appear
    local.  Both must therefore resolve to loopback for the extension endpoint.
    """
    original = request.environ.get("werkzeug.proxy_fix.orig") or {}
    raw_peer = original.get("REMOTE_ADDR", request.environ.get("REMOTE_ADDR"))
    return _is_loopback_address(raw_peer) and _is_loopback_address(request.remote_addr)


def _validate_submit_request() -> None:
    if not _request_is_strict_loopback():
        abort(403)

    length = request.content_length
    if length is not None and length > MAX_HELPER_BODY_BYTES:
        abort(413)
    if not request.is_json:
        abort(400)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400)

    token = payload.get("token")
    product_url = payload.get("product_url")
    observed_url = payload.get("observed_url")
    metadata = payload.get("metadata")
    if (not isinstance(token, str) or not token or len(token) > MAX_HELPER_TOKEN_LEN or
            not isinstance(product_url, str) or not isinstance(observed_url, str) or
            not isinstance(metadata, dict)):
        abort(400)

    try:
        expected_canonical, expected_item = canonical_helper_product(product_url)
        observed_canonical, observed_item = canonical_helper_product(observed_url)
        sanitize_helper_metadata(metadata)
    except ShopeeHelperError:
        abort(400)

    # The old route passes product_url into helper_pairing.submit().  Pairing
    # then checks that value against the token-bound canonical identity.  This
    # preflight adds the missing active-tab proof: observed_url must identify
    # the very same product before the old route is allowed to consume token.
    if expected_canonical != observed_canonical or expected_item != observed_item:
        abort(410)


def register_shopee_helper_hardening(app) -> None:
    @app.before_request
    def _shopee_helper_request_boundary():
        if request.path == HELPER_TOKEN_PATH and request.method == "POST":
            product_url = request.form.get("product_url", "")
            try:
                canonical_helper_product(product_url)
            except ShopeeHelperError:
                abort(400)
            return None

        if request.path == HELPER_SUBMIT_PATH and request.method == "POST":
            _validate_submit_request()
        return None
