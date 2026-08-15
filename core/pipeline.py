"""Điều phối pipeline (BRD mục 7).

    1. Nạp datafeed        ingest_datafeed()
    2. Chấm điểm            plan_content()  -> tạo job GENERATE_CONTENT
    3. Sinh nội dung        handler generate_content
    4. Duyệt thủ công       approve_post() / reject_post()  (do người gọi)
    5. Đăng bài             handler publish_post
    6. Thu chuyển đổi       ingest_postback() / reconcile_transactions()
    7. Hiệu chỉnh           tính lại CR danh mục, dùng ở vòng chấm điểm sau
"""
import json
import os
import random
from datetime import datetime, timedelta, timezone

from . import attribution, content, imaging, niche, scoring, storage
from .db import audit, now, ulid
from .jobs import enqueue, handler

MEDIA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "var", "media")


# ------------------------------------------------------------------ chặng 1

def ingest_datafeed(conn, source, category=None, limit=200) -> dict:
    """Upsert sản phẩm + ghi lịch sử giá. Sản phẩm biến mất khỏi feed được đánh
    dấu không khả dụng chứ không xoá cứng -- xoá là mất lịch sử quy kết."""
    stats = {"inserted": 0, "updated": 0, "price_changed": 0}
    seen = []
    for raw in source.fetch_products(category=category, limit=limit):
        row = conn.execute(
            "SELECT id, current_price FROM product WHERE source=? AND merchant=? AND external_product_id=?",
            (source.name, raw.merchant, raw.external_product_id)).fetchone()
        if row:
            pid = row["id"]
            conn.execute("""UPDATE product SET name=?, description=?, current_price=?, original_price=?,
                            commission_value=?, commission_rate=?, category_code=?, rating=?, review_count=?,
                            sold_count=?, image_url_original=?, image_path_local=?, product_url=?, is_available=1,
                            last_seen_at=?, updated_at=? WHERE id=?""",
                         (raw.name, raw.description, raw.current_price, raw.original_price,
                          raw.commission_value, raw.commission_rate, raw.category_code, raw.rating,
                          raw.review_count, raw.sold_count, raw.image_url_original,
                          getattr(raw, "image_path_local", None), raw.product_url,
                          now(), now(), pid))
            stats["updated"] += 1
            if row["current_price"] != raw.current_price:
                stats["price_changed"] += 1
        else:
            pid = ulid()
            conn.execute("""INSERT INTO product (id, source, merchant, external_product_id, name, description,
                            current_price, original_price, commission_value, commission_rate, category_code,
                            rating, review_count, sold_count, image_url_original, image_path_local, product_url, is_available,
                            last_seen_at, created_at, updated_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)""",
                         (pid, source.name, raw.merchant, raw.external_product_id, raw.name, raw.description,
                          raw.current_price, raw.original_price, raw.commission_value, raw.commission_rate,
                          raw.category_code, raw.rating, raw.review_count, raw.sold_count,
                          raw.image_url_original, getattr(raw, "image_path_local", None), raw.product_url,
                          now(), now(), now()))
            stats["inserted"] += 1
        conn.execute("INSERT INTO product_price_history (product_id, price, observed_at) VALUES (?,?,?)",
                     (pid, raw.current_price, now()))
        seen.append(pid)

    if seen:
        marks = ",".join("?" * len(seen))
        conn.execute(f"UPDATE product SET is_available=0 WHERE id NOT IN ({marks})", seen)
    return stats


# ------------------------------------------------------------------ chặng 2

