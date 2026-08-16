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
import re
import sqlite3
import unicodedata
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


# --------------------------------------------------- AccountGroup/preset

def _slugify(name: str) -> str:
    """Bỏ dấu tiếng Việt, hạ chữ thường, thay ký tự không phải chữ/số bằng
    '-' -- dùng để tự sinh account_group.code từ name (operator không tự
    gõ code tay)."""
    s = unicodedata.normalize("NFD", name or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "D").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "nhom"


def create_account_group(conn, name: str, channel_ids: list) -> dict:
    """Tạo 1 AccountGroup mới -- preset chọn nhanh channel ở /sanpham, thuần
    tiện ích UI, KHÔNG ảnh hưởng logic tạo bài/publish. Tất-cả-hoặc-không-gì:
    có 1 channel không tồn tại thì không tạo nhóm nào."""
    channel_ids = list(dict.fromkeys(channel_ids or []))  # bỏ trùng, giữ thứ tự
    for cid in channel_ids:
        if not conn.execute("SELECT 1 FROM channel WHERE id=?", (cid,)).fetchone():
            return {"ok": False, "error": f"Không tìm thấy kênh {cid}"}
    group_id = ulid()
    code = f"{_slugify(name)}-{group_id[-6:]}"
    try:
        conn.execute("INSERT INTO account_group (id, code, name, created_at) VALUES (?,?,?,?)",
                     (group_id, code, name, now()))
    except sqlite3.IntegrityError:
        return {"ok": False, "error": "Tên nhóm đã tồn tại (trùng mã tự sinh), thử lại hoặc đổi tên khác"}
    for cid in channel_ids:
        conn.execute(
            "INSERT INTO account_group_channel (group_id, channel_id, created_at) VALUES (?,?,?)",
            (group_id, cid, now()))
    audit(conn, "account_group", group_id, "created", actor="operator",
          detail={"name": name, "channel_ids": channel_ids})
    return {"ok": True, "group_id": group_id}


def update_account_group_channels(conn, group_id: str, channel_ids: list) -> dict:
    """Ghi đè TOÀN BỘ thành viên của 1 nhóm (không thêm/bớt từng cái)."""
    if not conn.execute("SELECT 1 FROM account_group WHERE id=?", (group_id,)).fetchone():
        return {"ok": False, "error": f"Không tìm thấy nhóm {group_id}"}
    channel_ids = list(dict.fromkeys(channel_ids or []))  # bỏ trùng, giữ thứ tự
    for cid in channel_ids:
        if not conn.execute("SELECT 1 FROM channel WHERE id=?", (cid,)).fetchone():
            return {"ok": False, "error": f"Không tìm thấy kênh {cid}"}
    conn.execute("DELETE FROM account_group_channel WHERE group_id=?", (group_id,))
    for cid in channel_ids:
        conn.execute(
            "INSERT INTO account_group_channel (group_id, channel_id, created_at) VALUES (?,?,?)",
            (group_id, cid, now()))
    audit(conn, "account_group", group_id, "updated_channels", actor="operator",
          detail={"channel_ids": channel_ids})
    return {"ok": True}


def delete_account_group(conn, group_id: str) -> dict:
    """Xoá nhóm + mọi dòng account_group_channel liên quan. Không có bảng
    nào khác tham chiếu account_group.id nên không cần chặn kiểu
    media_asset phải chặn khi còn post_media tham chiếu."""
    if not conn.execute("SELECT 1 FROM account_group WHERE id=?", (group_id,)).fetchone():
        return {"ok": False, "error": f"Không tìm thấy nhóm {group_id}"}
    conn.execute("DELETE FROM account_group_channel WHERE group_id=?", (group_id,))
    conn.execute("DELETE FROM account_group WHERE id=?", (group_id,))
    audit(conn, "account_group", group_id, "deleted", actor="operator")
    return {"ok": True}


def list_account_groups(conn) -> list:
    """Mỗi nhóm kèm channels (đủ object channel, theo thứ tự tạo thành
    viên) + channel_codes (list code phẳng, rút từ channels) -- dùng ở
    /kenh hiển thị + /sanpham dựng nút chọn nhanh."""
    groups = [dict(r) for r in conn.execute(
        "SELECT * FROM account_group ORDER BY created_at").fetchall()]
    for g in groups:
        g["channels"] = [dict(r) for r in conn.execute(
            """SELECT ch.* FROM account_group_channel agc
               JOIN channel ch ON ch.id = agc.channel_id
               WHERE agc.group_id=? ORDER BY agc.created_at""", (g["id"],)).fetchall()]
        g["channel_codes"] = [c["code"] for c in g["channels"]]
    return groups


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


