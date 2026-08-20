"""Encrypted per-account credentials for Account Factory V2."""
from __future__ import annotations

from core.crypto import decrypt, encrypt
from core.db import now


def store_account_password(conn, account_id: str, password: str) -> None:
    account_id = str(account_id or "").strip()
    password = str(password or "")
    if not account_id:
        raise ValueError("account_id is required")
    if not password:
        raise ValueError("password is required")
    timestamp = now()
    encrypted = encrypt(password)
    conn.execute(
        """INSERT INTO factory_account_credential(
               account_id,password_encrypted,created_at,updated_at
           ) VALUES(?,?,?,?)
           ON CONFLICT(account_id) DO UPDATE SET
             password_encrypted=excluded.password_encrypted,
             updated_at=excluded.updated_at""",
        (account_id, encrypted, timestamp, timestamp),
    )


def get_account_password(conn, account_id: str) -> str | None:
    account_id = str(account_id or "").strip()
    if not account_id:
        return None
    row = conn.execute(
        "SELECT password_encrypted FROM factory_account_credential WHERE account_id=?",
        (account_id,),
    ).fetchone()
    if row is None:
        return None
    return decrypt(row["password_encrypted"])


def has_account_password(conn, account_id: str) -> bool:
    account_id = str(account_id or "").strip()
    if not account_id:
        return False
    row = conn.execute(
        "SELECT 1 FROM factory_account_credential WHERE account_id=?",
        (account_id,),
    ).fetchone()
    return row is not None
