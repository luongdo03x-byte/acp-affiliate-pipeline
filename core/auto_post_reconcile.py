"""Selective reconciliation extensions for Auto Post plans.

The base plan engine owns target/product replacement and price snapshots. This
module adds the two approved selective refresh rules that must happen before the
existing publisher runs:

- source image changed -> rebuild only the composited media, keep caption/product;
- caption no longer passes current rules -> regenerate caption for the same
  product, keep slot/product.
"""
from __future__ import annotations

from . import auto_post_plans, pipeline
from .db import audit, now

_INSTALLED = False


def _refresh_image(conn, plan_id: str) -> tuple[bool, str | None]:
    plan, target, post, channel, product = auto_post_plans._context(conn, plan_id)
    if product is None:
        return False, "product_missing"
    try:
        discount = pipeline.scoring.real_discount_depth(
            conn, product["id"], product["current_price"]
        )
        image_path = pipeline.imaging.compose(
            product,
            pipeline.MEDIA_DIR,
            discount_pct=discount,
            handle=channel["handle"],
        )
        image_url = pipeline.storage.get_storage().put(image_path)
        image_url = str(image_url or "").strip()
        if not image_url:
            return False, "image_refresh_failed"
    except Exception:
        return False, "image_refresh_failed"

    stamp = now()
    conn.execute(
        "UPDATE post SET image_url_composited=?, updated_at=? WHERE id=?",
        (image_url, stamp, post["id"]),
    )
    conn.execute(
        """UPDATE auto_post_plan
           SET content_revision=content_revision+1,
               state='READY', last_change_reason='image_refreshed',
               last_reconciled_at=?, updated_at=?
           WHERE id=?""",
        (stamp, stamp, plan["id"]),
    )
    audit(conn, "auto_post_plan", plan["id"], "image_refreshed", actor="auto_scheduler")
    return True, None


def _regenerate_caption(conn, plan_id: str) -> tuple[bool, str | None]:
    plan, target, post, channel, product = auto_post_plans._context(conn, plan_id)
    if product is None:
        return False, "product_missing"
    template = None
    if post["caption_template_id"]:
        template = conn.execute(
            "SELECT * FROM caption_template WHERE id=?", (post["caption_template_id"],)
        ).fetchone()
    if template is None:
        template = conn.execute(
            "SELECT * FROM caption_template WHERE is_active=1 ORDER BY code LIMIT 1"
        ).fetchone()

    # Reviewer Caption v2 is provider-scoped and ignores the legacy template
    # code for Shopee. Existing/older auto posts may legitimately have no
    # caption_template row, so do not defer them forever just because that
    # legacy row is absent. Non-Shopee behavior keeps the old hard requirement.
    if template is not None:
        template_code = template["code"]
    elif str(product["provider"] or "") == "SHOPEE_AFFILIATE":
        template_code = "reviewer_v2"
    else:
        return False, "caption_template_missing"

    try:
        discount = pipeline.scoring.real_discount_depth(
            conn, product["id"], product["current_price"]
        )
        caption = pipeline.content.generate(
            product,
            template_code,
            post["affiliate_link"],
            discount_pct=discount,
            hook_code=post["variant_code"],
        )
        problems = pipeline.content.validate(
            caption,
            niches=pipeline.channel_niches(conn, channel["id"]),
            post_type=post["post_type"],
        )
    except Exception:
        return False, "caption_regeneration_failed"
    if problems:
        return False, "caption_regeneration_failed"

    stamp = now()
    conn.execute(
        "UPDATE post SET caption_body=?, caption_final=?, updated_at=? WHERE id=?",
        (caption, caption, stamp, post["id"]),
    )
    conn.execute(
        """UPDATE auto_post_plan
           SET content_revision=content_revision+1,
               state='READY', last_change_reason='caption_regenerated',
               last_reconciled_at=?, updated_at=?
           WHERE id=?""",
        (stamp, stamp, plan["id"]),
    )
    audit(conn, "auto_post_plan", plan["id"], "caption_regenerated", actor="auto_scheduler")
    return True, None


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original_reconcile = auto_post_plans.reconcile_plan

    def reconcile_plan(conn, plan_id: str, *, actor: str = "auto_scheduler") -> dict:
        before_plan, _target, before_post, _channel, before_product = auto_post_plans._context(conn, plan_id)
        old_image_snapshot = str(before_plan["product_image_snapshot"] or "")
        source_image_before = auto_post_plans._image_snapshot(before_product)
        image_changed = bool(before_product is not None and source_image_before != old_image_snapshot)

        result = original_reconcile(conn, plan_id, actor=actor)
        if result.get("action") not in ("kept", "refreshed"):
            return result

        plan, _target, post, channel, product = auto_post_plans._context(conn, plan_id)
        if image_changed:
            ok, reason = _refresh_image(conn, plan_id)
            if not ok:
                stamp = now()
                conn.execute(
                    """UPDATE auto_post_plan SET state='REGENERATING',
                           last_change_reason=?, last_reconciled_at=?, updated_at=? WHERE id=?""",
                    (reason, stamp, stamp, plan["id"]),
                )
                return {"ok": False, "action": "defer", "reason": reason,
                        "plan": dict(auto_post_plans._plan(conn, plan["id"]))}
            plan, _target, post, channel, product = auto_post_plans._context(conn, plan_id)

        problems = pipeline.content.validate(
            post["caption_final"],
            niches=pipeline.channel_niches(conn, channel["id"]),
            post_type=post["post_type"],
        )
        if problems:
            ok, reason = _regenerate_caption(conn, plan_id)
            if not ok:
                stamp = now()
                conn.execute(
                    """UPDATE auto_post_plan SET state='REGENERATING',
                           last_change_reason=?, last_reconciled_at=?, updated_at=? WHERE id=?""",
                    (reason, stamp, stamp, plan["id"]),
                )
                return {"ok": False, "action": "defer", "reason": reason,
                        "plan": dict(auto_post_plans._plan(conn, plan["id"]))}
            return {"ok": True, "action": "caption_regenerated",
                    "plan": dict(auto_post_plans._plan(conn, plan["id"]))}

        if image_changed:
            return {"ok": True, "action": "image_refreshed",
                    "plan": dict(auto_post_plans._plan(conn, plan["id"]))}
        return result

    auto_post_plans.reconcile_plan = reconcile_plan
    _INSTALLED = True
