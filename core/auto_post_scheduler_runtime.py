"""Reconcile existing Auto Post plans inside the existing auto-schedule pass.

This does not add a timer or scheduler. It wraps ``pipeline.fill_auto_schedule``
so the same command that maintains the rolling 48-hour slot window also checks
already-created plans for product/price/image/caption drift before their due
publish job runs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import auto_post_plans, pipeline

_INSTALLED = False


def reconcile_window(conn, now_utc=None, hours: int = 48) -> dict:
    current = now_utc or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    start = current.isoformat(timespec="seconds")
    end = (current + timedelta(hours=max(1, int(hours)))).isoformat(timespec="seconds")

    auto_post_plans.sync_existing_auto_targets(conn)
    rows = conn.execute(
        """SELECT id FROM auto_post_plan
           WHERE scheduled_at>=? AND scheduled_at<?
             AND state IN ('PLANNED','READY','REGENERATING')
           ORDER BY scheduled_at,id""",
        (start, end),
    ).fetchall()
    stats = {
        "reconciled": 0,
        "reconcile_replaced": 0,
        "reconcile_refreshed": 0,
        "reconcile_deferred": 0,
        "reconcile_errors": 0,
    }
    for row in rows:
        try:
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

    original_fill = pipeline.fill_auto_schedule

    def fill_auto_schedule(conn, campaign_code: str, now_utc=None, *, ctx=None) -> dict:
        stats = original_fill(conn, campaign_code, now_utc=now_utc, ctx=ctx)
        stats.update(reconcile_window(conn, now_utc=now_utc, hours=48))
        return stats

    pipeline.fill_auto_schedule = fill_auto_schedule
    _INSTALLED = True
