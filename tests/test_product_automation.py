"""Catalog-schema migration checks.

Run a focused migration check with:
    python3 tests/test_product_automation.py migration
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from acp.core import db  # noqa: E402


CATALOG_COLUMNS = {
    "provider", "shop_name", "detail_link", "main_image_url", "sale_region", "currency",
    "price_min", "price_max", "original_price_min", "original_price_max",
    "commission_rate_raw", "commission_rate_percent", "commission_amount",
    "commission_currency", "units_sold", "has_inventory", "category_data", "score",
    "affiliate_url", "affiliate_short_url", "affiliate_link_status", "affiliate_link_error",
    "first_seen_at", "last_seen_at", "last_synced_at", "affiliate_link_created_at",
    "last_posted_at", "post_count",
}


def test_product_catalog_migration_is_idempotent():
    with tempfile.TemporaryDirectory() as directory:
        previous_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(directory, "catalog.db")
        try:
            db.init_db()
            db.init_db()
            conn = db.connect()
            try:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(product)")}
                assert CATALOG_COLUMNS <= columns
                indexes = {row[1] for row in conn.execute("PRAGMA index_list(product)")}
                assert "idx_product_provider_external" in indexes
                lock_columns = {row[1] for row in conn.execute("PRAGMA table_info(product_sync_lock)")}
                assert lock_columns == {"name", "locked_at"}
            finally:
                conn.close()
        finally:
            db.DB_PATH = previous_db_path


def test_migration_preserves_existing_product_and_backfills_provider():
    created_at = "2026-08-11T10:00:00+00:00"
    with tempfile.TemporaryDirectory() as directory:
        previous_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(directory, "legacy.db")
        try:
            conn = sqlite3.connect(db.DB_PATH)
            try:
                conn.executescript("""
                    CREATE TABLE product (
                        id TEXT PRIMARY KEY,
                        source TEXT NOT NULL,
                        merchant TEXT NOT NULL,
                        external_product_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        description TEXT,
                        current_price INTEGER NOT NULL,
                        original_price INTEGER,
                        commission_value INTEGER NOT NULL,
                        commission_rate REAL,
                        category_code TEXT NOT NULL,
                        rating REAL,
                        review_count INTEGER DEFAULT 0,
                        sold_count INTEGER DEFAULT 0,
                        image_url_original TEXT,
                        image_path_local TEXT,
                        product_url TEXT NOT NULL,
                        is_available INTEGER NOT NULL DEFAULT 1,
                        last_seen_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE (source, merchant, external_product_id)
                    );
                    CREATE TABLE post (
                        id TEXT PRIMARY KEY,
                        product_id TEXT,
                        caption_final TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'DRAFT',
                        scheduled_at TEXT,
                        published_at TEXT
                    );
                    CREATE TABLE conversion (
                        id TEXT PRIMARY KEY,
                        post_id TEXT,
                        transaction_id TEXT NOT NULL,
                        external_product_id TEXT NOT NULL,
                        sale_amount INTEGER NOT NULL,
                        commission INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        converted_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                """)
                conn.execute(
                    """INSERT INTO product (
                        id, source, merchant, external_product_id, name, current_price,
                        commission_value, category_code, product_url, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    ("product-1", "legacy-feed", "Legacy Shop", "external-1", "Legacy product",
                     100000, 10000, "home", "https://example.test/product", created_at, created_at),
                )
                conn.execute(
                    "INSERT INTO post VALUES ('post-1', 'product-1', 'Legacy caption', 'PUBLISHED', NULL, ?)",
                    (created_at,),
                )
                conn.execute(
                    "INSERT INTO conversion VALUES (?,?,?,?,?,?,?,?,?)",
                    ("conversion-1", "post-1", "transaction-1", "external-1", 100000, 10000,
                     "approved", created_at, created_at),
                )
                conn.commit()
            finally:
                conn.close()

            db.init_db()
            migrated = db.connect()
            try:
                row = migrated.execute(
                    "SELECT provider, first_seen_at, name FROM product WHERE id = 'product-1'"
                ).fetchone()
                assert row["provider"] == "LEGACY_legacy-feed"
                assert row["first_seen_at"] == created_at
                assert row["name"] == "Legacy product"
                assert migrated.execute("SELECT caption_final FROM post WHERE id = 'post-1'").fetchone()[0] == "Legacy caption"
                assert migrated.execute("SELECT commission FROM conversion WHERE id = 'conversion-1'").fetchone()[0] == 10000
            finally:
                migrated.close()
        finally:
            db.DB_PATH = previous_db_path


def main():
    groups = {"migration": [test_product_catalog_migration_is_idempotent,
                            test_migration_preserves_existing_product_and_backfills_provider]}
    selected = sys.argv[1] if len(sys.argv) > 1 else "migration"
    tests = groups.get(selected)
    if tests is None:
        raise SystemExit(f"Unknown test group: {selected}")
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
