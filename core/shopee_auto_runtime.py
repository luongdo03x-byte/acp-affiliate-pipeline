"""Provider-aware Shopee Affiliate integration for the existing Auto scheduler.

This module does not create a second scheduler. ``install()`` extends the
existing pipeline/auto-scheduler functions with the contracts needed by
official Shopee Affiliate CSV rows:

- image enrichment must be READY;
- CSV snapshot freshness is 72 hours;
- unknown inventory/rating/review fields do not fail Shopee by themselves;
- the exact affiliate URL imported from CSV is used for the post;
- all existing niche, duplicate, quota, slot, validation and publish-worker
  safeguards remain authoritative.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from . import auto_scheduler, niche, pipeline, scoring

SHOPEE_PROVIDER = "SHOPEE_AFFILIATE"
SHOPEE_AUTO_FRESHNESS = timedelta(hours=72)
_BAD_LINK_STATES = {"ERROR", "FAILED", "INVALID", "STALE", "UNAVAILABLE"}
_INSTALLED = False


def _row_get(row, key, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key] if key in row.keys() else default
    except (AttributeError, IndexError, KeyError):
        return default


def _valid_absolute_http_url(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        parsed = urlparse(text)
    except (TypeError, ValueError):
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _shopee_snapshot_is_fresh(product, now_utc: datetime) -> bool:
    raw = _row_get(product, "last_synced_at") or _row_get(product, "last_seen_at")
    parsed = auto_scheduler._parse_iso_datetime(raw)
    if not parsed:
        return False
    current = now_utc if now_utc.tzinfo else now_utc.replace(tzinfo=timezone.utc)
    age = current.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)
    return timedelta(0) <= age <= SHOPEE_AUTO_FRESHNESS


def _enrichment_ready(conn, product_id: str) -> bool:
    row = conn.execute(
        "SELECT status FROM shopee_image_enrichment_job WHERE product_id=?",
        (product_id,),
    ).fetchone()
    return bool(row and row["status"] == "READY")


def _usable_enriched_image(product) -> bool:
    if str(_row_get(product, "main_image_url") or "").strip():
        return True
    path = str(_row_get(product, "image_path_local") or "").strip()
    return bool(path and os.path.isfile(path))


def _shopee_product_auto_eligibility(
    conn,
    product,
    channel,
    now_utc: datetime,
    *,
    exclude_post_id: str = None,
    slot_at: str = None,
    require_auto_schedule: bool = True,
) -> tuple[bool, str]:
    if not product or str(_row_get(product, "provider") or "") != SHOPEE_PROVIDER:
        return False, "product_provider_invalid"
    if not channel or not int(_row_get(channel, "enabled", 0) or 0) or _row_get(channel, "status") != "ACTIVE":
        return False, "channel_ineligible"
    if require_auto_schedule and not int(_row_get(channel, "auto_schedule_enabled", 0) or 0):
        return False, "channel_auto_disabled"
    if int(_row_get(product, "is_available", 0) or 0) != 1:
        return False, "product_unavailable"

    affiliate_url = str(_row_get(product, "affiliate_url") or "").strip()
    if not _valid_absolute_http_url(affiliate_url):
        return False, "affiliate_link_invalid"
    if str(_row_get(product, "affiliate_link_status") or "").upper() != "READY":
        return False, "affiliate_link_invalid"
    if not _shopee_snapshot_is_fresh(product, now_utc):
        return False, "product_sync_stale"
    if not _enrichment_ready(conn, _row_get(product, "id")) or not _usable_enriched_image(product):
        return False, "product_image_not_ready"

    _, filters = scoring.active_config(conn)
    if _row_get(product, "category_code") in set(filters.get("blocked_categories") or []):
        return False, "blocked_category"
    minimum_commission = int(filters.get(
        "min_commission_value", scoring.DEFAULT_FILTERS["min_commission_value"]
    ) or 0)
    if int(_row_get(product, "commission_value", 0) or 0) < minimum_commission:
        return False, "product_quality_filter"

    niches = pipeline.channel_niches(conn, _row_get(channel, "id"))
    if not niches or niche.match_reasons(product, niches):
        return False, "product_no_longer_matches_channel"

    max_per_category = int(filters.get(
        "max_per_category_per_day",
        scoring.DEFAULT_FILTERS["max_per_category_per_day"],
    ) or 0)
    if max_per_category > 0 and pipeline._category_count_for_channel_local_day(
        conn,
        channel,
        _row_get(product, "category_code"),
        now_utc,
        exclude_post_id=exclude_post_id,
        slot_at=slot_at,
    ) >= max_per_category:
        return False, "category_day_cap_full"

    if auto_scheduler._queued_or_recently_published_product_exists(
        conn, _row_get(product, "id"), now_utc, exclude_post_id=exclude_post_id
    ):
        return False, "product_already_routed"
    return True, "ok"


def _shopee_auto_candidates(conn, channel, limit: int, now_utc: datetime) -> list[dict]:
    if not channel or not int(_row_get(channel, "auto_schedule_enabled", 0) or 0):
        return []
    safe_limit = max(0, int(limit))
    rows = conn.execute(
        """
        SELECT p.*
        FROM product p
        JOIN shopee_image_enrichment_job j ON j.product_id=p.id
        WHERE p.provider=?
          AND p.is_available=1
          AND j.status='READY'
        ORDER BY COALESCE(p.score, 0) DESC,
                 COALESCE(p.commission_value, 0) DESC,
                 p.last_synced_at DESC,
                 p.id
        LIMIT ?
        """,
        (SHOPEE_PROVIDER, max(safe_limit, 1) * 5),
    ).fetchall()
    candidates = []
    for product in rows:
        eligible, _reason = _shopee_product_auto_eligibility(
            conn, product, channel, now_utc, require_auto_schedule=True
        )
        if not eligible:
            continue
        raw_score = _row_get(product, "score")
        if raw_score is not None:
            bounded_score = max(0.0, min(1.0, float(raw_score) / 100.0))
        else:
            commission = max(0.0, float(_row_get(product, "commission_value", 0) or 0))
            bounded_score = min(1.0, commission / 100_000.0)
        candidates.append({
            "product": product,
            "score": bounded_score,
            "rejected": [],
            "breakdown": {"shopee_commission": _row_get(product, "commission_value", 0) or 0},
        })
        if len(candidates) >= safe_limit:
            break
    return candidates


def _prepare_shopee_artifacts(conn, ctx, product, campaign, channel, template,
                              variant_code: str, score: float = None) -> dict:
    link = str(_row_get(product, "affiliate_url") or "").strip()
    if not _valid_absolute_http_url(link):
        return {"ok": False, "error": "Affiliate link Shopee không hợp lệ"}
    if not str(_row_get(product, "image_path_local") or "").strip():
        return {"ok": False, "error": "Ảnh Shopee chưa sẵn sàng"}

    post_id = pipeline.ulid()
    attribution_payload = {
        "provider": "shopee_affiliate_csv",
        "link_mode": "imported",
        "product_id": _row_get(product, "id"),
        "post_id": post_id,
    }
    discount = pipeline.scoring.real_discount_depth(
        conn, _row_get(product, "id"), _row_get(product, "current_price")
    )
    image_path = pipeline.imaging.compose(
        product,
        pipeline.MEDIA_DIR,
        discount_pct=discount,
        handle=_row_get(channel, "handle"),
    )
    image_url = ctx.get("storage", pipeline.storage.get_storage()).put(image_path)
    caption = pipeline.content.generate(
        product,
        _row_get(template, "code"),
        link,
        discount_pct=discount,
        hook_code=variant_code,
    )
    problems = pipeline.content.validate(
        caption, niches=pipeline.channel_niches(conn, _row_get(channel, "id"))
    )
    return {
        "ok": True,
        "post_id": post_id,
        "variant_code": variant_code,
        "caption": caption,
        "image_url": image_url,
        "affiliate_link": link,
        "sub_id_payload": json.dumps(attribution_payload, ensure_ascii=False, sort_keys=True),
        "score": score,
        "status": "PENDING_REVIEW" if not problems else "DRAFT",
        "problems": problems,
    }


def _shopee_preflight_auto_target(
    conn, target, post, channel, now_utc=None, eligibility_checker=None
) -> tuple[bool, str]:
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    if auto_scheduler._row_get(target, "status") == "SUCCESS" or auto_scheduler._row_get(target, "external_post_id"):
        return False, "target_already_published"
    product_id = auto_scheduler._row_get(post, "product_id")
    if not product_id:
        return False, "product_missing"
    product = conn.execute("SELECT * FROM product WHERE id=?", (product_id,)).fetchone()
    if not product:
        return False, "product_missing"
    if int(auto_scheduler._row_get(product, "is_available", 0) or 0) != 1:
        return False, "product_unavailable"
    if not _shopee_snapshot_is_fresh(product, now_utc):
        return False, "product_sync_stale"
    if not _enrichment_ready(conn, product_id) or not _usable_enriched_image(product):
        return False, "product_image_not_ready"
    if not auto_scheduler._valid_http_url(auto_scheduler._row_get(post, "affiliate_link")):
        return False, "affiliate_link_invalid"
    link_state = str(auto_scheduler._row_get(product, "affiliate_link_status") or "").upper()
    if link_state in _BAD_LINK_STATES:
        return False, "affiliate_link_invalid"

    niches = [
        code for code in auto_scheduler._channel_niches(channel)
        if code in auto_scheduler.niche.NICHES
    ]
    if niches:
        if auto_scheduler.niche.match_reasons(product, niches) or not auto_scheduler._matched_niches(product, niches):
            return False, "product_no_longer_matches_channel"

    if eligibility_checker is not None:
        eligible, reason = eligibility_checker(
            conn,
            product,
            channel,
            now_utc,
            exclude_post_id=auto_scheduler._row_get(post, "id"),
            slot_at=auto_scheduler._row_get(target, "scheduled_at") or auto_scheduler._row_get(post, "scheduled_at"),
        )
        if not eligible:
            return False, reason
    return True, "ok"


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_candidates = pipeline._candidate_products_for_channel
    original_eligibility = pipeline.current_auto_product_eligibility
    original_prepare = pipeline._prepare_auto_sales_post_artifacts
    original_preflight = auto_scheduler.preflight_auto_target

    def candidates(conn, channel, limit: int, now_utc=None):
        current = now_utc or datetime.now(timezone.utc)
        base = [
            item for item in original_candidates(conn, channel, limit, current)
            if str(_row_get(item.get("product"), "provider") or "") != SHOPEE_PROVIDER
        ]
        base.extend(_shopee_auto_candidates(conn, channel, limit, current))
        base.sort(key=lambda item: -float(item.get("score") or 0.0))
        return base[:limit]

    def eligibility(conn, product, channel, now_utc, *, require_auto_schedule=True,
                    exclude_post_id=None, slot_at=None):
        if str(_row_get(product, "provider") or "") == SHOPEE_PROVIDER:
            return _shopee_product_auto_eligibility(
                conn,
                product,
                channel,
                now_utc,
                exclude_post_id=exclude_post_id,
                slot_at=slot_at,
                require_auto_schedule=require_auto_schedule,
            )
        return original_eligibility(
            conn,
            product,
            channel,
            now_utc,
            require_auto_schedule=require_auto_schedule,
            exclude_post_id=exclude_post_id,
            slot_at=slot_at,
        )

    def prepare(conn, ctx, product, campaign, channel, template, variant_code, score=None):
        if str(_row_get(product, "provider") or "") == SHOPEE_PROVIDER:
            return _prepare_shopee_artifacts(
                conn, ctx, product, campaign, channel, template, variant_code, score=score
            )
        return original_prepare(
            conn, ctx, product, campaign, channel, template, variant_code, score=score
        )

    def preflight(conn, target, post, channel, now_utc=None, eligibility_checker=None):
        product_id = auto_scheduler._row_get(post, "product_id")
        product = conn.execute("SELECT * FROM product WHERE id=?", (product_id,)).fetchone() if product_id else None
        if product and str(auto_scheduler._row_get(product, "provider") or "") == SHOPEE_PROVIDER:
            return _shopee_preflight_auto_target(
                conn, target, post, channel, now_utc=now_utc, eligibility_checker=eligibility_checker
            )
        return original_preflight(
            conn, target, post, channel, now_utc=now_utc, eligibility_checker=eligibility_checker
        )

    pipeline.SHOPEE_PROVIDER = SHOPEE_PROVIDER
    pipeline.SHOPEE_AUTO_FRESHNESS = SHOPEE_AUTO_FRESHNESS
    pipeline._valid_absolute_http_url = _valid_absolute_http_url
    pipeline._shopee_snapshot_is_fresh = _shopee_snapshot_is_fresh
    pipeline._shopee_product_auto_eligibility = _shopee_product_auto_eligibility
    pipeline._shopee_auto_candidates = _shopee_auto_candidates
    pipeline._candidate_products_for_channel = candidates
    pipeline.current_auto_product_eligibility = eligibility
    pipeline._prepare_auto_sales_post_artifacts = prepare

    auto_scheduler.SHOPEE_PROVIDER = SHOPEE_PROVIDER
    auto_scheduler.MAX_SHOPEE_CSV_AGE = SHOPEE_AUTO_FRESHNESS
    auto_scheduler.preflight_auto_target = preflight
    _INSTALLED = True
