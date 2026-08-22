"""Read-only projection for the Shopee Affiliate Product Pool workspace.

The projection deliberately derives operational state from existing Product,
image-enrichment, Post, PublishTarget and Channel records.  It does not create a
second mutable Auto state table and never changes scheduler/publisher state.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from . import niche
from .shopee_auto_runtime import (
    SHOPEE_PROVIDER,
    _shopee_product_auto_eligibility,
    _shopee_snapshot_is_fresh,
)

DEFAULT_PER_PAGE = 20
ALLOWED_PER_PAGE = (20, 50, 100)
AUTO_FILTERS = frozenset({
    "all", "eligible", "waiting_image", "stale", "ineligible",
    "review", "scheduled", "published",
})
IMAGE_FILTERS = frozenset({
    "all", "ready", "missing", "needs_helper", "failed", "pending",
})
USAGE_FILTERS = frozenset({"all", "unused", "scheduled", "review", "published"})
LIVE_AUTO_TARGET_STATUSES = ("SCHEDULED", "PENDING", "RUNNING")
REVIEW_POST_STATUSES = ("DRAFT", "PENDING_REVIEW")
PENDING_IMAGE_STATUSES = frozenset({"PENDING", "PUBLIC_FETCH", "DOWNLOADING", "UNQUEUED"})


def _clean_choice(value, allowed, default="all") -> str:
    text = str(value or default).strip().lower()
    return text if text in allowed else default


def _positive_int(value, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def normalize_filters(values) -> dict:
    """Normalize untrusted query parameters to the documented filter contract."""
    values = values or {}
    q = str(values.get("q") or "").strip()[:200]
    niche_code = str(values.get("niche") or "all").strip()
    if niche_code != "all" and niche_code not in niche.NICHES:
        niche_code = "all"
    per_page = _positive_int(values.get("per_page"), DEFAULT_PER_PAGE)
    if per_page not in ALLOWED_PER_PAGE:
        per_page = DEFAULT_PER_PAGE
    return {
        "q": q,
        "niche": niche_code,
        "auto": _clean_choice(values.get("auto"), AUTO_FILTERS),
        "image": _clean_choice(values.get("image"), IMAGE_FILTERS),
        "usage": _clean_choice(values.get("usage"), USAGE_FILTERS),
        "page": _positive_int(values.get("page"), 1),
        "per_page": per_page,
    }


def _all_products(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT p.*,
               COALESCE(j.status, 'UNQUEUED') AS enrichment_status,
               j.last_error_code AS enrichment_error_code,
               j.last_error AS enrichment_error,
               j.updated_at AS enrichment_updated_at
        FROM product p
        LEFT JOIN shopee_image_enrichment_job j ON j.product_id=p.id
        WHERE p.provider=?
        ORDER BY COALESCE(p.last_synced_at, p.updated_at) DESC, p.id DESC
        """,
        (SHOPEE_PROVIDER,),
    ).fetchall()
    return [dict(row) for row in rows]


def _active_auto_channels(conn):
    return conn.execute(
        """
        SELECT *
        FROM channel
        WHERE platform='threads'
          AND enabled=1
          AND status='ACTIVE'
          AND auto_schedule_enabled=1
        ORDER BY handle, id
        """
    ).fetchall()


def _usage_state(conn, product_id: str) -> dict:
    published = conn.execute(
        """
        SELECT p.published_at, ch.handle AS channel_handle
        FROM post p
        LEFT JOIN publish_target pt ON pt.post_id=p.id
        LEFT JOIN channel ch ON ch.id=COALESCE(pt.channel_id, p.channel_id)
        WHERE p.product_id=?
          AND (p.status='PUBLISHED' OR pt.status='SUCCESS')
        ORDER BY p.published_at IS NULL, p.published_at DESC, p.id DESC
        LIMIT 1
        """,
        (product_id,),
    ).fetchone()
    if published:
        return {
            "state": "PUBLISHED",
            "channel_handle": published["channel_handle"],
            "scheduled_at": None,
            "published_at": published["published_at"],
        }

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
            "channel_handle": scheduled["channel_handle"],
            "scheduled_at": scheduled["scheduled_at"],
            "published_at": None,
        }

    review_placeholders = ",".join("?" for _ in REVIEW_POST_STATUSES)
    review = conn.execute(
        f"""SELECT 1
            FROM post
            WHERE product_id=? AND status IN ({review_placeholders})
            LIMIT 1""",
        (product_id, *REVIEW_POST_STATUSES),
    ).fetchone()
    if review:
        return {
            "state": "REVIEW",
            "channel_handle": None,
            "scheduled_at": None,
            "published_at": None,
        }
    return {
        "state": "UNUSED",
        "channel_handle": None,
        "scheduled_at": None,
        "published_at": None,
    }


def _matching_niches(product) -> list[str]:
    return [
        code
        for code in niche.NICHES
        if not niche.match_reasons(product, [code])
    ]


def _auto_state(conn, product, usage: dict, channels, now_utc: datetime) -> dict:
    usage_state = usage["state"]
    if usage_state != "UNUSED":
        return {
            "state": usage_state,
            "channel_handle": usage.get("channel_handle"),
            "scheduled_at": usage.get("scheduled_at"),
        }

    if str(product.get("enrichment_status") or "") != "READY":
        return {"state": "WAITING_IMAGE", "channel_handle": None, "scheduled_at": None}
    if not _shopee_snapshot_is_fresh(product, now_utc):
        return {"state": "STALE", "channel_handle": None, "scheduled_at": None}

    for channel in channels:
        eligible, _reason = _shopee_product_auto_eligibility(
            conn,
            product,
            channel,
            now_utc,
            require_auto_schedule=True,
        )
        if eligible:
            return {
                "state": "ELIGIBLE",
                "channel_handle": channel["handle"],
                "scheduled_at": None,
            }
    return {"state": "INELIGIBLE", "channel_handle": None, "scheduled_at": None}


