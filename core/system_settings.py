"""Cấu hình vận hành bền vững, tách khỏi biến môi trường runtime."""
from .db import audit, now


PUBLISH_WORKER_ENABLED = "publish_worker_enabled"
SEEDING_GLOBAL_PAUSED = "seeding_global_paused"


def get_system_setting(conn, key: str, default=None):
    """Trả về giá trị đã lưu, hoặc default khi operator chưa từng đặt khoá này."""
    row = conn.execute("SELECT value FROM system_setting WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_system_setting(conn, key: str, value, actor: str = "operator") -> None:
    """Ghi đè một setting và lưu dấu vết thay đổi cho operator audit."""
    value = str(value)
    conn.execute("""
        INSERT INTO system_setting (key, value, updated_at) VALUES (?,?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
    """, (key, value, now()))
    audit_value = value if key == PUBLISH_WORKER_ENABLED else "[redacted]"
    audit(conn, "system_setting", key, "set", actor=actor, detail={"value": audit_value})


def publish_worker_enabled(conn) -> bool:
    """Fail-safe: không có bản ghi nào đồng nghĩa worker đăng bài đang tắt."""
    return get_system_setting(conn, PUBLISH_WORKER_ENABLED, "0") == "1"


def seeding_global_paused(conn) -> bool:
    """Không có setting nghĩa là seeding đang hoạt động; campaign vẫn tự quyết auto-submit."""
    return get_system_setting(conn, SEEDING_GLOBAL_PAUSED, "0") == "1"


def set_seeding_global_paused(conn, paused: bool, actor: str = "operator") -> None:
    """Bật/tắt kill switch seeding và để lại audit qua set_system_setting()."""
    set_system_setting(conn, SEEDING_GLOBAL_PAUSED, "1" if paused else "0", actor=actor)
