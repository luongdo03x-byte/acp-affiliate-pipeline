"""Shopee Affiliate image-enrichment job state.

This module owns only the bounded post-import image-enrichment lifecycle.  It
never logs in to Shopee, reads browser credentials, solves CAPTCHA, calls
private Shopee endpoints, creates posts, or publishes content.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from .db import now
from .shopee_products import ShopeeProductError, identity_from_url


PROVIDER = "SHOPEE_AFFILIATE"
PENDING = "PENDING"
PUBLIC_FETCH = "PUBLIC_FETCH"
DOWNLOADING = "DOWNLOADING"
NEEDS_HELPER = "NEEDS_HELPER"
READY = "READY"
FAILED = "FAILED"
STATUSES = frozenset({PENDING, PUBLIC_FETCH, DOWNLOADING, NEEDS_HELPER, READY, FAILED})
TRANSIENT_STATUSES = frozenset({PUBLIC_FETCH, DOWNLOADING})
STALE_SECONDS = 10 * 60
MAX_PUBLIC_ATTEMPTS = 2
MAX_DOWNLOAD_ATTEMPTS = 2
MAX_BATCH_SIZE = 20
DEFAULT_DELAY_SECONDS = 1.5


class ShopeeImageEnrichmentError(ValueError):
    """Safe bounded enrichment error suitable for operator-facing handling."""

    def __init__(self, code: str, message: str):
        self.code = str(code)
        self.user_message = str(message)
        super().__init__(self.user_message)


def _product(conn, product_id: str):
    return conn.execute("SELECT * FROM product WHERE id=?", (str(product_id),)).fetchone()


def _has_product_image(product) -> bool:
    if product is None:
        return False
    if str(product["main_image_url"] or "").strip():
        return True
    local_path = str(product["image_path_local"] or "").strip()
    return bool(local_path and os.path.isfile(local_path))


def _eligible_identity(product):
    if product is None or str(product["provider"] or "") != PROVIDER:
        return None
    try:
        return identity_from_url(product["product_url"])
    except (ShopeeProductError, TypeError, ValueError) as exc:
        raise ShopeeImageEnrichmentError(
            "PRODUCT_IDENTITY_INVALID",
            "Link sản phẩm Shopee không có shop/item hợp lệ để enrich ảnh.",
        ) from exc


def get_job(conn, product_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM shopee_image_enrichment_job WHERE product_id=?",
        (str(product_id),),
    ).fetchone()
    return dict(row) if row is not None else None


def enqueue_product(conn, product_id: str) -> str | None:
    """Idempotently enroll one eligible Shopee Affiliate Product."""
    product = _product(conn, product_id)
    if product is None or str(product["provider"] or "") != PROVIDER:
        return None
    _eligible_identity(product)

    desired = READY if _has_product_image(product) else PENDING
    timestamp = now()
    existing = get_job(conn, product_id)
    if existing is None:
        conn.execute(
            """INSERT INTO shopee_image_enrichment_job (
                 product_id, status, attempt_count, download_attempt_count,
                 last_error_code, last_error, last_attempt_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (product_id, desired, 0, 0, None, None, None, timestamp, timestamp),
        )
        return desired

    if desired == READY and existing["status"] != READY:
        conn.execute(
            """UPDATE shopee_image_enrichment_job
               SET status=?, last_error_code=NULL, last_error=NULL, updated_at=?
               WHERE product_id=?""",
            (READY, timestamp, product_id),
        )
        return READY

    # Missing-image re-enqueue never resets retry/error/helper state.  Explicit
    # Retry owns that transition later; repeated CSV imports stay idempotent.
    return existing["status"]


def backfill_missing(conn, limit: int | None = None) -> int:
    """Enroll pre-feature Shopee Products that still have no usable image."""
    sql = "SELECT id FROM product WHERE provider=? ORDER BY created_at, id"
    params: list[object] = [PROVIDER]
    if limit is not None:
        safe_limit = max(0, int(limit))
        sql += " LIMIT ?"
        params.append(safe_limit)

    count = 0
    for row in conn.execute(sql, tuple(params)).fetchall():
        product = _product(conn, row["id"])
        if _has_product_image(product):
            continue
        status = enqueue_product(conn, row["id"])
        if status is not None:
            count += 1
    return count


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def recover_stale_jobs(conn, *, now_dt: datetime | None = None) -> int:
    """Recover transient work left behind by a process restart/crash."""
    current = now_dt or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    threshold = current - timedelta(seconds=STALE_SECONDS)
    recovered = 0

    rows = conn.execute(
        "SELECT * FROM shopee_image_enrichment_job WHERE status IN (?,?)",
        (PUBLIC_FETCH, DOWNLOADING),
    ).fetchall()
    for row in rows:
        updated = _parse_timestamp(row["updated_at"])
        if updated is not None and updated > threshold:
            continue
        product = _product(conn, row["product_id"])
        status = READY if _has_product_image(product) else PENDING
        conn.execute(
            """UPDATE shopee_image_enrichment_job
               SET status=?, last_error_code=NULL, last_error=NULL, updated_at=?
               WHERE product_id=?""",
            (status, now(), row["product_id"]),
        )
        recovered += 1
    return recovered
