"""Chấm điểm và chọn sản phẩm (BRD FR2).

Đây là module quyết định hệ thống kiếm được bao nhiêu tiền. Đăng đúng sản phẩm
với tần suất thấp thắng đăng bừa với tần suất cao -- nên toàn bộ trọng số và
ngưỡng lọc đều nằm trong DB, sửa được từ giao diện mà không phải deploy lại.
"""
import json
import math
from datetime import datetime, timedelta, timezone

from . import niche
from .db import now

DEFAULT_WEIGHTS = {
    "commission": 0.30,      # hoa hồng tuyệt đối kỳ vọng
    "category_cr": 0.25,     # tỷ lệ chuyển đổi lịch sử của danh mục
    "discount": 0.20,        # mức giảm thật so với trung vị 30 ngày
    "velocity": 0.15,        # tốc độ bán
    "rating": 0.10,          # điểm đánh giá có trọng số theo số lượt
    "recency_penalty": 0.35,
    "saturation_penalty": 0.20,
}

DEFAULT_FILTERS = {
    "min_rating": 4.0,
    "min_review_count": 20,
    "min_commission_value": 5000,
    "cooldown_days": 30,
    "max_per_category_per_day": 3,
    # Nhóm hàng cần giấy phép quảng cáo riêng -- loại thẳng, không chấm điểm.
    "blocked_categories": ["thuc-pham-chuc-nang", "thiet-bi-y-te", "tai-chinh", "duoc-pham"],
    # Chủ đề kênh. Rỗng = không lọc theo chủ đề. Ví dụ: ["thoi-trang-nu", "my-pham"]
    "niches": [],
}


