"""Operator UI routes for managing AUTO/MANUAL topics."""
from __future__ import annotations

from flask import Blueprint, redirect, request, url_for

from ..core import topic_admin
from ..core.db import connect

bp = Blueprint("topic_admin", __name__)


def _redirect(message: str | None = None, err: str | None = None):
    values = {}
    for key in ("q", "niche", "auto", "image", "usage", "per_page", "page"):
        value = request.form.get(key)
        if value:
            values[key] = value
    if message:
        values["message"] = message
    if err:
        values["err"] = err
    return redirect(url_for("shopee_image_enrichment.page", **values))


def _run(action, success: str):
    conn = connect()
    try:
        try:
            action(conn)
        except ValueError as exc:
            return _redirect(err=str(exc))
    finally:
        conn.close()
    return _redirect(message=success)


@bp.post("/topics/<topic_id>/rename")
def rename(topic_id):
    name = str(request.form.get("name") or "").strip()
    return _run(
        lambda conn: topic_admin.rename_topic(conn, topic_id, name),
        "Đã đổi tên chủ đề. Mã topic và routing hiện có được giữ nguyên.",
    )


@bp.post("/topics/<topic_id>/merge")
def merge(topic_id):
    target_id = str(request.form.get("target_id") or "").strip()
    return _run(
        lambda conn: topic_admin.merge_topic(conn, topic_id, target_id),
        "Đã merge chủ đề; sản phẩm, alias và cấu hình kênh đã chuyển sang topic đích.",
    )


@bp.post("/topics/<topic_id>/delete")
def delete(topic_id):
    return _run(
        lambda conn: topic_admin.delete_topic(conn, topic_id),
        "Đã xóa chủ đề khỏi routing. Lịch sử liên kết vẫn được giữ để audit.",
    )


def register_topic_admin(app):
    app.register_blueprint(bp)

    @app.context_processor
    def inject_topic_admin():
        if request.path != "/sanpham/shopee":
            return {}
        conn = connect()
        try:
            return {"dynamic_topics_admin": topic_admin.list_manageable(conn)}
        except Exception:
            return {"dynamic_topics_admin": []}
        finally:
            conn.close()
