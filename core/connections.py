"""Kết nối Meta (Facebook Login) + import/đồng bộ Facebook Page và Instagram
Professional account (PTYC §5, §20-24). Tách khỏi core/pipeline.py vì đây là
mối quan tâm khác: quản lý ACCOUNT có thể publish, không phải nội dung/luồng
publish của một post cụ thể.

Mỗi Page/IG account nhận một channel row RIÊNG với token RIÊNG
(channel.token_encrypted) -- đúng cách Publisher/publish_post đã tiêu thụ
channel từ sub-project A, không cần sửa gì ở đó. meta_connection.token_encrypted
(user token) chỉ dùng để chạy lại discovery ở đây, không dùng để publish.
"""
from .crypto import encrypt
from .db import audit, now, ulid


def _upsert_meta_connection(conn, exchanged) -> str:
    row = conn.execute("SELECT id FROM meta_connection WHERE meta_user_id=?",
                       (exchanged.meta_user_id,)).fetchone()
    if row:
        conn.execute("""UPDATE meta_connection SET token_encrypted=?, status='ACTIVE',
                        updated_at=? WHERE id=?""",
                     (encrypt(exchanged.token), now(), row["id"]))
        return row["id"]
    connection_id = ulid()
    conn.execute("""INSERT INTO meta_connection (id, provider, token_encrypted, meta_user_id,
                    status, created_at, updated_at) VALUES (?,'meta',?,?,'ACTIVE',?,?)""",
                 (connection_id, encrypt(exchanged.token), exchanged.meta_user_id, now(), now()))
    audit(conn, "meta_connection", connection_id, "connected",
          detail={"meta_user_id": exchanged.meta_user_id})
    return connection_id


def _upsert_channel_account(conn, *, connection_id: str, platform: str,
                            external_account_id: str, handle: str, username: str,
                            page_token: str) -> bool:
    """Trả True nếu đây là account MỚI (chưa từng thấy), False nếu là cập nhật
    account đã có. Khớp theo (platform, external_account_id) -- khoá tự nhiên
    của một Page/IG account trên Meta, không đổi qua các lần sync."""
    existing = conn.execute(
        "SELECT id FROM channel WHERE platform=? AND external_account_id=?",
        (platform, external_account_id)).fetchone()
    if existing:
        conn.execute("""UPDATE channel SET handle=?, username=?, token_encrypted=?,
                        status='ACTIVE', connection_id=?, last_sync_at=? WHERE id=?""",
                     (handle, username, encrypt(page_token), connection_id, now(), existing["id"]))
        return False
    code = f"{platform}_{external_account_id}"
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, token_encrypted,
                    connection_id, external_account_id, username, enabled, last_sync_at, created_at)
                    VALUES (?,?,?,?,'ACTIVE',?,?,?,?,1,?,?)""",
                 (ulid(), code, platform, handle, encrypt(page_token),
                  connection_id, external_account_id, username, now(), now()))
    return True


def sync_meta_accounts(conn, service, connection_id: str, actor: str = "operator") -> dict:
    """Chạy lại discovery cho một connection đã có -- dùng cho cả lần import
    đầu tiên (gọi từ connect_meta_account) lẫn nút "Đồng bộ lại" thủ công.
    Upsert theo (platform, external_account_id), không tạo trùng. Account
    trước đây thuộc connection này mà lần này Meta không còn trả về ->
    NEEDS_REAUTH, KHÔNG xoá (giữ lịch sử post/job)."""
    connection = conn.execute("SELECT * FROM meta_connection WHERE id=?", (connection_id,)).fetchone()
    if not connection:
        return {"ok": False, "error": "Không tìm thấy kết nối Meta"}

    from .crypto import decrypt
    user_token = decrypt(connection["token_encrypted"])
    pages = service.list_pages(user_token)

    seen_account_ids = []
    imported, updated = 0, 0
    for page in pages:
        is_new = _upsert_channel_account(
            conn, connection_id=connection_id, platform="facebook",
            external_account_id=page.external_account_id, handle=page.name,
            username=None, page_token=page.page_token)
        imported += int(is_new)
        updated += int(not is_new)
        seen_account_ids.append(page.external_account_id)

        ig = service.instagram_for_page(page.external_account_id, page.page_token)
        if ig:
            is_new_ig = _upsert_channel_account(
                conn, connection_id=connection_id, platform="instagram",
                external_account_id=ig.external_account_id, handle=f"@{ig.username}",
                username=ig.username, page_token=ig.page_token)
            imported += int(is_new_ig)
            updated += int(not is_new_ig)
            seen_account_ids.append(ig.external_account_id)

    reconnect_required = 0
    previously_seen = conn.execute(
        "SELECT id, external_account_id FROM channel WHERE connection_id=? AND status='ACTIVE'",
        (connection_id,)).fetchall()
    for row in previously_seen:
        if row["external_account_id"] and row["external_account_id"] not in seen_account_ids:
            conn.execute("UPDATE channel SET status='NEEDS_REAUTH', last_sync_at=? WHERE id=?",
                         (now(), row["id"]))
            reconnect_required += 1

    audit(conn, "meta_connection", connection_id, "synced", actor=actor,
          detail={"imported": imported, "updated": updated, "reconnect_required": reconnect_required})
    return {"ok": True, "connection_id": connection_id, "imported": imported,
            "updated": updated, "reconnect_required": reconnect_required}


def connect_meta_account(conn, service, code: str, redirect_uri: str,
                         actor: str = "operator") -> dict:
    """Điểm vào từ OAuth callback: đổi code lấy token, upsert connection,
    chạy discovery+sync ngay. Kết nối lại đúng tài khoản Meta đã có (khớp
    meta_user_id) không tạo connection thứ hai."""
    exchanged = service.exchange_code(code, redirect_uri)
    connection_id = _upsert_meta_connection(conn, exchanged)
    return sync_meta_accounts(conn, service, connection_id, actor=actor)