def _project_products(conn, products, *, now_utc: datetime) -> list[dict]:
    channels = _active_auto_channels(conn)
    projected = []
    for product in products:
        item = dict(product)
        usage = _usage_state(conn, str(item["id"]))
        auto = _auto_state(conn, item, usage, channels, now_utc)
        item["usage_state"] = usage["state"]
        item["auto_state"] = auto["state"]
        item["auto_channel_handle"] = auto.get("channel_handle")
        item["auto_scheduled_at"] = auto.get("scheduled_at")
        item["published_at_effective"] = usage.get("published_at")
        item["niche_codes"] = _matching_niches(item)
        projected.append(item)
    return projected


def _empty_summary() -> dict:
    return {
        "total": 0,
        "unused": 0,
        "auto_eligible": 0,
        "scheduled": 0,
        "published": 0,
        "review": 0,
        "ready": 0,
        "missing": 0,
        "needs_helper": 0,
        "failed": 0,
        "pending": 0,
        "stale": 0,
        # Compatibility aliases used by the pre-v2 template/tests.
        "auto_scheduled": 0,
        "auto_review": 0,
        "auto_stale": 0,
    }


def _summarize(items) -> dict:
    summary = _empty_summary()
    for item in items:
        summary["total"] += 1
        usage = item["usage_state"]
        auto = item["auto_state"]
        image = str(item.get("enrichment_status") or "UNQUEUED")
        if usage == "UNUSED":
            summary["unused"] += 1
        elif usage == "SCHEDULED":
            summary["scheduled"] += 1
        elif usage == "REVIEW":
            summary["review"] += 1
        elif usage == "PUBLISHED":
            summary["published"] += 1
        if auto == "ELIGIBLE":
            summary["auto_eligible"] += 1
        if auto == "STALE":
            summary["stale"] += 1
        if image == "READY":
            summary["ready"] += 1
        else:
            summary["missing"] += 1
        if image == "NEEDS_HELPER":
            summary["needs_helper"] += 1
        elif image == "FAILED":
            summary["failed"] += 1
        elif image in PENDING_IMAGE_STATUSES:
            summary["pending"] += 1
    summary["auto_scheduled"] = summary["scheduled"]
    summary["auto_review"] = summary["review"]
    summary["auto_stale"] = summary["stale"]
    return summary


def _niche_stats(items) -> list[dict]:
    stats = []
    for code, definition in niche.NICHES.items():
        matched = [item for item in items if code in item["niche_codes"]]
        summary = _summarize(matched)
        stats.append({
            "code": code,
            "name": definition["name"],
            **summary,
        })
    return stats


def _matches_filters(item: dict, filters: dict) -> bool:
    query = filters["q"].casefold()
    if query:
        haystack = " ".join((
            str(item.get("name") or ""),
            str(item.get("shop_name") or ""),
        )).casefold()
        if query not in haystack:
            return False

    niche_filter = filters["niche"]
    if niche_filter != "all" and niche_filter not in item["niche_codes"]:
        return False

    auto_filter = filters["auto"]
    if auto_filter != "all" and item["auto_state"] != auto_filter.upper():
        return False

    usage_filter = filters["usage"]
    if usage_filter != "all" and item["usage_state"] != usage_filter.upper():
        return False

    image_filter = filters["image"]
    image_state = str(item.get("enrichment_status") or "UNQUEUED")
    if image_filter == "ready" and image_state != "READY":
        return False
    if image_filter == "missing" and image_state == "READY":
        return False
    if image_filter == "needs_helper" and image_state != "NEEDS_HELPER":
        return False
    if image_filter == "failed" and image_state != "FAILED":
        return False
    if image_filter == "pending" and image_state not in PENDING_IMAGE_STATUSES:
        return False
    return True


def _page_params(filters: dict, page: int) -> dict:
    return {
        "q": filters["q"],
        "niche": filters["niche"],
        "auto": filters["auto"],
        "image": filters["image"],
        "usage": filters["usage"],
        "per_page": filters["per_page"],
        "page": page,
    }


def build_product_pool(conn, values=None, *, now_utc=None) -> dict:
    """Build one globally consistent Product Pool view and a paginated slice."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_utc = now_utc.astimezone(timezone.utc)

    filters = normalize_filters(values)
    all_items = _project_products(conn, _all_products(conn), now_utc=now_utc)
    summary = _summarize(all_items)
    niche_stats = _niche_stats(all_items)
    filtered = [item for item in all_items if _matches_filters(item, filters)]

    total_filtered = len(filtered)
    total_pages = max(1, math.ceil(total_filtered / filters["per_page"]))
    page = min(max(1, filters["page"]), total_pages)
    filters["page"] = page
    start = (page - 1) * filters["per_page"]
    end = start + filters["per_page"]

    pagination = {
        "page": page,
        "per_page": filters["per_page"],
        "total_filtered": total_filtered,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_params": _page_params(filters, max(1, page - 1)),
        "next_params": _page_params(filters, min(total_pages, page + 1)),
    }
    return {
        "items": filtered[start:end],
        "filters": filters,
        "summary": summary,
        "niche_stats": niche_stats,
        "pagination": pagination,
        "niche_options": [
            {"code": code, "name": definition["name"]}
            for code, definition in niche.NICHES.items()
        ],
    }