def plan_content(conn, campaign_code: str, limit: int = 10, rng=None) -> list:
    """Chấm điểm, chọn top-K, tạo job sinh nội dung cho từng sản phẩm."""
    rng = rng or random.Random()
    campaign = conn.execute("SELECT * FROM campaign WHERE code=?", (campaign_code,)).fetchone()
    # Chỉ Threads: chưa có publisher đăng ký cho facebook/instagram (sub-project
    # sau mới thêm), và enabled=0 nghĩa là operator đã tắt kênh -- không được
    # sinh content/job cho những kênh này (spec §8.2, §10).
    channels = conn.execute(
        "SELECT * FROM channel WHERE status='ACTIVE' AND platform='threads' AND enabled=1").fetchall()
    templates = conn.execute("SELECT * FROM caption_template WHERE is_active=1").fetchall()
    if not campaign or not channels or not templates:
        return []

    created = []
    per_channel = max(1, limit // max(1, len(channels)))
    for ch in channels:
        # Mỗi kênh có ngách riêng -> phải chấm điểm riêng, không dùng chung một
        # danh sách ứng viên rồi bốc kênh ngẫu nhiên như trước.
        nl = channel_niches(conn, ch["id"])
        for item in scoring.score_candidates(conn, limit=per_channel, niches=nl):
            tpl = rng.choice(list(templates))
            variant = "A"
            job_id = enqueue(conn, "GENERATE_CONTENT", {
                "product_id": item["product"]["id"], "channel_id": ch["id"],
                "campaign_id": campaign["id"], "template_id": tpl["id"],
                "variant_code": variant, "score": item["score"],
            }, priority=int(item["score"] * 100),
               idempotency_key=f"gen:{item['product']['id']}:{variant}")
            if job_id:
                created.append(job_id)
    return created


def channel_niches(conn, channel_id: str) -> list:
    """Chủ đề của MỘT kênh. Đây là nguồn sự thật -- mỗi kênh một ngách riêng.

    Rỗng nghĩa là kênh đó không lọc theo chủ đề (nhận mọi danh mục).
    """
    row = conn.execute("SELECT niches FROM channel WHERE id=?", (channel_id,)).fetchone()
    if not row:
        return []
    try:
        return json.loads(row["niches"] or "[]")
    except (ValueError, TypeError):
        return []


def set_channel_niches(conn, channel_id: str, codes: list) -> list:
    """Đổi chủ đề của kênh. Có thể gọi bất cứ lúc nào -- không ảnh hưởng bài đã đăng."""
    valid = [c for c in codes if c in niche.NICHES]
    conn.execute("UPDATE channel SET niches=? WHERE id=?",
                 (json.dumps(valid, ensure_ascii=False), channel_id))
    audit(conn, "channel", channel_id, "set_niches", actor="operator", detail={"niches": valid})
    return valid


def active_niches(conn, channel_id: str = None) -> list:
    """Tương thích ngược: có channel_id thì lấy của kênh, không thì lấy cấu hình chung."""
    if channel_id:
        return channel_niches(conn, channel_id)
    _, filters = scoring.active_config(conn)
    return filters.get("niches") or []


def upsert_one(conn, source, raw) -> str:
    """Ghi một sản phẩm đơn lẻ vào kho, trả về product_id."""
    row = conn.execute(
        "SELECT id FROM product WHERE source=? AND merchant=? AND external_product_id=?",
        (source.name, raw.merchant, raw.external_product_id)).fetchone()
    local_image = getattr(raw, "image_path_local", None)
    if row:
        pid = row["id"]
        conn.execute("""UPDATE product SET name=?, description=?, current_price=?, original_price=?,
                        commission_value=?, commission_rate=?, category_code=?, rating=?, review_count=?,
                        sold_count=?, image_url_original=?, image_path_local=?, product_url=?, is_available=1,
                        last_seen_at=?, updated_at=? WHERE id=?""",
                     (raw.name, raw.description, raw.current_price, raw.original_price,
                      raw.commission_value, raw.commission_rate, raw.category_code, raw.rating,
                      raw.review_count, raw.sold_count, raw.image_url_original, local_image,
                      raw.product_url, now(), now(), pid))
    else:
        pid = ulid()
        conn.execute("""INSERT INTO product (id, source, merchant, external_product_id, name, description,
                        current_price, original_price, commission_value, commission_rate, category_code,
                        rating, review_count, sold_count, image_url_original, image_path_local, product_url,
                        is_available, last_seen_at, created_at, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)""",
                     (pid, source.name, raw.merchant, raw.external_product_id, raw.name, raw.description,
                      raw.current_price, raw.original_price, raw.commission_value, raw.commission_rate,
                      raw.category_code, raw.rating, raw.review_count, raw.sold_count,
                      raw.image_url_original, local_image, raw.product_url, now(), now(), now()))
    conn.execute("INSERT INTO product_price_history (product_id, price, observed_at) VALUES (?,?,?)",
                 (pid, raw.current_price, now()))
    return pid


def _resolve_channels_by_code(conn, codes: list):
    """Trả (list channel row theo đúng thứ tự codes, None) hoặc (None, lỗi).
    Dùng ở luồng TẠO bài -- codes là channel.code (mã người đọc được), khớp
    với tham số channel_code đơn đã có sẵn."""
    rows = []
    for code in codes:
        row = conn.execute("SELECT * FROM channel WHERE code=? AND status='ACTIVE'", (code,)).fetchone()
        if not row:
            return None, f"Kênh {code} không tồn tại hoặc không hoạt động"
        if not row["enabled"]:
            return None, f"Kênh {code} đã bị tắt (disabled), không thể tạo bài"
        rows.append(row)
    return rows, None


def _union_niches(conn, channel_ids: list) -> list:
    """Hợp (union) niches của nhiều kênh, giữ thứ tự xuất hiện đầu tiên,
    không trùng lặp. Dùng để validate caption chặt hơn khi có nhiều kênh."""
    seen = []
    for cid in channel_ids:
        for n in channel_niches(conn, cid):
            if n not in seen:
                seen.append(n)
    return seen


def _save_channel_selection(conn, post_id: str, channel_ids: list) -> None:
    """Ghi lựa chọn kênh GỐC lúc tạo bài. KHÔNG bao giờ được UPDATE/DELETE lại
    sau đó -- đây là audit trail, không phải nguồn dữ liệu cho approve_post()."""
    for cid in channel_ids:
        conn.execute("INSERT INTO post_channel_selection (post_id, channel_id, created_at) VALUES (?,?,?)",
                     (post_id, cid, now()))


def post_channel_selections(conn, post_ids: list) -> dict:
    """post_id -> list[channel row dict] đã chọn lúc tạo bài (sắp theo
    platform, code). Dùng để tick sẵn checklist ở /duyet."""
    if not post_ids:
        return {}
    placeholders = ",".join("?" * len(post_ids))
    rows = conn.execute(f"""
        SELECT pcs.post_id AS post_id, ch.id AS id, ch.code AS code,
               ch.platform AS platform, ch.handle AS handle
        FROM post_channel_selection pcs JOIN channel ch ON ch.id = pcs.channel_id
        WHERE pcs.post_id IN ({placeholders})
        ORDER BY ch.platform, ch.code
    """, post_ids).fetchall()
    result = {}
    for r in rows:
        result.setdefault(r["post_id"], []).append(dict(r))
    return result


def _create_post_from_raw_product(conn, ctx, source, raw, campaign_code: str,
                                  channel_code: str = None, channel_codes: list = None,
                                  template_code: str = None,
                                  variant_code: str = "A", prebuilt_affiliate_link: str = None,
                                  attribution_payload: dict = None,
                                  audit_action: str = "created_single") -> dict:
    campaign = conn.execute("SELECT * FROM campaign WHERE code=?", (campaign_code,)).fetchone()
    if not campaign:
        return {"ok": False, "error": f"Chưa có chiến dịch {campaign_code}"}
    if channel_codes:
        # Đa kênh (sub-project D) -- kênh ĐẦU TIÊN trong danh sách là "kênh
        # chính" (post.channel_id, watermark khi chỉ 1 kênh, tracking link).
        channels, err = _resolve_channels_by_code(conn, channel_codes)
        if err:
            return {"ok": False, "error": err}
    else:
        channel = conn.execute(
            # Nhánh có channel_code giữ nguyên, không lọc platform -- gọi rõ tên kênh
            # thì tôn trọng lựa chọn đó (sau này facebook/instagram có publisher thật
            # vẫn dùng lại được hàm này). Nhánh mặc định (không truyền channel_code)
            # chỉ được rơi vào Threads -- kênh facebook/instagram import về chưa có
            # publisher đăng ký, để lọt vào đây thì bài kẹt vĩnh viễn ở SCHEDULED.
            "SELECT * FROM channel WHERE code=? AND status='ACTIVE'" if channel_code
            else "SELECT * FROM channel WHERE status='ACTIVE' AND platform='threads' ORDER BY code LIMIT 1",
            (channel_code,) if channel_code else ()).fetchone()
        if not channel:
            return {"ok": False, "error": "Không có kênh nào đang hoạt động"}
        if not channel["enabled"]:
            return {"ok": False, "error": f"Kênh {channel['code']} đã bị tắt (disabled), không thể tạo bài"}
        channels = [channel]

    channel = channels[0]  # kênh chính
    channel_ids = [ch["id"] for ch in channels]
    template = conn.execute(
        "SELECT * FROM caption_template WHERE code=? AND is_active=1" if template_code
        else "SELECT * FROM caption_template WHERE is_active=1 ORDER BY code LIMIT 1",
        (template_code,) if template_code else ()).fetchone()
    if not template:
        return {"ok": False, "error": "Không có template caption nào đang bật"}

    product_id = upsert_one(conn, source, raw)
    product = conn.execute("SELECT * FROM product WHERE id=?", (product_id,)).fetchone()

    post_id = ulid()
    if prebuilt_affiliate_link is None:
        stored_attribution = attribution.encode_sub_ids(
            post_id, campaign["code"], variant_code, channel["code"])
        link = source.create_tracking_link(product["product_url"], stored_attribution)
    else:
        link = prebuilt_affiliate_link
        stored_attribution = attribution_payload or {
            "provider": "shopee_direct",
            "link_mode": "prebuilt",
        }

    discount = scoring.real_discount_depth(conn, product_id, product["current_price"])
    image_path = imaging.compose(product, MEDIA_DIR, discount_pct=discount,
                                 handle=channel["handle"] if len(channels) == 1 else None)
    image_url = ctx.get("storage", storage.get_storage()).put(image_path)

    caption = content.generate(product, template["code"], link, discount_pct=discount)
    problems = content.validate(caption, niches=_union_niches(conn, channel_ids))
    status = "PENDING_REVIEW" if not problems else "DRAFT"

    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, caption_template_id,
                    variant_code, caption_body, disclosure_text, caption_final, image_url_composited,
                    affiliate_link, sub_id_payload, score, status, reject_reason, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (post_id, product_id, channel["id"], campaign["id"], template["id"],
                  variant_code, caption, content.DISCLOSURE_DEFAULT, caption,
                  image_url, link, json.dumps(stored_attribution, ensure_ascii=False, sort_keys=True), None,
                  status, "; ".join(problems) if problems else None, now(), now()))
    _save_channel_selection(conn, post_id, channel_ids)
    audit(conn, "post", post_id, audit_action, actor="operator",
          detail={"source": source.name, "external_product_id": raw.external_product_id,
                  "template": template["code"], "problems": problems, "channel_ids": channel_ids})

    return {"ok": True, "post_id": post_id, "product_id": product_id,
            "product_name": product["name"], "affiliate_link": link,
            "image_url": image_url, "caption": caption, "problems": problems,
            "status": status}


