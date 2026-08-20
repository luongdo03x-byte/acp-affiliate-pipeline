"""Account Factory OAuth onboarding for Threads.

Android only receives session metadata. Threads tokens, the Threads app secret,
and ACP_MASTER_KEY stay in ACP. This module never logs or returns access tokens.
"""
from __future__ import annotations

import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from urllib.parse import urlencode

import requests

from . import crypto

AUTH_URL = "https://threads.net/oauth/authorize"
GRAPH_ROOT = "https://graph.threads.net"
TOKEN_URL = f"{GRAPH_ROOT}/oauth/access_token"
LONG_TOKEN_URL = f"{GRAPH_ROOT}/access_token"
PROFILE_URL = f"{GRAPH_ROOT}/me"
SCOPES = "threads_basic,threads_content_publish"

SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_factory_oauth_session (
    id                  TEXT PRIMARY KEY,
    state               TEXT UNIQUE NOT NULL,
    batch_id            TEXT,
    account_local_id    TEXT,
    expected_username   TEXT NOT NULL,
    actual_username     TEXT,
    threads_user_id     TEXT,
    channel_id          TEXT,
    channel_code        TEXT,
    status              TEXT NOT NULL DEFAULT 'WAITING_AUTH',
    last_error          TEXT,
    created_at          TEXT NOT NULL,
    expires_at          TEXT NOT NULL,
    completed_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_factory_oauth_state
    ON account_factory_oauth_session(state);
CREATE INDEX IF NOT EXISTS idx_factory_oauth_status
    ON account_factory_oauth_session(status, expires_at);
"""


class OAuthSessionError(ValueError):
    pass


class AccountMismatchError(OAuthSessionError):
    pass


class ThreadsOAuthError(RuntimeError):
    pass


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return secrets.token_urlsafe(18)


def normalize_username(value: str) -> str:
    value = str(value or "").strip().lstrip("@").lower()
    if not value or len(value) > 100 or not re.fullmatch(r"[a-z0-9._]+", value):
        raise ValueError("Threads username không hợp lệ")
    return value


def ensure_schema(conn) -> None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_factory_oauth_session'"
    ).fetchone()
    if not exists:
        conn.executescript(SESSION_SCHEMA)
        return
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_factory_oauth_state ON account_factory_oauth_session(state)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_factory_oauth_status ON account_factory_oauth_session(status, expires_at)"
    )


def create_oauth_session(
    conn,
    *,
    expected_username: str,
    batch_id: str | None = None,
    account_local_id: str | None = None,
    ttl_seconds: int = 600,
) -> Dict[str, Any]:
    if ttl_seconds < 60 or ttl_seconds > 3600:
        raise ValueError("ttl_seconds ngoài phạm vi cho phép")
    ensure_schema(conn)
    username = normalize_username(expected_username)
    now_dt = _now_dt()
    row = {
        "id": _new_id(),
        "state": secrets.token_urlsafe(32),
        "batch_id": str(batch_id or "") or None,
        "account_local_id": str(account_local_id or "") or None,
        "expected_username": username,
        "status": "WAITING_AUTH",
        "created_at": _iso(now_dt),
        "expires_at": _iso(now_dt + timedelta(seconds=ttl_seconds)),
    }
    conn.execute(
        """INSERT INTO account_factory_oauth_session
           (id,state,batch_id,account_local_id,expected_username,status,created_at,expires_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            row["id"], row["state"], row["batch_id"], row["account_local_id"],
            row["expected_username"], row["status"], row["created_at"], row["expires_at"],
        ),
    )
    return row


def _dict(row):
    return dict(row) if row is not None else None


def get_session(conn, session_id: str):
    ensure_schema(conn)
    return _dict(conn.execute(
        "SELECT * FROM account_factory_oauth_session WHERE id=?", (session_id,)
    ).fetchone())


def get_session_by_state(conn, state: str):
    ensure_schema(conn)
    return _dict(conn.execute(
        "SELECT * FROM account_factory_oauth_session WHERE state=?", (state,)
    ).fetchone())


def public_session(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "status": row["status"],
        "expected_username": row["expected_username"],
        "actual_username": row.get("actual_username"),
        "threads_user_id": row.get("threads_user_id"),
        "channel_code": row.get("channel_code"),
        "error": row.get("last_error"),
        "expires_at": row["expires_at"],
    }


def _safe_channel_code(conn, username: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "_", username.lower()).strip("_") or "account"
    base = f"threads_{stem}"[:80]
    code = base
    suffix = 2
    while conn.execute("SELECT 1 FROM channel WHERE code=?", (code,)).fetchone():
        code = f"{base[:74]}_{suffix}"
        suffix += 1
    return code


def _set_terminal(conn, session_id: str, status: str, *, error: str | None = None,
                  actual_username: str | None = None, threads_user_id: str | None = None):
    conn.execute(
        """UPDATE account_factory_oauth_session
           SET status=?, last_error=?, actual_username=COALESCE(?,actual_username),
               threads_user_id=COALESCE(?,threads_user_id), completed_at=?
           WHERE id=?""",
        (status, error, actual_username, threads_user_id, _iso(_now_dt()), session_id),
    )


