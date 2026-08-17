"""Sanitized audit events for Shopee Direct workflows.

Only canonical product identity and a small allowlist of diagnostic fields may
reach `audit_log`. Full affiliate URLs, pairing tokens, cookies, raw provider
responses and browser/session data are deliberately not accepted.
"""
from .db import audit
from .shopee_products import ShopeeProductError, identity_from_url


ACTIONS = frozenset({
    "resolve_success",
    "canonicalized",
    "html_metadata_success",
    "html_captcha",
    "json_api_403",
    "helper_metadata_success",
    "cache_hit",
    "cache_stale",
    "manual_fallback",
    "price_refresh_success",
    "price_refresh_failed",
})
DETAIL_KEYS = frozenset({
    "source", "state", "error_category", "http_status", "metadata_fields", "price_changed",
})
METADATA_FIELDS = frozenset({"name", "current_price", "original_price", "image_url", "shop"})


class ShopeeObservabilityError(ValueError):
    """Invalid product identity/action for a Shopee audit event."""


def _short_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text[:64] if text else None


def _sanitize_detail(detail) -> dict:
    if not isinstance(detail, dict):
        return {}
    clean = {}
    for key in DETAIL_KEYS:
        if key not in detail:
            continue
        value = detail[key]
        if key in ("source", "state", "error_category"):
            text = _short_text(value)
            if text is not None:
                clean[key] = text
        elif key == "http_status":
            try:
                status = int(value)
            except (TypeError, ValueError):
                continue
            if 100 <= status <= 599:
                clean[key] = status
        elif key == "price_changed":
            if isinstance(value, bool):
                clean[key] = value
        elif key == "metadata_fields" and isinstance(value, (list, tuple, set)):
            fields = []
            for item in value:
                if isinstance(item, str) and item in METADATA_FIELDS and item not in fields:
                    fields.append(item)
            clean[key] = fields
    return clean


def record_shopee_event(conn, product_url: str, action: str, *, detail=None,
                        actor: str = "system") -> None:
    if action not in ACTIONS:
        raise ShopeeObservabilityError("Shopee audit action không hợp lệ.")
    try:
        identity = identity_from_url(product_url)
    except ShopeeProductError as exc:
        raise ShopeeObservabilityError("Không nhận diện được Shopee product cho audit.") from exc

    audit(
        conn,
        "shopee_product",
        f"{identity.shop_id}:{identity.item_id}",
        action,
        actor=_short_text(actor) or "system",
        detail=_sanitize_detail(detail),
    )
