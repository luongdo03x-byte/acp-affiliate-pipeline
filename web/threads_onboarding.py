"""Browser wizard for unpublished-app Threads tester onboarding.

The wizard never automates or bypasses Meta consent. It minimizes operator work by
tracking the two human tester milestones and starting the existing account-bound
OAuth flow immediately after tester acceptance is confirmed.
"""
from __future__ import annotations

import os

from flask import redirect, render_template, request, session, url_for

if __package__ and "." in __package__:
    from ..core.account_factory import (
        AccountMismatchError,
        OAuthSessionError,
        ThreadsOAuthClient,
        ThreadsOAuthError,
        complete_oauth_session,
        get_session_by_state,
    )
    from ..core.db import connect, now
    from ..core.factory_v2.oauth_bridge import sync_account_from_oauth_session
    from ..core.factory_v2.repository import FactoryRepository
    from ..core.factory_v2.schema import ensure_schema as ensure_factory_schema
    from ..core.factory_v2.service import FactoryService
    from ..core.factory_v2.threads_onboarding import (
        accept_and_start_oauth,
        list_onboarding_accounts,
        mark_tester_invited,
    )
else:
    from core.account_factory import (
        AccountMismatchError,
        OAuthSessionError,
        ThreadsOAuthClient,
        ThreadsOAuthError,
        complete_oauth_session,
        get_session_by_state,
    )
    from core.db import connect, now
    from core.factory_v2.oauth_bridge import sync_account_from_oauth_session
    from core.factory_v2.repository import FactoryRepository
    from core.factory_v2.schema import ensure_schema as ensure_factory_schema
    from core.factory_v2.service import FactoryService
    from core.factory_v2.threads_onboarding import (
        accept_and_start_oauth,
        list_onboarding_accounts,
        mark_tester_invited,
    )


def _redirect_uri() -> str:
    public_base = os.environ.get("ACP_PUBLIC_BASE_URL", "").strip().rstrip("/")
    base = public_base or request.host_url.rstrip("/")
    return base + "/oauth/threads/onboarding/callback"


def _provider(app):
    provider_factory = (
        app.config.get("THREADS_ONBOARDING_OAUTH_FACTORY")
        or app.config.get("ACCOUNT_FACTORY_OAUTH_FACTORY")
    )
    return provider_factory() if provider_factory else ThreadsOAuthClient()


def _meta_testers_url() -> str:
    return (
        os.environ.get("META_APP_TESTERS_URL", "").strip()
        or "https://developers.facebook.com/apps/"
    )


def _login_redirect(admin_password: str):
    if admin_password and not session.get("uid"):
        return redirect(url_for("login", next=request.path))
    return None


def _sync_safely(conn, session_id: str | None) -> None:
    if not session_id:
        return
    try:
        sync_account_from_oauth_session(conn, session_id)
    except (KeyError, ValueError):
        return


def _mark_denied(conn, state: str) -> str | None:
    oauth_session = get_session_by_state(conn, state)
    if oauth_session is None:
        return None
    if oauth_session["status"] == "WAITING_AUTH":
        conn.execute(
            """UPDATE account_factory_oauth_session
               SET status='OAUTH_ERROR', last_error=?, completed_at=?
               WHERE id=? AND status='WAITING_AUTH'""",
            ("Threads authorization was denied or cancelled", now(), oauth_session["id"]),
        )
    _sync_safely(conn, oauth_session["id"])
    return oauth_session["id"]


