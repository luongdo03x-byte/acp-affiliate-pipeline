"""Pairing between /sanpham and ACP Shopee Helper (Chrome extension).

Shopee can block server-side metadata requests with CAPTCHA/403.  ACP therefore
lets the operator open the real Shopee product page and explicitly click the
helper extension.  The helper only transfers rendered product metadata back to
ACP; it does not automate Shopee or read authentication state.

Security boundaries:
  1. Pairing tokens are one-time, live for 300 seconds and are bound to one
     canonical Shopee product identity.
  2. The HTTP submit endpoint independently restricts callers to loopback.
  3. Submitted metadata is allowlisted and validated before the token is
     consumed.  A wrong/invalid tab never burns the token.
"""
import secrets
import threading
import time

from .shopee_helper import (
    ShopeeHelperError,
    canonical_helper_product,
    validate_helper_submission,
)


TTL_SECONDS = 300

_lock = threading.Lock()
_tokens = {}


def _gc(now: float) -> None:
    expired = [token for token, entry in _tokens.items()
               if now - entry["created_at"] > TTL_SECONDS]
    for token in expired:
        _tokens.pop(token, None)


def issue(product_url: str) -> dict:
    """Issue a one-time token bound to a concrete canonical Shopee product."""
    canonical_url, product_id = canonical_helper_product(product_url)
    token = secrets.token_urlsafe(32)
    current = time.monotonic()
    with _lock:
        _gc(current)
        _tokens[token] = {
            "product_url": canonical_url,
            "product_id": product_id,
            "created_at": current,
            "metadata": None,
            "consumed": False,
        }
    return {"token": token, "expires_in": TTL_SECONDS}


def submit(token: str, observed_product_url: str, metadata: dict) -> bool:
    """Accept validated metadata from the product tab observed by the helper.

    False is intentionally returned for every invalid-token, expiry, replay,
    product-mismatch or metadata-validation failure so the endpoint does not
    reveal token state.  Invalid submissions do not consume a still-valid
    token, allowing the operator to switch back to the correct product tab.
    """
    current = time.monotonic()
    with _lock:
        _gc(current)
        entry = _tokens.get(token)
        if not entry or entry["consumed"]:
            return False
        try:
            submission = validate_helper_submission(
                entry["product_url"], observed_product_url, metadata)
        except ShopeeHelperError:
            return False
        if submission.product_id != entry["product_id"]:
            return False
        entry["metadata"] = submission.metadata
        # "consumed" here means the extension can no longer submit/replay this
        # token.  Dashboard poll/consume may still read the validated metadata.
        entry["consumed"] = True
        return True


def poll(token: str):
    """Return pending/ready state until the short-lived pairing entry expires."""
    current = time.monotonic()
    with _lock:
        _gc(current)
        entry = _tokens.get(token)
        if not entry:
            return None
        if entry["metadata"] is None:
            return {"status": "pending"}
        return {"status": "ready", "metadata": dict(entry["metadata"])}


def consume_ready_for_product(token: str, product_url: str):
    """Atomically take ready metadata only when the token is bound to product_url.

    This is the server-side completion primitive for workflows that persist
    Helper metadata.  A wrong Product cannot reuse another Product's ready token,
    and a successful consume removes the token so completion cannot be replayed.
    """
    try:
        canonical_url, product_id = canonical_helper_product(product_url)
    except ShopeeHelperError:
        return None

    current = time.monotonic()
    with _lock:
        _gc(current)
        entry = _tokens.get(token)
        if not entry or entry["metadata"] is None:
            return None
        if entry["product_url"] != canonical_url or entry["product_id"] != product_id:
            return None
        metadata = dict(entry["metadata"])
        _tokens.pop(token, None)
        return metadata


def reset() -> None:
    """Clear in-memory pairing state. Intended for tests only."""
    with _lock:
        _tokens.clear()
