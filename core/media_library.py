"""Thư viện ảnh dùng lại được giữa nhiều bài (sub-project D3).

ACP KHÔNG gọi bất kỳ API sinh ảnh AI nào (xem core/imaging.py -- nguyên tắc
đã chốt: model sinh ảnh làm biến dạng sản phẩm thật, gây hoàn đơn mất uy
tín kênh). Module này chỉ lưu trữ/xác thực ảnh operator tự upload hoặc tự
dán URL -- có thể là ảnh AI sinh ở công cụ ngoài (ChatGPT, DALL-E...),
nhưng operator tự quyết định dùng ảnh nào, ACP không tự động chèn ảnh nào
chưa qua mắt người.
"""
import os
from io import BytesIO

from PIL import Image

from .db import now, ulid
from ..adapters.safe_http import SafeHttpClient, SafeHttpError

_EXT_BY_FORMAT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}


class MediaValidationError(Exception):
    """Dữ liệu không phải ảnh thật/không đọc được."""


def _verify_image_bytes(data: bytes) -> str:
    """Xác thực đúng là ảnh hợp lệ, trả về đuôi file theo ĐỊNH DẠNG THẬT --
    không tin đuôi file/Content-Type người dùng khai báo."""
    try:
        probe = Image.open(BytesIO(data))
        fmt = (probe.format or "").upper()
        probe.verify()
    except Exception as exc:
        raise MediaValidationError("Dữ liệu ảnh không hợp lệ.") from exc
    return _EXT_BY_FORMAT.get(fmt, ".img")


def _write_local(data: bytes, media_dir: str, ext: str) -> str:
    os.makedirs(media_dir, exist_ok=True)
    path = os.path.join(media_dir, f"{ulid()}{ext}")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def materialize_uploaded_file(file_storage, media_dir: str) -> str:
    """file_storage: werkzeug FileStorage (request.files['image']). Xác
    thực đúng là ảnh thật rồi lưu cục bộ, trả về đường dẫn file."""
    data = file_storage.read()
    ext = _verify_image_bytes(data)
    return _write_local(data, media_dir, ext)


def materialize_external_image(url: str, media_dir: str, http_client=None) -> str:
    """Tải ảnh từ URL ngoài (cùng cơ chế an toàn với
    ShopeeAffiliateSource.materialize_image() -- SafeHttpClient chặn SSRF/
    redirect quá mức), xác thực đúng là ảnh thật rồi lưu cục bộ. KHÔNG lưu
    thẳng URL ngoài vào media_asset.url -- tránh carousel vỡ ảnh nếu link
    tạm (vd link ChatGPT sinh ra) hết hạn trước khi Meta kịp tải lúc
    publish."""
    client = http_client or SafeHttpClient(max_bytes=8 * 1024 * 1024)
    try:
        response = client.get(url, allowed_hosts=None, expected_content_prefix="image/")
    except SafeHttpError as exc:
        raise MediaValidationError(f"Không tải được ảnh từ URL: {exc}") from exc
    ext = _verify_image_bytes(response.content)
    return _write_local(response.content, media_dir, ext)


def create_media_asset(conn, local_path: str, source: str, storage_backend) -> dict:
    """Upload file cục bộ lên storage (S3/local, giống ảnh ghép ở
    imaging.compose()), ghi 1 dòng media_asset. source: 'upload' | 'url'."""
    url = storage_backend.put(local_path)
    asset_id = ulid()
    conn.execute("INSERT INTO media_asset (id, url, source, created_at) VALUES (?,?,?,?)",
                 (asset_id, url, source, now()))
    return {"id": asset_id, "url": url, "source": source}


def list_media_assets(conn) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM media_asset ORDER BY created_at DESC").fetchall()]


def delete_media_asset(conn, asset_id: str) -> dict:
    used = conn.execute(
        "SELECT COUNT(*) FROM post_media WHERE media_asset_id=?", (asset_id,)).fetchone()[0]
    if used:
        return {"ok": False, "error": f"Ảnh đang được dùng ở {used} bài, không xoá được"}
    row = conn.execute("SELECT id FROM media_asset WHERE id=?", (asset_id,)).fetchone()
    if not row:
        return {"ok": False, "error": "Không tìm thấy ảnh"}
    conn.execute("DELETE FROM media_asset WHERE id=?", (asset_id,))
    return {"ok": True}
