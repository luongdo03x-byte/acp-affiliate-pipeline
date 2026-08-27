"""Jinja context for Dynamic Topic trees and channel inheritance state."""
from __future__ import annotations

from flask import request

from ..core import topic_engine
from ..core.db import connect


def _channel_state(conn):
    """Trạng thái INCLUDE/EXCLUDE + số SP hợp lệ của từng kênh.

    Tính theo lối read-model: mọi dữ liệu topic được tải BẰNG MỘT VÀI query
    rồi đếm trong Python. Bản cũ gọi channel_accepts_product() cho từng cặp
    (kênh × sản phẩm) -- mỗi lần là lại channel_rules() + truy vấn
    product_topic + BFS descendants bằng SQL -- với catalog vài trăm SP và
    ~10 kênh thì trang /kenh mất cả giây đồng hồ chỉ để render số đếm.
    Ngữ nghĩa đếm giữ nguyên y hệt hàm cũ (xem test tương đương).
    """
    states = {}
    pool_counts = {}
    product_rows = conn.execute(
        "SELECT id FROM product WHERE provider='SHOPEE_AFFILIATE' AND is_available=1"
    ).fetchall()
    product_ids = {row["id"] for row in product_rows}

    def load_edges():
        edges = {}
        for row in conn.execute(
            """SELECT pt.product_id, pt.topic_id
               FROM product_topic pt JOIN topic t ON t.id=pt.topic_id
               WHERE t.status='ACTIVE'"""
        ):
            if row["product_id"] in product_ids:
                edges.setdefault(row["product_id"], set()).add(row["topic_id"])
        return edges

    topics_by_product = load_edges()

    # Hành vi lazy cũ: sản phẩm chưa có topic nào được sync SYSTEM topic ngay
    # khi gặp lần đầu. Làm trọn một lượt trước khi đếm để kết quả không phụ
    # thuộc thứ tự kênh.
    missing = [pid for pid in product_ids if not topics_by_product.get(pid)]
    if missing:
        for pid in missing:
            product = conn.execute("SELECT * FROM product WHERE id=?", (pid,)).fetchone()
            if product:
                topic_engine.sync_product_system_topics(conn, product)
        topics_by_product = load_edges()

    children = {}
    for row in conn.execute("SELECT id, parent_id FROM topic WHERE status='ACTIVE'"):
        children.setdefault(row["parent_id"], []).append(row["id"])

    def _closure(topic_id):
        seen = set()
        stack = [topic_id]
        while stack:
            for child in children.get(stack.pop(), ()):
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        return seen

    for channel in conn.execute("SELECT id FROM channel ORDER BY id").fetchall():
        cid = channel["id"]
        rules = topic_engine.channel_rules(conn, cid)
        states[cid] = {
            "includes": [item["code"] for item in rules["includes"]],
            "excludes": [item["code"] for item in rules["excludes"]],
        }
        excluded = set()
        included = set()
        for item in rules["excludes"]:
            excluded.add(item["id"])
            excluded |= _closure(item["id"])
        for item in rules["includes"]:
            included.add(item["id"])
            included |= _closure(item["id"])
        count = 0
        for pid in product_ids:
            owned = topics_by_product.get(pid) or set()
            if owned & excluded:
                continue
            if included and not (owned & included):
                continue
            count += 1
        pool_counts[cid] = count
    return states, pool_counts


def register_topic_ui(app):
    @app.context_processor
    def inject_dynamic_topics():
        # The hierarchy and per-channel pool count can be expensive on a large
        # catalog. Only /kenh renders them; Product Pool uses its own read model.
        if request.path != "/kenh":
            return {}
        conn = connect()
        try:
            topic_engine.ensure_system_topics(conn)
            tree = topic_engine.topic_tree(conn)
            states, pool_counts = _channel_state(conn)
            return {
                "dynamic_topic_tree": tree,
                "channel_topic_state": states,
                "channel_topic_pool": pool_counts,
            }
        except Exception:
            # Keep the channel page available while an additive migration is
            # still being applied; it can render an empty topic state safely.
            return {
                "dynamic_topic_tree": [],
                "channel_topic_state": {},
                "channel_topic_pool": {},
            }
        finally:
            conn.close()
