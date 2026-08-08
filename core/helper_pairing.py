"""Pairing giữa trang /sanpham và ACP Shopee Helper (Chrome extension).

Bài toán: Shopee chặn request server-side (CAPTCHA/403 -- xem
adapters/shopee_affiliate.py), nên khi ACP không tự đọc được metadata, người
vận hành có thể mở trang Shopee thật trong Chrome (đã render bình thường,
không phải scrape) rồi bấm nút của extension để gửi tên/giá/ảnh/shop về ACP.

Ba lớp phòng thủ, không lớp nào thay được lớp kia:
  1. Token một lần dùng, TTL ngắn (5 phút), gắn với ĐÚNG product_url đang xác
     nhận -- không cho nộp metadata cho sản phẩm khác bằng token cũ.
  2. Endpoint nhận dữ liệu (`/api/helper/shopee-product`) chỉ chấp nhận request
     từ loopback (127.0.0.1/::1) -- xem web/server.py.
  3. Endpoint đó KHÔNG bật CORS, nên một trang web bất kỳ không thể tự ý POST
     tới đây bằng fetch() của trình duyệt (preflight sẽ bị chặn) -- chỉ
     extension (request đặc quyền, không chịu CORS trang web) gọi được.

Không lưu cookie/session Shopee. Không tự động hoá thao tác trên Shopee --
người dùng tự bấm nút, extension chỉ đọc DOM đã render sẵn.
"""
import secrets
import threading
import time

TTL_SECONDS = 300  # 5 phút -- đủ để mở tab Shopee và bấm nút, không dài hơn.

_lock = threading.Lock()
_tokens = {}  # token -> {"product_url", "created_at", "metadata", "consumed"}


def _gc(now: float) -> None:
    expired = [t for t, e in _tokens.items() if now - e["created_at"] > TTL_SECONDS]
    for t in expired:
        _tokens.pop(t, None)


def issue(product_url: str) -> dict:
    """Gọi từ trang /sanpham (đã đăng nhập) khi bấm 'Mở Shopee & lấy thông tin'."""
    token = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _lock:
        _gc(now)
        _tokens[token] = {
            "product_url": product_url, "created_at": now,
            "metadata": None, "consumed": False,
        }
    return {"token": token, "expires_in": TTL_SECONDS}


def submit(token: str, product_url: str, metadata: dict) -> bool:
    """Extension gọi để nộp metadata đọc được từ tab Shopee.

    Trả False cho mọi lý do thất bại (token sai, hết hạn, đã dùng, hoặc
    product_url không khớp) -- không tiết lộ lý do cụ thể ra response để
    không lộ thông tin giúp dò token.
    """
    now = time.monotonic()
    with _lock:
        _gc(now)
        entry = _tokens.get(token)
        if not entry or entry["consumed"]:
            return False
        if entry["product_url"] != product_url:
            return False
        entry["metadata"] = dict(metadata or {})
        entry["consumed"] = True
        return True


def poll(token: str):
    """Trang /sanpham gọi định kỳ để hỏi đã có dữ liệu từ extension chưa.

    None nếu token không tồn tại/hết hạn. Vẫn trả "ready" ở các lần poll sau
    (không xoá ngay sau lần đọc đầu) để không mất dữ liệu nếu một lần poll bị
    rớt mạng -- token tự dọn theo TTL như bình thường.
    """
    now = time.monotonic()
    with _lock:
        _gc(now)
        entry = _tokens.get(token)
        if not entry:
            return None
        if entry["metadata"] is None:
            return {"status": "pending"}
        return {"status": "ready", "metadata": entry["metadata"]}


def reset() -> None:
    """Chỉ dùng trong test."""
    with _lock:
        _tokens.clear()