def post_media_urls(conn, post_id: str) -> list:
    """Ảnh THÊM (không gồm ảnh ghép tự động ở post.image_url_composited),
    đúng thứ tự position. Dùng ở publish_post() để dựng carousel."""
    return [r["url"] for r in conn.execute("""
        SELECT ma.url FROM post_media pm JOIN media_asset ma ON ma.id = pm.media_asset_id
        WHERE pm.post_id=? ORDER BY pm.position
    """, (post_id,)).fetchall()]


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


def latest_channel_caption_overrides(conn, post_ids: list) -> dict:
    """post_id -> {channel_id: override_text} -- override KHÔNG rỗng gần nhất
    (bất kể trạng thái publish_target, kể cả CANCELLED/FAILED) cho từng kênh
    của từng post. Dùng để tiền điền lại ô override theo account ở /duyet sau
    khi bài bị bounce về PENDING_REVIEW (ContentViolationError ở kênh khác,
    xem core/jobs.py) rồi duyệt lại -- nếu không, override đã nhập sẽ biến
    mất im lặng vì spec §8 cố ý không đọc lại publish_target cũ khi render."""
    if not post_ids:
        return {}
    placeholders = ",".join("?" * len(post_ids))
    rows = conn.execute(f"""
        SELECT post_id, channel_id, caption_override, created_at
        FROM publish_target
        WHERE post_id IN ({placeholders}) AND caption_override IS NOT NULL AND caption_override != ''
        ORDER BY created_at DESC, id DESC
    """, post_ids).fetchall()
    result = {}
    for r in rows:
        result.setdefault(r["post_id"], {})
        # ORDER BY created_at DESC -- dòng đầu tiên gặp mỗi (post_id, channel_id)
        # là mới nhất, không ghi đè nếu đã có trong dict. Phá hoà bằng id DESC:
        # now() chỉ chính xác tới GIÂY, hai lần duyệt lại trong cùng một giây
        # sẽ có created_at y hệt nhau -- id là ULID (10 ký tự đầu là mốc mili
        # giây) nên vẫn sắp đúng thứ tự, không rơi vào thứ tự tuỳ SQLite.
        if r["channel_id"] not in result[r["post_id"]]:
            result[r["post_id"]][r["channel_id"]] = r["caption_override"]
    return result


def _create_post_from_raw_product(conn, ctx, source, raw, campaign_code: str,
                                  channel_code: str = None, channel_codes: list = None,
                                  template_code: str = None,
                                  variant_code: str = None, rng=None,
                                  prebuilt_affiliate_link: str = None,
                                  attribution_payload: dict = None,
                                  audit_action: str = "created_single",
                                  media_asset_ids: list = None) -> dict:
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
    if media_asset_ids:
        # Bỏ trùng, giữ thứ tự -- chọn cùng 1 ảnh 2 lần không nên báo lỗi, và
        # post_media có UNIQUE(post_id, media_asset_id) nên submit trùng sẽ vỡ
        # INSERT sau nếu không bỏ trùng ở đây.
        media_asset_ids = list(dict.fromkeys(media_asset_ids))
        # Validate NGAY TRONG HÀM NÀY (không chỉ ở route web) -- pipeline là
        # nguồn sự thật duy nhất, web chỉ là 1 trong nhiều caller có thể có,
        # đúng khuôn channel_codes/_resolve_channels_by_code đã làm ở D1.
        if len(media_asset_ids) > 9:
            return {"ok": False, "error": f"Tối đa 9 ảnh thêm, nhận {len(media_asset_ids)}"}
        for aid in media_asset_ids:
            if not conn.execute("SELECT 1 FROM media_asset WHERE id=?", (aid,)).fetchone():
                return {"ok": False, "error": f"Không tìm thấy ảnh {aid} trong thư viện"}
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
    image_path = imaging.compose(product, MEDIA_DIR, discount_pct=discount,
                                 handle=channel["handle"] if len(channels) == 1 else None)
    image_url = ctx.get("storage", storage.get_storage()).put(image_path)

    caption = content.generate(product, template["code"], link, discount_pct=discount,
                                hook_code=variant_code, rng=rng)
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
    if media_asset_ids:
        for i, aid in enumerate(media_asset_ids, start=1):
            conn.execute("INSERT INTO post_media (post_id, media_asset_id, position) VALUES (?,?,?)",
                         (post_id, aid, i))
    audit(conn, "post", post_id, audit_action, actor="operator",
          detail={"source": source.name, "external_product_id": raw.external_product_id,
                  "template": template["code"], "problems": problems, "channel_ids": channel_ids})

    return {"ok": True, "post_id": post_id, "product_id": product_id,
            "product_name": product["name"], "affiliate_link": link,
            "image_url": image_url, "caption": caption, "problems": problems,
            "status": status}


