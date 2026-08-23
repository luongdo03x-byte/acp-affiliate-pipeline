"""Operator management for AUTO/MANUAL topics.

System topics remain immutable because they carry content-safety semantics from
``core.niche``. Dynamic topic deletion is intentionally soft so historical
Product/topic attribution remains auditable.
"""
from __future__ import annotations

from . import topic_engine
from .db import audit, now


def _topic(conn, topic_id: str):
    return conn.execute("SELECT * FROM topic WHERE id=?", (str(topic_id),)).fetchone()


def _editable(row) -> None:
    if row is None:
        raise ValueError("Không tìm thấy chủ đề")
    if row["topic_type"] == "SYSTEM":
        raise ValueError("Chủ đề hệ thống không thể sửa hoặc xóa")
    if row["status"] != "ACTIVE":
        raise ValueError("Chủ đề không còn hoạt động")


def rename_topic(conn, topic_id: str, name: str) -> dict:
    topic = _topic(conn, topic_id)
    _editable(topic)
    display = str(name or "").strip()
    if not display:
        raise ValueError("Tên chủ đề không được rỗng")
    if topic_engine.normalize_text(display) == topic_engine.normalize_text(topic["name"]):
        return dict(topic)

    duplicate = conn.execute(
        """SELECT id FROM topic
           WHERE id<>? AND status='ACTIVE' AND parent_id IS ?
             AND lower(name)=lower(?) LIMIT 1""",
        (topic["id"], topic["parent_id"], display),
    ).fetchone()
    if duplicate:
        raise ValueError("Đã có chủ đề cùng tên trong nhánh này; hãy dùng Merge")

    stamp = now()
    old_name = topic["name"]
    conn.execute("UPDATE topic SET name=?, updated_at=? WHERE id=?", (display, stamp, topic["id"]))
    conn.execute(
        """INSERT OR IGNORE INTO topic_alias
           (alias_normalized,alias_display,topic_id,created_at) VALUES (?,?,?,?)""",
        (topic_engine.normalize_text(old_name), old_name, topic["id"], stamp),
    )
    audit(conn, "topic", topic["id"], "renamed", actor="operator",
          detail={"old_name": old_name, "new_name": display})
    return dict(_topic(conn, topic["id"]))


def merge_topic(conn, source_id: str, target_id: str) -> dict:
    source = _topic(conn, source_id)
    target = _topic(conn, target_id)
    _editable(source)
    if target is None or target["status"] != "ACTIVE":
        raise ValueError("Chủ đề đích không tồn tại hoặc đã bị tắt")
    if source["id"] == target["id"]:
        raise ValueError("Không thể merge một chủ đề vào chính nó")
    if source["parent_id"] != target["parent_id"]:
        raise ValueError("Chỉ merge các chủ đề cùng nhánh cha")

    stamp = now()
    # Preserve the strongest Product confidence when both topics were already
    # attached to the same Product.
    rows = conn.execute(
        "SELECT product_id,confidence,created_at FROM product_topic WHERE topic_id=?",
        (source["id"],),
    ).fetchall()
    for row in rows:
        existing = conn.execute(
            "SELECT confidence FROM product_topic WHERE product_id=? AND topic_id=?",
            (row["product_id"], target["id"]),
        ).fetchone()
        confidence = max(float(row["confidence"] or 0), float(existing["confidence"] or 0) if existing else 0.0)
        conn.execute(
            """INSERT INTO product_topic
               (product_id,topic_id,confidence,source,created_at,updated_at)
               VALUES (?,?,?,'MERGED',?,?)
               ON CONFLICT(product_id,topic_id) DO UPDATE SET
                 confidence=excluded.confidence, source='MERGED', updated_at=excluded.updated_at""",
            (row["product_id"], target["id"], confidence, row["created_at"], stamp),
        )
    conn.execute("DELETE FROM product_topic WHERE topic_id=?", (source["id"],))

    rules = conn.execute(
        "SELECT channel_id,rule_mode,created_at FROM channel_topic_rule WHERE topic_id=?",
        (source["id"],),
    ).fetchall()
    for row in rules:
        conn.execute(
            """INSERT OR IGNORE INTO channel_topic_rule
               (channel_id,topic_id,rule_mode,created_at,updated_at)
               VALUES (?,?,?,?,?)""",
            (row["channel_id"], target["id"], row["rule_mode"], row["created_at"], stamp),
        )
    conn.execute("DELETE FROM channel_topic_rule WHERE topic_id=?", (source["id"],))

    conn.execute("UPDATE topic SET parent_id=?, updated_at=? WHERE parent_id=?", (target["id"], stamp, source["id"]))
    conn.execute("UPDATE topic_alias SET topic_id=? WHERE topic_id=?", (target["id"], source["id"]))
    conn.execute(
        """INSERT OR IGNORE INTO topic_alias
           (alias_normalized,alias_display,topic_id,created_at) VALUES (?,?,?,?)""",
        (topic_engine.normalize_text(source["name"]), source["name"], target["id"], stamp),
    )
    conn.execute(
        """UPDATE topic SET status='MERGED', duplicate_candidate_of=?, product_count=0,
               updated_at=? WHERE id=?""",
        (target["id"], stamp, source["id"]),
    )
    topic_engine._refresh_product_count(conn, target["id"])
    audit(conn, "topic", source["id"], "merged", actor="operator",
          detail={"target_id": target["id"], "target_name": target["name"]})
    return {"source_id": source["id"], "target_id": target["id"], "target": dict(_topic(conn, target["id"]))}


def delete_topic(conn, topic_id: str) -> dict:
    topic = _topic(conn, topic_id)
    _editable(topic)
    child = conn.execute(
        "SELECT id FROM topic WHERE parent_id=? AND status='ACTIVE' LIMIT 1", (topic["id"],)
    ).fetchone()
    if child:
        raise ValueError("Chủ đề còn nhánh con; hãy merge/xóa các nhánh con trước")
    stamp = now()
    conn.execute("DELETE FROM channel_topic_rule WHERE topic_id=?", (topic["id"],))
    conn.execute("UPDATE topic SET status='DISABLED', updated_at=? WHERE id=?", (stamp, topic["id"]))
    audit(conn, "topic", topic["id"], "disabled", actor="operator")
    return dict(_topic(conn, topic["id"]))


def list_manageable(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT t.*, p.name AS parent_name, d.name AS duplicate_name
           FROM topic t
           LEFT JOIN topic p ON p.id=t.parent_id
           LEFT JOIN topic d ON d.id=t.duplicate_candidate_of
           WHERE t.status='ACTIVE' AND t.topic_type IN ('AUTO','MANUAL')
           ORDER BY p.name, t.name, t.code"""
    ).fetchall()
    items = [dict(row) for row in rows]
    for item in items:
        targets = conn.execute(
            """SELECT id,code,name FROM topic
               WHERE status='ACTIVE' AND id<>? AND parent_id IS ?
               ORDER BY topic_type='SYSTEM' DESC,name,code""",
            (item["id"], item["parent_id"]),
        ).fetchall()
        item["merge_targets"] = [dict(row) for row in targets]
    return items
