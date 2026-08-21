"""Short-lived one-time preview batches for Shopee Affiliate CSV imports.

Only normalized rows and aggregate summaries are retained. Raw CSV bytes are
never stored here. The process-local store intentionally expires on restart.
"""
from __future__ import annotations

from copy import deepcopy
import secrets
import threading
import time


PREVIEW_TTL_SECONDS = 900

_store = {}
_lock = threading.Lock()


def _now(now_ts=None) -> float:
    return time.monotonic() if now_ts is None else float(now_ts)


def _purge_expired(now_ts: float) -> None:
    expired = [token for token, batch in _store.items() if now_ts >= batch["expires_at"]]
    for token in expired:
        _store.pop(token, None)


def issue_preview(rows, summary, *, now_ts=None) -> dict:
    current = _now(now_ts)
    token = secrets.token_urlsafe(32)
    batch = {
        "rows": deepcopy(list(rows or [])),
        "summary": deepcopy(dict(summary or {})),
        "expires_at": current + PREVIEW_TTL_SECONDS,
    }
    with _lock:
        _purge_expired(current)
        while token in _store:
            token = secrets.token_urlsafe(32)
        _store[token] = batch
    return {"token": token, "expires_in": PREVIEW_TTL_SECONDS}


def peek_preview(token: str, *, now_ts=None) -> dict | None:
    if not token:
        return None
    current = _now(now_ts)
    with _lock:
        _purge_expired(current)
        batch = _store.get(str(token))
        return deepcopy(batch) if batch is not None else None


def consume_preview(token: str, *, now_ts=None) -> dict | None:
    if not token:
        return None
    current = _now(now_ts)
    with _lock:
        _purge_expired(current)
        batch = _store.pop(str(token), None)
        return deepcopy(batch) if batch is not None else None


def reset_previews() -> None:
    """Test seam: clear this process's pending preview batches."""
    with _lock:
        _store.clear()
