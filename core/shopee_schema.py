"""Schema registrations for Shopee Product Intelligence and image enrichment.

`core.db` remains the central migration engine.  This module contributes
Shopee-specific DDL/migrations at package import time so existing `db.init_db()`
and `db.migrate()` semantics are reused without a second migration runner.
"""
from . import db


_CACHE_DDL = """

CREATE TABLE IF NOT EXISTS shopee_metadata_cache (
    shop_id         TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    product_id      TEXT REFERENCES product(id),
    name            TEXT,
    current_price   INTEGER,
    original_price  INTEGER,
    image_url       TEXT,
    shop_name       TEXT,
    source          TEXT NOT NULL,
    observed_at     TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (shop_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_shopee_metadata_cache_product
    ON shopee_metadata_cache(product_id);
"""

_ENRICHMENT_DDL = """

CREATE TABLE IF NOT EXISTS shopee_image_enrichment_job (
    product_id             TEXT PRIMARY KEY REFERENCES product(id),
    status                 TEXT NOT NULL,
    attempt_count          INTEGER NOT NULL DEFAULT 0,
    download_attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error_code        TEXT,
    last_error             TEXT,
    last_attempt_at        TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shopee_image_enrichment_status
    ON shopee_image_enrichment_job(status, updated_at);
"""

_PRICE_SOURCE_MIGRATION = (
    "product_price_history",
    "source",
    "ALTER TABLE product_price_history ADD COLUMN source TEXT",
)


def register() -> None:
    if "CREATE TABLE IF NOT EXISTS shopee_metadata_cache" not in db.SCHEMA:
        db.SCHEMA += _CACHE_DDL
    if "CREATE TABLE IF NOT EXISTS shopee_image_enrichment_job" not in db.SCHEMA:
        db.SCHEMA += _ENRICHMENT_DDL
    if not any(table == _PRICE_SOURCE_MIGRATION[0] and column == _PRICE_SOURCE_MIGRATION[1]
               for table, column, _sql in db.MIGRATIONS):
        db.MIGRATIONS.append(_PRICE_SOURCE_MIGRATION)


register()
