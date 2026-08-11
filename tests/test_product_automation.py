"""Catalog-schema migration checks.

Run a focused migration check with:
    python3 tests/test_product_automation.py migration
"""
import os
import sqlite3
import sys
import tempfile
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from acp.core import db  # noqa: E402


FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


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
                assert row["provider"] == "LEGACY_11:legacy-feed:11:Legacy Shop"
                assert row["first_seen_at"] == created_at
                assert row["name"] == "Legacy product"
                assert migrated.execute("SELECT caption_final FROM post WHERE id = 'post-1'").fetchone()[0] == "Legacy caption"
                assert migrated.execute("SELECT commission FROM conversion WHERE id = 'conversion-1'").fetchone()[0] == 10000
            finally:
                migrated.close()
        finally:
            db.DB_PATH = previous_db_path


def test_migration_distinguishes_duplicate_legacy_external_ids_by_merchant():
    created_at = "2026-08-11T10:00:00+00:00"
    with tempfile.TemporaryDirectory() as directory:
        previous_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(directory, "duplicate-legacy.db")
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
                        current_price INTEGER NOT NULL,
                        commission_value INTEGER NOT NULL,
                        category_code TEXT NOT NULL,
                        product_url TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE (source, merchant, external_product_id)
                    );
                """)
                conn.executemany(
                    """INSERT INTO product (
                        id, source, merchant, external_product_id, name, current_price,
                        commission_value, category_code, product_url, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    [
                        ("product-1", "legacy-feed", "First Shop", "shared-id", "First product",
                         100000, 10000, "home", "https://example.test/first", created_at, created_at),
                        ("product-2", "legacy-feed", "Second Shop", "shared-id", "Second product",
                         200000, 20000, "home", "https://example.test/second", created_at, created_at),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            db.init_db()
            db.init_db()
            migrated = db.connect()
            try:
                rows = migrated.execute(
                    "SELECT id, provider, name FROM product WHERE external_product_id = 'shared-id' ORDER BY id"
                ).fetchall()
                assert [(row["id"], row["provider"], row["name"]) for row in rows] == [
                    ("product-1", "LEGACY_11:legacy-feed:10:First Shop", "First product"),
                    ("product-2", "LEGACY_11:legacy-feed:11:Second Shop", "Second product"),
                ]
                indexes = {row[1] for row in migrated.execute("PRAGMA index_list(product)")}
                assert "idx_product_provider_external" in indexes
            finally:
                migrated.close()
        finally:
            db.DB_PATH = previous_db_path


class _Response:
    def __init__(self, status_code=200, payload=None, json_error=None):
        self.status_code = status_code
        self.payload = payload
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.headers = {}
        self.requests = []

    def get(self, url, **kwargs):
        return self._request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._request("POST", url, **kwargs)

    def _request(self, method, url, **kwargs):
        self.calls += 1
        self.requests.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


def _assert_error(message, fn, error_type=Exception):
    try:
        fn()
    except error_type as error:
        assert str(error) == message
        return
    raise AssertionError(f"Expected {error_type.__name__}: {message}")


def test_parser_keeps_missing_units_sold_as_none():
    """Replacing absent sales with zero would distort product ranking."""
    from acp.adapters.accesstrade_client import normalize_accesstrade_product

    product = normalize_accesstrade_product(
        {"id": "p1", "title": "A", "sales_price": {"minimum_amount": 1}})

    assert product.units_sold is None


def test_parser_preserves_missing_optional_catalog_values_as_none():
    """Absent shop, image, price, and commission must not become invented values."""
    from acp.adapters.accesstrade_client import normalize_accesstrade_product

    product = normalize_accesstrade_product({"id": "p1", "title": "A"})

    assert product.shop_name is None
    assert product.main_image_url is None
    assert product.price_min is None
    assert product.commission_rate_percent is None
    assert product.commission_amount is None


def test_commission_raw_is_scaled_correctly():
    """A basis-point commission rate must not be stored as a 100x percentage."""
    from acp.adapters.accesstrade_client import normalize_commission_rate_percent

    assert normalize_commission_rate_percent(1000) == 10.0
    assert normalize_commission_rate_percent(3587) == 35.87


def test_client_retries_429_then_returns_page():
    """A transient rate limit must retry before a successful catalog page is lost."""
    from acp.adapters.accesstrade_client import AccessTradeClient

    fake_session = _FakeSession([
        _Response(429, {"status": False}),
        _Response(200, _fixture("accesstrade_product_search_v2.json")),
    ])
    delays = []

    products, token = AccessTradeClient(
        session=fake_session, sleep=delays.append).search_products(limit=50)

    assert len(products) == 2
    assert token == "NEXT"
    assert fake_session.calls == 2
    assert delays == [1]
    assert fake_session.headers == {"Authorization": "Token "}
    assert fake_session.requests[1][2]["params"] == {"sort_field": "RECOMMENDED", "limit": 50}


def test_client_retries_timeout_and_server_error_twice():
    """Only retryable transport failures receive the bounded 1s/2s retry budget."""
    import requests
    from acp.adapters.accesstrade_client import AccessTradeClient

    fake_session = _FakeSession([
        requests.exceptions.Timeout("network timeout"),
        _Response(500, {"status": False}),
        _Response(200, {"status": True, "data": {"products": []}}),
    ])
    delays = []

    products, token = AccessTradeClient(
        session=fake_session, sleep=delays.append).search_products()

    assert products == []
    assert token is None
    assert fake_session.calls == 3
    assert delays == [1, 2]


def test_client_status_false_and_401_are_domain_errors():
    """Provider failures must be safe operator messages rather than response details."""
    from acp.adapters.accesstrade_client import AccessTradeClient
    from acp.adapters.base import PublishError

    _assert_error(
        "Token ACCESSTRADE không hợp lệ",
        lambda: AccessTradeClient(session=_FakeSession([_Response(401, {})])).search_products(),
        PublishError,
    )
    _assert_error(
        "ACCESSTRADE từ chối yêu cầu",
        lambda: AccessTradeClient(session=_FakeSession([
            _Response(200, {"status": False, "message": "secret response"})
        ])).search_products(),
        PublishError,
    )


def test_client_maps_final_rate_limit_network_and_bad_json_to_safe_errors():
    """No token, payload, or parser traceback may escape the HTTP boundary."""
    import requests
    from acp.adapters.accesstrade_client import AccessTradeClient
    from acp.adapters.base import PublishError, RateLimitError

    _assert_error(
        "ACCESSTRADE đang giới hạn yêu cầu; hãy thử lại sau",
        lambda: AccessTradeClient(
            session=_FakeSession([_Response(429, {})] * 3), sleep=lambda _: None).search_products(),
        RateLimitError,
    )
    _assert_error(
        "Không thể kết nối ACCESSTRADE; hãy thử lại sau",
        lambda: AccessTradeClient(
            session=_FakeSession([requests.exceptions.ConnectionError("token=secret")] * 3),
            sleep=lambda _: None).search_products(),
        PublishError,
    )
    _assert_error(
        "ACCESSTRADE trả dữ liệu không hợp lệ",
        lambda: AccessTradeClient(session=_FakeSession([
            _Response(200, json_error=ValueError("broken JSON"))
        ])).search_products(),
        PublishError,
    )


def test_create_product_link_uses_real_post_id_and_keeps_full_short_urls_separate():
    """Content attribution breaks if a link body uses a product-only sub1 or loses full URL."""
    from acp.adapters.accesstrade_client import AccessTradeClient

    fake_session = _FakeSession([_Response(200, {
        "status": True,
        "data": {"success_link": [{
            "aff_link": "https://tracking.example/full",
            "short_link": "https://short.example/post",
        }]},
    })])

    link = AccessTradeClient(session=fake_session).create_product_link(
        "https://vt.tiktok.com/product", post_id="POST-123", external_product_id="product-42")

    assert link.full_url == "https://tracking.example/full"
    assert link.short_url == "https://short.example/post"
    body = fake_session.requests[0][2]["json"]
    assert body["utm_source"] == "threads"
    assert body["utm_medium"] == "social"
    assert body["utm_campaign"] == "acp"
    assert body["utm_content"] == "product-42"
    assert body["sub1"] == "POST-123"


def test_create_product_only_link_uses_explicit_product_sub1():
    """The standalone operator link is deliberately marked so content can never reuse it."""
    from acp.adapters.accesstrade_client import AccessTradeClient

    fake_session = _FakeSession([_Response(200, {
        "status": True,
        "data": {"success_link": [{"aff_link": "https://tracking.example/full"}]},
    })])

    AccessTradeClient(session=fake_session).create_product_link(
        "https://vt.tiktok.com/product", post_id="product:product-42", external_product_id="product-42")

    assert fake_session.requests[0][2]["json"]["sub1"] == "product:product-42"


def test_legacy_tiktok_source_keeps_its_existing_fractional_rate_contract():
    """Moving HTTP to the client must not reinterpret the CLI adapter's legacy feed values."""
    from acp.adapters.tiktokshop import AccessTradeTikTokShopSource

    product = AccessTradeTikTokShopSource.normalize({
        "id": "p1", "title": "A", "sales_price": {"minimum_amount": "100000"},
        "commission": {"amount": "0", "rate": "0.06"},
    })

    assert product.commission_rate == 0.06
    assert product.commission_value == 6000


def test_factory_context_exposes_product_client():
    """Jobs need the same product client regardless of whether they start from CLI or web."""
    from acp.adapters import factory

    factory.reset_cache()
    context = factory.build_context("mock")

    assert "product_client" in context
    assert context["product_client"].__class__.__name__ == "AccessTradeClient"


def main():
    groups = {"migration": [test_product_catalog_migration_is_idempotent,
                            test_migration_preserves_existing_product_and_backfills_provider,
                            test_migration_distinguishes_duplicate_legacy_external_ids_by_merchant],
              "client": [test_parser_keeps_missing_units_sold_as_none,
                         test_parser_preserves_missing_optional_catalog_values_as_none,
                         test_commission_raw_is_scaled_correctly,
                         test_client_retries_429_then_returns_page,
                         test_client_retries_timeout_and_server_error_twice,
                         test_client_status_false_and_401_are_domain_errors,
                         test_client_maps_final_rate_limit_network_and_bad_json_to_safe_errors,
                         test_create_product_link_uses_real_post_id_and_keeps_full_short_urls_separate,
                         test_create_product_only_link_uses_explicit_product_sub1,
                         test_legacy_tiktok_source_keeps_its_existing_fractional_rate_contract,
                         test_factory_context_exposes_product_client]}
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