def create_post_for_product(conn, ctx, external_product_id: str, campaign_code: str,
                            channel_code: str = None, channel_codes: list = None,
                            template_code: str = None, variant_code: str = "A") -> dict:
    """Một sản phẩm cụ thể -> một bài PENDING_REVIEW. Không đăng."""
    source = ctx["source"]
    raw = source.get_product(external_product_id) if hasattr(source, "get_product") else None
    if raw is None:
        return {"ok": False, "error": f"Không tìm thấy sản phẩm {external_product_id} trong nguồn {source.name}"}
    if not raw.product_url:
        return {"ok": False, "error": "Sản phẩm không có product_url, không tạo được tracking link"}
    return _create_post_from_raw_product(
        conn, ctx, source, raw, campaign_code,
        channel_code=channel_code, channel_codes=channel_codes,
        template_code=template_code, variant_code=variant_code)


def create_post_from_manual_affiliate_product(conn, ctx, source, raw, affiliate_url: str,
                                               campaign_code: str, channel_code: str = None,
                                               channel_codes: list = None,
                                               template_code: str = None,
                                               variant_code: str = "A") -> dict:
    """Tạo bài review từ sản phẩm Shopee + affiliate URL có sẵn; không publish."""
    if not affiliate_url or not affiliate_url.startswith(("http://", "https://")):
        return {"ok": False, "error": "Thiếu link affiliate hợp lệ"}
    if not raw.name or raw.current_price <= 0 or not raw.image_url_original:
        return {"ok": False, "error": "Thiếu tên, giá hoặc ảnh sản phẩm"}
    return _create_post_from_raw_product(
        conn, ctx, source, raw, campaign_code,
        channel_code=channel_code, channel_codes=channel_codes,
        template_code=template_code, variant_code=variant_code,
        prebuilt_affiliate_link=affiliate_url,
        attribution_payload={"provider": "shopee_direct", "link_mode": "prebuilt"},
        audit_action="created_manual_shopee")


