"""Mã hoá access token khi lưu (NFR2).

Khoá lấy từ biến môi trường ACP_MASTER_KEY (base64 của 32 byte). Trên production
nên thay bằng KMS/Vault -- chỉ cần thay hai hàm dưới, phần gọi không đổi.
"""
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_ENV_KEY = "ACP_MASTER_KEY"
_DEV_FALLBACK = b"\x00" * 32


def _master_key() -> bytes:
    raw = os.environ.get(_ENV_KEY)
    if not raw:
        # Chỉ dùng cho môi trường dev. Production không có khoá -> phải fail sớm.
        if os.environ.get("ACP_ENV") == "production":
            raise RuntimeError(f"{_ENV_KEY} chưa được đặt trong môi trường production")
        return _DEV_FALLBACK
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise RuntimeError(f"{_ENV_KEY} phải là base64 của đúng 32 byte")
    return key


def generate_key() -> str:
    """Sinh khoá mới để đặt vào ACP_MASTER_KEY."""
    return base64.b64encode(os.urandom(32)).decode()


def encrypt(plaintext: str) -> bytes:
    """Trả về nonce(12 byte) || ciphertext||tag."""
    nonce = os.urandom(12)
    ct = AESGCM(_master_key()).encrypt(nonce, plaintext.encode(), None)
    return nonce + ct


def decrypt(blob: bytes) -> str:
    if not blob:
        return ""
    return AESGCM(_master_key()).decrypt(blob[:12], blob[12:], None).decode()


def redact(value: str, keep: int = 4) -> str:
    """Dùng khi buộc phải ghi log thứ gì đó liên quan tới token (NFR3)."""
    if not value:
        return ""
    return f"{'*' * 8}{value[-keep:]}" if len(value) > keep else "*" * 8
