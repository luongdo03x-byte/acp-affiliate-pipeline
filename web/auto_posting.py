"""Operator-facing 48-hour Auto Posting Control Center."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, redirect, render_template, request, url_for

from ..adapters import factory
from ..core import auto_post_plans, pipeline
from ..core.db import audit, connect, now
from ..core.system_settings import (
    PUBLISH_WORKER_ENABLED,
    publish_worker_enabled,
    set_system_setting,
)

bp = Blueprint("auto_posting", __name__)


def _pending_review_count() -> int:
    conn = connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM post WHERE status IN ('PENDING_REVIEW','DRAFT')"
        ).fetchone()[0]
    finally:
        conn.close()


def _parse_now(value: str | None):
    if not value:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _localize(item: dict) -> dict:
    row = dict(item)
    try:
        dt = datetime.fromisoformat(row["scheduled_at"])
        tz = ZoneInfo(row.get("posting_timezone") or "Asia/Bangkok")
        row["scheduled_local"] = dt.astimezone(tz).strftime("%d/%m %H:%M")
        row["scheduled_input"] = dt.astimezone(tz).strftime("%Y-%m-%dT%H:%M")
    except Exception:
        row["scheduled_local"] = row.get("scheduled_at") or "—"
        row["scheduled_input"] = ""
    return row


def _parse_slots(raw) -> list[str]:
    try:
        values = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(value) for value in values if str(value or "").strip()]


def _channel_topic_summary(conn, channel) -> tuple[str, str]:
    """Human-readable routing summary without changing routing semantics."""
    rules = conn.execute(
        """SELECT r.rule_mode, t.name
           FROM channel_topic_rule r
           JOIN topic t ON t.id=r.topic_id
           WHERE r.channel_id=? AND t.status='ACTIVE'
           ORDER BY r.rule_mode, t.name""",
        (channel["id"],),
    ).fetchall()
    includes = [row["name"] for row in rules if row["rule_mode"] == "INCLUDE"]
    excludes = [row["name"] for row in rules if row["rule_mode"] == "EXCLUDE"]
    if includes:
        included = " · ".join(includes)
    else:
        try:
            legacy_codes = json.loads(channel["niches"] or "[]")
        except (TypeError, ValueError):
            legacy_codes = []
        if legacy_codes:
            names = []
            for code in legacy_codes:
                topic = conn.execute(
                    "SELECT name FROM topic WHERE code=? AND status='ACTIVE'", (str(code),)
                ).fetchone()
                names.append(topic["name"] if topic else str(code))
            included = " · ".join(names)
        else:
            included = "Tất cả chủ đề"
    excluded = " · ".join(excludes)
    return included, excluded


def _auto_accounts(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT id,code,handle,status,enabled,niches,auto_schedule_enabled,
                  daily_post_target,daily_post_cap,posting_timezone,posting_slots
           FROM channel
           WHERE platform='threads' AND status='ACTIVE' AND enabled=1
           ORDER BY handle, code"""
    ).fetchall()
    accounts = []
    for row in rows:
        item = dict(row)
        item["posting_slots_list"] = _parse_slots(row["posting_slots"])
        item["topic_summary"], item["topic_excludes"] = _channel_topic_summary(conn, row)
        accounts.append(item)
    return accounts


def _redirect(message: str | None = None, err: str | None = None):
    values = {}
    if message:
        values["message"] = message
    if err:
        values["err"] = err
    return redirect(url_for("auto_posting.page", **values))


@bp.get("/auto-posting")
def page():
    now_utc = _parse_now(request.args.get("now"))
    conn = connect()
    try:
        plans = [_localize(item) for item in auto_post_plans.list_window(conn, now_utc, hours=48)]
        grouped = []
        by_channel = {}
        for plan in plans:
            group = by_channel.get(plan["channel_id"])
            if group is None:
                group = {
                    "channel_id": plan["channel_id"],
                    "channel_handle": plan["channel_handle"],
                    "channel_code": plan["channel_code"],
                    "plans": [],
                }
                by_channel[plan["channel_id"]] = group
                grouped.append(group)
            try:
                plan["replacement_candidates"] = auto_post_plans.replacement_candidates(
                    conn, plan["id"], limit=8
                ) if plan["state"] in auto_post_plans.LIVE_STATES else []
            except Exception:
                plan["replacement_candidates"] = []
            group["plans"].append(plan)
        counts = {
            "total": len(plans),
            "ready": sum(1 for p in plans if p["state"] == "READY"),
            "regenerating": sum(1 for p in plans if p["state"] == "REGENERATING"),
            "publishing": sum(1 for p in plans if p["state"] == "PUBLISHING"),
        }
        accounts = _auto_accounts(conn)
        auto_enabled_count = sum(1 for account in accounts if account["auto_schedule_enabled"])
        system_state = {
            "scheduler_enabled": auto_enabled_count > 0,
            "auto_enabled_count": auto_enabled_count,
            "account_count": len(accounts),
            "publish_worker_enabled": publish_worker_enabled(conn),
        }
    finally:
        conn.close()
    return render_template(
        "auto_posting.html",
        page="auto-posting",
        groups=grouped,
        counts=counts,
        accounts=accounts,
        system_state=system_state,
        horizon_hours=48,
        message=request.args.get("message"),
        err=request.args.get("err"),
        pending_review=_pending_review_count(),
    )


