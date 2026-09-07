"""Short-lived, one-use cloud pairing grants created by the server operator."""
from __future__ import annotations

import hashlib
import secrets
import time

from .device_credentials import _clean_identity, issue_device_token


def ensure_pairing_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS factory_pairing_grant (
        code_hash TEXT PRIMARY KEY,
        expires_at INTEGER NOT NULL,
        consumed_at INTEGER,
        device_id TEXT
    )""")


def create_pairing_code(conn, *, ttl_seconds=600):
    if not 60 <= ttl_seconds <= 900:
        raise ValueError("Pairing expiry must be between 60 and 900 seconds")
    ensure_pairing_schema(conn)
    code = secrets.token_urlsafe(24)
    conn.execute(
        "INSERT INTO factory_pairing_grant(code_hash, expires_at) VALUES (?, ?)",
        (hashlib.sha256(code.encode()).hexdigest(), int(time.time()) + ttl_seconds),
    )
    return code


def redeem_pairing_code(conn, code, device_id, device_name=None):
    clean_id, clean_name = _clean_identity(device_id, device_name)
    if not isinstance(code, str) or not 20 <= len(code) <= 128:
        raise ValueError("Mã ghép đôi không hợp lệ, đã dùng hoặc hết hạn")
    ensure_pairing_schema(conn)
    # Claim + token issuance must commit together. A lost response requires a
    # fresh operator grant; replay never rotates the already-issued token.
    conn.execute("BEGIN IMMEDIATE")
    try:
        timestamp = int(time.time())
        claimed = conn.execute(
            """UPDATE factory_pairing_grant SET consumed_at=?, device_id=?
               WHERE code_hash=? AND consumed_at IS NULL AND expires_at>?""",
            (timestamp, clean_id, hashlib.sha256(code.encode()).hexdigest(), timestamp),
        )
        if claimed.rowcount != 1:
            raise ValueError("Mã ghép đôi không hợp lệ, đã dùng hoặc hết hạn")
        token = issue_device_token(conn, clean_id, clean_name)
        conn.commit()
        return token
    except Exception:
        conn.rollback()
        raise
