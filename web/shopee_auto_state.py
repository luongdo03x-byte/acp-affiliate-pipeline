"""Derived observability state for Shopee products in the existing Auto pipeline."""
from __future__ import annotations

from datetime import datetime, timezone

from ..core.shopee_auto_runtime import _shopee_snapshot_is_fresh

LIVE_AUTO_TARGET_STATUSES = ("SCHEDULED", "PENDING", "RUNNING")
REVIEW_POST_STATUSES = ("DRAFT", "PENDING_REVIEW")


def _row_get(row, key, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key] if key in row.keys() else default
    except (AttributeError, IndexError, KeyError):
        return default


def derive_auto_state(conn, product_row, *, now_utc=None) -> dict:
    """Return one read-only state derived from Product/Post/PublishTarget records."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    product_id = str(_row_get(product_row, "id") or "")
    enrichment_status = str(_row_get(product_row, "enrichment_status") or "")
    if enrichment_status != "READY":
        return {"state": "WAITING_IMAGE"}

    if not _shopee_snapshot_is_fresh(product_row, now_utc):
        return {"state": "STALE"}

    placeholders = ",".join("?" for _ in LIVE_AUTO_TARGET_STATUSES)
    scheduled = conn.execute(
        f"""
        SELECT pt.scheduled_at, ch.handle AS channel_handle
        FROM publish_target pt
        JOIN post p ON p.id=pt.post_id
        LEFT JOIN channel ch ON ch.id=pt.channel_id
        WHERE p.product_id=?
          AND COALESCE(pt.auto_scheduled, 0)=1
          AND pt.status IN ({placeholders})
        ORDER BY pt.scheduled_at IS NULL, pt.scheduled_at, pt.id
        LIMIT 1
        """,
        (product_id, *LIVE_AUTO_TARGET_STATUSES),
    ).fetchone()
    if scheduled:
        return {
            "state": "SCHEDULED",
            "channel_handle": _row_get(scheduled, "channel_handle"),
            "scheduled_at": _row_get(scheduled, "scheduled_at"),
        }

    review_placeholders = ",".join("?" for _ in REVIEW_POST_STATUSES)
    review = conn.execute(
        f"SELECT 1 FROM post WHERE product_id=? AND status IN ({review_placeholders}) LIMIT 1",
        (product_id, *REVIEW_POST_STATUSES),
    ).fetchone()
    if review:
        return {"state": "REVIEW"}

    published = conn.execute(
        "SELECT 1 FROM post WHERE product_id=? AND status='PUBLISHED' LIMIT 1",
        (product_id,),
    ).fetchone()
    if not published:
        published = conn.execute(
            """SELECT 1
               FROM publish_target pt
               JOIN post p ON p.id=pt.post_id
               WHERE p.product_id=? AND pt.status='SUCCESS'
               LIMIT 1""",
            (product_id,),
        ).fetchone()
    if published:
        return {"state": "PUBLISHED"}

    return {"state": "ELIGIBLE"}


def auto_summary(items) -> dict:
    counts = {
        "auto_eligible": 0,
        "auto_scheduled": 0,
        "auto_review": 0,
        "auto_stale": 0,
    }
    mapping = {
        "ELIGIBLE": "auto_eligible",
        "SCHEDULED": "auto_scheduled",
        "REVIEW": "auto_review",
        "STALE": "auto_stale",
    }
    for item in items or []:
        key = mapping.get(str(item.get("auto_state") or ""))
        if key:
            counts[key] += 1
    return counts
