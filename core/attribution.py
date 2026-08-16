"""Quy kết doanh thu (BRD FR3 + FR8).

Module này là lý do hệ thống tồn tại. Không có nó, mọi thứ còn lại chỉ là một
cỗ máy đăng bài không biết mình kiếm được bao nhiêu.

Đường đi của một đồng hoa hồng:
    post_id -> sub1 trên tracking link -> click -> postback trả sub1 về
            -> conversion.post_id -> EPC theo danh mục/template/kênh
            -> hiệu chỉnh trọng số scoring cho vòng sau
"""
import json

from .db import now, ulid

SUB_KEYS = ("sub1", "sub2", "sub3", "sub4")


def encode_sub_ids(post_id: str, campaign_code: str, variant_code: str, channel_code: str) -> dict:
    """sub1 phải là post_id. Mọi thứ khác suy ra được từ post_id, nhưng giữ
    thêm ba trường kia để đọc báo cáo gốc của Accesstrade không phải join."""
    return {"sub1": post_id, "sub2": campaign_code, "sub3": variant_code, "sub4": channel_code}


def decode_sub_ids(payload: dict) -> dict:
    return {k: payload.get(k) for k in SUB_KEYS if payload.get(k)}


def extract_post_id(payload: dict):
    """post_id được gắn vào cả sub1 lẫn utm_content khi tạo link.

    Accesstrade có thể ghi đè utm_content với chiến dịch Shopee, và sub1 thì nằm
    trong _extra.parameters chứ không phải trường cấp một của API giao dịch. Đọc
    theo thứ tự ưu tiên để mất một đường vẫn còn đường kia.
    """
    for key in ("sub1", "utm_content", "pbid"):
        val = payload.get(key)
        if val and str(val).strip():
            return str(val).strip()
    return None


