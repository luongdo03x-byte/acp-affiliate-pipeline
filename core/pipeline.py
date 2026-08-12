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

from . import attribution, content, imaging, niche, playbook, scoring, storage, valuepost
from .db import audit, now, ulid
from .jobs import enqueue, handler
from .products import (PROVIDER as CATALOG_PROVIDER, CatalogImageError,
                       ProductService, materialize_catalog_image)

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
    channels = conn.execute("SELECT * FROM channel WHERE status='ACTIVE'").fetchall()
    templates = conn.execute("SELECT * FROM caption_template WHERE is_active=1").fetchall()
    if not campaign or not channels or not templates:
        return []

    created = []
    per_channel = max(1, limit // max(1, len(channels)))
    hooks = playbook.hook_codes()
    for ch in channels:
        # Mỗi kênh có ngách riêng -> phải chấm điểm riêng, không dùng chung một
        # danh sách ứng viên rồi bốc kênh ngẫu nhiên như trước.
        nl = channel_niches(conn, ch["id"])
        for i, item in enumerate(scoring.score_candidates(conn, limit=per_channel, niches=nl)):
            tpl = rng.choice(list(templates))
            # Xoay vòng hook làm biến thể -- variant_code = mã hook, đo hiệu quả
            # từng hook qua sub3 (xem attribution.encode_sub_ids).
            variant = hooks[i % len(hooks)]
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


def _create_post_from_raw_product(conn, ctx, source, raw, campaign_code: str,
                                  channel_code: str = None, template_code: str = None,
                                  variant_code: str = None, rng=None,
                                  prebuilt_affiliate_link: str = None,
                                  attribution_payload: dict = None,
                                  audit_action: str = "created_single") -> dict:
    """Lõi dùng chung cho mọi đường tạo-một-bài-thủ-công: chọn sản phẩm từ nguồn
    (create_post_for_product) hoặc dán link Shopee kèm metadata đã xác nhận
    (create_post_from_manual_affiliate_product). KHÔNG bỏ qua rào chắn nội dung --
    caption vẫn phải qua validate() y hệt đường hàng loạt, và bài luôn dừng ở
    màn hình duyệt.

    variant_code bỏ trống thì bốc một mã hook ngẫu nhiên (core/playbook.py) --
    variant_code LUÔN là mã hook, dùng để sinh caption lẫn gắn vào sub3.
    """
    rng = rng or random.Random()
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

    variant_code = playbook.pick_hook(variant_code, rng=rng)
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
    image_path = imaging.compose(product, MEDIA_DIR, discount_pct=discount, handle=channel["handle"])
    image_url = ctx.get("storage", storage.get_storage()).put(image_path)

    caption = content.generate(product, template["code"], link, discount_pct=discount,
                                hook_code=variant_code, rng=rng)
    problems = content.validate(caption, niches=channel_niches(conn, channel["id"]))
    status = "PENDING_REVIEW" if not problems else "DRAFT"

    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, caption_template_id,
                    variant_code, caption_body, disclosure_text, caption_final, image_url_composited,
                    affiliate_link, sub_id_payload, score, status, reject_reason, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (post_id, product_id, channel["id"], campaign["id"], template["id"],
                  variant_code, caption, content.DISCLOSURE_DEFAULT, caption,
                  image_url, link, json.dumps(stored_attribution, ensure_ascii=False, sort_keys=True), None,
                  status, "; ".join(problems) if problems else None, now(), now()))
    audit(conn, "post", post_id, audit_action, actor="operator",
          detail={"source": source.name, "external_product_id": raw.external_product_id,
                  "template": template["code"], "problems": problems})

    return {"ok": True, "post_id": post_id, "product_id": product_id,
            "product_name": product["name"], "affiliate_link": link,
            "image_url": image_url, "caption": caption, "problems": problems,
            "status": status}


def create_post_for_product(conn, ctx, external_product_id: str, campaign_code: str,
                            channel_code: str = None, template_code: str = None,
                            variant_code: str = None, rng=None) -> dict:
    """Một sản phẩm cụ thể (chọn qua nguồn/affiliate network) -> một bài
    PENDING_REVIEW. Không đăng. Bỏ qua chấm điểm vì người vận hành đã tự chọn
    sản phẩm."""
    source = ctx["source"]
    raw = source.get_product(external_product_id) if hasattr(source, "get_product") else None
    if raw is None:
        return {"ok": False, "error": f"Không tìm thấy sản phẩm {external_product_id} trong nguồn {source.name}"}
    if not raw.product_url:
        return {"ok": False, "error": "Sản phẩm không có product_url, không tạo được tracking link"}
    return _create_post_from_raw_product(
        conn, ctx, source, raw, campaign_code,
        channel_code=channel_code, template_code=template_code,
        variant_code=variant_code, rng=rng)