def complete_oauth_session(conn, *, state: str, code: str, redirect_uri: str, provider):
    session = get_session_by_state(conn, state)
    if not session:
        raise OAuthSessionError("OAuth state không tồn tại")
    if session["status"] != "WAITING_AUTH":
        raise OAuthSessionError("OAuth state đã được sử dụng")
    try:
        expires = datetime.fromisoformat(session["expires_at"])
    except ValueError as exc:
        raise OAuthSessionError("OAuth session có thời gian hết hạn không hợp lệ") from exc
    if expires <= _now_dt():
        _set_terminal(conn, session["id"], "SESSION_EXPIRED", error="OAuth session đã hết hạn")
        raise OAuthSessionError("OAuth session đã hết hạn")
    if not code:
        _set_terminal(conn, session["id"], "OAUTH_ERROR", error="Thiếu authorization code")
        raise OAuthSessionError("Thiếu authorization code")

    try:
        short = provider.exchange_code(code, redirect_uri)
        short_token = str(short.get("access_token") or "")
        if not short_token:
            raise ThreadsOAuthError("Threads không trả short-lived token")
        long_data = provider.exchange_long_lived(short_token)
        long_token = str(long_data.get("access_token") or "")
        expires_in = int(long_data.get("expires_in") or 0)
        if not long_token or expires_in <= 0:
            raise ThreadsOAuthError("Threads không trả long-lived token hợp lệ")
        profile = provider.fetch_profile(long_token)
        actual = normalize_username(profile.get("username") or "")
        user_id = str(profile.get("id") or short.get("user_id") or "").strip()
        if not user_id:
            raise ThreadsOAuthError("Threads không trả user id")
    except OAuthSessionError:
        raise
    except Exception as exc:
        _set_terminal(conn, session["id"], "OAUTH_ERROR", error="Không thể hoàn tất Threads OAuth")
        raise ThreadsOAuthError("Không thể hoàn tất Threads OAuth") from exc

    expected = normalize_username(session["expected_username"])
    if actual != expected:
        _set_terminal(
            conn, session["id"], "ACCOUNT_MISMATCH",
            error="Tài khoản OAuth không khớp account đang onboarding",
            actual_username=actual, threads_user_id=user_id,
        )
        raise AccountMismatchError(f"Expected @{expected}, received @{actual}")

    encrypted = crypto.encrypt(long_token)
    token_expires_at = _iso(_now_dt() + timedelta(seconds=expires_in))
    handle = f"@{actual}"

    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            """SELECT * FROM channel
               WHERE external_user_id=? OR lower(handle)=lower(?)
               ORDER BY CASE WHEN external_user_id=? THEN 0 ELSE 1 END
               LIMIT 1""",
            (user_id, handle, user_id),
        ).fetchone()
        if existing:
            channel_id = existing["id"]
            channel_code = existing["code"]
            conn.execute(
                """UPDATE channel
                   SET platform='threads', handle=?, external_user_id=?, status='ACTIVE',
                       token_encrypted=?, token_expires_at=?
                   WHERE id=?""",
                (handle, user_id, encrypted, token_expires_at, channel_id),
            )
        else:
            channel_id = _new_id()
            channel_code = _safe_channel_code(conn, actual)
            conn.execute(
                """INSERT INTO channel
                   (id,code,platform,handle,external_user_id,status,token_encrypted,
                    token_expires_at,daily_post_cap,min_gap_minutes,niches,created_at)
                   VALUES (?,?,'threads',?,?,'ACTIVE',?,?,12,90,'[]',?)""",
                (channel_id, channel_code, handle, user_id, encrypted, token_expires_at, _iso(_now_dt())),
            )
        conn.execute(
            """UPDATE account_factory_oauth_session
               SET status='ACTIVE', actual_username=?, threads_user_id=?, channel_id=?,
                   channel_code=?, last_error=NULL, completed_at=?
               WHERE id=? AND status='WAITING_AUTH'""",
            (actual, user_id, channel_id, channel_code, _iso(_now_dt()), session["id"]),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return {
        "status": "ACTIVE",
        "username": actual,
        "threads_user_id": user_id,
        "channel_id": channel_id,
        "channel_code": channel_code,
        "token_expires_at": token_expires_at,
    }


class ThreadsOAuthClient:
    def __init__(self, *, app_id: str | None = None, app_secret: str | None = None, http=None):
        self.app_id = app_id or os.environ.get("THREADS_APP_ID", "")
        self.app_secret = app_secret or os.environ.get("THREADS_APP_SECRET", "")
        if not self.app_id or not self.app_secret:
            raise RuntimeError("THREADS_APP_ID và THREADS_APP_SECRET bắt buộc cho Account Factory OAuth")
        self.http = http or requests.Session()

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        return AUTH_URL + "?" + urlencode({
            "client_id": self.app_id,
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "response_type": "code",
            "state": state,
        })

    @staticmethod
    def _json_or_error(response, message: str):
        if response.status_code >= 400:
            raise ThreadsOAuthError(message)
        try:
            data = response.json()
        except ValueError as exc:
            raise ThreadsOAuthError(message) from exc
        if not isinstance(data, dict):
            raise ThreadsOAuthError(message)
        return data

    def exchange_code(self, code: str, redirect_uri: str):
        response = self.http.post(
            TOKEN_URL,
            data={
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=20,
        )
        return self._json_or_error(response, "Threads từ chối authorization code")

    def exchange_long_lived(self, short_token: str):
        response = self.http.get(
            LONG_TOKEN_URL,
            params={
                "grant_type": "th_exchange_token",
                "client_secret": self.app_secret,
                "access_token": short_token,
            },
            timeout=20,
        )
        return self._json_or_error(response, "Không đổi được long-lived Threads token")

    def fetch_profile(self, token: str):
        response = self.http.get(
            PROFILE_URL,
            params={"fields": "id,username", "access_token": token},
            timeout=20,
        )
        return self._json_or_error(response, "Không xác minh được Threads account")
