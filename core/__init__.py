"""ACP core package feature registrations."""

# Additive schema registrations extend the central db.SCHEMA registry without
# introducing a second migration runner. Import for side effect before callers
# use db.init_db().
from . import shopee_schema as _shopee_schema  # noqa: F401,E402
from . import control_center_schema as _control_center_schema  # noqa: F401,E402

# The application imports this package as ``acp.core``. A number of legacy
# tests/tools still import ``core`` directly from the repository root. Installing
# runtime adapters from that top-level alias would pull modules that rely on the
# parent ``acp`` package and break those legacy imports. Keep registrations
# scoped to the canonical application namespace.
if __name__ == "acp.core":
    from . import shopee_import_runtime as _shopee_import_runtime  # noqa: E402
    from . import shopee_enrichment_jobs as _shopee_enrichment_jobs  # noqa: F401,E402
    from . import shopee_bulk_enrichment as _shopee_bulk_enrichment  # noqa: F401,E402
    from . import shopee_auto_runtime as _shopee_auto_runtime  # noqa: E402
    from . import topic_jobs as _topic_jobs  # noqa: F401,E402
    from . import topic_runtime as _topic_runtime  # noqa: E402
    from . import reviewer_caption_runtime as _reviewer_caption_runtime  # noqa: E402
    from . import auto_post_runtime as _auto_post_runtime  # noqa: E402

    _shopee_import_runtime.install()
    _shopee_auto_runtime.install()
    _topic_runtime.install()
    _reviewer_caption_runtime.install()
    _auto_post_runtime.install()
