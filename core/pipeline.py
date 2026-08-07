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
                            sold_count=?, image_url_original=?, product_url=?, is_available=1,
                            last_seen_at=?, updated_at=? WHERE id=?""",
                         (raw.name, raw.description, raw.current_price, raw.original_price,
                          raw.commission_value, raw.commission_rate, raw.category_code, raw.rating,
                          raw.review_count, raw.sold_count, raw.image_url_original, raw.product_url,
                          now(), now(), pid))
            stats["updated"] += 1
            if row["current_price"] != raw.current_price:
                stats["price_changed"] += 1
        else:
            pid = ulid()
            conn.execute("""INSERT INTO product (id, source, merchant, external_product_id, name, description,
                            current_price, original_price, commission_value, commission_rate, category_code,
                            rating, review_count, sold_count, image_url_original, product_url, is_available,
                            last_seen_at, created_at, updated_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)""",
                         (pid, source.name, raw.merchant, raw.external_product_id, raw.name, raw.description,
                          raw.current_price, raw.original_price, raw.commission_value, raw.commission_rate,
                          raw.category_code, raw.rating, raw.review_count, raw.sold_count,
                          raw.image_url_original, raw.product_url, now(), now(), now()))
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
    channels = conn.execute("SELECT * FROM channel WHERE status='ACTIVE'").fetchall()
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
    if row:
        pid = row["id"]
        conn.execute("""UPDATE product SET name=?, description=?, current_price=?, original_price=?,
                        commission_value=?, commission_rate=?, category_code=?, rating=?, review_count=?,
                        sold_count=?, image_url_original=?, product_url=?, is_available=1,
                        last_seen_at=?, updated_at=? WHERE id=?""",
                     (raw.name, raw.description, raw.current_price, raw.original_price,
                      raw.commission_value, raw.commission_rate, raw.category_code, raw.rating,
                      raw.review_count, raw.sold_count, raw.image_url_original, raw.product_url,
                      now(), now(), pid))
    else:
        pid = ulid()
        conn.execute("""INSERT INTO product (id, source, merchant, external_product_id, name, description,
                        current_price, original_price, commission_value, commission_rate, category_code,
                        rating, review_count, sold_count, image_url_original, product_url, is_available,
                        last_seen_at, created_at, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)""",
                     (pid, source.name, raw.merchant, raw.external_product_id, raw.name, raw.description,
                      raw.current_price, raw.original_price, raw.commission_value, raw.commission_rate,
                      raw.category_code, raw.rating, raw.review_count, raw.sold_count,
                      raw.image_url_original, raw.product_url, now(), now(), now()))
    conn.execute("INSERT INTO product_price_history (product_id, price, observed_at) VALUES (?,?,?)",
                 (pid, raw.current_price, now()))
    return pid


def create_post_for_product(conn, ctx, external_product_id: str, campaign_code: str,
                            channel_code: str = None, template_code: str = None,
                            variant_code: str = "A") -> dict:
    """Một sản phẩm cụ thể -> một bài PENDING_REVIEW. Không đăng.

    Bỏ qua chấm điểm vì người vận hành đã tự chọn sản phẩm. KHÔNG bỏ qua rào chắn
    nội dung -- caption vẫn phải qua validate() y hệt đường hàng loạt, và bài luôn
    dừng ở màn hình duyệt.
    """
    source = ctx["source"]
    raw = source.get_product(external_product_id) if hasattr(source, "get_product") else None
    if raw is None:
        return {"ok": False, "error": f"Không tìm thấy sản phẩm {external_product_id} trong nguồn {source.name}"}
    if not raw.product_url:
        return {"ok": False, "error": "Sản phẩm không có product_url, không tạo được tracking link"}

    campaign = conn.execute("SELECT * FROM campaign WHERE code=?", (campaign_code,)).fetchone()
    if not campaign:
        return {"ok": False, "error": f"Chưa có chiến dịch {campaign_code}"}
    channel = conn.execute(
        "SELECT * FROM channel WHERE code=? AND status='ACTIVE'" if channel_code
        else "SELECT * FROM channel WHERE status='ACTIVE' ORDER BY code LIMIT 1",
        (channel_code,) if channel_code else ()).fetchone()
    if not channel:
        return {"ok": False, "error": "Không có kênh nào đang hoạt động"}
    template = conn.execute(
        "SELECT * FROM caption_template WHERE code=? AND is_active=1" if template_code
        else "SELECT * FROM caption_template WHERE is_active=1 ORDER BY code LIMIT 1",
        (template_code,) if template_code else ()).fetchone()
    if not template:
        return {"ok": False, "error": "Không có template caption nào đang bật"}

    product_id = upsert_one(conn, source, raw)
    product = conn.execute("SELECT * FROM product WHERE id=?", (product_id,)).fetchone()

    post_id = ulid()
    subs = attribution.encode_sub_ids(post_id, campaign["code"], variant_code, channel["code"])
    link = source.create_tracking_link(product["product_url"], subs)

    discount = scoring.real_discount_depth(conn, product_id, product["current_price"])
    image_path = imaging.compose(product, MEDIA_DIR, discount_pct=discount, handle=channel["handle"])
    image_url = ctx.get("storage", storage.get_storage()).put(image_path)

    caption = content.generate(product, template["code"], link, discount_pct=discount)
    problems = content.validate(caption, niches=channel_niches(conn, channel["id"]))

    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, caption_template_id,
                    variant_code, caption_body, disclosure_text, caption_final, image_url_composited,
                    affiliate_link, sub_id_payload, score, status, reject_reason, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (post_id, product_id, channel["id"], campaign["id"], template["id"],
                  variant_code, caption, content.DISCLOSURE_DEFAULT, caption,
                  image_url, link, str(subs), None,
                  "PENDING_REVIEW" if not problems else "DRAFT",
                  "; ".join(problems) if problems else None, now(), now()))
    audit(conn, "post", post_id, "created_single", actor="operator",
          detail={"source": source.name, "external_product_id": external_product_id,
                  "template": template["code"], "problems": problems})

    return {"ok": True, "post_id": post_id, "product_id": product_id,
            "product_name": product["name"], "affiliate_link": link,
            "image_url": image_url, "caption": caption, "problems": problems,
            "status": "PENDING_REVIEW" if not problems else "DRAFT"}


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
    audit(conn, "post", post_id, "generated", detail={"template": template["code"], "problems": problems})


# ------------------------------------------------------------------ chặng 4

def approve_post(conn, post_id: str, actor: str = "operator", caption_override: str = None) -> dict:
    post = conn.execute("SELECT * FROM post WHERE id=?", (post_id,)).fetchone()
    if not post:
        return {"ok": False, "error": "Không tìm thấy bài đăng"}
    caption = caption_override or post["caption_final"]
    problems = content.validate(caption, niches=channel_niches(conn, post["channel_id"]))
    if problems:
        return {"ok": False, "error": "; ".join(problems)}

    scheduled = _next_slot(conn, post["channel_id"])
    conn.execute("""UPDATE post SET caption_final=?, status='SCHEDULED', scheduled_at=?,
                    reviewed_by=?, reviewed_at=?, reject_reason=NULL, updated_at=? WHERE id=?""",
                 (caption, scheduled, actor, now(), now(), post_id))
    enqueue(conn, "PUBLISH_POST", {"post_id": post_id, "channel_id": post["channel_id"]},
            priority=50, run_after=scheduled, idempotency_key=f"pub:{post_id}")
    audit(conn, "post", post_id, "approved", actor=actor, detail={"scheduled_at": scheduled})
    return {"ok": True, "scheduled_at": scheduled}


def reject_post(conn, post_id: str, reason: str, actor: str = "operator") -> dict:
    conn.execute("UPDATE post SET status='REJECTED', reject_reason=?, reviewed_by=?, reviewed_at=?, updated_at=? WHERE id=?",
                 (reason, actor, now(), now(), post_id))
    audit(conn, "post", post_id, "rejected", actor=actor, detail={"reason": reason})
    return {"ok": True}


def _next_slot(conn, channel_id: str) -> str:
    """Giãn cách tối thiểu giữa hai bài cùng kênh. Trần mềm 8-15 bài/ngày không
    phải để né gì -- đăng dày hơn thì chất lượng feed giảm và người theo dõi bỏ đi."""
    ch = conn.execute("SELECT * FROM channel WHERE id=?", (channel_id,)).fetchone()
    gap = timedelta(minutes=ch["min_gap_minutes"])
    last = conn.execute("""SELECT MAX(COALESCE(published_at, scheduled_at)) FROM post
                           WHERE channel_id=? AND status IN ('SCHEDULED','PUBLISHED')""",
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

@handler("PUBLISH_POST")
def publish_post(conn, payload, ctx):
    post = conn.execute("SELECT * FROM post WHERE id=?", (payload["post_id"],)).fetchone()
    if not post:
        raise ValueError("Không tìm thấy bài đăng")

    # Tuyến phòng thủ chống đăng trùng. Timeout mạng rồi retry trong khi bài đã
    # lên thành công là lỗi nghiêm trọng nhất của loại hệ thống này.
    if post["thread_id"]:
        return
    if post["status"] not in ("SCHEDULED", "APPROVED"):
        return

    channel = conn.execute("SELECT * FROM channel WHERE id=?", (post["channel_id"],)).fetchone()
    if channel["status"] != "ACTIVE":
        from ..adapters.base import AuthError
        raise AuthError(f"Kênh {channel['code']} đang ở trạng thái {channel['status']}")

    if _published_today(conn, channel["id"]) >= channel["daily_post_cap"]:
        from ..adapters.base import RateLimitError
        raise RateLimitError(f"Kênh {channel['code']} đã đạt trần {channel['daily_post_cap']} bài trong ngày")

    result = ctx["channel"].publish(channel, post["caption_final"], post["image_url_composited"])
    conn.execute("UPDATE post SET status='PUBLISHED', thread_id=?, published_at=?, updated_at=? WHERE id=?",
                 (result.external_post_id, result.published_at, now(), post["id"]))
    audit(conn, "post", post["id"], "published", detail={"thread_id": result.external_post_id})
    enqueue(conn, "FETCH_INSIGHTS", {"post_id": post["id"], "channel_id": channel["id"]},
            run_after=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(timespec="seconds"),
            idempotency_key=f"ins:{post['id']}")


def _published_today(conn, channel_id: str) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    return conn.execute(
        "SELECT COUNT(*) FROM post WHERE channel_id=? AND status='PUBLISHED' AND substr(published_at,1,10)=?",
        (channel_id, today)).fetchone()[0]


@handler("FETCH_INSIGHTS")
def fetch_insights(conn, payload, ctx):
    post = conn.execute("SELECT thread_id FROM post WHERE id=?", (payload["post_id"],)).fetchone()
    channel = conn.execute("SELECT * FROM channel WHERE id=?", (payload["channel_id"],)).fetchone()
    if not post or not post["thread_id"]:
        return
    attribution.update_insights(conn, payload["post_id"], ctx["channel"].fetch_insights(channel, post["thread_id"]))


# ------------------------------------------------------------------ chặng 6

def ingest_postback(conn, payload: dict) -> tuple:
    return attribution.record_conversion(conn, payload)


def reconcile_transactions(conn, source, since: str = None) -> dict:
    since = since or (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
    return attribution.reconcile(conn, source.fetch_transactions(since))
