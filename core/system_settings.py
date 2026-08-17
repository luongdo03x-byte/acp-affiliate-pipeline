"""Feature flag / cấu hình hệ thống dạng key-value (Content Engine v2, E6).

Không đụng core/pipeline.py's approve_post()/publish_post() -- module này
chỉ đọc/ghi 1 bảng cấu hình chung, không có logic nghiệp vụ.
"""
from .db import audit, now


def get_setting(conn, key: str, default: str = None) -> str:
    row = conn.execute("SELECT value FROM system_setting WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key: str, value: str, actor: str = "system") -> None:
    conn.execute("""INSERT INTO system_setting (key, value, updated_at, updated_by)
        VALUES (?,?,?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at,
            updated_by=excluded.updated_by""", (key, value, now(), actor))
    audit(conn, "system_setting", key, "updated", actor=actor, detail={"value": value})


def is_content_engine_v2_enabled(conn) -> bool:
    return get_setting(conn, "content_engine_v2_enabled", "0") == "1"
