"""Calendar-aware, fair Auto Post scheduling on the existing scheduler path.

This runtime is installed last in ``acp.core`` so it can compose with Shopee,
topic, reconciliation and Auto Post runtimes without creating a second timer,
queue or publisher.

Behavior owned here:

- plan the remainder of each channel's local today plus local tomorrow;
- keep earlier-today plans visible in the operator control center;
- distribute scarce eligible products round-robin across Auto channels;
- allow the same product on different channels while preserving same-channel
  active/cooldown protection;
- reconcile future plans inside the same calendar window after each fill pass.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import auto_post_plans, auto_scheduler, pipeline, scoring, topic_engine

_INSTALLED = False
_AUTO_CHANNEL_ID = ContextVar("acp_auto_channel_id", default=None)
_AUTO_NOW_UTC = ContextVar("acp_auto_now_utc", default=None)
_RECENT_REASON_PREFIX = "đã đăng trong "


def _normalize_utc(value=None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


@contextmanager
def _channel_scope(channel_id, now_utc=None):
    channel_token = _AUTO_CHANNEL_ID.set(str(channel_id) if channel_id else None)
    now_token = _AUTO_NOW_UTC.set(_normalize_utc(now_utc) if now_utc is not None else None)
    try:
        yield
    finally:
        _AUTO_NOW_UTC.reset(now_token)
        _AUTO_CHANNEL_ID.reset(channel_token)


def _channel_id(channel):
    if channel is None:
        return None
    try:
        return channel["id"]
    except (KeyError, IndexError, TypeError):
        return None


def _calendar_available_slots(conn, channel, now_utc: datetime) -> list[dict]:
    """Return open slots for local today + tomorrow, never a past slot."""
    current = _normalize_utc(now_utc)
    tz_name = channel["posting_timezone"] or "Asia/Bangkok"
    slots = auto_scheduler._parse_slots(channel["posting_slots"])
    if not slots:
        return []

    tzinfo = auto_scheduler._parse_timezone(tz_name)
    local_today = current.astimezone(tzinfo).date()
    local_dates = (local_today, local_today + timedelta(days=1))
    effective_target = min(
        auto_scheduler._core_daily_target(channel),
        auto_scheduler._core_daily_cap(channel),
    )
    if effective_target <= 0:
        return []

    available = []
    for local_date in local_dates:
        existing = auto_scheduler._quota_count_for_local_date(
            conn, channel["id"], tz_name, local_date
        )
        remaining = max(0, effective_target - existing)
        if remaining <= 0:
            continue

        occupied = auto_scheduler._occupied_slots_for_local_date(
            conn, channel["id"], tz_name, local_date
        )
        candidates = []
        for ranked in auto_scheduler.rank_slots(conn, channel["id"], local_date, slots):
            if ranked["slot"] in occupied:
                continue
            slot_dt = auto_scheduler._slot_datetime(local_date, ranked["slot"], tz_name)
            slot_utc = slot_dt.astimezone(timezone.utc)
            if slot_utc < current:
                continue
            candidates.append(
                {
                    "slot": slot_dt.isoformat(timespec="seconds"),
                    "slot_local": ranked["slot"],
                    "slot_hour_score": ranked["hour_score"],
                    "slot_sample_size": ranked["sample_size"],
                }
            )
        available.extend(candidates[:remaining])
    return available


def _calendar_list_window(conn, now_utc=None, hours: int = 48) -> list[dict]:
    """List Auto plans whose channel-local date is today or tomorrow.

    ``hours`` remains in the signature for backward compatibility; calendar
    membership, not a rolling-hour lower bound, is authoritative.
    """
    del hours
    auto_post_plans.sync_existing_auto_targets(conn)
    current = _normalize_utc(now_utc)

    # Any channel-local start-of-today is at most 24h behind the current
    # instant, and end-of-tomorrow is at most 48h ahead. Use that as a cheap
    # SQL prefilter, then apply the exact timezone-aware date check per row.
    broad_start = (current - timedelta(hours=24)).isoformat(timespec="seconds")
    broad_end = (current + timedelta(hours=48)).isoformat(timespec="seconds")
    rows = conn.execute(
        """SELECT ap.*, ch.handle AS channel_handle, ch.code AS channel_code,
                  ch.posting_timezone, p.caption_final, p.affiliate_link,
                  p.image_url_composited, p.variant_code, p.status AS post_status,
                  pr.name AS product_name, pr.current_price, pr.shop_name,
                  pr.main_image_url, pr.image_path_local, pt.status AS target_status
           FROM auto_post_plan ap
           JOIN channel ch ON ch.id=ap.channel_id
           JOIN post p ON p.id=ap.post_id
           JOIN publish_target pt ON pt.id=ap.publish_target_id
           LEFT JOIN product pr ON pr.id=ap.product_id
           WHERE ap.scheduled_at>=? AND ap.scheduled_at<?
             AND ap.state!='CANCELLED'
           ORDER BY ap.scheduled_at, ch.handle, ap.id""",
        (broad_start, broad_end),
    ).fetchall()

    result = []
    for row in rows:
        item = dict(row)
        try:
            scheduled = datetime.fromisoformat(item["scheduled_at"])
            if scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            tzinfo = ZoneInfo(item.get("posting_timezone") or "Asia/Bangkok")
        except Exception:
            continue
        local_today = current.astimezone(tzinfo).date()
        local_date = scheduled.astimezone(tzinfo).date()
        if local_date not in (local_today, local_today + timedelta(days=1)):
            continue
        item["topic_codes"] = (
            topic_engine.product_topic_codes(conn, item["product_id"])
            if item.get("product_id")
            else []
        )
        item["topic_paths"] = (
            topic_engine.topic_paths_for_product(conn, item["product_id"])
            if item.get("product_id")
            else []
        )
        result.append(item)
    return result


def _scoped_duplicate_checker(original_duplicate):
    def duplicate_exists(
        conn,
        product_id: str,
        now_utc: datetime,
        *,
        exclude_post_id: str = None,
        channel_id: str = None,
    ) -> bool:
        effective_channel = channel_id or _AUTO_CHANNEL_ID.get()
        if not effective_channel:
            return original_duplicate(
                conn,
                product_id,
                now_utc,
                exclude_post_id=exclude_post_id,
            )

        statuses = auto_scheduler.QUEUED_POST_STATUSES
        queued = conn.execute(
            f"""SELECT 1
                FROM post
                WHERE product_id = ?
                  AND channel_id = ?
                  AND (? IS NULL OR id <> ?)
                  AND status IN ({','.join('?' for _ in statuses)})
                LIMIT 1""",
            (product_id, effective_channel, exclude_post_id, exclude_post_id, *statuses),
        ).fetchone()
        if queued:
            return True

        current = _normalize_utc(now_utc)
        _, filters = scoring.active_config(conn)
        cooldown_days = filters.get("cooldown_days", scoring.DEFAULT_FILTERS["cooldown_days"])
        cutoff = (current - timedelta(days=cooldown_days)).isoformat(timespec="seconds")
        published = conn.execute(
            """SELECT 1
               FROM post
               WHERE product_id = ?
                 AND channel_id = ?
                 AND (? IS NULL OR id <> ?)
                 AND status = 'PUBLISHED'
                 AND published_at IS NOT NULL
                 AND published_at >= ?
               LIMIT 1""",
            (product_id, effective_channel, exclude_post_id, exclude_post_id, cutoff),
        ).fetchone()
        return bool(published)

    return duplicate_exists


def _channel_scoped_score_candidates(original_score, duplicate_exists):
    def score_candidates(
        conn,
        limit: int = 20,
        explain: bool = False,
        niches=None,
        enforce_category_day_cap: bool = True,
    ):
        channel_id = _AUTO_CHANNEL_ID.get()
        if not channel_id or explain:
            return original_score(
                conn,
                limit=limit,
                explain=explain,
                niches=niches,
                enforce_category_day_cap=enforce_category_day_cap,
            )

        # Ask the legacy scorer for explanations so products rejected only by
        # its global recent-product set can be reconsidered for this channel.
        scored = original_score(
            conn,
            limit=limit,
            explain=True,
            niches=niches,
            enforce_category_day_cap=enforce_category_day_cap,
        )
        current = _AUTO_NOW_UTC.get() or datetime.now(timezone.utc)
        eligible = []
        for item in scored:
            reasons = list(item.get("rejected") or [])
            recent_reasons = [reason for reason in reasons if str(reason).startswith(_RECENT_REASON_PREFIX)]
            hard_reasons = [reason for reason in reasons if reason not in recent_reasons]
            if hard_reasons:
                continue
            product = item.get("product")
            if recent_reasons and duplicate_exists(
                conn,
                product["id"],
                current,
                channel_id=channel_id,
            ):
                continue
            clean = dict(item)
            clean["rejected"] = []
            eligible.append(clean)
            if len(eligible) >= max(0, int(limit)):
                break
        return eligible

    return score_candidates


def _channel_scoped_catalog_candidates(conn, channel, limit: int, now_utc: datetime) -> list:
    """Catalog candidates with active/cooldown exclusion scoped to the channel."""
    nl = pipeline.channel_niches(conn, channel["id"])
    if not nl:
        return []
    _, filters = scoring.active_config(conn)
    filters = dict(filters, niches=nl)
    cooldown_days = filters.get("cooldown_days", scoring.DEFAULT_FILTERS["cooldown_days"])
    cutoff = (_normalize_utc(now_utc) - timedelta(days=cooldown_days)).isoformat(timespec="seconds")
    rows = conn.execute(
        """SELECT *
           FROM product
           WHERE provider = ?
             AND is_available = 1
             AND has_inventory = 1
             AND detail_link IS NOT NULL AND detail_link <> ''
             AND external_product_id IS NOT NULL AND external_product_id <> ''
             AND COALESCE(affiliate_link_status, '') <> 'UNAVAILABLE'
             AND NOT EXISTS (
                 SELECT 1
                 FROM post
                 WHERE post.product_id = product.id
                   AND post.channel_id = ?
                   AND post.post_type = 'SALES'
                   AND (
                       post.status IN ('DRAFT','PENDING_REVIEW','APPROVED','SCHEDULED')
                       OR post.published_at >= ?
                   )
             )
           ORDER BY COALESCE(score, 0) DESC, last_seen_at DESC
           LIMIT ?""",
        (
            pipeline.CATALOG_PROVIDER,
            channel["id"],
            cutoff,
            max(0, int(limit)),
        ),
    ).fetchall()
    candidates = []
    for row in rows:
        if scoring._reasons(row, filters):
            continue
        candidates.append(
            {
                "product": row,
                "score": float(row["score"] or 0.0) / 100.0,
                "rejected": [],
                "breakdown": {"catalog_score": row["score"] or 0.0},
            }
        )
    return candidates


def _attempt_assignment(
    conn,
    *,
    campaign,
    template,
    channel,
    item,
    slot,
    now_utc,
    ctx,
    hooks,
    variant_index,
):
    product = item["product"]
    with _channel_scope(channel["id"], now_utc):
        if auto_scheduler._queued_or_recently_published_product_exists(
            conn,
            product["id"],
            now_utc,
            channel_id=channel["id"],
        ):
            return "skipped"

        variant = hooks[variant_index % len(hooks)]
        try:
            prepared = pipeline._prepare_auto_sales_post_artifacts(
                conn,
                ctx,
                product,
                campaign,
                channel,
                template,
                variant,
                score=item["score"],
            )
        except Exception:
            return "skipped"
        if not prepared.get("ok"):
            return "skipped"

        try:
            with pipeline.transaction(conn):
                fresh_product = conn.execute(
                    "SELECT * FROM product WHERE id=?", (product["id"],)
                ).fetchone()
                fresh_channel = conn.execute(
                    "SELECT * FROM channel WHERE id=?", (channel["id"],)
                ).fetchone()
                if not fresh_channel:
                    return "skipped"
                fresh_auto_enabled = bool(fresh_channel["auto_schedule_enabled"])
                eligible, _reason = pipeline.current_auto_product_eligibility(
                    conn,
                    fresh_product,
                    fresh_channel,
                    now_utc,
                    require_auto_schedule=fresh_auto_enabled,
                    slot_at=slot,
                )
                if not eligible:
                    return "skipped"
                if fresh_auto_enabled:
                    if not slot or auto_scheduler.live_slot_occupied(
                        conn, fresh_channel["id"], slot
                    ):
                        return "skipped"

                post = pipeline._insert_prepared_auto_sales_post(
                    conn,
                    fresh_product,
                    campaign,
                    fresh_channel,
                    template,
                    prepared,
                    actor="auto_scheduler",
                )
                if not post.get("ok"):
                    return "skipped"

                if fresh_auto_enabled and post["status"] == "PENDING_REVIEW":
                    approved = pipeline.approve_post(
                        conn,
                        post["post_id"],
                        actor="auto_scheduler",
                        scheduled_at=slot,
                        auto_scheduled=True,
                    )
                    return "scheduled" if approved.get("ok") else "review"
                return "review"
        except Exception:
            return "skipped"


def _fair_fill_auto_schedule(conn, campaign_code: str, now_utc=None, *, ctx=None) -> dict:
    """Fill Auto channels one assignment per channel per round."""
    current = _normalize_utc(now_utc)
    campaign = conn.execute(
        "SELECT * FROM campaign WHERE code=?", (campaign_code,)
    ).fetchone()
    template = conn.execute(
        "SELECT * FROM caption_template WHERE is_active=1 ORDER BY code LIMIT 1"
    ).fetchone()
    if not campaign or not template:
        return {"scheduled": 0, "review": 0, "skipped": 0, "cancelled": 0}

    if ctx is None:
        from ..adapters import factory

        ctx = factory.build_context()
    hooks = pipeline.playbook.hook_codes()
    stats = {"scheduled": 0, "review": 0, "skipped": 0, "cancelled": 0}
    channels = conn.execute(
        """SELECT *
           FROM channel
           WHERE platform='threads'
             AND status='ACTIVE'
             AND COALESCE(enabled, 1)=1
           ORDER BY code"""
    ).fetchall()

    auto_states = []
    manual_channels = []
    for channel in channels:
        if channel["auto_schedule_enabled"]:
            slots = auto_scheduler.available_slots(conn, channel, current)
            if not slots:
                continue
            with _channel_scope(channel["id"], current):
                candidates = pipeline._candidate_products_for_channel(
                    conn,
                    channel,
                    limit=max(20, len(slots) * 5),
                    now_utc=current,
                )
            if not candidates:
                stats["skipped"] += len(slots)
                continue
            auto_states.append(
                {
                    "channel": channel,
                    "slots": list(slots),
                    "candidates": list(candidates),
                    "candidate_index": 0,
                }
            )
        else:
            manual_channels.append(channel)

    while auto_states:
        made_progress = False
        has_open_state = False
        for state in auto_states:
            if not state["slots"]:
                continue
            has_open_state = True
            assigned = False
            while state["candidate_index"] < len(state["candidates"]):
                item = state["candidates"][state["candidate_index"]]
                state["candidate_index"] += 1
                outcome = _attempt_assignment(
                    conn,
                    campaign=campaign,
                    template=template,
                    channel=state["channel"],
                    item=item,
                    slot=state["slots"][0]["slot"],
                    now_utc=current,
                    ctx=ctx,
                    hooks=hooks,
                    variant_index=stats["scheduled"] + stats["review"],
                )
                if outcome == "skipped":
                    stats["skipped"] += 1
                    continue
                stats[outcome] += 1
                state["slots"].pop(0)
                assigned = True
                made_progress = True
                break

            if not assigned and state["candidate_index"] >= len(state["candidates"]):
                stats["skipped"] += len(state["slots"])
                state["slots"].clear()

        if not has_open_state or not made_progress:
            break

    # Preserve the legacy Auto-OFF behavior: keep a small review queue without
    # creating publish targets. This phase is separate so it cannot consume a
    # full round ahead of Auto-enabled channels.
    for channel in manual_channels:
        missing = max(
            0,
            auto_scheduler._core_daily_target(channel)
            - pipeline._active_review_count_for_channel(conn, channel["id"]),
        )
        if missing <= 0:
            continue
        with _channel_scope(channel["id"], current):
            candidates = pipeline._candidate_products_for_channel(
                conn,
                channel,
                limit=max(20, missing * 5),
                now_utc=current,
            )
        if not candidates:
            stats["skipped"] += missing
            continue
        for item in candidates:
            if missing <= 0:
                break
            outcome = _attempt_assignment(
                conn,
                campaign=campaign,
                template=template,
                channel=channel,
                item=item,
                slot=None,
                now_utc=current,
                ctx=ctx,
                hooks=hooks,
                variant_index=stats["scheduled"] + stats["review"],
            )
            if outcome == "skipped":
                stats["skipped"] += 1
                continue
            stats[outcome] += 1
            missing -= 1
    return stats


def reconcile_window(conn, now_utc=None, hours: int = 48) -> dict:
    """Reconcile future plans that belong to local today or tomorrow."""
    del hours
    current = _normalize_utc(now_utc)
    rows = auto_post_plans.list_window(conn, current, hours=48)
    stats = {
        "reconciled": 0,
        "reconcile_replaced": 0,
        "reconcile_refreshed": 0,
        "reconcile_deferred": 0,
        "reconcile_errors": 0,
    }
    for row in rows:
        if row.get("state") not in ("PLANNED", "READY", "REGENERATING"):
            continue
        try:
            scheduled = datetime.fromisoformat(row["scheduled_at"])
            if scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            if scheduled.astimezone(timezone.utc) < current:
                continue
            result = auto_post_plans.reconcile_plan(conn, row["id"])
        except Exception:
            stats["reconcile_errors"] += 1
            continue
        stats["reconciled"] += 1
        action = str(result.get("action") or "")
        if action == "replaced":
            stats["reconcile_replaced"] += 1
        elif action in ("refreshed", "image_refreshed", "caption_regenerated"):
            stats["reconcile_refreshed"] += 1
        elif action == "defer":
            stats["reconcile_deferred"] += 1
    return stats


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_duplicate = auto_scheduler._queued_or_recently_published_product_exists
    original_score_candidates = scoring.score_candidates
    original_eligibility = pipeline.current_auto_product_eligibility
    original_preflight = auto_scheduler.preflight_auto_target

    duplicate_exists = _scoped_duplicate_checker(original_duplicate)
    score_candidates = _channel_scoped_score_candidates(
        original_score_candidates, duplicate_exists
    )

    def scoped_eligibility(
        conn,
        product,
        channel,
        now_utc,
        *,
        require_auto_schedule=True,
        exclude_post_id=None,
        slot_at=None,
    ):
        with _channel_scope(_channel_id(channel), now_utc):
            return original_eligibility(
                conn,
                product,
                channel,
                now_utc,
                require_auto_schedule=require_auto_schedule,
                exclude_post_id=exclude_post_id,
                slot_at=slot_at,
            )

    def scoped_preflight(
        conn,
        target,
        post,
        channel,
        now_utc=None,
        eligibility_checker=None,
    ):
        with _channel_scope(_channel_id(channel), now_utc):
            return original_preflight(
                conn,
                target,
                post,
                channel,
                now_utc=now_utc,
                eligibility_checker=eligibility_checker,
            )

    auto_scheduler._queued_or_recently_published_product_exists = duplicate_exists
    scoring.score_candidates = score_candidates
    pipeline._catalog_auto_candidates = _channel_scoped_catalog_candidates
    pipeline.current_auto_product_eligibility = scoped_eligibility
    auto_scheduler.preflight_auto_target = scoped_preflight
    auto_scheduler.available_slots = _calendar_available_slots
    auto_post_plans.list_window = _calendar_list_window

    def fill_auto_schedule(conn, campaign_code: str, now_utc=None, *, ctx=None) -> dict:
        stats = _fair_fill_auto_schedule(
            conn,
            campaign_code,
            now_utc=now_utc,
            ctx=ctx,
        )
        stats.update(reconcile_window(conn, now_utc=now_utc, hours=48))
        return stats

    pipeline.fill_auto_schedule = fill_auto_schedule
    _INSTALLED = True
