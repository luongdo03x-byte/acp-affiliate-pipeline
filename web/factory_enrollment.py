"""LAN discovery and zero-config Android enrollment for Account Factory.

This module deliberately stays separate from the large Factory V2 route file.
It installs a small auth bridge so existing V2 endpoints accept a per-device
credential while preserving the legacy operator Factory Key path.
"""
from __future__ import annotations

import ipaddress
import os

from flask import abort, jsonify, request

from core.db import connect
from core.factory_v2.device_credentials import authenticate_device_token, issue_device_token


DEVICE_TOKEN_HEADER = "X-ACP-Device-Token"
LEGACY_FACTORY_KEY_HEADER = "X-ACP-Factory-Key"
_INSTALLED = False
_LEGACY_REQUIRE_FACTORY_KEY = None


def _env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _private_remote(remote_addr: str | None) -> bool:
    try:
        address = ipaddress.ip_address(str(remote_addr or "").split("%", 1)[0])
    except ValueError:
        return False
    return address.is_private or address.is_link_local or address.is_loopback


def install_factory_device_auth() -> None:
    """Extend Factory V2 auth without rewriting every existing route.

    New Android builds may send a device credential in X-ACP-Device-Token. For
    backward-compatible APK wiring they may also store that credential in the
    existing Factory Key slot; in that case we try it as a device credential
    before delegating to the original Factory Key checker.
    """
    global _INSTALLED, _LEGACY_REQUIRE_FACTORY_KEY
    if _INSTALLED:
        return

    from . import factory_v2

    _LEGACY_REQUIRE_FACTORY_KEY = factory_v2._require_factory_key

    def require_factory_auth() -> None:
        explicit_device_token = request.headers.get(DEVICE_TOKEN_HEADER, "").strip()
        candidate = explicit_device_token or request.headers.get(LEGACY_FACTORY_KEY_HEADER, "").strip()
        if candidate:
            conn = connect()
            try:
                credential = authenticate_device_token(conn, candidate)
            finally:
                conn.close()
            if credential is not None:
                return None
            if explicit_device_token:
                abort(401, "Device token không hợp lệ")
        return _LEGACY_REQUIRE_FACTORY_KEY()

    factory_v2._require_factory_key = require_factory_auth
    _INSTALLED = True


def register_factory_enrollment_routes(app) -> None:
    @app.get("/api/factory/discovery")
    def factory_discovery():
        return jsonify(ok=True, service="account-factory", api_version=2)

    @app.post("/api/factory/enroll")
    def factory_enroll():
        if not _env_true("ACP_FACTORY_LAN_AUTO_ENROLL"):
            return jsonify(ok=False, error="LAN auto-enroll chưa được bật"), 403
        if not _private_remote(request.remote_addr):
            return jsonify(ok=False, error="Enrollment chỉ cho phép từ mạng LAN riêng"), 403

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(ok=False, error="Body JSON không hợp lệ"), 400
        unknown = set(data) - {"device_id", "device_name"}
        if unknown:
            return jsonify(ok=False, error=f"Field không được phép: {sorted(unknown)}"), 400

        conn = connect()
        try:
            try:
                token = issue_device_token(
                    conn,
                    data.get("device_id"),
                    data.get("device_name"),
                )
            except ValueError as exc:
                return jsonify(ok=False, error=str(exc)), 400
        finally:
            conn.close()

        return jsonify(
            ok=True,
            service="account-factory",
            api_version=2,
            device_token=token,
        ), 201
