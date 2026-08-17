"""Controller-owned automatic ACP activation for completed Threads accounts."""
from __future__ import annotations

from datetime import datetime, timezone

from core.account_factory import ThreadsOAuthClient, get_session

from .channel_schema import ensure_factory_channel_schema
from .models import AccountStage
from .oauth_bridge import start_account_oauth, sync_account_from_oauth_session
from .oauth_config import build_factory_redirect_uri, configured_factory_public_base_url
from .repository import FactoryRepository


def _is_unexpired_waiting(session: dict | None) -> bool:
    if not session or session.get("status") != "WAITING_AUTH":
        return False
    try:
        expires = datetime.fromisoformat(session["expires_at"])
    except (TypeError, ValueError):
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires > datetime.now(timezone.utc)


class FactoryActivationService:
    def __init__(
        self,
        conn,
        *,
        provider=None,
        public_base_url: str | None = None,
    ):
        self.conn = conn
        ensure_factory_channel_schema(conn)
        self.repo = FactoryRepository(conn)
        self.provider = provider or ThreadsOAuthClient()
        self.public_base_url = configured_factory_public_base_url(public_base_url)
        self.redirect_uri = build_factory_redirect_uri(self.public_base_url)

    def _result_for_session(self, session: dict) -> dict:
        return {
            "session_id": session["id"],
            "status": session["status"],
            "authorization_url": self.provider.authorization_url(
                session["state"], self.redirect_uri
            ),
            "expires_at": session["expires_at"],
        }

    def start(self, account_id: str) -> dict:
        account = self.repo.get_account(account_id)
        if account is None:
            raise KeyError(account_id)

        session_id = account.get("oauth_session_id")
        if account["stage"] == AccountStage.ACP_CONNECTING.value and session_id:
            existing = get_session(self.conn, session_id)
            if _is_unexpired_waiting(existing):
                return self._result_for_session(existing)
            reconciled = self.reconcile(account_id)
            if reconciled["stage"] == AccountStage.ACP_ACTIVE.value:
                raise ValueError("account is already ACP_ACTIVE")
            account = reconciled

        if account["stage"] == AccountStage.RETRY_PENDING.value:
            if account.get("last_safe_stage") != AccountStage.THREADS_CREATED.value:
                raise ValueError("OAuth retry requires THREADS_CREATED last safe stage")
            if account.get("last_error_code") != "OAUTH_FAILED":
                raise ValueError("account retry is not an OAuth retry")
        elif account["stage"] != AccountStage.THREADS_CREATED.value:
            raise ValueError(f"account cannot auto-activate from {account['stage']}")

        return start_account_oauth(
            self.conn,
            account_id,
            self.redirect_uri,
            self.provider,
        )

    def reconcile(self, account_id: str) -> dict:
        account = self.repo.get_account(account_id)
        if account is None:
            raise KeyError(account_id)
        session_id = account.get("oauth_session_id")
        if not session_id:
            raise ValueError("account has no OAuth session")
        return sync_account_from_oauth_session(self.conn, session_id)