@bp.post("/auto-posting/channel/<channel_id>/auto-toggle")
def toggle_channel_auto(channel_id):
    enabled_raw = str(request.form.get("enabled") or "").strip()
    if enabled_raw not in ("0", "1"):
        return _redirect(err="Trạng thái Auto không hợp lệ.")
    conn = connect()
    try:
        channel = conn.execute(
            "SELECT id,platform,status,enabled,handle FROM channel WHERE id=?", (str(channel_id),)
        ).fetchone()
        if not channel or channel["platform"] != "threads":
            return _redirect(err="Chỉ kênh Threads mới dùng được Auto Posting.")
        if channel["status"] != "ACTIVE" or not int(channel["enabled"] or 0):
            return _redirect(err="Kênh Threads chưa ACTIVE/enabled nên chưa thể bật Auto.")
        enabled = int(enabled_raw)
        conn.execute(
            "UPDATE channel SET auto_schedule_enabled=? WHERE id=?", (enabled, channel["id"])
        )
        audit(
            conn,
            "channel",
            channel["id"],
            "auto_schedule_toggled",
            actor="operator",
            detail={"enabled": bool(enabled)},
        )
    finally:
        conn.close()
    return _redirect(message=f"Auto Posting cho {channel['handle']} đã {'BẬT' if enabled else 'TẮT'}.")


@bp.post("/auto-posting/worker-toggle")
def toggle_publish_worker():
    enabled_raw = str(request.form.get("enabled") or "").strip()
    if enabled_raw not in ("0", "1"):
        return _redirect(err="Trạng thái Publish Worker không hợp lệ.")
    conn = connect()
    try:
        enabled = enabled_raw == "1"
        set_system_setting(
            conn,
            PUBLISH_WORKER_ENABLED,
            "1" if enabled else "0",
            actor="operator",
        )
    finally:
        conn.close()
    return _redirect(message=f"Publish Worker đã {'BẬT' if enabled else 'TẮT'}.")


@bp.post("/auto-posting/run-scheduler")
def run_scheduler_now():
    conn = connect()
    try:
        campaign = conn.execute(
            """SELECT code FROM campaign
               WHERE is_active=1
               ORDER BY CASE WHEN code='gd2026' THEN 0 ELSE 1 END, created_at, code
               LIMIT 1"""
        ).fetchone()
        if not campaign:
            return _redirect(err="Chưa có campaign đang hoạt động để tạo lịch Auto.")
        try:
            ctx = factory.build_context()
            stats = pipeline.fill_auto_schedule(
                conn,
                campaign["code"],
                now_utc=datetime.now(timezone.utc),
                ctx=ctx,
            )
        except Exception:
            return _redirect(err="Không tạo được lịch Auto. Kiểm tra cấu hình sản phẩm/kênh tại Vận hành.")
    finally:
        conn.close()
    scheduled = int(stats.get("scheduled", 0) or 0)
    review = int(stats.get("review", 0) or 0)
    skipped = int(stats.get("skipped", 0) or 0)
    return _redirect(
        message=(
            f"Đã chạy scheduler 48h: tạo {scheduled} slot, "
            f"chờ duyệt {review}, bỏ qua {skipped}. Không publish ngay."
        )
    )


@bp.post("/auto-posting/<plan_id>/caption")
def edit_caption(plan_id):
    conn = connect()
    try:
        try:
            auto_post_plans.edit_caption(conn, plan_id, request.form.get("caption", ""), actor="operator")
        except ValueError as exc:
            return _redirect(err=str(exc))
    finally:
        conn.close()
    return _redirect(message="Đã cập nhật caption; lịch đăng vẫn giữ nguyên.")


@bp.post("/auto-posting/<plan_id>/time")
def move_time(plan_id):
    raw = str(request.form.get("scheduled_at") or "").strip()
    timezone_name = str(request.form.get("timezone") or "Asia/Bangkok").strip()
    try:
        local_dt = datetime.fromisoformat(raw)
        local_dt = local_dt.replace(tzinfo=ZoneInfo(timezone_name)) if local_dt.tzinfo is None else local_dt
        slot = local_dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return _redirect(err="Giờ đăng không hợp lệ.")
    conn = connect()
    try:
        try:
            auto_post_plans.move_slot(conn, plan_id, slot, actor="operator")
        except ValueError as exc:
            return _redirect(err=str(exc))
    finally:
        conn.close()
    return _redirect(message="Đã đổi giờ đăng và cập nhật publish job tương ứng.")


@bp.post("/auto-posting/<plan_id>/product")
def replace_product(plan_id):
    product_id = str(request.form.get("product_id") or "").strip()
    conn = connect()
    try:
        try:
            auto_post_plans.replace_product(conn, plan_id, product_id, actor="operator")
        except ValueError as exc:
            return _redirect(err=str(exc))
    finally:
        conn.close()
    return _redirect(message="Đã thay sản phẩm và tạo lại nội dung cho đúng slot.")


@bp.post("/auto-posting/<plan_id>/cancel")
def cancel(plan_id):
    conn = connect()
    try:
        try:
            auto_post_plans.cancel_plan(conn, plan_id, actor="operator")
        except ValueError as exc:
            return _redirect(err=str(exc))
    finally:
        conn.close()
    return _redirect(message="Đã hủy plan; publish job tương ứng đã được vô hiệu hóa.")


def register_auto_posting_routes(app):
    app.register_blueprint(bp)
