"""Flask routes for Android Account Factory pairing and Threads OAuth."""
from __future__ import annotations

import hmac
import logging
import os

from flask import abort, jsonify, request

from ..core.account_factory import (
    AccountMismatchError,
    OAuthSessionError,
    ThreadsOAuthClient,
    ThreadsOAuthError,
    complete_oauth_session,
    create_oauth_session,
    get_session,
    get_session_by_state,
    public_session,
)
from ..core.db import connect
from ..core.factory_v2.oauth_bridge import sync_account_from_oauth_session


FACTORY_KEY_HEADER = "X-ACP-Factory-Key"
_LOG = logging.getLogger(__name__)


def _factory_key() -> str:
    return os.environ.get("ACP_FACTORY_API_KEY", "").strip()


def _require_factory_key() -> None:
    expected = _factory_key()
    if not expected:
        abort(503, "ACP_FACTORY_API_KEY chưa được cấu hình")
    received = request.headers.get(FACTORY_KEY_HEADER, "")
    if not received or not hmac.compare_digest(received.encode(), expected.encode()):
        abort(401, "Factory key không hợp lệ")


def _base_url() -> str:
    configured = os.environ.get("ACP_PUBLIC_BASE_URL", "").strip().rstrip("/")
    return configured or request.host_url.rstrip("/")


def _redirect_uri() -> str:
    return _base_url() + "/oauth/account-factory/threads/callback"


def _provider(app):
    factory = app.config.get("ACCOUNT_FACTORY_OAUTH_FACTORY")
    return factory() if factory else ThreadsOAuthClient()


def _sync_v2_safely(conn, session_id: str | None) -> None:
    if not session_id:
        return
    try:
        sync_account_from_oauth_session(conn, session_id)
    except Exception as exc:  # OAuth/channel result must remain durable; status poll can retry V2 sync.
        _LOG.warning("Factory V2 OAuth reconciliation deferred (%s)", type(exc).__name__)


def register_account_factory_routes(app):
    @app.post("/oauth/account-factory/start")
    def account_factory_start():
        _require_factory_key()
        data = request.get_json(silent=True) or {}
        expected_username = str(data.get("expected_username") or "").strip()
        if not expected_username:
            return jsonify(ok=False, error="Thiếu expected_username"), 400
        conn = connect()
        try:
            created = create_oauth_session(
                conn,
                expected_username=expected_username,
                batch_id=data.get("batch_id"),
                account_local_id=data.get("account_local_id"),
            )
            provider = _provider(app)
            authorization_url = provider.authorization_url(created["state"], _redirect_uri())
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        except RuntimeError:
            return jsonify(ok=False, error="Threads OAuth chưa được cấu hình trên ACP"), 503
        finally:
            conn.close()
        return jsonify(
            ok=True,
            session_id=created["id"],
            status=created["status"],
            authorization_url=authorization_url,
            expires_at=created["expires_at"],
        ), 201

    @app.get("/oauth/account-factory/session/<session_id>")
    def account_factory_status(session_id):
        _require_factory_key()
        conn = connect()
        try:
            row = get_session(conn, session_id)
        finally:
            conn.close()
        if not row:
            return jsonify(ok=False, error="OAuth session không tồn tại"), 404
        return jsonify(ok=True, **public_session(row))

    @app.get("/oauth/account-factory/threads/callback")
    def account_factory_threads_callback():
        state = request.args.get("state", "")
        code = request.args.get("code", "")
        provider_error = request.args.get("error") or request.args.get("error_description")
        if provider_error:
            return (
                "<h2>Threads authorization đã bị hủy hoặc từ chối.</h2>"
                "<p>Quay lại ACP Account Factory và thử lại.</p>",
                400,
                {"Content-Type": "text/html; charset=utf-8"},
            )
        if not state or not code:
            abort(400, "Thiếu code/state OAuth")
        conn = connect()
        session = get_session_by_state(conn, state)
        session_id = session["id"] if session else None
        try:
            result = complete_oauth_session(
                conn,
                state=state,
                code=code,
                redirect_uri=_redirect_uri(),
                provider=_provider(app),
            )
        except AccountMismatchError:
            _sync_v2_safely(conn, session_id)
            return (
                "<h2>Sai tài khoản Threads.</h2>"
                "<p>Không có token nào được gắn vào kênh. Quay lại app và thử lại đúng account.</p>",
                409,
                {"Content-Type": "text/html; charset=utf-8"},
            )
        except OAuthSessionError:
            _sync_v2_safely(conn, session_id)
            return (
                "<h2>Phiên OAuth không hợp lệ hoặc đã hết hạn.</h2>",
                400,
                {"Content-Type": "text/html; charset=utf-8"},
            )
        except (ThreadsOAuthError, RuntimeError):
            _sync_v2_safely(conn, session_id)
            return (
                "<h2>Không thể hoàn tất Threads OAuth.</h2>"
                "<p>Quay lại app và thử lại. Token không được hiển thị.</p>",
                502,
                {"Content-Type": "text/html; charset=utf-8"},
            )
        else:
            _sync_v2_safely(conn, session_id)
        finally:
            conn.close()
        username = result["username"]
        return (
            f"<h2>Đã kết nối @{username} với ACP.</h2>"
            "<p>Bạn có thể đóng trang này và quay lại Account Factory.</p>",
            200,
            {"Content-Type": "text/html; charset=utf-8"},
        )

    return app