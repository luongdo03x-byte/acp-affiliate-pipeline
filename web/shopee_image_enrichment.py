"""Authenticated Shopee Affiliate Product Pool image-enrichment workspace."""
from __future__ import annotations

import os

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, url_for

from ..adapters.safe_http import SafeHttpClient
from ..adapters.shopee_affiliate import ProductMetadataResolver
from ..core import helper_pairing, storage
from ..core.db import connect
from ..core.shopee_image_enrichment import (
    MAX_BATCH_SIZE,
    MAX_IMAGE_BYTES,
    NEEDS_HELPER,
    ShopeeImageEnrichmentError,
    backfill_missing,
    complete_from_helper,
    enrich_product,
    list_products,
    reset_for_retry,
    run_batch,
)
from .shopee_auto_state import auto_summary, derive_auto_state


bp = Blueprint("shopee_image_enrichment", __name__)
MEDIA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "var",
    "media",
)
_ALLOWED_FILTERS = frozenset({"all", "missing", "ready", "needs_helper", "failed", "pending"})


def _pending_review_count() -> int:
    conn = connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM post WHERE status IN ('PENDING_REVIEW','DRAFT')"
        ).fetchone()[0]
    finally:
        conn.close()


def _safe_status(value: str | None) -> str:
    status = str(value or "all").strip().lower()
    return status if status in _ALLOWED_FILTERS else "all"


def _shopee_product(conn, product_id: str):
    return conn.execute(
        "SELECT * FROM product WHERE id=? AND provider='SHOPEE_AFFILIATE'",
        (str(product_id),),
    ).fetchone()


def _media_dir() -> str:
    return str(current_app.config.get("SHOPEE_ENRICHMENT_MEDIA_DIR") or MEDIA_DIR)


def _storage_backend():
    return current_app.config.get("SHOPEE_ENRICHMENT_STORAGE") or storage.get_storage()


def _metadata_resolver():
    factory = current_app.config.get("SHOPEE_ENRICHMENT_METADATA_RESOLVER_FACTORY")
    return factory() if callable(factory) else ProductMetadataResolver()


def _image_http():
    factory = current_app.config.get("SHOPEE_ENRICHMENT_IMAGE_HTTP_FACTORY")
    return factory() if callable(factory) else SafeHttpClient(max_bytes=MAX_IMAGE_BYTES)


def _summary_counts(conn) -> dict:
    rows = conn.execute(
        """SELECT COALESCE(j.status, 'UNQUEUED') AS status, COUNT(*) AS count
           FROM product p
           LEFT JOIN shopee_image_enrichment_job j ON j.product_id=p.id
           WHERE p.provider='SHOPEE_AFFILIATE'
           GROUP BY COALESCE(j.status, 'UNQUEUED')"""
    ).fetchall()
    counts = {str(row["status"]): int(row["count"]) for row in rows}
    total = sum(counts.values())
    ready = counts.get("READY", 0)
    return {
        "total": total,
        "ready": ready,
        "missing": max(0, total - ready),
        "needs_helper": counts.get("NEEDS_HELPER", 0),
        "failed": counts.get("FAILED", 0),
        "pending": counts.get("PENDING", 0) + counts.get("UNQUEUED", 0),
    }


def _workspace_redirect(*, status=None, message=None, err=None):
    values = {"status": _safe_status(status or request.form.get("status") or request.args.get("status"))}
    if message:
        values["message"] = message
    if err:
        values["err"] = err
    return redirect(url_for("shopee_image_enrichment.page", **values))


def _bounded_message(error: Exception) -> str:
    if isinstance(error, ShopeeImageEnrichmentError):
        return error.user_message
    return "Không thể enrich ảnh Shopee. Hãy thử lại."


@bp.get("/sanpham/shopee")
def page():
    status = _safe_status(request.args.get("status"))
    conn = connect()
    try:
        rows = list_products(conn, status=status, limit=200)
        for item in rows:
            auto = derive_auto_state(conn, item)
            item["auto_state"] = auto["state"]
            item["auto_channel_handle"] = auto.get("channel_handle")
            item["auto_scheduled_at"] = auto.get("scheduled_at")
        summary = _summary_counts(conn)
        summary.update(auto_summary(rows))
    finally:
        conn.close()
    return render_template(
        "shopee_image_enrichment.html",
        page="shopee-product-pool",
        items=rows,
        status_filter=status,
        summary=summary,
        message=request.args.get("message"),
        err=request.args.get("err"),
        pending_review=_pending_review_count(),
    )


