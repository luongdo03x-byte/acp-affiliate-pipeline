"""ACP core package feature registrations."""

# Phase 3 extends the central db.SCHEMA/MIGRATIONS registry without introducing
# a second migration runner. Import for side effect before callers use db.init_db().
from . import shopee_schema as _shopee_schema  # noqa: F401,E402
