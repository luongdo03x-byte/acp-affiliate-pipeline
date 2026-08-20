import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import niche, scoring

LIVE_TARGET_STATUSES = ("SCHEDULED", "PENDING", "RUNNING", "SUCCESS")
QUEUED_POST_STATUSES = ("DRAFT", "PENDING_REVIEW", "APPROVED", "SCHEDULED")
MIN_HOUR_SAMPLE_SIZE = 5


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
        ) >= (row["daily_post_cap"] or 0):
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


def _best_slot_for_channel(conn, channel, now_utc: datetime):
    tz_name = channel["posting_timezone"]
    slots = _parse_slots(channel["posting_slots"])
    if not slots:
        return None
    tzinfo = _parse_timezone(tz_name)
    local_now = now_utc.astimezone(tzinfo)
    for day_offset in range(2):
        local_date = (local_now + timedelta(days=day_offset)).date()
        if _quota_count_for_local_date(conn, channel["id"], tz_name, local_date) >= channel["daily_post_cap"]:
            continue
        occupied = _occupied_slots_for_local_date(conn, channel["id"], tz_name, local_date)
        for slot in rank_slots(conn, channel["id"], local_date, slots[: channel["daily_post_target"]]):
            if slot["slot"] in occupied:
                continue
            slot_dt = _slot_datetime(local_date, slot["slot"], tz_name)
            if slot_dt <= local_now:
                continue
            return {
                "slot": slot_dt.isoformat(timespec="seconds"),
                "slot_local": slot["slot"],
                "slot_hour_score": slot["hour_score"],
                "slot_sample_size": slot["sample_size"],
            }
    return None


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