@bp.post("/sanpham/shopee/enrichment/backfill")
def backfill():
    conn = connect()
    try:
        count = backfill_missing(conn)
    finally:
        conn.close()
    return _workspace_redirect(message=f"Đã đưa {count} sản phẩm thiếu ảnh vào hàng đợi.")


@bp.post("/sanpham/shopee/enrichment/run")
def run_enrichment_batch():
    runner = current_app.config.get("SHOPEE_ENRICHMENT_BATCH_RUNNER") or run_batch
    try:
        result = runner(
            connect,
            limit=MAX_BATCH_SIZE,
            delay_seconds=float(current_app.config.get("SHOPEE_ENRICHMENT_DELAY_SECONDS", 1.5)),
            metadata_resolver_factory=(
                current_app.config.get("SHOPEE_ENRICHMENT_METADATA_RESOLVER_FACTORY")
                or ProductMetadataResolver
            ),
            image_http_factory=(
                current_app.config.get("SHOPEE_ENRICHMENT_IMAGE_HTTP_FACTORY")
                or (lambda: SafeHttpClient(max_bytes=MAX_IMAGE_BYTES))
            ),
            media_dir=_media_dir(),
            storage_backend=_storage_backend(),
        )
    except Exception as exc:
        current_app.logger.warning(
            "Shopee enrichment batch failed: error_type=%s", type(exc).__name__
        )
        return _workspace_redirect(err=_bounded_message(exc))

    message = (
        f"Đã xử lý {int(result.get('processed', 0))}: "
        f"{int(result.get('ready', 0))} READY, "
        f"{int(result.get('needs_helper', 0))} cần Helper, "
        f"{int(result.get('failed', 0))} lỗi."
    )
    return _workspace_redirect(message=message)


@bp.post("/sanpham/shopee/<product_id>/enrich")
def enrich_one(product_id):
    conn = connect()
    try:
        if _shopee_product(conn, product_id) is None:
            abort(404)
        runner = current_app.config.get("SHOPEE_ENRICHMENT_SINGLE_RUNNER") or enrich_product
        result = runner(
            conn,
            product_id,
            metadata_resolver=_metadata_resolver(),
            media_dir=_media_dir(),
            storage_backend=_storage_backend(),
            image_http=_image_http(),
        )
    except ShopeeImageEnrichmentError as exc:
        return _workspace_redirect(err=exc.user_message)
    finally:
        conn.close()
    return _workspace_redirect(message=f"{product_id}: {result.get('status') or 'đã xử lý'}")


@bp.post("/sanpham/shopee/<product_id>/retry")
def retry_one(product_id):
    conn = connect()
    try:
        if _shopee_product(conn, product_id) is None:
            abort(404)
        status = reset_for_retry(conn, product_id)
    finally:
        conn.close()
    if status is None:
        abort(404)
    return _workspace_redirect(message="Đã đặt lại retry budget cho sản phẩm.")


@bp.post("/sanpham/shopee/<product_id>/helper/token")
def helper_token(product_id):
    """Issue the existing one-time Helper token from a server-selected Product."""
    conn = connect()
    try:
        product = _shopee_product(conn, product_id)
        if product is None:
            abort(404)
        product_url = product["product_url"]
    finally:
        conn.close()
    try:
        issued = helper_pairing.issue(product_url)
    except Exception:
        abort(400)
    return jsonify({
        "token": issued["token"],
        "expires_in": issued["expires_in"],
        "product_url": product_url,
    })


@bp.post("/sanpham/shopee/<product_id>/helper/complete")
def helper_complete(product_id):
    token = str(request.form.get("token") or "").strip()
    if not token:
        abort(410)

    conn = connect()
    try:
        product = _shopee_product(conn, product_id)
        if product is None:
            abort(404)
        metadata = helper_pairing.consume_ready_for_product(token, product["product_url"])
        if metadata is None:
            abort(410)
        completer = (
            current_app.config.get("SHOPEE_ENRICHMENT_HELPER_COMPLETER")
            or complete_from_helper
        )
        try:
            result = completer(
                conn,
                product_id,
                metadata,
                media_dir=_media_dir(),
                storage_backend=_storage_backend(),
                image_http=_image_http(),
            )
        except ShopeeImageEnrichmentError as exc:
            return _workspace_redirect(status="failed", err=exc.user_message)
    finally:
        conn.close()
    return _workspace_redirect(
        status="needs_helper" if result.get("status") == NEEDS_HELPER else "all",
        message=f"Chrome Helper đã hoàn tất: {result.get('status') or 'đã xử lý'}.",
    )


def register_shopee_image_enrichment_routes(app):
    app.register_blueprint(bp)