def _norm(value, lo, hi):
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def median(xs):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def active_config(conn):
    row = conn.execute(
        "SELECT weights, filters FROM scoring_config WHERE is_active = 1 ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if not row:
        return dict(DEFAULT_WEIGHTS), dict(DEFAULT_FILTERS)
    w = dict(DEFAULT_WEIGHTS); w.update(json.loads(row["weights"]))
    f = dict(DEFAULT_FILTERS); f.update(json.loads(row["filters"]))
    return w, f


def save_config(conn, weights: dict, filters: dict, note: str = "") -> int:
    ver = (conn.execute("SELECT COALESCE(MAX(version), 0) FROM scoring_config").fetchone()[0] or 0) + 1
    conn.execute("UPDATE scoring_config SET is_active = 0")
    conn.execute(
        "INSERT INTO scoring_config (version, weights, filters, is_active, note, created_at) VALUES (?,?,?,1,?,?)",
        (ver, json.dumps(weights, ensure_ascii=False), json.dumps(filters, ensure_ascii=False), note, now()),
    )
    return ver


def category_conversion_rates(conn) -> dict:
    """CR thực đo theo danh mục. Đây là chỗ dữ liệu chuyển đổi quay ngược lại
    ảnh hưởng tới việc chọn sản phẩm -- vòng lặp khép kín của BRD."""
    rows = conn.execute("""
        SELECT pr.category_code AS cat,
               COUNT(DISTINCT c.id) AS orders,
               COALESCE(SUM(m.clicks), 0) AS clicks
        FROM post p
        JOIN product pr ON pr.id = p.product_id
        LEFT JOIN post_metrics m ON m.post_id = p.id
        LEFT JOIN conversion c ON c.post_id = p.id AND c.status = 'approved'
        WHERE p.status = 'PUBLISHED'
        GROUP BY pr.category_code
    """).fetchall()
    out = {}
    for r in rows:
        clicks = r["clicks"] or 0
        out[r["cat"]] = (r["orders"] / clicks) if clicks >= 30 else None  # cần đủ mẫu
    return out


def real_discount_depth(conn, product_id: str, current_price: int) -> float:
    """Giảm giá thật = so với trung vị 30 ngày, không tin 'giá gốc' sàn công bố.
    Đây là cách chặn chiêu nâng giá rồi giảm."""
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    prices = [r[0] for r in conn.execute(
        "SELECT price FROM product_price_history WHERE product_id = ? AND observed_at >= ?",
        (product_id, since)).fetchall()]
    med = median(prices)
    if not med or med <= 0:
        return 0.0
    return max(0.0, (med - current_price) / med)


def _reasons(p, filters) -> list:
    """Trả về lý do bị loại. Rỗng nghĩa là qua vòng lọc cứng."""
    out = []
    if p["category_code"] in filters["blocked_categories"]:
        out.append("danh mục cần giấy phép quảng cáo riêng")
    out.extend(niche.match_reasons(p, filters.get("niches") or []))
    if (p["rating"] or 0) < filters["min_rating"]:
        out.append(f"đánh giá {p['rating'] or 0} dưới ngưỡng {filters['min_rating']}")
    if (p["review_count"] or 0) < filters["min_review_count"]:
        out.append(f"chỉ {p['review_count'] or 0} lượt đánh giá")
    if (p["commission_value"] or 0) < filters["min_commission_value"]:
        out.append(f"hoa hồng {p['commission_value']:,}đ dưới ngưỡng")
    if not p["is_available"]:
        out.append("không còn trong feed")
    return out


def score_candidates(conn, limit: int = 20, explain: bool = False, niches=None):
    """Chấm điểm toàn bộ sản phẩm khả dụng, trả về top-K.

    niches: chủ đề của MỘT kênh cụ thể. Truyền vào thì ghi đè cấu hình chung --
    mỗi kênh có ngách riêng nên phải chấm điểm riêng cho từng kênh.

    Trả kèm giải thích để giao diện xem trước được ảnh hưởng khi đổi trọng số --
    người vận hành cần thấy vì sao một món lên đầu, không chỉ thấy thứ hạng.
    """
    weights, filters = active_config(conn)
    if niches is not None:
        filters = dict(filters, niches=niches)
    cat_cr = category_conversion_rates(conn)
    default_cr = 0.015

    cutoff = (datetime.now(timezone.utc) - timedelta(days=filters["cooldown_days"])).isoformat(timespec="seconds")
    recent = {r[0] for r in conn.execute(
        "SELECT DISTINCT product_id FROM post WHERE published_at >= ? OR status IN ('SCHEDULED','APPROVED','PENDING_REVIEW')",
        (cutoff,)).fetchall()}

    today = datetime.now(timezone.utc).date().isoformat()
    cat_today = {r[0]: r[1] for r in conn.execute("""
        SELECT pr.category_code, COUNT(*) FROM post p JOIN product pr ON pr.id = p.product_id
        WHERE substr(COALESCE(p.published_at, p.scheduled_at, p.created_at), 1, 10) = ?
        GROUP BY pr.category_code""", (today,)).fetchall()}

    products = conn.execute("SELECT * FROM product WHERE is_available = 1").fetchall()
    if not products:
        return []

    comms = [p["commission_value"] or 0 for p in products]
    vels = [(p["sold_count"] or 0) for p in products]
    c_lo, c_hi = min(comms), max(comms)
    v_lo, v_hi = min(vels), max(vels)

    scored = []
    for p in products:
        rejected = _reasons(p, filters)
        if p["id"] in recent:
            rejected.append(f"đã đăng trong {filters['cooldown_days']} ngày gần đây")
        if cat_today.get(p["category_code"], 0) >= filters["max_per_category_per_day"]:
            rejected.append("đủ hạn mức danh mục trong ngày")
        if rejected and not explain:
            continue

        s_comm = _norm(p["commission_value"] or 0, c_lo, c_hi)
        s_cr = _norm(cat_cr.get(p["category_code"]) or default_cr, 0.0, 0.04)
        s_disc = min(1.0, real_discount_depth(conn, p["id"], p["current_price"]) / 0.4)
        s_vel = _norm(math.log1p(p["sold_count"] or 0), math.log1p(v_lo), math.log1p(v_hi))
        s_rate = _norm((p["rating"] or 0) * math.log1p(p["review_count"] or 0), 0, 5 * math.log1p(5000))

        total = (weights["commission"] * s_comm + weights["category_cr"] * s_cr +
                 weights["discount"] * s_disc + weights["velocity"] * s_vel +
                 weights["rating"] * s_rate)

        scored.append({
            "product": p, "score": round(total, 4), "rejected": rejected,
            "breakdown": {"hoa hồng": round(s_comm, 3), "CR danh mục": round(s_cr, 3),
                          "giảm giá thật": round(s_disc, 3), "tốc độ bán": round(s_vel, 3),
                          "đánh giá": round(s_rate, 3)},
        })

    scored.sort(key=lambda x: (bool(x["rejected"]), -x["score"]))
    return scored if explain else [s for s in scored if not s["rejected"]][:limit]
