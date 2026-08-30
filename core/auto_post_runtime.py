"""Runtime bridge from the existing scheduler/publisher to Auto Post plans.

No second scheduler or publisher is introduced. The existing ``approve_post``
creates targets/jobs, then this layer records an operator-visible plan. The
existing PUBLISH_POST handler is wrapped to reconcile an auto plan immediately
before the real publisher is called.
"""
from __future__ import annotations

from . import auto_post_plans, jobs, pipeline

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_approve = pipeline.approve_post
    original_publish = jobs._handlers.get("PUBLISH_POST") or pipeline.publish_post

    def approve_post(
        conn,
        post_id: str,
        actor: str = "operator",
        caption_override: str = None,
        channel_ids: list = None,
        caption_facebook: str = None,
        caption_instagram: str = None,
        caption_overrides: dict = None,
        scheduled_at: str = None,
        slots_by_channel: dict = None,
        auto_scheduled: bool = False,
    ) -> dict:
        result = original_approve(
            conn,
            post_id,
            actor=actor,
            caption_override=caption_override,
            channel_ids=channel_ids,
            caption_facebook=caption_facebook,
            caption_instagram=caption_instagram,
            caption_overrides=caption_overrides,
            scheduled_at=scheduled_at,
            slots_by_channel=slots_by_channel,
            auto_scheduled=auto_scheduled,
        )
        if result.get("ok") and auto_scheduled:
            for target in result.get("targets") or []:
                auto_post_plans.upsert_from_target(
                    conn,
                    post_id,
                    target["publish_target_id"],
                    reason="auto_scheduled",
                )
        return result

    def publish_post(conn, payload, ctx):
        target_id = str((payload or {}).get("publish_target_id") or "").strip()
        if target_id:
            target = conn.execute("SELECT * FROM publish_target WHERE id=?", (target_id,)).fetchone()
            # Chốt chặn áp dụng cho MỌI target, không riêng Auto. Khi worker
            # treo rồi được thu hồi, các job publish cũ quay lại hàng đợi; phần
            # lớn trong số đó là target thủ công (auto_scheduled=0) không đi qua
            # đường reconcile. Không có chốt này, việc phục hồi sẽ đẩy nguyên
            # loạt caption và giá của mấy ngày trước lên kênh thật.
            if target and auto_post_plans.is_overdue(conn, target_id):
                auto_post_plans.surface_overdue(conn, target_id, "slot_overdue")
                return
            if target and int(target["auto_scheduled"] or 0):
                try:
                    plan = auto_post_plans.upsert_from_target(
                        conn, target["post_id"], target["id"], reason="publish_preflight"
                    )
                    reconciled = auto_post_plans.reconcile_plan(conn, plan["id"])
                except ValueError:
                    reconciled = {"ok": False, "action": "defer", "reason": "plan_reconcile_failed"}
                if reconciled.get("action") == "cancelled":
                    return
                if reconciled.get("action") == "defer":
                    reason = reconciled.get("reason") or "pending"
                    # Hoãn chỉ hợp lý khi slot còn kịp. Quá thời gian ân hạn thì
                    # điều kiện đã không tự hết (catalog quá hạn đồng bộ không
                    # tự làm mới), và hoãn tiếp chỉ tạo vòng lặp im lặng. Đẩy
                    # bài về /duyet để operator nhìn thấy và quyết định.
                    if auto_post_plans.is_overdue(conn, target["id"]):
                        auto_post_plans.surface_overdue(conn, target["id"], reason)
                        return
                    # Reuse existing job-queue rate-limit semantics: the same
                    # publish job returns to READY in 60 minutes without burning
                    # retry budget, while image/data/product recovery continues.
                    from ..adapters.base import RateLimitError
                    raise RateLimitError(f"Auto plan đang chờ reconcile: {reason}")
        result = original_publish(conn, payload, ctx)
        if target_id:
            auto_post_plans.sync_target_state(conn, target_id)
        return result

    pipeline.approve_post = approve_post
    pipeline.publish_post = publish_post
    jobs._handlers["PUBLISH_POST"] = publish_post
    _INSTALLED = True
