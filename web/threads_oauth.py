"""Browser-driven Threads OAuth used by the ACP /kenh page.

This keeps the Threads app secret and all access tokens server-side. The browser
only receives the provider authorization URL and the final success/error redirect.
"""
from __future__ import annotations

import os

from flask import redirect, request, session, url_for

# Main ACP imports this module as ``acp.web.threads_oauth`` while focused tests
# import it as ``web.threads_oauth`` from the repository root. Support both
# package layouts without changing the OAuth behavior.
if __package__ and "." in __package__:
    from ..core.account_factory import (
        OAuthSessionError,
        ThreadsOAuthClient,
        ThreadsOAuthError,
        complete_oauth_session,
        create_oauth_session,
    )
    from ..core.db import connect
    from .threads_onboarding import register_threads_onboarding_routes
else:
    from core.account_factory import (
        OAuthSessionError,
        ThreadsOAuthClient,
        ThreadsOAuthError,
        complete_oauth_session,
        create_oauth_session,
    )
    from core.db import connect
    from web.threads_onboarding import register_threads_onboarding_routes


def _redirect_uri() -> str:
    public_base = os.environ.get("ACP_PUBLIC_BASE_URL", "").strip().rstrip("/")
    base = public_base or request.host_url.rstrip("/")
    return base + "/oauth/threads/connect/callback"


def _provider(app):
    provider_factory = app.config.get("THREADS_CHANNEL_OAUTH_FACTORY")
    return provider_factory() if provider_factory else ThreadsOAuthClient()


def register_threads_channel_oauth_routes(app, *, admin_password: str):
    @app.get("/oauth/threads/start")
    def threads_channel_oauth_start():
        if admin_password and not session.get("uid"):
            return redirect(url_for("login", next="/oauth/threads/start"))

        try:
            provider = _provider(app)
        except RuntimeError:
            return redirect(url_for(
                "channels",
                err="Threads OAuth chưa được cấu hình: thiếu THREADS_APP_ID/THREADS_APP_SECRET",
            ))

        conn = connect()
        try:
            oauth_session = create_oauth_session(conn)
        finally:
            conn.close()
        return redirect(provider.authorization_url(oauth_session["state"], _redirect_uri()))

    @app.get("/oauth/threads/connect/callback")
    def threads_channel_oauth_callback():
        provider_error = request.args.get("error_description") or request.args.get("error")
        if provider_error:
            return redirect(url_for("channels", err="Bạn đã hủy hoặc từ chối cấp quyền Threads"))

        state = request.args.get("state", "")
        code = request.args.get("code", "")
        if not state or not code:
            return redirect(url_for("channels", err="Callback Threads OAuth thiếu code/state"))

        conn = connect()
        try:
            result = complete_oauth_session(
                conn,
                state=state,
                code=code,
                redirect_uri=_redirect_uri(),
                provider=_provider(app),
            )
        except OAuthSessionError:
            return redirect(url_for("channels", err="Phiên Threads OAuth không hợp lệ hoặc đã hết hạn"))
        except (ThreadsOAuthError, RuntimeError):
            return redirect(url_for("channels", err="Không thể hoàn tất Threads OAuth"))
        finally:
            conn.close()

        return redirect(url_for(
            "channels",
            summary=f"Đã kết nối @{result['username']} và kích hoạt kênh Threads",
        ))

    register_threads_onboarding_routes(app, admin_password=admin_password)
    return app