def create_post_for_product(conn, ctx, external_product_id: str, campaign_code: str,
                            channel_code: str = None, channel_codes: list = None,
                            template_code: str = None,
                            variant_code: str = None, rng=None,
                            media_asset_ids: list = None) -> dict:
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
        channel_code=channel_code, channel_codes=channel_codes,
        template_code=template_code, variant_code=variant_code, rng=rng,
        media_asset_ids=media_asset_ids)


def create_post_from_manual_affiliate_product(conn, ctx, source, raw, affiliate_url: str,
                                               campaign_code: str, channel_code: str = None,
                                               channel_codes: list = None,
                                               template_code: str = None,
                                               variant_code: str = None, rng=None,
                                               media_asset_ids: list = None) -> dict:
    """Tạo bài review từ sản phẩm Shopee + affiliate URL có sẵn (dán link, không
    cần tự tạo tracking link); không publish."""
    if not affiliate_url or not affiliate_url.startswith(("http://", "https://")):
        return {"ok": False, "error": "Thiếu link affiliate hợp lệ"}
    if not raw.name or raw.current_price <= 0 or not raw.image_url_original:
        return {"ok": False, "error": "Thiếu tên, giá hoặc ảnh sản phẩm"}
    return _create_post_from_raw_product(
        conn, ctx, source, raw, campaign_code,
        channel_code=channel_code, channel_codes=channel_codes,
        template_code=template_code, variant_code=variant_code, rng=rng,
        media_asset_ids=media_asset_ids,
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
                  channel_ids: list = None, caption_facebook: str = None,
                  caption_instagram: str = None, caption_overrides: dict = None,
                  scheduled_at: str = None) -> dict:
    """scheduled_at: giờ đăng do operator tự chọn (ISO 8601, có timezone). Bỏ
    trống thì mỗi kênh tự tính slot riêng (_next_slot()). Không tự quy đổi
    timezone ở đây -- gọi từ web/server.py đã quy đổi giờ địa phương của
    operator sang UTC trước khi truyền vào."""
    post = conn.execute("SELECT * FROM post WHERE id=?", (post_id,)).fetchone()
    if not post:
        return {"ok": False, "error": "Không tìm thấy bài đăng"}

    ids = channel_ids or [post["channel_id"]]
    channels, err = _resolve_channels_by_id(conn, ids)
    if err:
        return {"ok": False, "error": err}

    caption = caption_override or post["caption_final"]

    # Validate phải dùng đúng giá trị SẮP được lưu trong lần duyệt này, không
    # phải giá trị cũ trong CSDL -- operator có thể đang set/sửa
    # caption_facebook/caption_instagram ngay trong request này.
    post_effective = dict(post)
    post_effective["caption_final"] = caption
    if caption_facebook is not None:
        post_effective["caption_facebook"] = caption_facebook.strip() or None
    if caption_instagram is not None:
        post_effective["caption_instagram"] = caption_instagram.strip() or None

    # Gom kênh theo đúng chuỗi caption chúng sẽ dùng -- validate mỗi nhóm 1
    # lần bằng union niches TRONG NHÓM ĐÓ thôi (không phải toàn bộ kênh được
    # chọn như D1, vì giờ các nhóm có thể dùng caption khác nhau hoàn toàn).
    # Khi mọi kênh vẫn dùng chung 1 caption thì công thức này tự nhiên rút
    # gọn về đúng y hệt cách D1 làm.
    groups = {}  # caption_text -> [channel row, ...]
    for ch in channels:
        text = _resolve_caption(post_effective, {"caption_override": (caption_overrides or {}).get(ch["id"])}, ch)
        groups.setdefault(text, []).append(ch)
    for text, chs in groups.items():
        ids_in_group = [c["id"] for c in chs]
        platforms_in_group = {c["platform"] for c in chs}
        max_len = min(content.PLATFORM_MAX_LEN.get(p, content.MAX_LEN) for p in platforms_in_group)
        # post_type phải đọc từ DB -- bỏ sót chỗ này thì bài giá trị (không
        # link, không CTA) bị áp nhầm luật của bài bán hàng và KHÔNG BAO GIỜ
        # duyệt được.
        problems = content.validate(text, niches=_union_niches(conn, ids_in_group), max_len=max_len,
                                     post_type=post["post_type"])
        if problems:
            return {"ok": False, "error": "; ".join(problems)}

    # scheduled_at do operator tự chọn được ưu tiên tuyệt đối, áp dụng cho MỌI
    # kênh được duyệt trong lượt này. Bỏ trống thì mỗi kênh tự tính slot riêng
    # -- rate-limit độc lập theo channel.min_gap_minutes của chính kênh đó (xem
    # _next_slot). post.scheduled_at chỉ còn là "sớm nhất trong N giờ", dùng để
    # sort/hiển thị, không còn là giờ đăng chính xác khi có từ 2 kênh trở lên.
    if scheduled_at:
        try:
            datetime.fromisoformat(scheduled_at)
        except ValueError:
            return {"ok": False, "error": "Giờ đăng không hợp lệ"}
        slots = {ch["id"]: scheduled_at for ch in channels}
    else:
        slots = {ch["id"]: _next_slot(conn, ch["id"]) for ch in channels}
    earliest = min(slots.values())
    conn.execute("""UPDATE post SET caption_final=?, status='SCHEDULED', scheduled_at=?,
                    reviewed_by=?, reviewed_at=?, reject_reason=NULL, updated_at=? WHERE id=?""",
                 (caption, earliest, actor, now(), now(), post_id))
    if caption_facebook is not None:
        conn.execute("UPDATE post SET caption_facebook=? WHERE id=?",
                     (caption_facebook.strip() or None, post_id))
    if caption_instagram is not None:
        conn.execute("UPDATE post SET caption_instagram=? WHERE id=?",
                     (caption_instagram.strip() or None, post_id))

    targets = []
    for ch in channels:
        target_id = ulid()
        slot = slots[ch["id"]]
        override = (caption_overrides or {}).get(ch["id"])
        conn.execute("""INSERT INTO publish_target (id, post_id, channel_id, status, scheduled_at,
                        caption_override, created_at, updated_at) VALUES (?,?,?,'SCHEDULED',?,?,?,?)""",
                     (target_id, post_id, ch["id"], slot, override, now(), now()))
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

