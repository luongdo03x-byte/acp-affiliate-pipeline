"""Runtime compatibility layer for Dynamic Topic channel routing.

Legacy channel forms still POST a list named ``niches``. Values prefixed with
``!`` are explicit exclusions; all other values are includes. Static system
roots continue to be mirrored into ``channel.niches`` for old scoring/content
safety consumers.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import auto_scheduler, pipeline, scoring, shopee_auto_runtime, topic_engine

_INSTALLED = False


def _topic_aware_shopee_eligibility(
    conn,
    product,
    channel,
    now_utc: datetime,
    *,
    exclude_post_id: str = None,
    slot_at: str = None,
    require_auto_schedule: bool = True,
    persist_topics: bool = True,
):
    """``persist_topics=False`` cho đường hiển thị: quyết định y hệt, nhưng
    không gán chủ đề vào DB. Trang danh sách là một request GET, mà mỗi lệnh
    ghi ở đó phải xếp hàng sau acp-worker nên tốn hàng trăm mili giây."""
    row_get = shopee_auto_runtime._row_get
    if not product or str(row_get(product, "provider") or "") != shopee_auto_runtime.SHOPEE_PROVIDER:
        return False, "product_provider_invalid"
    if not channel or not int(row_get(channel, "enabled", 0) or 0) or row_get(channel, "status") != "ACTIVE":
        return False, "channel_ineligible"
    if require_auto_schedule and not int(row_get(channel, "auto_schedule_enabled", 0) or 0):
        return False, "channel_auto_disabled"
    if int(row_get(product, "is_available", 0) or 0) != 1:
        return False, "product_unavailable"

    affiliate_url = str(row_get(product, "affiliate_url") or "").strip()
    if not shopee_auto_runtime._valid_absolute_http_url(affiliate_url):
        return False, "affiliate_link_invalid"
    if str(row_get(product, "affiliate_link_status") or "").upper() != "READY":
        return False, "affiliate_link_invalid"
    if not shopee_auto_runtime._enrichment_ready(conn, row_get(product, "id")):
        return False, "product_image_not_ready"
    if not shopee_auto_runtime._usable_enriched_image(product):
        return False, "product_image_not_ready"

    _, filters = scoring.active_config(conn)
    if row_get(product, "category_code") in set(filters.get("blocked_categories") or []):
        return False, "blocked_category"
    minimum_commission = int(
        filters.get("min_commission_value", scoring.DEFAULT_FILTERS["min_commission_value"]) or 0
    )
    if int(row_get(product, "commission_value", 0) or 0) < minimum_commission:
        return False, "product_quality_filter"

    # New authoritative routing layer. Empty INCLUDE means all topics; explicit
    # EXCLUDE still wins. System topic safety remains in content.validate().
    # Chủ đề hệ thống của kênh vẫn là điều kiện BẮT BUỘC, không phải tuỳ chọn.
    # Preflight ngay trước giờ đăng dùng đúng phép so khớp này trên cột
    # channel.niches; nếu khâu chọn dễ dãi hơn thì nó sẽ chọn những thứ chắc
    # chắn chết ở preflight, làm mất slot của hàng thật sự hợp kênh.
    #
    # Cụ thể: khi không niche nào khớp, sync_product_system_topics vẫn gắn một
    # chủ đề đoán gần nhất (SYSTEM_FALLBACK). Trước đây tầng chủ đề tin vào nhãn
    # đoán đó và nhận -- trên máy thật là 198 nhãn phỏng đoán, khiến 33/55 bài
    # đã lên lịch bị huỷ ngay trước giờ đăng.
    #
    # Tầng chủ đề vẫn giữ vai trò của nó: thêm luật EXCLUDE và các chủ đề động.
    # Nó lọc HẸP thêm, không nới rộng ra.
    channel_niches = [
        code for code in auto_scheduler._channel_niches(channel)
        if code in auto_scheduler.niche.NICHES
    ]
    # Kênh chưa đặt ngách nào thì bỏ qua kiểm tra -- đúng như preflight
    # (_shopee_preflight_auto_target chỉ so khớp khi danh sách ngách khác rỗng).
    # Chặt hơn preflight cũng sai như dễ dãi hơn: nó loại mất hàng mà preflight
    # sẽ cho qua.
    if channel_niches and auto_scheduler.niche.match_reasons(product, channel_niches):
        return False, "product_no_longer_matches_channel"

    if persist_topics:
        topic_engine.sync_product_system_topics(conn, product)
    if not topic_engine.channel_accepts_product(
        conn, row_get(channel, "id"), row_get(product, "id"),
        product=product, persist=persist_topics,
    ):
        return False, "product_no_longer_matches_channel"

    max_per_category = int(
        filters.get("max_per_category_per_day", scoring.DEFAULT_FILTERS["max_per_category_per_day"]) or 0
    )
    if max_per_category > 0 and pipeline._category_count_for_channel_local_day(
        conn,
        channel,
        row_get(product, "category_code"),
        now_utc,
        exclude_post_id=exclude_post_id,
        slot_at=slot_at,
    ) >= max_per_category:
        return False, "category_day_cap_full"

    if auto_scheduler._queued_or_recently_published_product_exists(
        conn, row_get(product, "id"), now_utc, exclude_post_id=exclude_post_id
    ):
        return False, "product_already_routed"
    return True, "ok"


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_set_channel_niches = pipeline.set_channel_niches

    def set_channel_niches(conn, channel_id: str, codes: list):
        includes = []
        excludes = []
        for raw in codes or []:
            text = str(raw or "").strip()
            if not text:
                continue
            if text.startswith("!"):
                excludes.append(text[1:])
            else:
                includes.append(text)
        # If topic schema is unavailable for a legacy/top-level import, retain
        # old behavior rather than breaking unrelated tools.
        try:
            result = topic_engine.set_channel_rules(conn, channel_id, includes, excludes)
        except Exception as exc:
            if "no such table" in str(exc).lower():
                return original_set_channel_niches(conn, channel_id, includes)
            raise
        return result["includes"]

    pipeline.set_channel_niches = set_channel_niches

    # Shopee runtime's internal candidate function resolves this module global
    # at call time, so replacing it upgrades both candidate and preflight paths
    # without adding another scheduler.
    shopee_auto_runtime._shopee_product_auto_eligibility = _topic_aware_shopee_eligibility
    pipeline._shopee_product_auto_eligibility = _topic_aware_shopee_eligibility

    previous_current = pipeline.current_auto_product_eligibility

    def current_eligibility(conn, product, channel, now_utc, **kwargs):
        if str(shopee_auto_runtime._row_get(product, "provider") or "") == shopee_auto_runtime.SHOPEE_PROVIDER:
            return _topic_aware_shopee_eligibility(conn, product, channel, now_utc, **kwargs)
        return previous_current(conn, product, channel, now_utc, **kwargs)

    pipeline.current_auto_product_eligibility = current_eligibility
    _INSTALLED = True
