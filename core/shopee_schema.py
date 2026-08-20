"""Small schema registration for Shopee Product Intelligence.

`core.db` remains the central migration engine.  This module contributes Phase 3
DDL/migrations at package import time so existing `db.init_db()` and
`db.migrate()` semantics are reused without a second migration runner.
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

_PRICE_SOURCE_MIGRATION = (
    "product_price_history",
    "source",
    "ALTER TABLE product_price_history ADD COLUMN source TEXT",
)


def register() -> None:
    if "CREATE TABLE IF NOT EXISTS shopee_metadata_cache" not in db.SCHEMA:
        db.SCHEMA += _CACHE_DDL
    if not any(table == _PRICE_SOURCE_MIGRATION[0] and column == _PRICE_SOURCE_MIGRATION[1]
               for table, column, _sql in db.MIGRATIONS):
        db.MIGRATIONS.append(_PRICE_SOURCE_MIGRATION)


register()
