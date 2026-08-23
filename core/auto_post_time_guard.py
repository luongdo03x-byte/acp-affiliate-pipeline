"""Guard Auto Posting time edits against all existing live publish targets."""
from __future__ import annotations

from datetime import datetime, timezone

from . import auto_post_plans, auto_scheduler

_INSTALLED = False


def _utc(value: str) -> str:
    parsed = datetime.fromisoformat(str(value or ""))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _other_live_target_occupies(conn, channel_id: str, target_id: str, scheduled_at: str) -> bool:
    desired = _utc(scheduled_at)
    marks = ",".join("?" for _ in auto_scheduler.LIVE_TARGET_STATUSES)
    rows = conn.execute(
        f"""SELECT id,scheduled_at FROM publish_target
            WHERE channel_id=? AND id<>? AND status IN ({marks})""",
        (str(channel_id), str(target_id), *auto_scheduler.LIVE_TARGET_STATUSES),
    ).fetchall()
    for row in rows:
        try:
            if _utc(row["scheduled_at"]) == desired:
                return True
        except (TypeError, ValueError):
            continue
    return False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_move_slot = auto_post_plans.move_slot

    def move_slot(conn, plan_id: str, scheduled_at: str, actor: str = "operator") -> dict:
        plan, target, _post, channel, _product = auto_post_plans._context(conn, plan_id)
        if _other_live_target_occupies(conn, channel["id"], target["id"], scheduled_at):
            raise ValueError("Slot này đã có bài khác")
        return original_move_slot(conn, plan_id, scheduled_at, actor=actor)

    auto_post_plans.move_slot = move_slot
    _INSTALLED = True
