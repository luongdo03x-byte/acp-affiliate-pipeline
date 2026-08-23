"""Background controller for enriching every missing Shopee image.

The controller never holds a web request open and never creates a second
scheduler. It uses the existing ``job_queue`` and normal ACP worker. A small
pump job queues at most 20 product-enrichment jobs at a time, then schedules the
next pump for a later worker pass.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .db import now
from .jobs import enqueue, handler
from .shopee_enrichment_jobs import queue_pending_products
from .shopee_image_enrichment import (
    DOWNLOADING,
    FAILED,
    NEEDS_HELPER,
    PENDING,
    PUBLIC_FETCH,
    READY,
    backfill_missing,
    reset_for_retry,
)

JOB_TYPE = "SHOPEE_ENRICH_ALL_PUMP"
BATCH_SIZE = 20
STATE_KEY = "shopee_enrich_all.state"
UPDATED_KEY = "shopee_enrich_all.updated_at"
RUN_ID_KEY = "shopee_enrich_all.run_id"
RUNNING = "RUNNING"
PAUSED = "PAUSED"
COMPLETE = "COMPLETE"
IDLE = "IDLE"


def _get_setting(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM system_setting WHERE key=?", (key,)).fetchone()
    return str(row["value"] if row else default)


def _set_setting(conn, key: str, value: str, actor: str = "operator") -> None:
    stamp = now()
    conn.execute(
        """INSERT INTO system_setting (key, value, updated_at, updated_by)
           VALUES (?,?,?,?)
           ON CONFLICT(key) DO UPDATE SET
             value=excluded.value,
             updated_at=excluded.updated_at,
             updated_by=excluded.updated_by""",
        (key, str(value), stamp, actor),
    )


def _set_state(conn, state: str, *, actor: str = "operator") -> str:
    _set_setting(conn, STATE_KEY, state, actor)
    _set_setting(conn, UPDATED_KEY, now(), actor)
    return state


def _next_run_id(conn) -> str:
    current = _get_setting(conn, RUN_ID_KEY, "0")
    try:
        number = int(current) + 1
    except ValueError:
        number = 1
    _set_setting(conn, RUN_ID_KEY, str(number), "system")
    return str(number)


def _status_counts(conn) -> dict:
    counts = {
        "total": 0,
        "ready": 0,
        "pending": 0,
        "needs_helper": 0,
        "failed": 0,
        "working": 0,
    }
    rows = conn.execute(
        """SELECT j.status, COUNT(*) AS n
           FROM shopee_image_enrichment_job j
           JOIN product p ON p.id=j.product_id
           WHERE p.provider='SHOPEE_AFFILIATE'
           GROUP BY j.status"""
    ).fetchall()
    for row in rows:
        state = str(row["status"] or "")
        number = int(row["n"] or 0)
        counts["total"] += number
        if state == READY:
            counts["ready"] += number
        elif state == PENDING:
            counts["pending"] += number
        elif state == NEEDS_HELPER:
            counts["needs_helper"] += number
        elif state == FAILED:
            counts["failed"] += number
        elif state in (PUBLIC_FETCH, DOWNLOADING):
            counts["working"] += number
    return counts


def status(conn) -> dict:
    counts = _status_counts(conn)
    state = _get_setting(conn, STATE_KEY, IDLE) or IDLE
    if state == RUNNING and counts["pending"] == 0 and counts["working"] == 0:
        state = _set_state(conn, COMPLETE, actor="system")
    processed = counts["ready"] + counts["needs_helper"] + counts["failed"]
    percent = round((processed / counts["total"] * 100.0), 1) if counts["total"] else 100.0
    return {
        **counts,
        "state": state,
        "processed": processed,
        "percent": percent,
        "updated_at": _get_setting(conn, UPDATED_KEY, ""),
    }


def _enqueue_pump(conn, *, delay_seconds: int = 60) -> int:
    run_id = _get_setting(conn, RUN_ID_KEY, "0") or "0"
    due = (datetime.now(timezone.utc) + timedelta(seconds=max(0, int(delay_seconds)))).isoformat(timespec="seconds")
    # Minute bucket keeps repeated calls idempotent while still allowing future
    # pump generations for the same bulk run.
    minute_bucket = due[:16]
    return enqueue(
        conn,
        JOB_TYPE,
        {"run_id": run_id},
        priority=-20,
        run_after=due,
        idempotency_key=f"shopee-enrich-all-pump:{run_id}:{minute_bucket}",
    )


def pump(conn, limit: int = BATCH_SIZE) -> dict:
    if _get_setting(conn, STATE_KEY, IDLE) != RUNNING:
        return {"queued": 0, "duplicate": 0, "skipped": 0, "state": _get_setting(conn, STATE_KEY, IDLE)}

    rows = conn.execute(
        """SELECT j.product_id
           FROM shopee_image_enrichment_job j
           JOIN product p ON p.id=j.product_id
           WHERE p.provider='SHOPEE_AFFILIATE' AND j.status=?
           ORDER BY j.created_at, j.product_id
           LIMIT ?""",
        (PENDING, min(BATCH_SIZE, max(0, int(limit)))),
    ).fetchall()
    result = queue_pending_products(conn, [row["product_id"] for row in rows])
    current = status(conn)
    if current["state"] == RUNNING and (current["pending"] > 0 or current["working"] > 0):
        _enqueue_pump(conn, delay_seconds=60)
    return {**result, "state": current["state"]}


def start(conn) -> dict:
    backfill_missing(conn)
    _next_run_id(conn)
    _set_state(conn, RUNNING)
    pump(conn)
    return status(conn)


def pause(conn) -> dict:
    current = _get_setting(conn, STATE_KEY, IDLE)
    if current == RUNNING:
        _set_state(conn, PAUSED)
    return status(conn)


def resume(conn) -> dict:
    current = status(conn)
    if current["pending"] == 0 and current["working"] == 0:
        _set_state(conn, COMPLETE)
        return status(conn)
    _set_state(conn, RUNNING)
    _enqueue_pump(conn, delay_seconds=0)
    return status(conn)


def retry_failed(conn) -> dict:
    rows = conn.execute(
        """SELECT j.product_id
           FROM shopee_image_enrichment_job j
           JOIN product p ON p.id=j.product_id
           WHERE p.provider='SHOPEE_AFFILIATE' AND j.status=?
           ORDER BY j.product_id""",
        (FAILED,),
    ).fetchall()
    reset = 0
    for row in rows:
        if reset_for_retry(conn, row["product_id"]) == PENDING:
            reset += 1
    if reset:
        if _get_setting(conn, STATE_KEY, IDLE) in (IDLE, COMPLETE):
            _next_run_id(conn)
        _set_state(conn, RUNNING)
        pump(conn)
    result = status(conn)
    result["reset"] = reset
    return result


@handler(JOB_TYPE)
def pump_job(conn, payload: dict, ctx: dict | None = None):
    expected = str((payload or {}).get("run_id") or "")
    current = _get_setting(conn, RUN_ID_KEY, "0")
    if expected and expected != current:
        return {"queued": 0, "stale": True}
    return pump(conn)
