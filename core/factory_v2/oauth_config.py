"""Shared configuration helpers for Account Factory Threads OAuth."""
from __future__ import annotations

import os

from ..account_factory import ThreadsOAuthClient

_CALLBACK_PATH = "/oauth/account-factory/threads/callback"


def build_factory_redirect_uri(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("Factory public base URL is required")
    return base + _CALLBACK_PATH


def configured_factory_public_base_url(fallback: str | None = None) -> str:
    configured = os.environ.get("ACP_PUBLIC_BASE_URL", "").strip()
    base = configured or str(fallback or "").strip()
    if not base:
        raise RuntimeError("ACP_PUBLIC_BASE_URL is required for automatic ACP activation")
    return base.rstrip("/")


def build_threads_oauth_provider(app=None):
    if app is not None:
        factory = app.config.get("ACCOUNT_FACTORY_OAUTH_FACTORY")
        if factory:
            return factory()
    return ThreadsOAuthClient()
