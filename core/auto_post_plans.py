"""Persistent 48-hour Auto Posting plans and operator controls."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

from . import pipeline, topic_engine
from .db import audit, now, ulid

LIVE_STATES = frozenset({"PLANNED", "READY", "REGENERATING", "PUBLISHING"})
TERMINAL_STATES = frozenset({"PUBLISHED", "CANCELLED", "FAILED"})

# Quá ngần này giờ so với giờ đã lên lịch thì slot coi như lỡ: có đăng được
# nữa cũng không còn đúng ý đồ lịch, và quan trọng hơn là operator cần biết.
# Không có mốc này, một điều kiện không tự hết (catalog quá hạn đồng bộ) sẽ
# khiến job hoãn lại mỗi giờ mãi mãi mà không ai nhìn thấy.
OVERDUE_GRACE_HOURS = 6


def _utc_iso(value: str) -> str:
    parsed = datetime.fromisoformat(str(value or ""))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _target_state(status: str) -> str:
    return {
        "SCHEDULED": "READY",
        "PENDING": "READY",
        "RUNNING": "PUBLISHING",
        "SUCCESS": "PUBLISHED",
        "FAILED": "FAILED",
        "CANCELLED": "CANCELLED",
    }.get(str(status or "").upper(), "PLANNED")


def _image_snapshot(product) -> str:
    if product is None:
        return ""
    for key in ("main_image_url", "image_path_local", "image_url_original"):
        try:
            value = product[key]
        except (IndexError, KeyError):
            value = None
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _plan(conn, plan_id: str):
    return conn.execute("SELECT * FROM auto_post_plan WHERE id=?", (str(plan_id),)).fetchone()


def _context(conn, plan_id: str):
    plan = _plan(conn, plan_id)
    if not plan:
        raise ValueError("Không tìm thấy Auto Post plan")
    target = conn.execute("SELECT * FROM publish_target WHERE id=?", (plan["publish_target_id"],)).fetchone()
    post = conn.execute("SELECT * FROM post WHERE id=?", (plan["post_id"],)).fetchone()
    channel = conn.execute("SELECT * FROM channel WHERE id=?", (plan["channel_id"],)).fetchone()
    product = conn.execute("SELECT * FROM product WHERE id=?", (plan["product_id"],)).fetchone() if plan["product_id"] else None
    if not target or not post or not channel:
        raise ValueError("Auto Post plan không còn đủ target/post/channel")
    return plan, target, post, channel, product


def upsert_from_target(conn, post_id: str, target_id: str, reason: str = "scheduled") -> dict:
    target = conn.execute("SELECT * FROM publish_target WHERE id=?", (str(target_id),)).fetchone()
    post = conn.execute("SELECT * FROM post WHERE id=?", (str(post_id),)).fetchone()
    if not target or not post or target["post_id"] != post["id"]:
        raise ValueError("Không tìm thấy target/post để tạo Auto Post plan")
    if not int(target["auto_scheduled"] or 0):
        raise ValueError("Chỉ tạo plan cho target Auto")
    product = conn.execute("SELECT * FROM product WHERE id=?", (post["product_id"],)).fetchone() if post["product_id"] else None
    scheduled_at = _utc_iso(target["scheduled_at"] or post["scheduled_at"])
    state = _target_state(target["status"])
    stamp = now()
    existing = conn.execute(
        "SELECT * FROM auto_post_plan WHERE publish_target_id=?", (target["id"],)
    ).fetchone()
    price = product["current_price"] if product else None
    image = _image_snapshot(product)
    if existing:
        conn.execute(
            """UPDATE auto_post_plan
               SET channel_id=?, scheduled_at=?, product_id=?, post_id=?, state=?,
                   product_price_snapshot=COALESCE(product_price_snapshot, ?),
                   product_image_snapshot=CASE WHEN COALESCE(product_image_snapshot,'')='' THEN ? ELSE product_image_snapshot END,
                   updated_at=?
               WHERE id=?""",
            (
                target["channel_id"], scheduled_at, post["product_id"], post["id"], state,
                price, image, stamp, existing["id"],
            ),
        )
        return dict(conn.execute("SELECT * FROM auto_post_plan WHERE id=?", (existing["id"],)).fetchone())

    plan_id = ulid()
    conn.execute(
        """INSERT INTO auto_post_plan (
             id, channel_id, scheduled_at, product_id, post_id, publish_target_id,
             state, content_revision, generated_at, last_reconciled_at,
             replacement_count, last_change_reason, product_price_snapshot,
             product_image_snapshot, created_at, updated_at)
           VALUES (?,?,?,?,?,? ,?,1,?,NULL,0,?,?,?, ?,?)""",
        (
            plan_id, target["channel_id"], scheduled_at, post["product_id"], post["id"], target["id"],
            state, stamp, reason, price, image, stamp, stamp,
        ),
    )
    audit(
        conn, "auto_post_plan", plan_id, "created", actor="auto_scheduler",
        detail={"target_id": target["id"], "channel_id": target["channel_id"], "reason": reason},
    )
    return dict(conn.execute("SELECT * FROM auto_post_plan WHERE id=?", (plan_id,)).fetchone())


def sync_existing_auto_targets(conn) -> int:
    rows = conn.execute(
        """SELECT pt.id AS target_id, pt.post_id
           FROM publish_target pt
           LEFT JOIN auto_post_plan ap ON ap.publish_target_id=pt.id
           WHERE pt.auto_scheduled=1 AND ap.id IS NULL
             AND pt.status IN ('SCHEDULED','PENDING','RUNNING','SUCCESS')
           ORDER BY pt.scheduled_at, pt.id"""
    ).fetchall()
    for row in rows:
        upsert_from_target(conn, row["post_id"], row["target_id"], reason="backfill")
    return len(rows)


def list_window(conn, now_utc=None, hours: int = 48) -> list[dict]:
    sync_existing_auto_targets(conn)
    current = now_utc or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    start = current.astimezone(timezone.utc).isoformat(timespec="seconds")
    end = (current.astimezone(timezone.utc) + timedelta(hours=max(1, int(hours)))).isoformat(timespec="seconds")
    rows = conn.execute(
        """SELECT ap.*, ch.handle AS channel_handle, ch.code AS channel_code,
                  ch.posting_timezone, p.caption_final, p.affiliate_link,
                  p.image_url_composited, p.variant_code, p.status AS post_status,
                  pr.name AS product_name, pr.current_price, pr.shop_name,
                  pr.main_image_url, pr.image_path_local, pt.status AS target_status
           FROM auto_post_plan ap
           JOIN channel ch ON ch.id=ap.channel_id
           JOIN post p ON p.id=ap.post_id
           JOIN publish_target pt ON pt.id=ap.publish_target_id
           LEFT JOIN product pr ON pr.id=ap.product_id
           WHERE ap.scheduled_at>=? AND ap.scheduled_at<?
             AND ap.state!='CANCELLED'
           ORDER BY ap.scheduled_at, ch.handle, ap.id""",
        (start, end),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["topic_codes"] = topic_engine.product_topic_codes(conn, item["product_id"]) if item.get("product_id") else []
        item["topic_paths"] = topic_engine.topic_paths_for_product(conn, item["product_id"]) if item.get("product_id") else []
        result.append(item)
    return result


def edit_caption(conn, plan_id: str, caption: str, actor: str = "operator") -> dict:
    plan, target, post, channel, product = _context(conn, plan_id)
    if plan["state"] in TERMINAL_STATES or target["status"] in ("SUCCESS", "CANCELLED"):
        raise ValueError("Plan đã kết thúc, không thể sửa caption")
    text = str(caption or "").strip()
    if not text:
        raise ValueError("Caption không được rỗng")
    problems = pipeline.content.validate(text, niches=pipeline.channel_niches(conn, channel["id"]), post_type=post["post_type"])
    if problems:
        raise ValueError("; ".join(problems))
    stamp = now()
    conn.execute(
        "UPDATE post SET caption_body=?, caption_final=?, updated_at=? WHERE id=?",
        (text, text, stamp, post["id"]),
    )
    conn.execute(
        """UPDATE auto_post_plan SET content_revision=content_revision+1,
               last_change_reason='manual_caption_edit', updated_at=? WHERE id=?""",
        (stamp, plan["id"]),
    )
    audit(conn, "auto_post_plan", plan["id"], "caption_edited", actor=actor)
    return dict(_plan(conn, plan["id"]))


def _matching_publish_jobs(conn, target_id: str, statuses=("READY", "RUNNING")):
    if not statuses:
        return []
    marks = ",".join("?" for _ in statuses)
    rows = conn.execute(
        f"SELECT id, payload, status FROM job_queue WHERE job_type='PUBLISH_POST' AND status IN ({marks})",
        tuple(statuses),
    ).fetchall()
    result = []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        if str(payload.get("publish_target_id") or "") == str(target_id):
            result.append(row)
    return result


def move_slot(conn, plan_id: str, scheduled_at: str, actor: str = "operator") -> dict:
    plan, target, post, channel, product = _context(conn, plan_id)
    if plan["state"] in TERMINAL_STATES or target["status"] not in ("SCHEDULED", "PENDING"):
        raise ValueError("Plan đang chạy hoặc đã kết thúc, không thể đổi giờ")
    slot = _utc_iso(scheduled_at)
    if datetime.fromisoformat(slot) <= datetime.now(timezone.utc):
        raise ValueError("Giờ đăng phải ở tương lai")
    conflict = conn.execute(
        """SELECT id FROM auto_post_plan
           WHERE channel_id=? AND scheduled_at=? AND id<>?
             AND state IN ('PLANNED','READY','REGENERATING','PUBLISHING')
           LIMIT 1""",
        (channel["id"], slot, plan["id"]),
    ).fetchone()
    if conflict:
        raise ValueError("Slot này đã có bài khác")
    stamp = now()
    conn.execute("UPDATE publish_target SET scheduled_at=?, updated_at=? WHERE id=?", (slot, stamp, target["id"]))
    conn.execute("UPDATE post SET scheduled_at=?, updated_at=? WHERE id=?", (slot, stamp, post["id"]))
    for job in _matching_publish_jobs(conn, target["id"], statuses=("READY",)):
        conn.execute("UPDATE job_queue SET run_after=?, updated_at=? WHERE id=?", (slot, stamp, job["id"]))
    conn.execute(
        """UPDATE auto_post_plan SET scheduled_at=?, content_revision=content_revision+1,
               last_change_reason='manual_time_change', updated_at=? WHERE id=?""",
        (slot, stamp, plan["id"]),
    )
    audit(conn, "auto_post_plan", plan["id"], "time_changed", actor=actor, detail={"scheduled_at": slot})
    return dict(_plan(conn, plan["id"]))


def cancel_plan(conn, plan_id: str, actor: str = "operator") -> dict:
    plan, target, post, channel, product = _context(conn, plan_id)
    if target["status"] == "SUCCESS" or plan["state"] == "PUBLISHED":
        raise ValueError("Bài đã đăng, không thể hủy")
    stamp = now()
    conn.execute(
        "UPDATE publish_target SET status='CANCELLED', last_error='cancelled_by_operator', updated_at=? WHERE id=?",
        (stamp, target["id"]),
    )
    for job in _matching_publish_jobs(conn, target["id"], statuses=("READY",)):
        conn.execute(
            """UPDATE job_queue SET status='DONE', last_error='cancelled_by_operator',
                   locked_at=NULL, locked_by=NULL, updated_at=? WHERE id=?""",
            (stamp, job["id"]),
        )
    conn.execute(
        """UPDATE auto_post_plan SET state='CANCELLED', content_revision=content_revision+1,
               last_change_reason='manual_cancel', updated_at=? WHERE id=?""",
        (stamp, plan["id"]),
    )
    live = conn.execute(
        """SELECT 1 FROM publish_target
           WHERE post_id=? AND id<>? AND status IN ('SCHEDULED','PENDING','RUNNING') LIMIT 1""",
        (post["id"], target["id"]),
    ).fetchone()
    if not live:
        conn.execute(
            """UPDATE post SET status='PENDING_REVIEW', scheduled_at=NULL,
                   reject_reason='auto_plan_cancelled', updated_at=? WHERE id=?""",
            (stamp, post["id"]),
        )
    audit(conn, "auto_post_plan", plan["id"], "cancelled", actor=actor)
    return dict(_plan(conn, plan["id"]))


def cancel_all_pending(conn, actor: str = "operator") -> dict:
    """Cancel every Auto plan that has not started publishing.

    RUNNING/PUBLISHING is deliberately excluded: cancelling a target already
    claimed by a worker cannot reliably stop the external publish request.
    """
    rows = conn.execute(
        """SELECT ap.id
           FROM auto_post_plan ap
           JOIN publish_target pt ON pt.id=ap.publish_target_id
           WHERE ap.state IN ('PLANNED','READY','REGENERATING')
             AND pt.status IN ('SCHEDULED','PENDING')
             AND pt.external_post_id IS NULL
           ORDER BY ap.scheduled_at, ap.id"""
    ).fetchall()
    cancelled = 0
    skipped = 0
    for row in rows:
        try:
            cancel_plan(conn, row["id"], actor=actor)
            cancelled += 1
        except ValueError:
            skipped += 1
    running = conn.execute(
        """SELECT COUNT(*) FROM auto_post_plan ap
           JOIN publish_target pt ON pt.id=ap.publish_target_id
           WHERE (ap.state='PUBLISHING' OR pt.status='RUNNING')
             AND pt.external_post_id IS NULL"""
    ).fetchone()[0]
    audit(conn, "auto_post_plan", "bulk-cancel", "bulk_cancelled", actor=actor,
          detail={"cancelled": cancelled, "skipped": skipped, "running": running})
    return {"cancelled": cancelled, "skipped": skipped, "running": running}


def _money_tokens(value) -> set[str]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return set()
    full = f"{number:,}".replace(",", ".") + "đ"
    out = {full, f"{number:,}".replace(",", ".")}
    if number >= 1000:
        short = number / 1000.0
        label = (f"{short:.1f}".rstrip("0").rstrip(".")).replace(".", ",") + "k"
        out.add(label)
    return out


def _replace_price_only(caption: str, old_price, new_price) -> tuple[str, bool]:
    text = str(caption or "")
    new_tokens = _money_tokens(new_price)
    replacement = next(iter(sorted(new_tokens, key=len, reverse=True)), str(new_price))
    changed = False
    for token in sorted(_money_tokens(old_price), key=len, reverse=True):
        if token and token in text:
            text = text.replace(token, replacement)
            changed = True
    return text, changed


def _ensure_shopee_image_work(conn, product_id: str) -> None:
    try:
        from .shopee_image_enrichment import PENDING, READY, reset_for_retry
        from .shopee_enrichment_jobs import queue_pending_products
        row = conn.execute(
            "SELECT status FROM shopee_image_enrichment_job WHERE product_id=?", (product_id,)
        ).fetchone()
        if row and row["status"] != READY:
            if row["status"] != PENDING:
                reset_for_retry(conn, product_id)
            queue_pending_products(conn, [product_id])
    except Exception:
        return


def replace_product(conn, plan_id: str, product_id: str, actor: str = "operator", reason: str = "manual_product_change") -> dict:
    plan, target, post, channel, old_product = _context(conn, plan_id)
    if plan["state"] in TERMINAL_STATES or target["status"] not in ("SCHEDULED", "PENDING"):
        raise ValueError("Plan đang chạy hoặc đã kết thúc, không thể đổi sản phẩm")
    product = conn.execute("SELECT * FROM product WHERE id=?", (str(product_id),)).fetchone()
    if not product:
        raise ValueError("Không tìm thấy sản phẩm")
    if str(product["provider"] or "") != "SHOPEE_AFFILIATE":
        raise ValueError("Control Center hiện chỉ tự thay sản phẩm Shopee Affiliate")
    current = datetime.now(timezone.utc)
    eligible, why = pipeline.current_auto_product_eligibility(
        conn,
        product,
        channel,
        current,
        require_auto_schedule=True,
        exclude_post_id=post["id"],
        slot_at=target["scheduled_at"],
    )
    if not eligible:
        raise ValueError(f"Sản phẩm thay thế chưa đủ điều kiện ({why})")
    campaign = conn.execute("SELECT * FROM campaign WHERE id=?", (post["campaign_id"],)).fetchone()
    template = conn.execute("SELECT * FROM caption_template WHERE id=?", (post["caption_template_id"],)).fetchone()
    if not template:
        template = conn.execute("SELECT * FROM caption_template WHERE is_active=1 ORDER BY code LIMIT 1").fetchone()
    if not campaign or not template:
        raise ValueError("Thiếu campaign/template để tạo lại nội dung")
    prepared = pipeline._prepare_auto_sales_post_artifacts(
        conn, {}, product, campaign, channel, template, post["variant_code"], score=product["score"]
    )
    if not prepared.get("ok"):
        raise ValueError(prepared.get("error") or "Không tạo được nội dung thay thế")
    if prepared.get("problems"):
        raise ValueError("; ".join(prepared["problems"]))
    stamp = now()
    conn.execute(
        """UPDATE post SET product_id=?, caption_body=?, caption_final=?, image_url_composited=?,
               affiliate_link=?, sub_id_payload=?, score=?, status='SCHEDULED', reject_reason=NULL,
               updated_at=? WHERE id=?""",
        (
            product["id"], prepared["caption"], prepared["caption"], prepared["image_url"],
            prepared["affiliate_link"], prepared["sub_id_payload"], prepared.get("score"), stamp, post["id"],
        ),
    )
    replacement_delta = 0 if str(product["id"]) == str(plan["product_id"]) else 1
    conn.execute(
        """UPDATE auto_post_plan SET product_id=?, state='READY',
               content_revision=content_revision+1,
               replacement_count=replacement_count+?, last_change_reason=?,
               product_price_snapshot=?, product_image_snapshot=?, last_reconciled_at=?, updated_at=?
           WHERE id=?""",
        (
            product["id"], replacement_delta, reason, product["current_price"], _image_snapshot(product),
            stamp, stamp, plan["id"],
        ),
    )
    audit(
        conn, "auto_post_plan", plan["id"], "product_replaced", actor=actor,
        detail={"old_product_id": plan["product_id"], "new_product_id": product["id"], "reason": reason},
    )
    return dict(_plan(conn, plan["id"]))


def replacement_candidates(conn, plan_id: str, limit: int = 20) -> list[dict]:
    plan, target, post, channel, current_product = _context(conn, plan_id)
    items = pipeline._candidate_products_for_channel(
        conn, channel, limit=max(1, int(limit)), now_utc=datetime.now(timezone.utc)
    )
    result = []
    for item in items:
        product = item["product"]
        if str(product["id"]) == str(plan["product_id"]):
            continue
        if str(product["provider"] or "") != "SHOPEE_AFFILIATE":
            continue
        eligible, _ = pipeline.current_auto_product_eligibility(
            conn,
            product,
            channel,
            datetime.now(timezone.utc),
            require_auto_schedule=True,
            exclude_post_id=post["id"],
            slot_at=target["scheduled_at"],
        )
        if eligible:
            row = dict(product)
            row["topic_paths"] = topic_engine.topic_paths_for_product(conn, product["id"])
            result.append(row)
    return result[:limit]


def reconcile_plan(conn, plan_id: str, *, actor: str = "auto_scheduler") -> dict:
    plan, target, post, channel, product = _context(conn, plan_id)
    if target["status"] == "SUCCESS":
        conn.execute(
            "UPDATE auto_post_plan SET state='PUBLISHED', last_reconciled_at=?, updated_at=? WHERE id=?",
            (now(), now(), plan["id"]),
        )
        return {"ok": True, "action": "published", "plan": dict(_plan(conn, plan["id"]))}
    if target["status"] == "CANCELLED" or plan["state"] == "CANCELLED":
        return {"ok": False, "action": "cancelled", "plan": dict(plan)}
    if not product:
        eligible, why = False, "product_missing"
    else:
        eligible, why = pipeline.current_auto_product_eligibility(
            conn,
            product,
            channel,
            datetime.now(timezone.utc),
            require_auto_schedule=True,
            exclude_post_id=post["id"],
            slot_at=target["scheduled_at"],
        )
    if not eligible:
        if why == "product_image_not_ready" and product is not None:
            _ensure_shopee_image_work(conn, product["id"])
            stamp = now()
            conn.execute(
                """UPDATE auto_post_plan SET state='REGENERATING', last_change_reason=?,
                       last_reconciled_at=?, updated_at=? WHERE id=?""",
                (why, stamp, stamp, plan["id"]),
            )
            return {"ok": False, "action": "defer", "reason": why, "plan": dict(_plan(conn, plan["id"]))}
        candidates = replacement_candidates(conn, plan["id"], limit=25)
        if not candidates:
            stamp = now()
            conn.execute(
                """UPDATE auto_post_plan SET state='REGENERATING', last_change_reason=?,
                       last_reconciled_at=?, updated_at=? WHERE id=?""",
                (why, stamp, stamp, plan["id"]),
            )
            return {"ok": False, "action": "defer", "reason": why, "plan": dict(_plan(conn, plan["id"]))}
        updated = replace_product(conn, plan["id"], candidates[0]["id"], actor=actor, reason=why)
        return {"ok": True, "action": "replaced", "reason": why, "plan": updated}

    stamp = now()
    changed = False
    if product is not None and plan["product_price_snapshot"] is not None and int(product["current_price"] or 0) != int(plan["product_price_snapshot"] or 0):
        new_caption, price_changed = _replace_price_only(
            post["caption_final"], plan["product_price_snapshot"], product["current_price"]
        )
        if price_changed:
            conn.execute(
                "UPDATE post SET caption_body=?, caption_final=?, updated_at=? WHERE id=?",
                (new_caption, new_caption, stamp, post["id"]),
            )
            conn.execute(
                "UPDATE auto_post_plan SET content_revision=content_revision+1 WHERE id=?",
                (plan["id"],),
            )
            changed = True
        conn.execute(
            "UPDATE auto_post_plan SET product_price_snapshot=? WHERE id=?",
            (product["current_price"], plan["id"]),
        )
    image = _image_snapshot(product)
    if image != str(plan["product_image_snapshot"] or ""):
        conn.execute(
            "UPDATE auto_post_plan SET product_image_snapshot=? WHERE id=?",
            (image, plan["id"]),
        )
        changed = True
    conn.execute(
        """UPDATE auto_post_plan SET state='READY', last_reconciled_at=?,
               last_change_reason=?, updated_at=? WHERE id=?""",
        (stamp, "data_refreshed" if changed else "validated", stamp, plan["id"]),
    )
    return {"ok": True, "action": "refreshed" if changed else "kept", "plan": dict(_plan(conn, plan["id"]))}


def is_overdue(conn, target_id: str, *, now_utc: datetime = None) -> bool:
    """Slot đã trôi qua quá thời gian ân hạn chưa?"""
    row = conn.execute(
        "SELECT scheduled_at FROM publish_target WHERE id=?", (target_id,)
    ).fetchone()
    if not row or not row["scheduled_at"]:
        return False
    try:
        scheduled = datetime.fromisoformat(_utc_iso(row["scheduled_at"]))
    except (TypeError, ValueError):
        return False
    current = now_utc or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current - scheduled > timedelta(hours=OVERDUE_GRACE_HOURS)


def surface_overdue(conn, target_id: str, reason: str, *, now_utc: datetime = None,
                    actor: str = "auto_post_runtime") -> dict:
    """Đưa một slot Auto lỡ giờ về /duyet thay vì hoãn tiếp trong im lặng.

    Bài đã PUBLISHED thì không đụng tới: một publish_target khác của cùng bài
    có thể đã lên sóng, và rút bài đang live về PENDING_REVIEW sẽ huỷ oan các
    target còn lại -- cùng lý do đã ghi ở nhánh ContentViolationError của
    hàng đợi.
    """
    target = conn.execute("SELECT * FROM publish_target WHERE id=?", (target_id,)).fetchone()
    if not target:
        return {"ok": False, "action": "missing"}

    stamp = now()
    message = (
        f"Auto không đăng được đúng giờ ({reason}). "
        f"Dữ liệu sản phẩm cần được làm mới trước khi đăng lại."
    )
    conn.execute(
        "UPDATE publish_target SET status='CANCELLED', last_error=?, updated_at=? WHERE id=?",
        (message[:500], stamp, target_id),
    )
    post = conn.execute("SELECT status FROM post WHERE id=?", (target["post_id"],)).fetchone()
    if post and post["status"] != "PUBLISHED":
        conn.execute(
            "UPDATE post SET status='PENDING_REVIEW', reject_reason=?, updated_at=? WHERE id=?",
            (message[:500], stamp, target["post_id"]),
        )
    conn.execute(
        """UPDATE auto_post_plan SET state='CANCELLED', last_change_reason=?,
               last_reconciled_at=?, updated_at=? WHERE publish_target_id=?""",
        (f"overdue:{reason}"[:200], stamp, stamp, target_id),
    )
    audit(conn, actor, "auto_post_overdue_surfaced", "publish_target", target_id,
          {"reason": reason})
    return {"ok": True, "action": "surfaced", "reason": reason}


def sync_target_state(conn, target_id: str) -> None:
    row = conn.execute(
        """SELECT ap.id, pt.status FROM auto_post_plan ap
           JOIN publish_target pt ON pt.id=ap.publish_target_id
           WHERE ap.publish_target_id=?""",
        (str(target_id),),
    ).fetchone()
    if not row:
        return
    conn.execute(
        "UPDATE auto_post_plan SET state=?, updated_at=? WHERE id=?",
        (_target_state(row["status"]), now(), row["id"]),
    )
