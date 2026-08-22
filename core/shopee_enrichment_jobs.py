"""Immediate, non-publish queue integration for Shopee image enrichment.

The official CSV import remains transactional and network-free.  After a
successful import, callers may enqueue one ``SHOPEE_ENRICH_PRODUCT`` job for
each touched Product whose enrichment generation is still ``PENDING``.  The
normal ACP worker then executes the existing bounded ``enrich_product``
primitive on its next pass.
"""
from __future__ import annotations

import os

from ..adapters.safe_http import SafeHttpClient
from ..adapters.shopee_affiliate import ProductMetadataResolver
from . import shopee_image_enrichment as enrichment, storage
from .jobs import enqueue, handler

JOB_TYPE = "SHOPEE_ENRICH_PRODUCT"
MEDIA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "var", "media")


def _pending_generation(conn, product_id: str):
    return conn.execute(
        """SELECT status, updated_at
           FROM shopee_image_enrichment_job
           WHERE product_id=?""",
        (str(product_id),),
    ).fetchone()


def queue_pending_products(conn, product_ids) -> dict:
    """Queue touched PENDING products without duplicating one generation."""
    queued = 0
    duplicate = 0
    skipped = 0
    seen = set()
    for raw_product_id in product_ids or []:
        product_id = str(raw_product_id or "").strip()
        if not product_id or product_id in seen:
            continue
        seen.add(product_id)
        generation = _pending_generation(conn, product_id)
        if generation is None or str(generation["status"] or "") != enrichment.PENDING:
            skipped += 1
            continue
        generation_key = str(generation["updated_at"] or "unknown")
        job_id = enqueue(
            conn,
            JOB_TYPE,
            {"product_id": product_id},
            idempotency_key=f"shopee-enrich:{product_id}:{generation_key}",
        )
        if job_id:
            queued += 1
        else:
            duplicate += 1
    return {
        "queued": queued,
        "duplicate": duplicate,
        "skipped": skipped,
    }


@handler(JOB_TYPE)
def enrich_shopee_product(conn, payload: dict, ctx: dict | None = None):
    """Execute one current PENDING generation through existing safe primitives."""
    product_id = str((payload or {}).get("product_id") or "").strip()
    if not product_id:
        raise ValueError("Thiếu product_id cho Shopee enrichment job.")

    generation = _pending_generation(conn, product_id)
    if generation is None or str(generation["status"] or "") != enrichment.PENDING:
        # A stale duplicate may run after Helper/recovery/import already moved
        # the product forward.  Treat it as an idempotent no-op.
        return {"product_id": product_id, "status": None, "skipped": True}

    ctx = ctx or {}
    resolver = ctx.get("shopee_metadata_resolver") or ProductMetadataResolver()
    media_dir = str(ctx.get("shopee_media_dir") or MEDIA_DIR)
    storage_backend = ctx.get("shopee_storage") or storage.get_storage()
    image_http = ctx.get("shopee_image_http") or SafeHttpClient(
        max_bytes=enrichment.MAX_IMAGE_BYTES
    )
    return enrichment.enrich_product(
        conn,
        product_id,
        metadata_resolver=resolver,
        media_dir=media_dir,
        storage_backend=storage_backend,
        image_http=image_http,
    )
