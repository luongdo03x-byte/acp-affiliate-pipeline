"""Bridge authoritative Factory V2 accounts to the existing Threads OAuth flow.

This module never owns or handles access tokens. Token exchange, verification,
encryption, and channel upsert remain in ``core.account_factory``.
"""
from __future__ import annotations

from core.account_factory import create_oauth_session, ensure_schema as ensure_oauth_schema, get_session
from core.db import now, transaction

from .models import AccountStage
from .repository import FactoryRepository
from .service import FactoryService


_RETRYABLE_START_STAGES = {AccountStage.THREADS_CREATED.value, AccountStage.RETRY_PENDING.value}
_RETRYABLE_OAUTH_ERRORS = {None, "OAUTH_FAILED"}


def start_account_oauth(conn, account_id: str, redirect_uri: str, provider) -> dict:
    """Create OAuth state from the controller-owned username and mark it connecting."""
    repo = FactoryRepository(conn)
    service = FactoryService(repo)
    account = repo.get_account(account_id)
    if account is None:
        raise KeyError(account_id)
    if account["stage"] not in _RETRYABLE_START_STAGES:
        raise ValueError(f"account cannot start OAuth from {account['stage']}")
    if account["stage"] == AccountStage.RETRY_PENDING.value and account["last_error_code"] not in _RETRYABLE_OAUTH_ERRORS:
        raise ValueError("account retry is not an OAuth retry")

    # Ensure DDL is complete before the controller transaction. Once the table
    # exists, ensure_schema only maintains indexes with transaction-safe execute().
    ensure_oauth_schema(conn)
    with transaction(conn):
        session = create_oauth_session(
            conn,
            expected_username=account["username"],
            batch_id=account["batch_id"],
            account_local_id=account["id"],
        )
        authorization_url = provider.authorization_url(session["state"], redirect_uri)
        service.transition_account(account["id"], AccountStage.ACP_CONNECTING)
        conn.execute(
            """UPDATE factory_account
               SET oauth_session_id=?, updated_at=?
               WHERE id=?""",
            (session["id"], now(), account["id"]),
        )

    return {
        "session_id": session["id"],
        "status": session["status"],
        "authorization_url": authorization_url,
        "expires_at": session["expires_at"],
    }


def sync_account_from_oauth_session(conn, session_id: str) -> dict:
    """Reconcile one persisted OAuth session into the authoritative V2 account."""
    session = get_session(conn, session_id)
    if session is None:
        raise KeyError(session_id)
    account_id = session.get("account_local_id")
    if not account_id:
        raise ValueError("OAuth session is not linked to a Factory V2 account")

    repo = FactoryRepository(conn)
    service = FactoryService(repo)
    account = repo.get_account(account_id)
    if account is None:
        raise KeyError(account_id)
    if account.get("oauth_session_id") != session_id:
        raise ValueError("OAuth session is stale for this Factory V2 account")

    status = session["status"]
    if status == "WAITING_AUTH":
        return account

    if status == "ACTIVE":
        with transaction(conn):
            current = repo.get_account(account_id)
            if current["stage"] != AccountStage.ACP_ACTIVE.value:
                if current["stage"] != AccountStage.ACP_CONNECTING.value:
                    raise ValueError(f"cannot activate account from {current['stage']}")
                service.transition_account(account_id, AccountStage.ACP_ACTIVE)
            conn.execute(
                """UPDATE factory_account
                   SET threads_user_id=?, channel_id=?, channel_code=?,
                       last_error_code=NULL, last_error_message=NULL, updated_at=?
                   WHERE id=?""",
                (
                    session.get("threads_user_id"),
                    session.get("channel_id"),
                    session.get("channel_code"),
                    now(),
                    account_id,
                ),
            )
        return repo.get_account(account_id)

    if status == "ACCOUNT_MISMATCH":
        current = repo.get_account(account_id)
        if current["stage"] != AccountStage.ERROR.value:
            if current["stage"] != AccountStage.ACP_CONNECTING.value:
                raise ValueError(f"cannot record OAuth mismatch from {current['stage']}")
            service.transition_account(
                account_id,
                AccountStage.ERROR,
                error_code="ACCOUNT_MISMATCH",
                error_message=session.get("last_error") or "OAuth account mismatch",
            )
        return repo.get_account(account_id)

    if status in {"OAUTH_ERROR", "SESSION_EXPIRED"}:
        current = repo.get_account(account_id)
        if current["stage"] != AccountStage.RETRY_PENDING.value:
            if current["stage"] != AccountStage.ACP_CONNECTING.value:
                raise ValueError(f"cannot retry OAuth from {current['stage']}")
            service.transition_account(
                account_id,
                AccountStage.RETRY_PENDING,
                error_code="OAUTH_FAILED",
                error_message=session.get("last_error") or "Threads OAuth failed",
            )
        return repo.get_account(account_id)

    raise ValueError(f"unsupported OAuth session status: {status}")
