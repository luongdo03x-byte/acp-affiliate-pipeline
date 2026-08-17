"""ACP web package composition.

`web.server` is still the legacy app factory.  New isolated workspaces can be
registered here so callers that import `acp.web.server.create_app` keep working
without growing the server monolith further.
"""
from . import server as _server
from .shopee_bulk import register_shopee_bulk_routes

_base_create_app = _server.create_app


def create_app():
    app = _base_create_app()
    register_shopee_bulk_routes(app)
    return app


# Existing runtime/tests import from acp.web.server directly.  Preserve that
# public import path while composing the extra blueprint at package load time.
_server.create_app = create_app

__all__ = ["create_app"]