def create_post_from_manual_affiliate_product(conn, ctx, source, raw, affiliate_url: str,
                                               campaign_code: str, channel_code: str = None,
                                               template_code: str = None,
                                               variant_code: str = None, rng=None) -> dict:
    """Tạo bài review từ sản phẩm Shopee + affiliate URL có sẵn (dán link, không
    cần tự tạo tracking link); không publish."""
    if not affiliate_url or not affiliate_url.startswith(("http://", "https://")):
        return {"ok": False, "error": "Thiếu link affiliate hợp lệ"}
    if not raw.name or raw.current_price <= 0 or not raw.image_url_original:
        return {"ok": False, "error": "Thiếu tên, giá hoặc ảnh sản phẩm"}
    return _create_post_from_raw_product(
        conn, ctx, source, raw, campaign_code,
        channel_code=channel_code, template_code=template_code,
        variant_code=variant_code, rng=rng,
        prebuilt_affiliate_link=affiliate_url,
        attribution_payload={"provider": "shopee_direct", "link_mode": "prebuilt"},
        audit_action="created_manual_shopee")


def _catalog_post_context(conn, campaign_code: str, channel_code: str = None):
    """Resolve the existing review-post prerequisites before requesting a link."""
    campaign = conn.execute("SELECT * FROM campaign WHERE code=?", (campaign_code,)).fetchone()
    if not campaign:
        return None, {"ok": False, "error": f"Chưa có chiến dịch {campaign_code}"}
    channel = conn.execute(
        "SELECT * FROM channel WHERE code=? AND status='ACTIVE'" if channel_code
        else "SELECT * FROM channel WHERE status='ACTIVE' ORDER BY code LIMIT 1",
        (channel_code,) if channel_code else ()).fetchone()
    if not channel:
        return None, {"ok": False, "error": "Không có kênh nào đang hoạt động"}
    template = conn.execute("SELECT * FROM caption_template WHERE is_active=1 ORDER BY code LIMIT 1").fetchone()
    if not template:
        return None, {"ok": False, "error": "Không có template caption nào đang bật"}
    return (campaign, channel, template), None


def _set_catalog_link_state(conn, product_id: str, status: str, error: str = None) -> None:
    """Store a safe, operator-visible link state without persisting provider details."""
    conn.execute("""UPDATE product SET affiliate_link_status=?, affiliate_link_error=?, updated_at=?
                    WHERE id=? AND provider=?""",
                 (status, error, now(), product_id, CATALOG_PROVIDER))


def _redacted_link_error(error: Exception) -> str:
    # Provider exceptions can embed tokens, headers, URLs, and response bodies.
    # Persist only the exception class, never its message.
    return f"Link creation failed ({type(error).__name__})"


def _create_post_from_catalog_product(conn, ctx, product, post_id: str, link,
                                      campaign_code: str, channel_code: str = None) -> dict:
    """Create a review post from a catalog row using a link bound to ``post_id``."""
    post_context, error = _catalog_post_context(conn, campaign_code, channel_code)
    if error:
        return error
    campaign, channel, template = post_context

    full_url = getattr(link, "full_url", None)
    short_url = getattr(link, "short_url", None)
    affiliate_url = short_url or full_url
    if not affiliate_url:
        return {"ok": False, "error": "Không thể tạo link affiliate cho sản phẩm"}

    discount = scoring.real_discount_depth(conn, product["id"], product["current_price"])
    image_path = imaging.compose(product, MEDIA_DIR, discount_pct=discount, handle=channel["handle"])
    image_url = ctx.get("storage", storage.get_storage()).put(image_path)
    variant_code = playbook.pick_hook()
    caption = content.generate(product, template["code"], affiliate_url, discount_pct=discount,
                               hook_code=variant_code)
    problems = content.validate(caption, niches=channel_niches(conn, channel["id"]))
    status = "PENDING_REVIEW" if not problems else "DRAFT"
    attribution_payload = {
        "provider": "accesstrade_product",
        "link_mode": "post_specific",
        "sub1": post_id,
        "external_product_id": product["external_product_id"],
    }
    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, caption_template_id,
                    variant_code, caption_body, disclosure_text, caption_final, image_url_composited,
                    affiliate_link, sub_id_payload, score, status, reject_reason, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (post_id, product["id"], channel["id"], campaign["id"], template["id"],
                  variant_code, caption, content.DISCLOSURE_DEFAULT, caption, image_url, affiliate_url,
                  json.dumps(attribution_payload, ensure_ascii=False, sort_keys=True), product["score"],
                  status, "; ".join(problems) if problems else None, now(), now()))
    audit(conn, "post", post_id, "created_catalog_product", actor="operator",
          detail={"external_product_id": product["external_product_id"],
                  "template": template["code"], "problems": problems})
    return {"ok": True, "post_id": post_id, "product_id": product["id"],
            "product_name": product["name"], "affiliate_link": affiliate_url,
            "image_url": image_url, "caption": caption, "problems": problems, "status": status}


