"""ACP core package feature registrations."""

# Phase 3 extends the central db.SCHEMA/MIGRATIONS registry without introducing
# a second migration runner. Import for side effect before callers use db.init_db().
from . import shopee_schema as _shopee_schema  # noqa: F401,E402

# Extend the existing scheduler/pipeline for official Shopee Affiliate CSV rows.
# This is intentionally an install-on-import adapter: there is still one router,
# one scheduler and one publish worker.
from . import shopee_auto_runtime as _shopee_auto_runtime  # noqa: E402

_shopee_auto_runtime.install()