# ------------------------------------------------------------------ chặng 3

@handler("GENERATE_CONTENT")
def generate_content(conn, payload, ctx):
    product = conn.execute("SELECT * FROM product WHERE id=?", (payload["product_id"],)).fetchone()
    channel = conn.execute("SELECT * FROM channel WHERE id=?", (payload["channel_id"],)).fetchone()
    campaign = conn.execute("SELECT * FROM campaign WHERE id=?", (payload["campaign_id"],)).fetchone()
    template = conn.execute("SELECT * FROM caption_template WHERE id=?", (payload["template_id"],)).fetchone()
    if not (product and channel and campaign and template):
        raise ValueError("Thiếu dữ liệu tham chiếu khi sinh nội dung")

    post_id = ulid()
    subs = attribution.encode_sub_ids(post_id, campaign["code"], payload["variant_code"], channel["code"])
    link = ctx["source"].create_tracking_link(product["product_url"], subs)

    discount = scoring.real_discount_depth(conn, product["id"], product["current_price"])
    image_path = imaging.compose(product, MEDIA_DIR, discount_pct=discount, handle=channel["handle"])
    # Đẩy lên nơi có URL công khai. Local thì chỉ ghép URL, S3/R2 thì upload thật.
    image_url = ctx.get("storage", storage.get_storage()).put(image_path)

    caption = content.generate(product, template["code"], link, discount_pct=discount)
    problems = content.validate(caption, niches=channel_niches(conn, channel["id"]))
    status = "PENDING_REVIEW" if not problems else "DRAFT"

    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, caption_template_id,
                    variant_code, caption_body, disclosure_text, caption_final, image_url_composited,
                    affiliate_link, sub_id_payload, score, status, reject_reason, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (post_id, product["id"], channel["id"], campaign["id"], template["id"],
                  payload["variant_code"], caption, content.DISCLOSURE_DEFAULT, caption,
                  image_url, link, str(subs), payload.get("score"), status,
                  "; ".join(problems) if problems else None, now(), now()))
    # Bài do pipeline tự động sinh chỉ có 1 kênh, nhưng VẪN phải ghi lựa chọn
    # kênh: /duyet dựng checklist "kênh sẽ đăng" thuần từ bảng này, thiếu dòng
    # là checklist rỗng -> rào "chọn ít nhất 1 kênh" chặn duyệt vĩnh viễn.
    _save_channel_selection(conn, post_id, [channel["id"]])
    audit(conn, "post", post_id, "generated", detail={"template": template["code"], "problems": problems})


