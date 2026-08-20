"""Authenticated operator workspace for official Shopee Affiliate CSV imports."""
from __future__ import annotations

import os
import sqlite3
import threading

from flask import Blueprint, render_template, request

from ..core.db import connect
from ..core.shopee_csv_batches import consume_preview, issue_preview, peek_preview
from ..core.shopee_csv_import import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_ROWS,
    ShopeeCsvError,
    dedupe_upload_rows,
    import_rows,
    parse_shopee_affiliate_csv,
    preview_rows_against_db,
)


bp = Blueprint("shopee_csv_import", __name__)
_confirm_lock = threading.Lock()
MAX_TOTAL_BYTES = MAX_FILES * MAX_FILE_BYTES


def _pending_review_count():
    conn = connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM post WHERE status IN ('PENDING_REVIEW','DRAFT')"
        ).fetchone()[0]
    finally:
        conn.close()


def _safe_filename(value: str) -> str:
    name = os.path.basename(str(value or "").replace("\\", "/")).strip()
    return name[:160] or "upload.csv"


def _summary(rows, *, files: int) -> dict:
    counts = {
        "files": int(files),
        "rows": len(rows or []),
        "new": 0,
        "updated": 0,
        "unchanged": 0,
        "duplicate": 0,
        "error": 0,
    }
    mapping = {
        "NEW": "new",
        "UPDATED": "updated",
        "UNCHANGED": "unchanged",
        "DUPLICATE_IN_UPLOAD": "duplicate",
        "ERROR": "error",
    }
    for result in rows or []:
        key = mapping.get(result.status)
        if key:
            counts[key] += 1
    counts["valid_unique"] = counts["new"] + counts["updated"] + counts["unchanged"]
    return counts


def _render(*, rows=None, summary=None, preview_token=None, import_summary=None,
            err=None, status=200):
    return render_template(
        "shopee_csv_import.html",
        page="shopee-csv-import",
        rows=rows or [],
        summary=summary,
        preview_token=preview_token,
        import_summary=import_summary,
        err=err,
        max_files=MAX_FILES,
        max_file_mib=MAX_FILE_BYTES // (1024 * 1024),
        max_rows=MAX_ROWS,
        pending_review=_pending_review_count(),
    ), status


@bp.get("/sanpham/shopee-import")
def page():
    return _render()


@bp.post("/sanpham/shopee-import/preview")
def preview():
    files = [uploaded for uploaded in request.files.getlist("files") if uploaded and uploaded.filename]
    if not files:
        return _render(err="Chọn ít nhất một file CSV Shopee Affiliate.", status=400)
    if len(files) > MAX_FILES:
        return _render(err=f"Tối đa {MAX_FILES} file CSV mỗi lần preview.", status=400)

    parsed_rows = []
    total_bytes = 0
    for uploaded in files:
        filename = _safe_filename(uploaded.filename)
        if not filename.lower().endswith(".csv"):
            return _render(err=f"Chỉ hỗ trợ file .csv: {filename}", status=400)
        data = uploaded.stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            return _render(err=f"File {filename} vượt giới hạn 5 MiB.", status=400)
        total_bytes += len(data)
        if total_bytes > MAX_TOTAL_BYTES:
            return _render(err="Tổng dung lượng upload vượt giới hạn.", status=400)
        try:
            rows = parse_shopee_affiliate_csv(data, filename)
        except ShopeeCsvError as exc:
            return _render(err=f"{filename}: {exc}", status=400)
        parsed_rows.extend(rows)
        if len(parsed_rows) > MAX_ROWS:
            return _render(err=f"Tối đa {MAX_ROWS:,} dòng mỗi lần preview.".replace(",", "."), status=400)

    if not parsed_rows:
        return _render(err="CSV không có dòng sản phẩm.", status=400)

    deduped = dedupe_upload_rows(parsed_rows)
    conn = connect()
    try:
        preview_rows = preview_rows_against_db(conn, deduped)
    finally:
        conn.close()

    summary = _summary(preview_rows, files=len(files))
    issued = issue_preview(preview_rows, summary)
    return _render(rows=preview_rows, summary=summary, preview_token=issued["token"])


@bp.post("/sanpham/shopee-import/confirm")
def confirm():
    token = str(request.form.get("preview_token", "") or "").strip()
    if not token:
        return _render(err="Thiếu phiên preview.", status=410)

    # Serialize confirmation so the same one-time token cannot be imported by
    # two concurrent requests while still remaining retryable after DB failure.
    with _confirm_lock:
        batch = peek_preview(token)
        if batch is None:
            return _render(err="Phiên preview đã hết hạn hoặc đã được sử dụng.", status=410)

        conn = connect()
        try:
            result = import_rows(conn, batch["rows"])
        except sqlite3.DatabaseError:
            return _render(
                rows=batch["rows"],
                summary=batch["summary"],
                preview_token=token,
                err="Không thể import vào Product Pool. Dữ liệu chưa được xác nhận; hãy thử lại.",
                status=500,
            )
        finally:
            conn.close()

        consume_preview(token)
        return _render(import_summary=result)


def register_shopee_csv_import_routes(app):
    app.register_blueprint(bp)
