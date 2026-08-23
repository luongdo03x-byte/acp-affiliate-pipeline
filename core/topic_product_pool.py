"""Topic-aware Shopee Product Pool read model.

Keeps the existing operational projection but replaces the static niche filter
with the DB topic tree. The query parameter stays named ``niche`` for backward
compatibility with existing bookmarks/tests.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from . import pipeline, shopee_product_pool as legacy, topic_engine


def _normalize(conn, values) -> dict:
    values = values or {}
    base_values = dict(values)
    base_values["niche"] = "all"
    filters = legacy.normalize_filters(base_values)
    requested = str(values.get("niche") or "all").strip()
    if requested != "all":
        topic_engine.ensure_system_topics(conn)
        if not conn.execute(
            "SELECT 1 FROM topic WHERE code=? AND status='ACTIVE'", (requested,)
        ).fetchone():
            requested = "all"
    filters["niche"] = requested
    return filters


def _topic_auto_state(conn, item, usage, channels, now_utc):
    if usage["state"] != "UNUSED":
        return {
            "state": usage["state"],
            "channel_handle": usage.get("channel_handle"),
            "scheduled_at": usage.get("scheduled_at"),
        }
    if str(item.get("enrichment_status") or "") != "READY":
        return {"state": "WAITING_IMAGE", "channel_handle": None, "scheduled_at": None}
    try:
        from .shopee_auto_runtime import _shopee_snapshot_is_fresh
        if not _shopee_snapshot_is_fresh(item, now_utc):
            return {"state": "STALE", "channel_handle": None, "scheduled_at": None}
    except Exception:
        return {"state": "STALE", "channel_handle": None, "scheduled_at": None}
    for channel in channels:
        eligible, _reason = pipeline.current_auto_product_eligibility(
            conn, item, channel, now_utc, require_auto_schedule=True
        )
        if eligible:
            return {"state": "ELIGIBLE", "channel_handle": channel["handle"], "scheduled_at": None}
    return {"state": "INELIGIBLE", "channel_handle": None, "scheduled_at": None}


def _project(conn, now_utc):
    channels = legacy._active_auto_channels(conn)
    products = legacy._all_products(conn)
    projected = []
    for product in products:
        item = dict(product)
        topic_engine.sync_product_system_topics(conn, item)
        usage = legacy._usage_state(conn, str(item["id"]))
        auto = _topic_auto_state(conn, item, usage, channels, now_utc)
        item["usage_state"] = usage["state"]
        item["auto_state"] = auto["state"]
        item["auto_channel_handle"] = auto.get("channel_handle")
        item["auto_scheduled_at"] = auto.get("scheduled_at")
        item["published_at_effective"] = usage.get("published_at")
        item["topic_codes"] = topic_engine.product_topic_codes(conn, str(item["id"]))
        item["topic_paths"] = topic_engine.topic_paths_for_product(conn, str(item["id"]))
        item["niche_codes"] = [code for code in item["topic_codes"] if code in legacy.niche.NICHES]
        projected.append(item)
    return projected


def _matches(item, filters):
    legacy_filters = dict(filters)
    legacy_filters["niche"] = "all"
    if not legacy._matches_filters(item, legacy_filters):
        return False
    topic_code = filters["niche"]
    return topic_code == "all" or topic_code in item["topic_codes"]


def _flat_options(tree, depth=0):
    out = []
    for item in tree:
        label = ("— " * depth) + item["name"]
        out.append({
            "code": item["code"],
            # Existing Product Pool template renders option.name, so expose the
            # indented hierarchy there without rewriting the large template.
            "name": label,
            "raw_name": item["name"],
            "label": label,
            "depth": depth,
            "topic_type": item["topic_type"],
        })
        out.extend(_flat_options(item.get("children") or [], depth + 1))
    return out


def _topic_stats(items, options):
    stats = []
    for option in options:
        matched = [item for item in items if option["code"] in item["topic_codes"]]
        summary = legacy._summarize(matched)
        stats.append({"code": option["code"], "name": option["label"], **summary})
    return stats


def _page_params(filters: dict, page: int) -> dict:
    return {
        "q": filters["q"], "niche": filters["niche"], "auto": filters["auto"],
        "image": filters["image"], "usage": filters["usage"],
        "per_page": filters["per_page"], "page": page,
    }


def build_product_pool(conn, values=None, *, now_utc=None) -> dict:
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_utc = now_utc.astimezone(timezone.utc)
    topic_engine.ensure_system_topics(conn)

    filters = _normalize(conn, values)
    all_items = _project(conn, now_utc)
    summary = legacy._summarize(all_items)
    tree = topic_engine.topic_tree(conn)
    options = _flat_options(tree)
    niche_stats = _topic_stats(all_items, options)
    filtered = [item for item in all_items if _matches(item, filters)]

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
        "niche_options": options,
        "topic_tree": tree,
    }
