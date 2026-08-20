import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from . import niche, scoring

LIVE_TARGET_STATUSES = ("SCHEDULED", "PENDING", "RUNNING", "SUCCESS")
QUEUED_POST_STATUSES = ("DRAFT", "PENDING_REVIEW", "APPROVED", "SCHEDULED")
MIN_HOUR_SAMPLE_SIZE = 5
MAX_CORE_DAILY_TARGET = 3
MAX_AUTO_PRODUCT_SYNC_AGE = timedelta(hours=48)


def _row_get(row, key: str, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        if key in row.keys():
            return row[key]
    except AttributeError:
        pass
    except (IndexError, KeyError):
        pass
    return default


def _parse_iso_datetime(value: str):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _valid_http_url(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = urlparse(value)
    except (TypeError, ValueError):
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _channel_niches(channel) -> list:
    try:
        return json.loads(_row_get(channel, "niches", "[]") or "[]")
    except (TypeError, ValueError):
        return []


def preflight_auto_target(conn, target, post, channel, now_utc=None) -> tuple[bool, str]:
    """Validate an auto-scheduled target immediately before publishing.

    Reasons are stable sanitized codes; callers can persist them without leaking
    affiliate URLs, tokens, or raw provider payloads.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    if _row_get(target, "status") == "SUCCESS" or _row_get(target, "external_post_id"):
        return False, "target_already_published"

    product_id = _row_get(post, "product_id")
    if not product_id:
        return False, "product_missing"

    product = conn.execute("SELECT * FROM product WHERE id=?", (product_id,)).fetchone()
    if not product:
        return False, "product_missing"

    if int(_row_get(product, "is_available", 0) or 0) != 1:
        return False, "product_unavailable"

    has_inventory = _row_get(product, "has_inventory")
    if has_inventory is not None and int(has_inventory or 0) != 1:
        return False, "product_inventory_empty"

    last_synced = _parse_iso_datetime(_row_get(product, "last_synced_at") or _row_get(product, "last_seen_at"))
    if not last_synced or now_utc - last_synced.astimezone(timezone.utc) > MAX_AUTO_PRODUCT_SYNC_AGE:
        return False, "product_sync_stale"

    if not _valid_http_url(_row_get(post, "affiliate_link")):
        return False, "affiliate_link_invalid"

    affiliate_link_status = _row_get(product, "affiliate_link_status")
    if affiliate_link_status and str(affiliate_link_status).upper() in {"ERROR", "FAILED", "INVALID", "STALE"}:
        return False, "affiliate_link_invalid"

    niches = [code for code in _channel_niches(channel) if code in niche.NICHES]
    if niches:
        if niche.match_reasons(product, niches) or not _matched_niches(product, niches):
            return False, "product_no_longer_matches_channel"

    return True, "ok"


def _parse_timezone(name: str) -> ZoneInfo:
    return ZoneInfo(name or "Asia/Bangkok")


def _parse_slots(raw_slots) -> list[str]:
    try:
        slots = json.loads(raw_slots or "[]")
    except (TypeError, ValueError):
        return []
    parsed = []
    for slot in slots:
        text = str(slot or "").strip()
        if len(text) == 5 and text[2] == ":":
            parsed.append(text)
    return parsed


def _core_daily_cap(channel) -> int:
    try:
        cap = int(channel["daily_post_cap"] or 0)
    except (TypeError, ValueError):
        cap = 0
    return max(0, min(cap, MAX_CORE_DAILY_TARGET))


def _core_daily_target(channel) -> int:
    try:
        target = int(channel["daily_post_target"] or 0)
    except (TypeError, ValueError):
        target = 0
    return max(0, min(target, _core_daily_cap(channel), MAX_CORE_DAILY_TARGET))


def _local_date_key(iso_value: str, tz_name: str) -> str | None:
    if not iso_value:
        return None
    try:
        dt = datetime.fromisoformat(iso_value)
    except ValueError:
        return None
    return dt.astimezone(_parse_timezone(tz_name)).date().isoformat()


def _slot_datetime(local_date, slot_text: str, tz_name: str) -> datetime:
    hour, minute = (int(part) for part in slot_text.split(":", 1))
    tzinfo = _parse_timezone(tz_name)
    return datetime(local_date.year, local_date.month, local_date.day, hour, minute, tzinfo=tzinfo)


def _queued_or_recently_published_product_exists(conn, product_id: str, now_utc: datetime) -> bool:
    queued_row = conn.execute(
        f"""
        SELECT 1
        FROM post
        WHERE product_id = ?
          AND status IN ({",".join("?" for _ in QUEUED_POST_STATUSES)})
        LIMIT 1
        """,
        (product_id, *QUEUED_POST_STATUSES),
    ).fetchone()
    if queued_row:
        return True

    _, filters = scoring.active_config(conn)
    cooldown_days = filters.get("cooldown_days", scoring.DEFAULT_FILTERS["cooldown_days"])
    cutoff = (now_utc - timedelta(days=cooldown_days)).isoformat(timespec="seconds")
    row = conn.execute(
        """
        SELECT 1
        FROM post
        WHERE product_id = ?
          AND status = 'PUBLISHED'
          AND published_at IS NOT NULL
          AND published_at >= ?
        LIMIT 1
        """,
        (product_id, cutoff),
    ).fetchone()
    return bool(row)


def _channel_target_rows(conn, channel_id: str):
    return conn.execute(
        f"""
        SELECT status, scheduled_at, updated_at
        FROM publish_target
        WHERE channel_id = ?
          AND status IN ({",".join("?" for _ in LIVE_TARGET_STATUSES)})
        """,
        (channel_id, *LIVE_TARGET_STATUSES),
    ).fetchall()


def _quota_count_for_local_date(conn, channel_id: str, tz_name: str, local_date) -> int:
    local_key = local_date.isoformat()
    count = 0
    for row in _channel_target_rows(conn, channel_id):
        iso_value = row["scheduled_at"] or row["updated_at"]
        if _local_date_key(iso_value, tz_name) == local_key:
            count += 1
    return count


def live_slot_occupied(conn, channel_id: str, scheduled_at: str) -> bool:
    row = conn.execute(
        f"""
        SELECT 1
        FROM publish_target
        WHERE channel_id = ?
          AND scheduled_at = ?
          AND status IN ({",".join("?" for _ in LIVE_TARGET_STATUSES)})
        LIMIT 1
        """,
        (channel_id, scheduled_at, *LIVE_TARGET_STATUSES),
    ).fetchone()
    return bool(row)


def _occupied_slots_for_local_date(conn, channel_id: str, tz_name: str, local_date) -> set[str]:
    local_key = local_date.isoformat()
    occupied = set()
    for row in _channel_target_rows(conn, channel_id):
        iso_value = row["scheduled_at"] or row["updated_at"]
        if _local_date_key(iso_value, tz_name) != local_key:
            continue
        try:
            dt = datetime.fromisoformat(iso_value).astimezone(_parse_timezone(tz_name))
        except ValueError:
            continue
        occupied.add(dt.strftime("%H:%M"))
    return occupied


def _channel_hour_metrics(conn, channel_id: str, tz_name: str) -> dict:
    rows = conn.execute(
        """
        SELECT pt.updated_at, pt.scheduled_at, pr.commission_value, pm.clicks
        FROM publish_target pt
        JOIN post p ON p.id = pt.post_id
        JOIN product pr ON pr.id = p.product_id
        LEFT JOIN post_metrics pm ON pm.post_id = p.id
        WHERE pt.channel_id = ?
          AND pt.status = 'SUCCESS'
        """,
        (channel_id,),
    ).fetchall()
    metrics = {}
    tzinfo = _parse_timezone(tz_name)
    for row in rows:
        iso_value = row["updated_at"] or row["scheduled_at"]
        try:
            local_dt = datetime.fromisoformat(iso_value).astimezone(tzinfo)
        except ValueError:
            continue
        clicks = row["clicks"] or 0
        if clicks <= 0:
            score = 0.0
        else:
            score = (row["commission_value"] or 0) / clicks
        metrics.setdefault(local_dt.strftime("%H:%M"), []).append(score)
    return metrics


def _matched_niches(product, configured_niches: list[str]) -> list[str]:
    return [code for code in configured_niches if code in niche.NICHES and not niche.match_reasons(product, [code])]


def candidate_channels(conn, product, now_utc: datetime) -> list:
    rows = conn.execute(
        """
        SELECT *
        FROM channel
        WHERE platform='threads'
          AND status='ACTIVE'
          AND COALESCE(enabled, 1)=1
          AND COALESCE(auto_schedule_enabled, 0)=1
        ORDER BY code
        """
    ).fetchall()
    candidates = []
    for row in rows:
        niches = []
        try:
            niches = json.loads(row["niches"] or "[]")
        except (TypeError, ValueError):
            niches = []
        if not niches:
            continue
        reasons = niche.match_reasons(product, niches)
        if reasons:
            continue
        matched_niches = _matched_niches(product, niches)
        if not matched_niches:
            continue
        if _quota_count_for_local_date(
            conn,
            row["id"],
            row["posting_timezone"],
            now_utc.astimezone(_parse_timezone(row["posting_timezone"])).date(),
        ) >= _core_daily_cap(row):
            continue
        payload = dict(row)
        payload["matched_niches"] = matched_niches
        payload["match_count"] = len(matched_niches)
        candidates.append(payload)
    return candidates


def rank_slots(conn, channel_id: str, local_date, slots) -> list:
    row = conn.execute("SELECT posting_timezone FROM channel WHERE id=?", (channel_id,)).fetchone()
    tz_name = row["posting_timezone"] if row else "Asia/Bangkok"
    metrics = _channel_hour_metrics(conn, channel_id, tz_name)
    ranked = []
    for index, slot in enumerate(slots):
        values = metrics.get(slot, [])
        ranked.append(
            {
                "slot": slot,
                "hour_score": scoring.median(values) or 0.0,
                "sample_size": len(values),
                "configured_index": index,
            }
        )
    enough = [item for item in ranked if item["sample_size"] >= MIN_HOUR_SAMPLE_SIZE]
    fallback = [item for item in ranked if item["sample_size"] < MIN_HOUR_SAMPLE_SIZE]
    enough.sort(key=lambda item: (-item["hour_score"], item["configured_index"], item["slot"]))
    fallback.sort(key=lambda item: item["configured_index"])
    return enough + fallback


def available_slots(conn, channel, now_utc: datetime) -> list[dict]:
    tz_name = channel["posting_timezone"]
    slots = _parse_slots(channel["posting_slots"])
    if not slots:
        return []
    tzinfo = _parse_timezone(tz_name)
    local_now = now_utc.astimezone(tzinfo)
    horizon_utc = now_utc + timedelta(hours=48)
    local_horizon = horizon_utc.astimezone(tzinfo)
    local_date = local_now.date()
    available = []
    while local_date <= local_horizon.date():
        if _quota_count_for_local_date(conn, channel["id"], tz_name, local_date) >= _core_daily_cap(channel):
            local_date = local_date + timedelta(days=1)
            continue
        occupied = _occupied_slots_for_local_date(conn, channel["id"], tz_name, local_date)
        for slot in rank_slots(conn, channel["id"], local_date, slots[: _core_daily_target(channel)]):
            if slot["slot"] in occupied:
                continue
            slot_dt = _slot_datetime(local_date, slot["slot"], tz_name)
            slot_utc = slot_dt.astimezone(timezone.utc)
            if not (now_utc <= slot_utc < horizon_utc):
                continue
            available.append({
                "slot": slot_dt.isoformat(timespec="seconds"),
                "slot_local": slot["slot"],
                "slot_hour_score": slot["hour_score"],
                "slot_sample_size": slot["sample_size"],
            })
        local_date = local_date + timedelta(days=1)
    return available


def _best_slot_for_channel(conn, channel, now_utc: datetime):
    slots = available_slots(conn, channel, now_utc)
    return slots[0] if slots else None


def route_product(conn, product, now_utc) -> dict | None:
    if _queued_or_recently_published_product_exists(conn, product["id"], now_utc):
        return {"reason": "product_already_routed"}

    ranked = []
    for channel in candidate_channels(conn, product, now_utc):
        best_slot = _best_slot_for_channel(conn, channel, now_utc)
        if not best_slot:
            continue
        ranked.append(
            {
                "channel_id": channel["id"],
                "channel_code": channel["code"],
                "matched_niches": channel["matched_niches"],
                "match_count": channel["match_count"],
                "slot": best_slot["slot"],
                "slot_local": best_slot["slot_local"],
                "slot_hour_score": best_slot["slot_hour_score"],
                "reason": "matched_niche",
                "published_today": _quota_count_for_local_date(
                    conn,
                    channel["id"],
                    channel["posting_timezone"],
                    now_utc.astimezone(_parse_timezone(channel["posting_timezone"])).date(),
                ),
            }
        )
    if not ranked:
        return {"reason": "no_candidate_channels"}
    ranked.sort(
        key=lambda item: (
            -item["match_count"],
            -item["slot_hour_score"],
            item["published_today"],
            item["channel_code"],
        )
    )
    return ranked[0]