def create_post_for_catalog_product(conn, ctx, product_id: str, campaign_code: str,
                                    channel_code: str = None, on_link_error=None) -> dict:
    """Create one catalog-backed review post with a newly allocated, per-post link.

    A copied product-card link deliberately uses ``product:<external_product_id>``
    as sub1. It is never read here: every content post obtains a fresh link tied to
    a real post id before media or caption generation begins.
    """
    product = ProductService(conn, ctx["product_client"]).get(product_id)
    if not product:
        return {"ok": False, "error": "Không tìm thấy sản phẩm trong catalog"}
    if not product["has_inventory"] or not product["detail_link"]:
        _set_catalog_link_state(conn, product_id, "UNAVAILABLE")
        return {"ok": False, "error": "Sản phẩm không đủ điều kiện tạo nội dung"}

    post_context, error = _catalog_post_context(conn, campaign_code, channel_code)
    if error:
        return error
    del post_context  # Preflight completes before making the externally visible link request.

    if product["main_image_url"] or product["image_url_original"]:
        try:
            materialize_catalog_image(conn, product, MEDIA_DIR, http=ctx.get("catalog_image_http"))
        except CatalogImageError as error:
            return {"ok": False, "error": str(error)}
        product = ProductService(conn, ctx["product_client"]).get(product_id)

    post_id = ulid()
    _set_catalog_link_state(conn, product_id, "CREATING")
    try:
        link = ctx["product_client"].create_product_link(
            product["detail_link"], post_id=post_id,
            external_product_id=product["external_product_id"])
        full_url = getattr(link, "full_url", None)
        short_url = getattr(link, "short_url", None)
        if not (full_url or short_url):
            raise ValueError("empty product link")
    except Exception as error:
        _set_catalog_link_state(conn, product_id, "FAILED", _redacted_link_error(error))
        if on_link_error is not None:
            on_link_error(error)
        return {"ok": False, "error": "Không thể tạo link affiliate cho sản phẩm"}

    linked_at = now()
    conn.execute("""UPDATE product
                    SET affiliate_url=?, affiliate_short_url=?, affiliate_link_status='READY',
                        affiliate_link_error=NULL, affiliate_link_created_at=?, updated_at=?
                    WHERE id=? AND provider=?""",
                 (full_url or short_url, short_url, linked_at, linked_at, product_id, CATALOG_PROVIDER))
    return _create_post_from_catalog_product(conn, ctx, product, post_id, link,
                                             campaign_code, channel_code)


# ------------------------------------------------------------- bài giá trị

