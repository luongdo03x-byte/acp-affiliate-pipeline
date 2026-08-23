"""Web controls for background Shopee Enrich All."""
from __future__ import annotations

from flask import Blueprint, jsonify, redirect, request, url_for

from ..core import shopee_bulk_enrichment
from ..core.db import connect

bp = Blueprint("shopee_enrich_all", __name__)


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


def _run(action, success_message):
    conn = connect()
    try:
        result = action(conn)
    finally:
        conn.close()
    return _redirect(message=success_message.format(**result))


@bp.post("/sanpham/shopee/enrichment/all/start")
def start():
    return _run(
        shopee_bulk_enrichment.start,
        "Đã bật Enrich toàn bộ: {processed}/{total} sản phẩm đã xử lý.",
    )


@bp.post("/sanpham/shopee/enrichment/all/pause")
def pause():
    return _run(shopee_bulk_enrichment.pause, "Đã tạm dừng Enrich toàn bộ.")


@bp.post("/sanpham/shopee/enrichment/all/resume")
def resume():
    return _run(shopee_bulk_enrichment.resume, "Đã tiếp tục Enrich toàn bộ.")


@bp.post("/sanpham/shopee/enrichment/all/retry-failed")
def retry_failed():
    return _run(
        shopee_bulk_enrichment.retry_failed,
        "Đã đặt lại {reset} sản phẩm lỗi và đưa lại vào hàng đợi.",
    )


@bp.get("/sanpham/shopee/enrichment/all/status")
def progress_status():
    conn = connect()
    try:
        return jsonify(shopee_bulk_enrichment.status(conn))
    finally:
        conn.close()


def register_enrich_all_ui(app):
    app.register_blueprint(bp)

    @app.context_processor
    def inject_enrich_all_status():
        # Only Product Pool renders this widget. Avoid one extra DB connection
        # on every unrelated dashboard page.
        if request.path != "/sanpham/shopee":
            return {}
        conn = connect()
        try:
            return {"enrich_all": shopee_bulk_enrichment.status(conn)}
        except Exception:
            return {"enrich_all": {
                "state": "IDLE", "total": 0, "processed": 0, "ready": 0,
                "pending": 0, "working": 0, "needs_helper": 0, "failed": 0,
                "percent": 0.0, "updated_at": "",
            }}
        finally:
            conn.close()