# ------------------------------------------------------------------ chặng 4

def _resolve_channels_by_id(conn, channel_ids: list):
    """Trả (list channel row theo đúng thứ tự channel_ids, None) hoặc
    (None, lỗi). Dùng ở luồng DUYỆT bài -- channel_ids là channel.id (ULID),
    khớp với post.channel_id/publish_target.channel_id."""
    rows = []
    for cid in channel_ids:
        row = conn.execute("SELECT * FROM channel WHERE id=?", (cid,)).fetchone()
        if not row:
            return None, f"Không tìm thấy kênh {cid}"
        if not row["enabled"]:
            return None, f"Kênh {row['code']} đang bị tắt (disabled), không thể duyệt"
        rows.append(row)
    return rows, None


def _resolve_caption(post, target, channel) -> str:
    """Thứ tự ưu tiên: override riêng account > caption riêng theo platform
    > caption gốc. Thuần tính toán, không query. `post`/`target` chỉ cần hỗ
    trợ post["col"]/target["col"] -- dict thường (lúc validate, chưa có
    publish_target thật) hay sqlite3.Row (lúc publish, target là row CSDL
    thật) đều dùng được, không cần phân biệt loại."""
    if target["caption_override"]:
        return target["caption_override"]
    platform_col = {"facebook": "caption_facebook", "instagram": "caption_instagram"}.get(channel["platform"])
    if platform_col and post[platform_col]:
        return post[platform_col]
    return post["caption_final"]


def approve_post(conn, post_id: str, actor: str = "operator", caption_override: str = None,
                  channel_ids: list = None) -> dict:
    post = conn.execute("SELECT * FROM post WHERE id=?", (post_id,)).fetchone()
    if not post:
        return {"ok": False, "error": "Không tìm thấy bài đăng"}

    ids = channel_ids or [post["channel_id"]]
    channels, err = _resolve_channels_by_id(conn, ids)
    if err:
        return {"ok": False, "error": err}

    caption = caption_override or post["caption_final"]
    problems = content.validate(caption, niches=_union_niches(conn, ids))
    if problems:
        return {"ok": False, "error": "; ".join(problems)}

    # Slot riêng cho từng kênh -- rate-limit độc lập theo channel.min_gap_minutes
    # của chính kênh đó (xem _next_slot). post.scheduled_at chỉ còn là "sớm
    # nhất trong N giờ", dùng để sort/hiển thị, không còn là giờ đăng chính xác
    # khi có từ 2 kênh trở lên.
    slots = {ch["id"]: _next_slot(conn, ch["id"]) for ch in channels}
    earliest = min(slots.values())
    conn.execute("""UPDATE post SET caption_final=?, status='SCHEDULED', scheduled_at=?,
                    reviewed_by=?, reviewed_at=?, reject_reason=NULL, updated_at=? WHERE id=?""",
                 (caption, earliest, actor, now(), now(), post_id))

    targets = []
    for ch in channels:
        target_id = ulid()
        slot = slots[ch["id"]]
        conn.execute("""INSERT INTO publish_target (id, post_id, channel_id, status, scheduled_at,
                        created_at, updated_at) VALUES (?,?,?,'SCHEDULED',?,?,?)""",
                     (target_id, post_id, ch["id"], slot, now(), now()))
        # post_id/channel_id ở lại payload để jobs.py xử lý AuthError/ContentViolationError
        # (đánh dấu kênh NEEDS_REAUTH, đẩy bài về PENDING_REVIEW) không phải sửa.
        enqueue(conn, "PUBLISH_POST",
                {"publish_target_id": target_id, "post_id": post_id, "channel_id": ch["id"]},
                priority=50, run_after=slot, idempotency_key=f"pub:{target_id}")
        targets.append({"channel_id": ch["id"], "publish_target_id": target_id, "scheduled_at": slot})

    audit(conn, "post", post_id, "approved", actor=actor, detail={"targets": targets})
    return {"ok": True, "scheduled_at": earliest, "publish_target_id": targets[0]["publish_target_id"],
            "targets": targets}


def reject_post(conn, post_id: str, reason: str, actor: str = "operator") -> dict:
    conn.execute("UPDATE post SET status='REJECTED', reject_reason=?, reviewed_by=?, reviewed_at=?, updated_at=? WHERE id=?",
                 (reason, actor, now(), now(), post_id))
    audit(conn, "post", post_id, "rejected", actor=actor, detail={"reason": reason})
    return {"ok": True}