def mark_product_posted(conn, product_id: str, published_at: str) -> None:
    """Record a completed catalog publish for ranking and repost cooldowns."""
    conn.execute("""UPDATE product
                    SET last_posted_at=?, post_count=COALESCE(post_count, 0)+1, updated_at=?
                    WHERE id=? AND provider=?""",
                 (published_at, now(), product_id, CATALOG_PROVIDER))


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


# Trùng đúng giới hạn đã hard-code trong publish() của từng Publisher
# (adapters/mock.py, adapters/live.py: Threads len(media)>1 báo lỗi,
# Facebook/Instagram 1-10 ảnh) -- 2 nguồn cùng giá trị, sửa 1 chỗ nhớ sửa
# chỗ kia, cùng rủi ro/cách xử lý đã chốt ở content.PLATFORM_MAX_LEN (D2).
MEDIA_MAX_COUNT = {"threads": 1, "facebook": 10, "instagram": 10}


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
        media += post_media_urls(conn, post["id"])
        media = media[:MEDIA_MAX_COUNT.get(channel["platform"], 1)]
        caption = _resolve_caption(post, target, channel)
        result = publisher.publish(channel, caption, media=media)
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
    if post["product_id"]:
        mark_product_posted(conn, post["product_id"], result.published_at)
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
