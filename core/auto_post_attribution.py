"""Attribution hardening for in-place Auto Post product replacement.

Artifact preparation allocates a fresh candidate post id because the normal
scheduler uses it before inserting a new post. Control Center replacement is
intentionally different: it reuses the existing post/target/slot. Rebind the
stored Shopee attribution payload to that existing post after replacement so
analytics/audit never point at an unpersisted temporary id.
"""
from __future__ import annotations

import json

from . import auto_post_plans
from .db import now

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_replace = auto_post_plans.replace_product

    def replace_product(
        conn,
        plan_id: str,
        product_id: str,
        actor: str = "operator",
        reason: str = "manual_product_change",
    ) -> dict:
        result = original_replace(
            conn,
            plan_id,
            product_id,
            actor=actor,
            reason=reason,
        )
        plan = conn.execute(
            "SELECT post_id,product_id FROM auto_post_plan WHERE id=?",
            (str(plan_id),),
        ).fetchone()
        if not plan or not plan["post_id"] or not plan["product_id"]:
            return result
        product = conn.execute(
            "SELECT provider FROM product WHERE id=?", (plan["product_id"],)
        ).fetchone()
        if not product or str(product["provider"] or "") != "SHOPEE_AFFILIATE":
            return result
        post = conn.execute(
            "SELECT sub_id_payload FROM post WHERE id=?", (plan["post_id"],)
        ).fetchone()
        try:
            payload = json.loads(post["sub_id_payload"] or "{}") if post else {}
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload["post_id"] = str(plan["post_id"])
        payload["product_id"] = str(plan["product_id"])
        conn.execute(
            "UPDATE post SET sub_id_payload=?, updated_at=? WHERE id=?",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True), now(), plan["post_id"]),
        )
        return result

    auto_post_plans.replace_product = replace_product
    _INSTALLED = True