def retry_publish_target(conn, target_id: str, actor: str = "operator") -> dict:
    """Retry khi FAILED, hoặc khi RUNNING (worker có thể đã crash giữa lúc đăng,
    kẹt lại vĩnh viễn ở RUNNING -- cho retry ở đây là thao tác thủ công, người vận
    hành tự xác nhận an toàn để thử lại, không phải tự động). Reset về PENDING,
    enqueue lại đúng target đó -- không tạo publish_target mới, không đụng target
    khác của cùng post. KHÔNG cho retry từ SUCCESS/PENDING/SCHEDULED/CANCELLED."""
    target = conn.execute("SELECT * FROM publish_target WHERE id=?", (target_id,)).fetchone()
    if not target:
        return {"ok": False, "error": "Không tìm thấy publish_target"}
    if target["status"] not in ("FAILED", "RUNNING"):
        return {"ok": False,
                "error": f"Chỉ retry được target FAILED hoặc RUNNING (nghi treo), hiện tại là {target['status']}"}

    conn.execute("UPDATE publish_target SET status='PENDING', updated_at=? WHERE id=?", (now(), target_id))
    retry_key = f"pub:{target_id}:retry:{target['attempt_count']}"
    job_id = enqueue(conn, "PUBLISH_POST",
                      {"publish_target_id": target_id, "post_id": target["post_id"], "channel_id": target["channel_id"]},
                      priority=50, idempotency_key=retry_key)
    audit(conn, "publish_target", target_id, "retry", actor=actor,
          detail={"attempt_count": target["attempt_count"]})
    return {"ok": True, "job_id": job_id}


def _next_slot(conn, channel_id: str) -> str:
    """Giãn cách tối thiểu giữa hai bài cùng kênh. Trần mềm 8-15 bài/ngày không
    phải để né gì -- đăng dày hơn thì chất lượng feed giảm và người theo dõi bỏ đi.

    Đọc theo publish_target (không phải post) -- kể từ sub-project D một post
    có thể có nhiều target trên nhiều kênh khác nhau, post.channel_id chỉ còn
    là "kênh chính". Đọc theo post sẽ khiến các kênh phụ không bao giờ thấy
    lịch sử đăng của chính mình."""
    ch = conn.execute("SELECT * FROM channel WHERE id=?", (channel_id,)).fetchone()
    gap = timedelta(minutes=ch["min_gap_minutes"])
    last = conn.execute("""
        SELECT MAX(CASE WHEN status='SUCCESS' THEN updated_at ELSE scheduled_at END)
        FROM publish_target WHERE channel_id=? AND status IN ('SCHEDULED','SUCCESS')""",
                        (channel_id,)).fetchone()[0]
    base = datetime.now(timezone.utc)
    if last:
        try:
            prev = datetime.fromisoformat(last)
            base = max(base, prev + gap)
        except ValueError:
            pass
    return base.isoformat(timespec="seconds")


# ------------------------------------------------------------------ chặng 5

def _mark_target_failed(conn, target_id: str, error) -> None:
    conn.execute("""UPDATE publish_target SET status='FAILED', last_error=?,
                    attempt_count=attempt_count+1, updated_at=? WHERE id=?""",
                 (str(error)[:500], now(), target_id))


def _cancel_target_stale_post(conn, target_id: str, post_status: str) -> None:
    """Bài đứng sau target này không còn ở trạng thái đăng được nữa (ví dụ vừa bị
    ContentViolationError đẩy về PENDING_REVIEW trong lúc target đang PENDING chờ
    retry). Đóng target ở CANCELLED kèm lý do thay vì bỏ lửng ở PENDING -- lửng thì
    /vanhanh không còn cách nào hiển thị lại nút retry (chỉ render cho FAILED) và
    retry_publish_target cũng không nhận PENDING. CANCELLED là trạng thái cuối, cố
    ý không cho retry lại: muốn đăng phải duyệt lại qua /duyet để approve_post() tạo
    publish_target mới, không phải hồi sinh target cũ."""
    conn.execute("""UPDATE publish_target SET status='CANCELLED', last_error=?, updated_at=?
                    WHERE id=?""",
                 (f"Bài không còn ở trạng thái có thể đăng (status hiện tại: {post_status})"[:500],
                  now(), target_id))


