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
    reset_for_retry,
    run_batch,
)
from ..core.shopee_product_pool import build_product_pool


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


def _workspace_redirect(*, status=None, message=None, err=None):
    image = _safe_status(
        status
        or request.form.get("image")
        or request.form.get("status")
        or request.args.get("image")
        or request.args.get("status")
    )
    values = {"image": image}
    for key in ("q", "niche", "auto", "usage", "per_page", "page"):
        value = request.form.get(key) or request.args.get(key)
        if value:
            values[key] = value
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
    values = request.args.to_dict(flat=True)
    # Backward-compatible alias for links/tests created before Product Pool v2.
    if "image" not in values and "status" in values:
        values["image"] = _safe_status(values.get("status"))

    conn = connect()
    try:
        pool = build_product_pool(conn, values)
    finally:
        conn.close()
    return render_template(
        "shopee_image_enrichment.html",
        page="shopee-product-pool",
        items=pool["items"],
        filters=pool["filters"],
        status_filter=pool["filters"]["image"],
        summary=pool["summary"],
        niche_stats=pool["niche_stats"],
        niche_options=pool["niche_options"],
        pagination=pool["pagination"],
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
