"""Server-rendered Shopee bulk affiliate workspace."""
import os

from flask import Blueprint, render_template, request

from ..core.db import connect
from ..core.shopee_bulk_affiliate import BulkAffiliateError, MAX_BULK_URLS, generate_bulk_links

bp = Blueprint("shopee_bulk", __name__)


def _pending_review_count():
    conn = connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM post WHERE status IN ('PENDING_REVIEW','DRAFT')"
        ).fetchone()[0]
    finally:
        conn.close()


def _summary(results):
    counts = {"created": 0, "linked": 0, "duplicate": 0, "error": 0}
    for row in results or []:
        key = row.status.lower()
        if key in counts:
            counts[key] += 1
    counts["total"] = sum(counts.values())
    return counts


def _render(*, raw_urls="", sub_tag="default", results=None, err=None, status=200):
    return render_template(
        "shopee_bulk_affiliate.html",
        page="san-pham",
        mode="bulk-affiliate",
        raw_urls=raw_urls,
        sub_tag=sub_tag,
        results=results or [],
        summary=_summary(results),
        err=err,
        max_urls=MAX_BULK_URLS,
        affiliate_configured=bool(os.environ.get("SHOPEE_AFFILIATE_ID", "").strip()),
        pending_review=_pending_review_count(),
    ), status


@bp.get("/sanpham/shopee-bulk")
def page():
    return _render()


@bp.post("/sanpham/shopee-bulk/generate")
def generate():
    raw_urls = request.form.get("product_urls", "")
    sub_tag = request.form.get("sub_tag", "default")
    affiliate_id = os.environ.get("SHOPEE_AFFILIATE_ID", "")
    conn = connect()
    try:
        results = generate_bulk_links(raw_urls, affiliate_id, sub_tag=sub_tag, conn=conn)
    except BulkAffiliateError as exc:
        return _render(raw_urls=raw_urls, sub_tag=sub_tag, err=str(exc), status=400)
    finally:
        conn.close()
    return _render(raw_urls=raw_urls, sub_tag=sub_tag, results=results)


def register_shopee_bulk_routes(app):
    app.register_blueprint(bp)
