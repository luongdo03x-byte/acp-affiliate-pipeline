"""Cấu hình vận hành bền vững, tách khỏi biến môi trường runtime.

Gộp 2 nhánh độc lập từng cùng đặt tên file này: publish worker fail-safe
(main, "feat: add fail-safe publish worker setting") và feature flag
Content Engine v2 (feat/content-engine-v2, E6) -- cả hai chỉ cần đúng 1
bảng key-value `system_setting` dùng chung, nên gộp lại thành 1 API thay vì
2 module trùng tên. Tên hàm gốc của publish worker (`get_system_setting`,
`set_system_setting`, `publish_worker_enabled`) giữ nguyên 100% vì
`web/server.py`/`core/jobs.py`/test suite đã phụ thuộc trực tiếp -- không
đổi tên, không đổi chữ ký. `get_setting`/`set_setting` (tên gốc bên E6) giữ
làm alias tương thích ngược cho `core/pipeline.py`/`web/server.py`'s
regenerate actions/toàn bộ test Content Engine v2 đã viết theo tên đó.
"""
from .db import audit, now


PUBLISH_WORKER_ENABLED = "publish_worker_enabled"
SEEDING_GLOBAL_PAUSED = "seeding_global_paused"
CONTENT_ENGINE_V2_ENABLED = "content_engine_v2_enabled"
CONTENT_GUARDS_DISABLED = "content_guards_disabled"

# Giá trị các khoá này không nhạy cảm -- ghi thật vào audit thay vì "[redacted]"
# để vận hành viên xem lịch sử bật/tắt trực tiếp từ audit_log.
_UNREDACTED_KEYS = {
    PUBLISH_WORKER_ENABLED,
    SEEDING_GLOBAL_PAUSED,
    CONTENT_ENGINE_V2_ENABLED,
    CONTENT_GUARDS_DISABLED,
}


def get_system_setting(conn, key: str, default=None):
    """Trả về giá trị đã lưu, hoặc default khi operator chưa từng đặt khoá này."""
    row = conn.execute("SELECT value FROM system_setting WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_system_setting(conn, key: str, value, actor: str = "operator") -> None:
    """Ghi đè một setting và lưu dấu vết thay đổi cho operator audit."""
    value = str(value)
    conn.execute("""
        INSERT INTO system_setting (key, value, updated_at, updated_by) VALUES (?,?,?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at,
            updated_by=excluded.updated_by
    """, (key, value, now(), actor))
    audit_value = value if key in _UNREDACTED_KEYS else "[redacted]"
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


def is_content_engine_v2_enabled(conn) -> bool:
    return get_system_setting(conn, CONTENT_ENGINE_V2_ENABLED, "0") == "1"


def content_guards_disabled(conn=None) -> bool:
    """Công tắc BỎ RÀO CHẾN NỘI DUNG (quyết định vận hành 2026-08, operator
    tự chịu trách nhiệm pháp lý về nội dung đăng).

    Khi bật (=1): bỏ kiểm tra cam kết công dụng, từ tuyệt đối hoá, cụm cấm
    theo ngách, bịa trải nghiệm, social proof, urgency... ở content.validate()
    và reviewer _safe_rewrite. GIỮ NGUYÊN hai rào kỹ thuật: caption phải còn
    link affiliate và không vượt giới hạn ký tự của nền tảng.

    Không có conn thì tự mở connection đọc. Lỗi CSDL (chưa migrate/bảng chưa
    có) -> coi như CHƯA tắt rào (fail-safe giữ bảo vệ).
    """
    import sqlite3

    def _read(c) -> bool:
        try:
            return get_system_setting(c, CONTENT_GUARDS_DISABLED, "0") == "1"
        except sqlite3.OperationalError:
            return False

    if conn is not None:
        return _read(conn)
    from .db import connect
    conn = connect()
    try:
        return _read(conn)
    finally:
        conn.close()


# Alias tương thích ngược -- toàn bộ core/pipeline.py, web/server.py (regenerate
# actions), tests/test_pipeline.py, tests/test_pilot.py của Content Engine v2
# (E1-E6) gọi bằng tên này, không phải get_system_setting/set_system_setting.
get_setting = get_system_setting
set_setting = set_system_setting
