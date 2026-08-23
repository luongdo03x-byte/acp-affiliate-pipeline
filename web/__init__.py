"""ACP web package composition.

`web.server` is still the legacy app factory. New isolated workspaces are
registered here so callers that import `acp.web.server.create_app` keep working
without growing the server monolith further.
"""
from . import server as _server
from . import shopee_image_enrichment as _shopee_image_enrichment
from ..core import topic_product_pool as _topic_product_pool
from .auto_posting import register_auto_posting_routes
from .enrich_all_ui import register_enrich_all_ui
from .topic_admin import register_topic_admin
from .topic_ui import register_topic_ui
from .shopee_bulk import register_shopee_bulk_routes
from .shopee_csv_import import register_shopee_csv_import_routes
from .shopee_helper import register_shopee_helper_hardening
from .shopee_image_enrichment import register_shopee_image_enrichment_routes
from .shopee_product_intel import register_shopee_product_intel
from .shopee_polish import register_shopee_polish

# Product Pool v2's route keeps its public endpoint but reads through the new
# topic-aware projection. The function global is resolved at request time.
_shopee_image_enrichment.build_product_pool = _topic_product_pool.build_product_pool

# Import account extensions before server.create_app() registers the shared
# Seeding blueprint; the extension routes reuse its blueprint instance.
from . import seeding_account_routes as _seeding_account_routes  # noqa: F401,E402

_base_create_app = _server.create_app


def create_app():
    app = _base_create_app()
    register_topic_ui(app)
    register_topic_admin(app)
    register_shopee_helper_hardening(app)
    register_shopee_product_intel(app)
    register_shopee_polish(app)
    register_shopee_bulk_routes(app)
    register_shopee_csv_import_routes(app)
    register_shopee_image_enrichment_routes(app)
    register_enrich_all_ui(app)
    register_auto_posting_routes(app)
    return app


# Existing runtime/tests import from acp.web.server directly. Preserve that
# public import path while composing isolated feature modules at package load.
_server.create_app = create_app

__all__ = ["create_app"]
