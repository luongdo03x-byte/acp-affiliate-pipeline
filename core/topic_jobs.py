"""Background Dynamic Topic discovery jobs.

CSV import remains transactional/network-free. A successful import may enqueue
this follow-up job; the normal ACP worker executes deterministic topic discovery.
"""
from __future__ import annotations

import hashlib

from . import topic_engine
from .jobs import enqueue, handler

JOB_TYPE = "SHOPEE_DISCOVER_TOPICS"


def queue_discovery(conn, product_ids) -> dict:
    ids = sorted({str(value or "").strip() for value in (product_ids or []) if str(value or "").strip()})
    if not ids:
        return {"queued": 0, "duplicate": 0}
    version_parts = []
    for product_id in ids:
        row = conn.execute("SELECT updated_at FROM product WHERE id=?", (product_id,)).fetchone()
        version_parts.append(f"{product_id}:{row['updated_at'] if row else 'missing'}")
    digest = hashlib.sha256("|".join(version_parts).encode("utf-8")).hexdigest()[:20]
    job_id = enqueue(
        conn,
        JOB_TYPE,
        {"product_count": len(ids)},
        priority=-5,
        idempotency_key=f"shopee-topic-discovery:{digest}",
    )
    return {"queued": 1 if job_id else 0, "duplicate": 0 if job_id else 1}


@handler(JOB_TYPE)
def discover_topics_job(conn, payload: dict, ctx: dict | None = None):
    return topic_engine.discover_dynamic_topics(conn)
