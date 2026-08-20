"""ACP web package composition.

`web.server` is still the legacy app factory. New isolated workspaces are
registered here so callers that import `acp.web.server.create_app` keep working
without growing the server monolith further.
"""
from . import server as _server
from .shopee_bulk import register_shopee_bulk_routes
from .shopee_helper import register_shopee_helper_hardening
from .shopee_product_intel import register_shopee_product_intel
from .shopee_polish import register_shopee_polish

_base_create_app = _server.create_app


def create_app():
    app = _base_create_app()
    register_shopee_helper_hardening(app)
    register_shopee_product_intel(app)
    register_shopee_polish(app)
    register_shopee_bulk_routes(app)
    return app


# Existing runtime/tests import from acp.web.server directly. Preserve that
# public import path while composing isolated feature modules at package load.
_server.create_app = create_app

__all__ = ["create_app"]