def record_conversion(conn, payload: dict) -> tuple:
    """Ghi nhận một postback. Trả về (trạng thái, id).

    Accesstrade gửi MỖI SẢN PHẨM một postback riêng khi khách mua nhiều món
    trong một đơn -- nên khoá khử trùng lặp là (transaction_id, product_id),
    không phải transaction_id đơn lẻ. Nhầm chỗ này là mất doanh thu trên báo cáo.
    """
    txn = str(payload.get("transaction_id") or "").strip()
    pid = str(payload.get("external_product_id") or "").strip()
    if not txn or not pid:
        return ("invalid", None)

    post_id = extract_post_id(payload)
    if post_id and not conn.execute("SELECT 1 FROM post WHERE id = ?", (post_id,)).fetchone():
        post_id = None  # mã lạ -> vẫn ghi nhận doanh thu, chỉ là không quy kết được

    existing = conn.execute(
        "SELECT id, status FROM conversion WHERE transaction_id = ? AND external_product_id = ?", (txn, pid)
    ).fetchone()
    if existing:
        return ("duplicate", existing["id"])

    cid = ulid()
    conn.execute("""
        INSERT INTO conversion (id, post_id, transaction_id, external_product_id, sale_amount,
                                commission, status, converted_at, updated_at, raw_payload)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (cid, post_id, txn, pid, int(payload.get("sale_amount") or 0),
          int(payload.get("commission") or 0), payload.get("status", "pending"),
          payload.get("converted_at") or now(), now(),
          json.dumps(payload, ensure_ascii=False)))
    return ("recorded", cid)


def reconcile(conn, transactions) -> dict:
    """Đối soát mỗi 6 giờ. Postback chỉ báo lúc phát sinh; trạng thái approved
    hay rejected chỉ chốt được ở đây. CHỈ approved mới tính vào KPI doanh thu."""
    stats = {"updated": 0, "inserted": 0, "unchanged": 0}
    for t in transactions:
        txn, pid = str(t.get("transaction_id")), str(t.get("external_product_id"))
        row = conn.execute(
            "SELECT id, status FROM conversion WHERE transaction_id = ? AND external_product_id = ?", (txn, pid)
        ).fetchone()
        if not row:
            record_conversion(conn, t)
            stats["inserted"] += 1
            continue
        if row["status"] != t.get("status"):
            conn.execute("UPDATE conversion SET status = ?, commission = ?, updated_at = ? WHERE id = ?",
                         (t.get("status"), int(t.get("commission") or 0), now(), row["id"]))
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1
    return stats


def add_clicks(conn, post_id: str, clicks: int) -> None:
    conn.execute("""
        INSERT INTO post_metrics (post_id, clicks, updated_at) VALUES (?,?,?)
        ON CONFLICT(post_id) DO UPDATE SET clicks = clicks + excluded.clicks, updated_at = excluded.updated_at
    """, (post_id, clicks, now()))


def update_insights(conn, post_id: str, insights: dict) -> None:
    if not insights:
        # Publisher chưa hỗ trợ fetch_insights thật (mặc định base class trả {},
        # và Threads cũng trả {} khi API lỗi) -- không được ghi một dòng post_metrics
        # toàn số 0 trông như đã đo đạc thật, và càng không được đè số liệu thật đã
        # có trước đó về 0 chỉ vì lần fetch này rỗng.
        return
    conn.execute("""
        INSERT INTO post_metrics (post_id, views, likes, replies, reposts, updated_at)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(post_id) DO UPDATE SET
            views = excluded.views, likes = excluded.likes,
            replies = excluded.replies, reposts = excluded.reposts, updated_at = excluded.updated_at
    """, (post_id, insights.get("views", 0), insights.get("likes", 0),
          insights.get("replies", 0), insights.get("reposts", 0), now()))


# ---------------------------------------------------------------- báo cáo

FUNNEL_SQL = """
SELECT
    (SELECT COUNT(*) FROM post WHERE status = 'PUBLISHED') AS posts,
    (SELECT COALESCE(SUM(views), 0) FROM post_metrics) AS views,
    (SELECT COALESCE(SUM(clicks), 0) FROM post_metrics) AS clicks,
    (SELECT COUNT(*) FROM conversion WHERE status = 'approved') AS orders,
    (SELECT COALESCE(SUM(commission), 0) FROM conversion WHERE status = 'approved') AS commission,
    (SELECT COALESCE(SUM(commission), 0) FROM conversion WHERE status = 'pending') AS pending
"""


def funnel(conn) -> dict:
    r = dict(conn.execute(FUNNEL_SQL).fetchone())
    r["epc"] = round(r["commission"] / r["clicks"]) if r["clicks"] else 0
    r["cr"] = round(r["orders"] / r["clicks"] * 100, 2) if r["clicks"] else 0.0
    r["ctr"] = round(r["clicks"] / r["views"] * 100, 2) if r["views"] else 0.0
    posts_with_click = conn.execute(
        "SELECT COUNT(*) FROM post_metrics WHERE clicks > 0").fetchone()[0]
    r["post_click_rate"] = round(posts_with_click / r["posts"] * 100, 1) if r["posts"] else 0.0
    return r


def _breakdown(conn, dimension_sql: str, label: str):
    return [dict(r) for r in conn.execute(f"""
        SELECT {dimension_sql} AS label,
               COUNT(DISTINCT p.id) AS posts,
               COALESCE(SUM(DISTINCT m.clicks), 0) AS clicks,
               COUNT(c.id) AS orders,
               COALESCE(SUM(CASE WHEN c.status = 'approved' THEN c.commission ELSE 0 END), 0) AS commission
        FROM post p
        JOIN product pr ON pr.id = p.product_id
        LEFT JOIN caption_template t ON t.id = p.caption_template_id
        LEFT JOIN channel ch ON ch.id = p.channel_id
        LEFT JOIN post_metrics m ON m.post_id = p.id
        LEFT JOIN conversion c ON c.post_id = p.id
        WHERE p.status = 'PUBLISHED'
        GROUP BY label
        HAVING label IS NOT NULL
        ORDER BY commission DESC
    """).fetchall()]


def epc_by(conn, dimension: str):
    """dimension: category | template | channel | variant"""
    sql = {
        "category": "pr.category_code",
        "template": "t.name",
        "channel": "ch.handle",
        "variant": "p.variant_code",
    }[dimension]
    rows = _breakdown(conn, sql, dimension)
    for r in rows:
        r["epc"] = round(r["commission"] / r["clicks"]) if r["clicks"] else 0
        r["cr"] = round(r["orders"] / r["clicks"] * 100, 2) if r["clicks"] else 0.0
    return sorted(rows, key=lambda r: -r["epc"])


def top_posts(conn, limit: int = 20):
    return [dict(r) for r in conn.execute("""
        SELECT p.id, p.caption_final, p.variant_code, p.published_at,
               pr.name AS product_name, pr.category_code, pr.image_path_local,
               COALESCE(m.clicks, 0) AS clicks, COALESCE(m.views, 0) AS views,
               COUNT(c.id) AS orders,
               COALESCE(SUM(CASE WHEN c.status = 'approved' THEN c.commission ELSE 0 END), 0) AS commission
        FROM post p
        JOIN product pr ON pr.id = p.product_id
        LEFT JOIN post_metrics m ON m.post_id = p.id
        LEFT JOIN conversion c ON c.post_id = p.id
        WHERE p.status = 'PUBLISHED'
        GROUP BY p.id
        ORDER BY commission DESC, clicks DESC
        LIMIT ?
    """, (limit,)).fetchall()]
