"""Operator-guided Threads tester onboarding for Factory V2 accounts.

Meta tester invitation/acceptance are human actions while the app is unpublished.
ACP persists only the two operator-confirmed milestones and derives the next step;
Factory V2 remains the authoritative account registry.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


_TESTER_READY_STAGE = "THREADS_CREATED"
_OAUTH_RETRY_STAGE = "RETRY_PENDING"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dict(row) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _is_oauth_retry(account: Mapping[str, Any]) -> bool:
    return (
        str(account.get("stage") or "") == _OAUTH_RETRY_STAGE
        and str(account.get("last_safe_stage") or "") == _TESTER_READY_STAGE
        and str(account.get("last_error_code") or "") == "OAUTH_FAILED"
    )


def _is_tester_ready(account: Mapping[str, Any]) -> bool:
    return str(account.get("stage") or "") == _TESTER_READY_STAGE or _is_oauth_retry(account)


def onboarding_status(account: Mapping[str, Any]) -> str | None:
    """Return the browser onboarding step for one Factory V2 account."""
    stage = str(account.get("stage") or "")
    if stage == "ACP_ACTIVE":
        return "ACTIVE"
    if stage == "DISABLED":
        return "DISABLED"
    if stage == "ACP_CONNECTING":
        return "OAUTH_IN_PROGRESS"
    if not _is_tester_ready(account):
        return None
    if account.get("tester_accepted_at"):
        return "READY_FOR_OAUTH"
    if account.get("tester_invited_at"):
        return "NEEDS_TESTER_ACCEPT"
    return "NEEDS_TESTER_INVITE"


def _get_account(conn, account_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM factory_account WHERE id=?", (str(account_id),)
    ).fetchone()
    account = _dict(row)
    if account is None:
        raise KeyError(account_id)
    return account


def _require_tester_ready(account: Mapping[str, Any]) -> None:
    if not _is_tester_ready(account):
        raise ValueError(
            f"account cannot update Threads tester milestones from {account.get('stage')}"
        )


def mark_tester_invited(conn, account_id: str, *, timestamp: str | None = None) -> dict[str, Any]:
    """Persist that the operator sent the Meta Threads tester invitation."""
    account = _get_account(conn, account_id)
    _require_tester_ready(account)
    at = str(timestamp or _now())
    conn.execute(
        """UPDATE factory_account
           SET tester_invited_at=COALESCE(tester_invited_at, ?)
           WHERE id=?""",
        (at, account_id),
    )
    return _get_account(conn, account_id)


def mark_tester_accepted(conn, account_id: str, *, timestamp: str | None = None) -> dict[str, Any]:
    """Persist acceptance; backfill invite so one click can cover both milestones."""
    account = _get_account(conn, account_id)
    _require_tester_ready(account)
    at = str(timestamp or _now())
    conn.execute(
        """UPDATE factory_account
           SET tester_invited_at=COALESCE(tester_invited_at, ?),
               tester_accepted_at=COALESCE(tester_accepted_at, ?)
           WHERE id=?""",
        (at, at, account_id),
    )
    return _get_account(conn, account_id)


def accept_and_start_oauth(
    conn,
    account_id: str,
    redirect_uri: str,
    provider,
    *,
    timestamp: str | None = None,
    start_oauth=None,
) -> dict[str, Any]:
    """Record tester acceptance and immediately hand the account to Threads OAuth."""
    mark_tester_accepted(conn, account_id, timestamp=timestamp)
    if start_oauth is None:
        from .oauth_bridge import start_account_oauth

        start_oauth = start_account_oauth
    return start_oauth(conn, account_id, redirect_uri, provider)


def list_onboarding_accounts(conn) -> list[dict[str, Any]]:
    """Return only accounts relevant to tester/OAuth onboarding, in batch sequence order."""
    result: list[dict[str, Any]] = []
    rows = conn.execute(
        "SELECT * FROM factory_account ORDER BY batch_id, sequence, id"
    ).fetchall()
    for row in rows:
        account = dict(row)
        status = onboarding_status(account)
        if status is None:
            continue
        account["onboarding_status"] = status
        result.append(account)
    return result