def _median_30d(conn, category_code: str = None, niches: list = None):
    """Trung vị giá 30 ngày qua -- dữ liệu cho hook/bài so sánh giá.

    Ưu tiên category_code (khớp đúng, nhanh) nếu có; không thì lọc theo niches
    bằng niche.match_reasons() trên toàn bộ lịch sử giá gần đây.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    if category_code:
        prices = [r[0] for r in conn.execute(
            """SELECT h.price FROM product_price_history h
               JOIN product pr ON pr.id = h.product_id
               WHERE pr.category_code = ? AND h.observed_at >= ?""",
            (category_code, since)).fetchall()]
    else:
        rows = conn.execute(
            """SELECT h.price AS price, pr.* FROM product_price_history h
               JOIN product pr ON pr.id = h.product_id
               WHERE h.observed_at >= ?""", (since,)).fetchall()
        prices = [r["price"] for r in rows if not niches or not niche.match_reasons(r, niches)]
    return scoring.median(prices)


def create_value_post(conn, campaign_code: str, channel_code: str, kind: str = None,
                       rng=None) -> dict:
    """Tạo một bài KHÔNG bán hàng cho một kênh (core/valuepost.py). Dừng ở
    PENDING_REVIEW/DRAFT y hệt bài bán hàng -- vẫn phải qua duyệt tay."""
    rng = rng or random.Random()
    campaign = conn.execute("SELECT * FROM campaign WHERE code=?", (campaign_code,)).fetchone()
    if not campaign:
        return {"ok": False, "error": f"Chưa có chiến dịch {campaign_code}"}
    channel = conn.execute("SELECT * FROM channel WHERE code=? AND status='ACTIVE'",
                            (channel_code,)).fetchone()
    if not channel:
        return {"ok": False, "error": f"Kênh {channel_code} không tồn tại hoặc không hoạt động"}

    nl = channel_niches(conn, channel["id"])
    niche_code = rng.choice(nl) if nl else None
    niche_name = niche.NICHES[niche_code]["name"] if niche_code else "sản phẩm đang theo dõi"
    kind = kind or valuepost.pick_kind(rng)

    median_price, discounted = None, None
    if kind == "price_level":
        median_price = _median_30d(conn, niches=[niche_code] if niche_code else None)
    elif kind == "real_discount":
        candidates = scoring.score_candidates(conn, limit=5, niches=nl)
        discounted = [{"name": c["product"]["name"], "current_price": c["product"]["current_price"],
                        "discount_pct": c["breakdown"]["giảm giá thật"]}
                       for c in candidates if c["breakdown"]["giảm giá thật"] > 0]

    caption = valuepost.build(kind, niche_code=niche_code, niche_name=niche_name,
                               median_price=median_price, discounted_products=discounted)
    if caption is None:
        return {"ok": False, "error": f"Chưa đủ dữ liệu để tạo bài giá trị loại '{kind}' cho kênh {channel_code}"}

    problems = content.validate(caption, disclosure=valuepost.DISCLOSURE_VALUE,
                                 niches=nl, post_type="VALUE")
    post_id = ulid()
    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, caption_template_id,
                    variant_code, caption_body, disclosure_text, caption_final, sub_id_payload,
                    score, post_type, status, reject_reason, created_at, updated_at)
                    VALUES (?,NULL,?,?,NULL,?,?,?,?,NULL,NULL,'VALUE',?,?,?,?)""",
                 (post_id, channel["id"], campaign["id"], kind, caption, valuepost.DISCLOSURE_VALUE,
                  caption, "PENDING_REVIEW" if not problems else "DRAFT",
                  "; ".join(problems) if problems else None, now(), now()))
    audit(conn, "post", post_id, "created_value_post", actor="operator",
          detail={"kind": kind, "channel": channel_code, "problems": problems})
    return {"ok": True, "post_id": post_id, "kind": kind, "caption": caption, "problems": problems,
            "status": "PENDING_REVIEW" if not problems else "DRAFT"}


def post_mix(conn, ctx, campaign_code: str, channel_code: str = None, ratio: int = 3,
             rng=None) -> dict:
    """"Phương pháp 3 bài": cứ mỗi `ratio` bài thì có (ratio - 1) bài bán hàng và
    1 bài giá trị, tính riêng cho từng kênh (hoặc một kênh chỉ định).

    Bài bán hàng tạo qua job GENERATE_CONTENT như plan_content(); bài giá trị tạo
    ngay lập tức qua create_value_post() vì không cần chấm điểm sản phẩm.
    """
    rng = rng or random.Random()
    channels = conn.execute(
        "SELECT * FROM channel WHERE code=? AND status='ACTIVE'" if channel_code
        else "SELECT * FROM channel WHERE status='ACTIVE'",
        (channel_code,) if channel_code else ()).fetchall()
    campaign = conn.execute("SELECT * FROM campaign WHERE code=?", (campaign_code,)).fetchone()
    templates = conn.execute("SELECT * FROM caption_template WHERE is_active=1").fetchall()
    if not channels or not campaign or not templates:
        return {"sales_jobs": [], "value_posts": []}

    hooks = playbook.hook_codes()
    sales_jobs, value_posts = [], []
    n_sales = max(0, ratio - 1)
    for ch in channels:
        nl = channel_niches(conn, ch["id"])
        for i, item in enumerate(scoring.score_candidates(conn, limit=n_sales, niches=nl)):
            tpl = rng.choice(list(templates))
            variant = hooks[i % len(hooks)]
            job_id = enqueue(conn, "GENERATE_CONTENT", {
                "product_id": item["product"]["id"], "channel_id": ch["id"],
                "campaign_id": campaign["id"], "template_id": tpl["id"],
                "variant_code": variant, "score": item["score"],
            }, priority=int(item["score"] * 100),
               idempotency_key=f"gen:{item['product']['id']}:{variant}")
            if job_id:
                sales_jobs.append(job_id)

        res = create_value_post(conn, campaign_code, ch["code"], rng=rng)
        value_posts.append({"channel": ch["code"], **res})

    return {"sales_jobs": sales_jobs, "value_posts": value_posts}