def _find_or_create_legacy_target(conn, post_id: str, channel_id: str) -> str:
    """Tương thích ngược: job PUBLISH_POST được enqueue bởi bản trước khi có
    publish_target (payload chỉ {post_id, channel_id}) có thể còn nằm trong
    job_queue sau khi nâng cấp -- manage.sh upgrade giữ nguyên var/. Dùng lại
    target đã có cho đúng post_id+channel_id nếu có, không thì tạo mới, rồi đi
    tiếp luồng bình thường.

    CANCELLED bị loại khỏi lựa chọn: đó là trạng thái cuối, cố ý không cho hồi
    sinh (xem _cancel_target_stale_post) -- một job payload cũ lạc mất không
    được phép đăng lại qua một target đã bị huỷ. Lấy target CÒN SỐNG mới nhất
    (created_at DESC, id là ULID nên dùng làm tiêu chí phụ khi trùng giây) --
    đúng như cách người vận hành hiểu "target hiện hành" của post/channel này
    sau khi đã duyệt lại qua /duyet. Nếu mọi target trước đó đều đã CANCELLED
    (hoặc chưa có target nào), coi như chưa có gì -- tạo mới, y hệt trước đây."""
    existing = conn.execute(
        """SELECT id FROM publish_target WHERE post_id=? AND channel_id=? AND status != 'CANCELLED'
           ORDER BY created_at DESC, id DESC LIMIT 1""",
        (post_id, channel_id)).fetchone()
    if existing:
        return existing["id"]
    target_id = ulid()
    conn.execute("""INSERT INTO publish_target (id, post_id, channel_id, status, created_at, updated_at)
                    VALUES (?,?,?,'PENDING',?,?)""",
                 (target_id, post_id, channel_id, now(), now()))
    audit(conn, "publish_target", target_id, "created_from_legacy_payload",
          detail={"post_id": post_id, "channel_id": channel_id})
    return target_id


@handler("PUBLISH_POST")
def publish_post(conn, payload, ctx):
    target_id = payload.get("publish_target_id")
    if not target_id:
        post_id, channel_id = payload.get("post_id"), payload.get("channel_id")
        if not post_id or not channel_id:
            raise ValueError("Không tìm thấy publish_target")
        target_id = _find_or_create_legacy_target(conn, post_id, channel_id)

    target = conn.execute("SELECT * FROM publish_target WHERE id=?", (target_id,)).fetchone()
    if not target:
        raise ValueError("Không tìm thấy publish_target")
    # Tuyến phòng thủ chống đăng trùng, khoá theo TARGET chứ không theo post --
    # cần thiết khi một post có nhiều target độc lập (sub-project D).
    if target["status"] == "SUCCESS":
        return

    post = conn.execute("SELECT * FROM post WHERE id=?", (target["post_id"],)).fetchone()
    channel = conn.execute("SELECT * FROM channel WHERE id=?", (target["channel_id"],)).fetchone()
    if not post or not channel:
        raise ValueError("publish_target trỏ tới post/channel không tồn tại")
    # Blocklist chứ không phải allowlist: kể từ sub-project D một post có thể
    # có N target trên N kênh khác nhau. Target đầu tiên publish thành công sẽ
    # đẩy post.status sang PUBLISHED (dưới đây) -- các target còn lại (kênh
    # khác) vẫn phải được publish bình thường, KHÔNG được coi PUBLISHED là
    # "bài không còn đăng được" như trước đây (khi 1 post luôn chỉ có 1
    # target). Chỉ thực sự huỷ khi post bị bounce khỏi trạng thái duyệt được:
    # PENDING_REVIEW (một target khác gặp ContentViolationError), REJECTED,
    # hoặc DRAFT (chưa từng qua duyệt). "APPROVED" trong điều kiện cũ chưa
    # từng được set ở bất kỳ đâu trong code -- bỏ, không mất hành vi nào.
    if post["status"] in ("PENDING_REVIEW", "REJECTED", "DRAFT"):
        _cancel_target_stale_post(conn, target["id"], post["status"])
        return

    if channel["status"] != "ACTIVE":
        from ..adapters.base import AuthError
        _mark_target_failed(conn, target["id"], f"Kênh {channel['code']} đang ở trạng thái {channel['status']}")
        raise AuthError(f"Kênh {channel['code']} đang ở trạng thái {channel['status']}")

    if not channel["enabled"]:
        # Operator bấm "Tắt" ở /kenh thì kỳ vọng dừng đăng NGAY, kể cả target đã
        # SCHEDULED từ trước khi tắt -- không chỉ chặn job MỚI (spec chỉ bắt buộc
        # chặn job mới, đây là hardening theo yêu cầu operator).
        #
        # KHÔNG dùng AuthError ở đây: jobs.py bắt AuthError bằng cách đẩy
        # channel.status sang NEEDS_REAUTH -- đúng cho token hỏng (nhánh status
        # != ACTIVE ở trên), nhưng SAI cho tắt kênh thủ công. Facebook/Instagram
        # còn có connections.py tự đồng bộ đưa NEEDS_REAUTH về ACTIVE, Threads thì
        # không có cơ chế phục hồi nào -- kênh sẽ kẹt NEEDS_REAUTH vĩnh viễn dù
        # token vẫn tốt, chỉ vì operator từng tắt/bật. Dùng ContentViolationError
        # (như nhánh "chưa có publisher" bên dưới): FAILED ngay, không retry,
        # không đụng channel.status -- đúng bản chất "thao tác thủ công có chủ
        # đích", không phải lỗi xác thực.
        from ..adapters.base import ContentViolationError
        _mark_target_failed(conn, target["id"], f"Kênh {channel['code']} đã bị tắt (disabled)")
        raise ContentViolationError(f"Kênh {channel['code']} đã bị tắt (disabled)")

    if _published_today(conn, channel["id"]) >= channel["daily_post_cap"]:
        from ..adapters.base import RateLimitError
        raise RateLimitError(f"Kênh {channel['code']} đã đạt trần {channel['daily_post_cap']} bài trong ngày")

    if channel["platform"] not in ctx["publishers"]:
        # Chưa có publisher đăng ký cho platform này (facebook/instagram trước khi
        # sub-project sau đăng ký) -- đây là lỗi cấu hình, không phải lỗi tạm thời,
        # retry không giúp gì. ContentViolationError để jobs.py xử lý KHÔNG retry
        # (đẩy bài về PENDING_REVIEW cho người xem lại) thay vì Exception chung
        # chung sẽ bị retry vô ích rồi lộ ra KeyError khó hiểu ở /vanhanh.
        from ..adapters.base import ContentViolationError
        msg = f"Không có publisher đã đăng ký cho nền tảng {channel['platform']}"
        _mark_target_failed(conn, target["id"], msg)
        raise ContentViolationError(msg)

    conn.execute("UPDATE publish_target SET status='RUNNING', updated_at=? WHERE id=?", (now(), target["id"]))
    try:
        publisher = ctx["publishers"][channel["platform"]]
        media = [post["image_url_composited"]] if post["image_url_composited"] else []
        result = publisher.publish(channel, post["caption_final"], media=media)
    except Exception as e:
        from ..adapters.base import RateLimitError as _RateLimitError
        if isinstance(e, _RateLimitError):
            # Hạn mức không phải lỗi -- không tính là FAILED, không tăng attempt_count,
            # trả target về SCHEDULED để job_queue tự hoãn và thử lại đúng như trước.
            conn.execute("UPDATE publish_target SET status='SCHEDULED', last_error=?, updated_at=? WHERE id=?",
                         (str(e)[:500], now(), target["id"]))
        else:
            _mark_target_failed(conn, target["id"], e)
        raise

    conn.execute("""UPDATE publish_target SET status='SUCCESS', external_post_id=?, updated_at=?
                    WHERE id=?""", (result.external_post_id, now(), target["id"]))
    conn.execute("UPDATE post SET status='PUBLISHED', thread_id=?, published_at=?, updated_at=? WHERE id=?",
                 (result.external_post_id, result.published_at, now(), post["id"]))
    audit(conn, "post", post["id"], "published",
          detail={"thread_id": result.external_post_id, "publish_target_id": target["id"]})
    if result.native_label_status != "not_attempted":
        audit(conn, "publish_target", target["id"], "native_label_requested",
              detail={"status": result.native_label_status, "platform": channel["platform"]})
    enqueue(conn, "FETCH_INSIGHTS",
            {"post_id": post["id"], "channel_id": channel["id"], "publish_target_id": target["id"]},
            run_after=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(timespec="seconds"),
            idempotency_key=f"ins:{target['id']}")


