"""Operator-facing 48-hour Auto Posting Control Center."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, redirect, render_template, request, url_for

from ..core import auto_post_plans
from ..core.db import connect

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
    finally:
        conn.close()
    return render_template(
        "auto_posting.html",
        page="auto-posting",
        groups=grouped,
        counts=counts,
        horizon_hours=48,
        message=request.args.get("message"),
        err=request.args.get("err"),
        pending_review=_pending_review_count(),
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