# ------------------------------------------------------------------ chặng 3

@handler("GENERATE_CONTENT")
def generate_content(conn, payload, ctx):
    product = conn.execute("SELECT * FROM product WHERE id=?", (payload["product_id"],)).fetchone()
    channel = conn.execute("SELECT * FROM channel WHERE id=?", (payload["channel_id"],)).fetchone()
    campaign = conn.execute("SELECT * FROM campaign WHERE id=?", (payload["campaign_id"],)).fetchone()
    template = conn.execute("SELECT * FROM caption_template WHERE id=?", (payload["template_id"],)).fetchone()
    if not (product and channel and campaign and template):
        raise ValueError("Thiếu dữ liệu tham chiếu khi sinh nội dung")
    if product["provider"] == CATALOG_PROVIDER:
        raise ValueError("Catalog product must use the catalog content pipeline")

    post_id = ulid()
    subs = attribution.encode_sub_ids(post_id, campaign["code"], payload["variant_code"], channel["code"])
    link = ctx["source"].create_tracking_link(product["product_url"], subs)

    discount = scoring.real_discount_depth(conn, product["id"], product["current_price"])
    image_path = imaging.compose(product, MEDIA_DIR, discount_pct=discount, handle=channel["handle"])
    # Đẩy lên nơi có URL công khai. Local thì chỉ ghép URL, S3/R2 thì upload thật.
    image_url = ctx.get("storage", storage.get_storage()).put(image_path)

    caption = content.generate(product, template["code"], link, discount_pct=discount,
                                hook_code=payload["variant_code"])
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

def approve_post(conn, post_id: str, actor: str = "operator", caption_override: str = None,
                  scheduled_at: str = None) -> dict:
    """scheduled_at: giờ đăng do operator tự chọn (ISO 8601, có timezone). Bỏ
    trống thì tự động chọn slot gần nhất như trước (_next_slot()). Không tự
    quy đổi timezone ở đây -- gọi từ web/server.py đã quy đổi giờ địa phương
    của operator sang UTC trước khi truyền vào."""
    post = conn.execute("SELECT * FROM post WHERE id=?", (post_id,)).fetchone()
    if not post:
        return {"ok": False, "error": "Không tìm thấy bài đăng"}
    caption = caption_override or post["caption_final"]
    # post_type phải đọc từ DB -- bỏ sót chỗ này thì bài giá trị (không link, không
    # CTA) bị áp nhầm luật của bài bán hàng và KHÔNG BAO GIỜ duyệt được.
    problems = content.validate(caption, disclosure=post["disclosure_text"],
                                 niches=channel_niches(conn, post["channel_id"]),
                                 post_type=post["post_type"])
    if problems:
        return {"ok": False, "error": "; ".join(problems)}

    if scheduled_at:
        try:
            datetime.fromisoformat(scheduled_at)
        except ValueError:
            return {"ok": False, "error": "Giờ đăng không hợp lệ"}
        scheduled = scheduled_at
    else:
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

def mark_product_posted(conn, product_id: str, published_at: str) -> None:
    """Record a completed catalog publish for ranking and repost cooldowns."""
    conn.execute("""UPDATE product
                    SET last_posted_at=?, post_count=COALESCE(post_count, 0)+1, updated_at=?
                    WHERE id=? AND provider=?""",
                 (published_at, now(), product_id, CATALOG_PROVIDER))

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
    if post["product_id"]:
        mark_product_posted(conn, post["product_id"], result.published_at)
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