def register_threads_onboarding_routes(app, *, admin_password: str):
    @app.get("/kenh/threads/onboarding")
    def threads_onboarding():
        auth_redirect = _login_redirect(admin_password)
        if auth_redirect is not None:
            return auth_redirect

        conn = connect()
        try:
            ensure_factory_schema(conn)
            accounts = list_onboarding_accounts(conn)
            for account in accounts:
                if account["onboarding_status"] == "OAUTH_IN_PROGRESS":
                    _sync_safely(conn, account.get("oauth_session_id"))
            accounts = list_onboarding_accounts(conn)
        finally:
            conn.close()

        actionable = {
            "NEEDS_TESTER_INVITE",
            "NEEDS_TESTER_ACCEPT",
            "READY_FOR_OAUTH",
        }
        next_account = next(
            (account for account in accounts if account["onboarding_status"] in actionable),
            None,
        )
        counts = {}
        for account in accounts:
            status = account["onboarding_status"]
            counts[status] = counts.get(status, 0) + 1

        return render_template(
            "threads_onboarding.html",
            page="kenh",
            accounts=accounts,
            next_account=next_account,
            counts=counts,
            meta_testers_url=_meta_testers_url(),
            summary=request.args.get("summary"),
            err=request.args.get("err"),
        )

    @app.post("/kenh/threads/onboarding/<account_id>/tester-invited")
    def threads_onboarding_tester_invited(account_id):
        auth_redirect = _login_redirect(admin_password)
        if auth_redirect is not None:
            return auth_redirect

        conn = connect()
        try:
            ensure_factory_schema(conn)
            account = mark_tester_invited(conn, account_id)
        except KeyError:
            return redirect(url_for("threads_onboarding", err="Account không tồn tại"))
        except ValueError:
            return redirect(url_for("threads_onboarding", err="Account chưa sẵn sàng cho Threads OAuth"))
        finally:
            conn.close()
        return redirect(url_for(
            "threads_onboarding",
            summary=f"Đã ghi nhận invite cho @{account['username']}",
        ))

    @app.post("/kenh/threads/onboarding/<account_id>/continue")
    def threads_onboarding_continue(account_id):
        auth_redirect = _login_redirect(admin_password)
        if auth_redirect is not None:
            return auth_redirect

        try:
            provider = _provider(app)
        except RuntimeError:
            return redirect(url_for(
                "threads_onboarding",
                err="Threads OAuth chưa được cấu hình: thiếu THREADS_APP_ID/THREADS_APP_SECRET",
            ))

        conn = connect()
        try:
            ensure_factory_schema(conn)
            oauth = accept_and_start_oauth(
                conn,
                account_id,
                _redirect_uri(),
                provider,
            )
        except KeyError:
            return redirect(url_for("threads_onboarding", err="Account không tồn tại"))
        except ValueError:
            return redirect(url_for(
                "threads_onboarding",
                err="Account chưa sẵn sàng hoặc OAuth hiện tại chưa thể thử lại",
            ))
        finally:
            conn.close()
        return redirect(oauth["authorization_url"])

    @app.get("/oauth/threads/onboarding/callback")
    def threads_onboarding_callback():
        state = request.args.get("state", "")
        provider_error = request.args.get("error_description") or request.args.get("error")
        if provider_error:
            conn = connect()
            try:
                ensure_factory_schema(conn)
                found = _mark_denied(conn, state) if state else None
            finally:
                conn.close()
            message = (
                "Bạn đã hủy hoặc từ chối cấp quyền Threads; có thể thử OAuth lại ngay"
                if found
                else "Threads OAuth bị hủy nhưng state không hợp lệ"
            )
            return redirect(url_for("threads_onboarding", err=message))

        code = request.args.get("code", "")
        if not state or not code:
            return redirect(url_for(
                "threads_onboarding",
                err="Callback Threads OAuth thiếu code/state",
            ))

        conn = connect()
        oauth_session = get_session_by_state(conn, state)
        session_id = oauth_session["id"] if oauth_session else None
        try:
            result = complete_oauth_session(
                conn,
                state=state,
                code=code,
                redirect_uri=_redirect_uri(),
                provider=_provider(app),
            )
        except AccountMismatchError:
            _sync_safely(conn, session_id)
            account_id = oauth_session.get("account_local_id") if oauth_session else None
            if account_id:
                try:
                    FactoryService(FactoryRepository(conn)).retry_account(account_id)
                except (KeyError, ValueError):
                    pass
            return redirect(url_for(
                "threads_onboarding",
                err="Sai tài khoản Threads; hãy đăng nhập đúng account rồi thử OAuth lại",
            ))
        except OAuthSessionError:
            _sync_safely(conn, session_id)
            return redirect(url_for(
                "threads_onboarding",
                err="Phiên Threads OAuth không hợp lệ hoặc đã hết hạn",
            ))
        except (ThreadsOAuthError, RuntimeError):
            _sync_safely(conn, session_id)
            return redirect(url_for(
                "threads_onboarding",
                err="Không thể hoàn tất Threads OAuth; có thể thử lại từ wizard",
            ))
        else:
            _sync_safely(conn, session_id)
        finally:
            conn.close()

        return redirect(url_for(
            "threads_onboarding",
            summary=f"Đã kết nối @{result['username']} và kích hoạt kênh Threads",
        ))

    return app