def _published_today(conn, channel_id: str) -> int:
    """Đếm theo publish_target -- cùng lý do đã ghi ở _next_slot."""
    today = datetime.now(timezone.utc).date().isoformat()
    return conn.execute(
        "SELECT COUNT(*) FROM publish_target WHERE channel_id=? AND status='SUCCESS' AND substr(updated_at,1,10)=?",
        (channel_id, today)).fetchone()[0]


@handler("FETCH_INSIGHTS")
def fetch_insights(conn, payload, ctx):
    # Đọc external_post_id từ ĐÚNG publish_target, không phải post.thread_id
    # (post.thread_id chỉ phản ánh target thành công đầu tiên -- sai cho các
    # kênh phụ kể từ sub-project D). payload cũ (trước D1) không có
    # publish_target_id -- fallback về post.thread_id để tương thích ngược.
    target = None
    if payload.get("publish_target_id"):
        target = conn.execute(
            "SELECT external_post_id FROM publish_target WHERE id=?", (payload["publish_target_id"],)).fetchone()
    post = conn.execute("SELECT thread_id FROM post WHERE id=?", (payload["post_id"],)).fetchone()
    external_post_id = (target["external_post_id"] if target else None) or (post["thread_id"] if post else None)
    channel = conn.execute("SELECT * FROM channel WHERE id=?", (payload["channel_id"],)).fetchone()
    if not post or not external_post_id:
        return
    publisher = ctx["publishers"][channel["platform"]]
    attribution.update_insights(conn, payload["post_id"], publisher.fetch_insights(channel, external_post_id))


# ------------------------------------------------------------------ chặng 6

def ingest_postback(conn, payload: dict) -> tuple:
    return attribution.record_conversion(conn, payload)


def reconcile_transactions(conn, source, since: str = None) -> dict:
    since = since or (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
    return attribution.reconcile(conn, source.fetch_transactions(since))
