"""ACP core package feature registrations."""

# Phase 3 extends the central db.SCHEMA/MIGRATIONS registry without introducing
# a second migration runner. Import for side effect before callers use db.init_db().
from . import shopee_schema as _shopee_schema  # noqa: F401,E402

# The application imports this package as ``acp.core``. A number of legacy
# tests/tools still import ``core`` directly from the repository root. Installing
# runtime adapters from that top-level alias would pull modules that rely on the
# parent ``acp`` package and break those legacy imports. Keep registrations
# scoped to the canonical application namespace.
if __name__ == "acp.core":
    from . import shopee_enrichment_jobs as _shopee_enrichment_jobs  # noqa: F401,E402
    from . import shopee_auto_runtime as _shopee_auto_runtime  # noqa: E402

    _shopee_auto_runtime.install()
