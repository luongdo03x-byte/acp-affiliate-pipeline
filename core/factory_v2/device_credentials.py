"""Per-device credentials for zero-config Account Factory Android enrollment.

Raw device tokens are returned only at enrollment time. SQLite stores SHA-256
hashes so a copied database does not reveal usable mobile API credentials.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from ..db import now, ulid


_SCHEMA = """
CREATE TABLE IF NOT EXISTS factory_device_credential (
    id TEXT PRIMARY KEY,
    device_id TEXT UNIQUE NOT NULL,
    device_name TEXT,
    token_hash TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_factory_device_credential_status
ON factory_device_credential(status, device_id);
"""


def _ensure_table(conn) -> None:
    conn.executescript(_SCHEMA)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _clean_identity(device_id: str, device_name: str | None) -> tuple[str, str]:
    clean_id = str(device_id or "").strip()
    if not 8 <= len(clean_id) <= 160:
        raise ValueError("device_id phải dài từ 8 đến 160 ký tự")
    if any(ord(ch) < 32 for ch in clean_id):
        raise ValueError("device_id chứa ký tự không hợp lệ")
    clean_name = " ".join(str(device_name or "Android device").split())[:120]
    return clean_id, clean_name or "Android device"


def issue_device_token(conn, device_id: str, device_name: str | None) -> str:
    """Issue/rotate one credential for a stable Android device id."""
    _ensure_table(conn)
    clean_id, clean_name = _clean_identity(device_id, device_name)
    token = secrets.token_urlsafe(32)
    digest = _token_hash(token)
    timestamp = now()
    existing = conn.execute(
        "SELECT id FROM factory_device_credential WHERE device_id=?",
        (clean_id,),
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE factory_device_credential
               SET device_name=?, token_hash=?, status='ACTIVE', last_used_at=NULL,
                   revoked_at=NULL
               WHERE device_id=?""",
            (clean_name, digest, clean_id),
        )
    else:
        conn.execute(
            """INSERT INTO factory_device_credential
               (id, device_id, device_name, token_hash, status, created_at)
               VALUES (?,?,?,?, 'ACTIVE', ?)""",
            (ulid(), clean_id, clean_name, digest, timestamp),
        )
    return token


def authenticate_device_token(conn, token: str | None) -> dict | None:
    """Return the active credential for token, otherwise None."""
    _ensure_table(conn)
    raw = str(token or "").strip()
    if not raw:
        return None
    digest = _token_hash(raw)
    row = conn.execute(
        """SELECT id, device_id, device_name, token_hash, status, created_at,
                  last_used_at, revoked_at
           FROM factory_device_credential
           WHERE token_hash=? AND status='ACTIVE'""",
        (digest,),
    ).fetchone()
    if row is None or not hmac.compare_digest(str(row["token_hash"]), digest):
        return None
    conn.execute(
        "UPDATE factory_device_credential SET last_used_at=? WHERE id=?",
        (now(), row["id"]),
    )
    result = dict(row)
    result.pop("token_hash", None)
    result["last_used_at"] = now()
    return result


def revoke_device_token(conn, device_id: str) -> bool:
    _ensure_table(conn)
    clean_id = str(device_id or "").strip()
    timestamp = now()
    cursor = conn.execute(
        """UPDATE factory_device_credential
           SET status='REVOKED', revoked_at=?
           WHERE device_id=? AND status='ACTIVE'""",
        (timestamp, clean_id),
    )
    return cursor.rowcount > 0
