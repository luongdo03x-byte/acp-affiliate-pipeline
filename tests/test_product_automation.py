"""Catalog-schema migration checks.

Run a focused migration check with:
    python3 tests/test_product_automation.py migration
"""
import os
import sqlite3
import sys
import tempfile
import json
from datetime import datetime, timedelta, timezone

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


def test_client_maps_final_rate_limit_and_bad_json_to_safe_errors():
    """No token, payload, or parser traceback may escape the HTTP boundary."""
    from acp.adapters.accesstrade_client import AccessTradeClient
    from acp.adapters.base import PublishError, RateLimitError

    _assert_error(
        "ACCESSTRADE đang giới hạn yêu cầu; hãy thử lại sau",
        lambda: AccessTradeClient(
            session=_FakeSession([_Response(429, {})] * 3), sleep=lambda _: None).search_products(),
        RateLimitError,
    )
    _assert_error(
        "ACCESSTRADE trả dữ liệu không hợp lệ",
        lambda: AccessTradeClient(session=_FakeSession([
            _Response(200, json_error=ValueError("broken JSON"))
        ])).search_products(),
        PublishError,
    )


def test_client_maps_non_timeout_network_error_without_retry():
    """Retrying a connection or TLS failure violates the bounded retry policy."""
    import requests
    from acp.adapters.accesstrade_client import AccessTradeClient
    from acp.adapters.base import PublishError

    fake_session = _FakeSession([requests.exceptions.ConnectionError("token=secret")])
    delays = []

    _assert_error(
        "Không thể kết nối ACCESSTRADE; hãy thử lại sau",
        lambda: AccessTradeClient(session=fake_session, sleep=delays.append).search_products(),
        PublishError,
    )

    assert fake_session.calls == 1
    assert delays == []


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


class _ProductClient:
    """Small in-memory Product Search V2 boundary for ProductService tests."""

    def __init__(self, pages=None, error=None):
        self.pages = list(pages or [])
        self.error = error
        self.calls = []

    def search_products(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.pages.pop(0) if self.pages else ([], None)


class _CatalogPipelineClient(_ProductClient):
    """External link boundary used by catalog-post pipeline tests."""

    def __init__(self, *, error=None):
        super().__init__()
        self.error = error
        self.link_calls = []
        self.last_body = None

    def create_product_link(self, detail_link, *, post_id, external_product_id):
        from acp.adapters.accesstrade_client import LinkResult

        self.last_body = {"detail_link": detail_link, "sub1": post_id,
                          "external_product_id": external_product_id}
        self.link_calls.append(self.last_body)
        if self.error:
            raise self.error
        return LinkResult(full_url="https://tracking.example/post",
                          short_url="https://short.example/post")


class _CatalogStorage:
    def put(self, local_path):
        return "https://media.example/" + os.path.basename(local_path)


class _PublishingChannel:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    def publish(self, channel, caption, image_url):
        from acp.adapters.base import PublishResult

        self.calls += 1
        if self.error:
            raise self.error
        return PublishResult("thread-catalog-1", "2026-08-11T12:00:00+00:00")


def _catalog_conn():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    db.migrate(conn)
    return conn


def _insert_catalog_product(conn, *, product_id, external_id, name="Product", shop="Shop",
                            detail_link="https://example.test/product", has_inventory=1,
                            affiliate_status="NOT_CREATED", last_posted_at=None, units_sold=1,
                            commission_rate_percent=10, commission_amount=1000,
                            price_min=10000, last_seen_at="2026-08-11T10:00:00+00:00"):
    timestamp = "2026-08-11T10:00:00+00:00"
    conn.execute("""INSERT INTO product (
                    id, source, merchant, external_product_id, name, description,
                    current_price, commission_value, category_code, product_url,
                    is_available, created_at, updated_at, provider, shop_name, detail_link,
                    price_min, commission_rate_percent, commission_amount, units_sold,
                    has_inventory, affiliate_link_status, last_posted_at, first_seen_at, last_seen_at,
                    last_synced_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (product_id, "accesstrade_tiktok", shop, external_id, name, "", price_min or 0,
                  commission_amount or 0, "catalog", detail_link or "", 1, timestamp, timestamp,
                  "ACCESSTRADE_TIKTOK", shop, detail_link, price_min, commission_rate_percent,
                  commission_amount, units_sold, has_inventory, affiliate_status, last_posted_at,
                  timestamp, last_seen_at, timestamp))


def _catalog_pipeline_conn():
    from acp.core.db import now

    conn = _catalog_conn()
    conn.execute("INSERT INTO campaign (id, code, name, created_at) VALUES (?,?,?,?)",
                 ("campaign-1", "gd2026", "Catalog campaign", now()))
    conn.execute("INSERT INTO caption_template (id, code, name, body) VALUES (?,?,?,?)",
                 ("template-1", "price_drop", "Price drop", "price_drop"))
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, daily_post_cap,
                                           min_gap_minutes, created_at)
                    VALUES (?,?, 'threads', ?, 'ACTIVE', 12, 90, ?)""",
                 ("channel-1", "ch1", "@catalog", now()))
    _insert_catalog_product(conn, product_id="catalog-product", external_id="external-42",
                            name="Nồi chiên Catalog", detail_link="https://detail.example/product")
    return conn


def test_catalog_product_creates_fresh_per_post_link_and_pending_review():
    """Reusing an operator's product-only link would attribute sales to no post."""
    from acp.core import pipeline
    import tempfile

    conn = _catalog_pipeline_conn()
    client = _CatalogPipelineClient()
    old_media_dir = pipeline.MEDIA_DIR
    try:
        client.create_product_link("https://detail.example/product", post_id="product:external-42",
                                   external_product_id="external-42")
        conn.execute("""UPDATE product SET affiliate_url=?, affiliate_short_url=?,
                        affiliate_link_status='PRODUCT_ONLY' WHERE id='catalog-product'""",
                     ("https://tracking.example/product-only", "https://short.example/product-only"))
        with tempfile.TemporaryDirectory() as media_dir:
            pipeline.MEDIA_DIR = media_dir
            result = pipeline.create_post_for_catalog_product(
                conn, {"product_client": client, "storage": _CatalogStorage()},
                "catalog-product", "gd2026", "ch1")

        post = conn.execute("SELECT * FROM post WHERE id=?", (result["post_id"],)).fetchone()
        product = conn.execute("SELECT * FROM product WHERE id='catalog-product'").fetchone()
        assert result["status"] == "PENDING_REVIEW"
        assert post["affiliate_link"] == "https://short.example/post"
        assert post["affiliate_link"] != "https://short.example/product-only"
        assert "https://detail.example/product" not in post["caption_final"]
        assert client.last_body["sub1"] == post["id"]
        assert len(client.link_calls) == 2
        assert client.link_calls[0]["sub1"] == "product:external-42"
        assert product["affiliate_url"] == "https://tracking.example/post"
        assert product["affiliate_short_url"] == "https://short.example/post"
        assert product["affiliate_link_status"] == "READY"
        assert conn.execute("SELECT COUNT(*) FROM job_queue WHERE job_type='PUBLISH_POST'").fetchone()[0] == 0
    finally:
        pipeline.MEDIA_DIR = old_media_dir
        conn.close()


def test_catalog_link_failure_stops_before_caption_or_post_generation():
    """Falling back to a catalog detail URL would publish untracked content."""
    from acp.adapters.base import PublishError
    from acp.core import pipeline

    conn = _catalog_pipeline_conn()
    client = _CatalogPipelineClient(error=PublishError("provider token=secret-value"))
    try:
        result = pipeline.create_post_for_catalog_product(
            conn, {"product_client": client, "storage": _CatalogStorage()},
            "catalog-product", "gd2026", "ch1")

        product = conn.execute("SELECT * FROM product WHERE id='catalog-product'").fetchone()
        assert not result["ok"]
        assert conn.execute("SELECT COUNT(*) FROM post").fetchone()[0] == 0
        assert product["affiliate_link_status"] == "FAILED"
        assert "secret-value" not in product["affiliate_link_error"]
    finally:
        conn.close()


def test_catalog_product_publish_updates_post_metadata_once_after_success():
    """A retried publish after a successful post must not double-increment product history."""
    from acp.core import pipeline
    import tempfile

    conn = _catalog_pipeline_conn()
    client = _CatalogPipelineClient()
    old_media_dir = pipeline.MEDIA_DIR
    try:
        with tempfile.TemporaryDirectory() as media_dir:
            pipeline.MEDIA_DIR = media_dir
            created = pipeline.create_post_for_catalog_product(
                conn, {"product_client": client, "storage": _CatalogStorage()},
                "catalog-product", "gd2026", "ch1")
        assert pipeline.approve_post(conn, created["post_id"])["ok"]

        channel = _PublishingChannel()
        pipeline.publish_post(conn, {"post_id": created["post_id"], "channel_id": "channel-1"},
                              {"channel": channel})
        pipeline.publish_post(conn, {"post_id": created["post_id"], "channel_id": "channel-1"},
                              {"channel": channel})

        product = conn.execute("SELECT last_posted_at, post_count FROM product WHERE id='catalog-product'").fetchone()
        assert product["last_posted_at"] == "2026-08-11T12:00:00+00:00"
        assert product["post_count"] == 1
        assert channel.calls == 1
    finally:
        pipeline.MEDIA_DIR = old_media_dir
        conn.close()


def test_catalog_product_publish_failure_leaves_post_metadata_unchanged():
    """Failed channel publishes must not trigger catalog cooldown or inflate post counts."""
    from acp.adapters.base import PublishError
    from acp.core import pipeline
    import tempfile

    conn = _catalog_pipeline_conn()
    client = _CatalogPipelineClient()
    old_media_dir = pipeline.MEDIA_DIR
    try:
        with tempfile.TemporaryDirectory() as media_dir:
            pipeline.MEDIA_DIR = media_dir
            created = pipeline.create_post_for_catalog_product(
                conn, {"product_client": client, "storage": _CatalogStorage()},
                "catalog-product", "gd2026", "ch1")
        assert pipeline.approve_post(conn, created["post_id"])["ok"]
        try:
            pipeline.publish_post(
                conn, {"post_id": created["post_id"], "channel_id": "channel-1"},
                {"channel": _PublishingChannel(error=PublishError("network failed"))})
        except PublishError:
            pass
        else:
            raise AssertionError("Expected PublishError")

        product = conn.execute("SELECT last_posted_at, post_count FROM product WHERE id='catalog-product'").fetchone()
        assert product["last_posted_at"] is None
        assert product["post_count"] == 0
    finally:
        pipeline.MEDIA_DIR = old_media_dir
        conn.close()


def test_sync_paginates_and_upserts_without_duplicate():
    """A repeated provider ID must update one row, retain its first sighting, and keep paging."""
    from acp.core.products import ProductService

    conn = _catalog_conn()
    try:
        client = _ProductClient([
            ([
                {"id": "p1", "title": "First", "detail_link": "https://example.test/p1",
                 "sales_price": {"minimum_amount": "10000"},
                 "commission": {"amount": "1000", "rate": "1000"}, "units_sold": 4,
                 "shop": {"name": "Shop"}, "has_inventory": True},
                {"id": "p2", "title": "Second", "detail_link": "https://example.test/p2",
                 "sales_price": {"minimum_amount": "20000"},
                 "commission": {"amount": "2000", "rate": "2000"}, "units_sold": 8,
                 "shop": {"name": "Shop"}, "has_inventory": True},
            ], "NEXT"),
            ([
                {"id": "p1", "title": "First updated", "detail_link": "https://example.test/p1",
                 "sales_price": {"minimum_amount": "15000"},
                 "commission": {"amount": "1500", "rate": "1500"},
                 "shop": {"name": "Shop"}, "has_inventory": True},
            ], None),
        ])
        service = ProductService(conn, client)
        result = service.sync(max_pages=10)

        row = conn.execute("""SELECT first_seen_at, price_min, commission_amount, units_sold
                              FROM product WHERE provider=? AND external_product_id=?""",
                           ("ACCESSTRADE_TIKTOK", "p1")).fetchone()
        assert (result.fetched, result.inserted, result.updated, result.pages) == (3, 2, 1, 2)
        assert conn.execute("SELECT COUNT(*) FROM product WHERE provider=?",
                            ("ACCESSTRADE_TIKTOK",)).fetchone()[0] == 2
        assert row["first_seen_at"] is not None
        assert row["price_min"] == 15000
        assert row["commission_amount"] == 1500
        assert row["units_sold"] is None
        assert [call["page_token"] for call in client.calls] == [None, "NEXT"]
    finally:
        conn.close()


def test_recommendation_excludes_stockout_unavailable_and_cooldown():
    """Automatic candidates must exclude unavailable, stockless, and recently posted products."""
    from acp.core.products import ProductService

    conn = _catalog_conn()
    try:
        _insert_catalog_product(conn, product_id="eligible", external_id="eligible", units_sold=100,
                                commission_rate_percent=20, commission_amount=5000)
        _insert_catalog_product(conn, product_id="stockout", external_id="stockout", has_inventory=0,
                                units_sold=1000, commission_rate_percent=50, commission_amount=9000)
        _insert_catalog_product(conn, product_id="unavailable", external_id="unavailable",
                                affiliate_status="UNAVAILABLE", units_sold=1000,
                                commission_rate_percent=50, commission_amount=9000)
        _insert_catalog_product(
            conn, product_id="cooldown", external_id="cooldown",
            last_posted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), units_sold=1000,
            commission_rate_percent=50, commission_amount=9000)

        service = ProductService(conn, _ProductClient())
        service.recalculate_scores()

        assert [row["id"] for row in service.recommended(20)] == ["eligible"]
    finally:
        conn.close()


def test_local_search_filters_and_sorts_catalog_rows():
    """Local filters must constrain catalog results without interpolating their values into SQL."""
    from acp.core.products import ProductFilters, ProductService

    conn = _catalog_conn()
    try:
        _insert_catalog_product(conn, product_id="a", external_id="a", name="Áo khoác", shop="Fashion",
                                units_sold=10, commission_rate_percent=10, commission_amount=1000,
                                price_min=30000)
        _insert_catalog_product(conn, product_id="b", external_id="b", name="Áo len", shop="Fashion",
                                units_sold=30, commission_rate_percent=20, commission_amount=3000,
                                price_min=10000)
        _insert_catalog_product(conn, product_id="c", external_id="c", name="Nồi", shop="Kitchen",
                                units_sold=100, commission_rate_percent=50, commission_amount=9000,
                                price_min=50000, affiliate_status="UNAVAILABLE")

        rows = ProductService(conn, _ProductClient()).search_local(ProductFilters(
            title_keyword="Áo", shop_keyword="fashion", min_units_sold=20,
            min_commission_amount=2000, max_price=20000, affiliate_link_status="NOT_CREATED",
            sort="sold"))

        assert [row["id"] for row in rows] == ["b"]
    finally:
        conn.close()


def test_product_filters_parse_catalog_request_values():
    """Route input must become typed filters, with malformed numbers safely ignored."""
    from acp.core.products import ProductFilters

    filters = ProductFilters.from_request({
        "q": "váy", "shop": "Store", "inventory": "1", "min_price": "25000",
        "max_units_sold": "not-a-number", "affiliate_status": "READY", "sort": "price_asc",
    })

    assert filters.keyword == "váy"
    assert filters.shop_keyword == "Store"
    assert filters.has_inventory is True
    assert filters.min_price == 25000
    assert filters.max_units_sold is None
    assert filters.affiliate_link_status == "READY"
    assert filters.sort == "price_asc"


def test_sync_retries_recommended_once_when_commission_sort_is_rejected():
    """A rejected commission sort must fall back once, not abandon a manual catalog sync."""
    from acp.adapters.base import PublishError
    from acp.core.products import ProductService

    class _CommissionRejectingClient(_ProductClient):
        def search_products(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["sort_field"] == "COMMISSION":
                raise PublishError("ACCESSTRADE từ chối yêu cầu")
            return ([], None)

    conn = _catalog_conn()
    try:
        client = _CommissionRejectingClient()
        result = ProductService(conn, client).sync(sort_field="COMMISSION", max_pages=1)

        assert result.warning
        assert [call["sort_field"] for call in client.calls] == ["COMMISSION", "RECOMMENDED"]
    finally:
        conn.close()


def test_sync_lock_rejects_fresh_lock_and_releases_after_error():
    """A failed sync must not leave a lock that blocks the next operator request."""
    from acp.adapters.base import PublishError
    from acp.core.products import ProductService, SyncAlreadyRunning

    conn = _catalog_conn()
    try:
        conn.execute("INSERT INTO product_sync_lock (name, locked_at) VALUES (?, ?)",
                     ("accesstrade_tiktok", datetime.now(timezone.utc).isoformat(timespec="seconds")))
        try:
            ProductService(conn, _ProductClient()).sync(max_pages=1)
        except SyncAlreadyRunning as error:
            assert "đang chạy" in str(error)
        else:
            raise AssertionError("Expected SyncAlreadyRunning")
        conn.execute("DELETE FROM product_sync_lock WHERE name=?", ("accesstrade_tiktok",))

        try:
            ProductService(conn, _ProductClient(error=PublishError("provider failed"))).sync(max_pages=1)
        except PublishError:
            pass
        else:
            raise AssertionError("Expected provider failure")
        assert conn.execute("SELECT COUNT(*) FROM product_sync_lock").fetchone()[0] == 0
    finally:
        conn.close()


def test_stale_lock_owner_cannot_release_new_owner_lease():
    """A stale owner finishing late must not remove the lock taken over by a new sync."""
    from acp.core.products import LOCK_NAME, ProductService

    conn = _catalog_conn()
    try:
        old_owner = ProductService(conn, _ProductClient())
        new_owner = ProductService(conn, _ProductClient())
        old_owner._acquire_lock()
        conn.execute("UPDATE product_sync_lock SET locked_at=? WHERE name=?", (
            (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat(timespec="seconds"), LOCK_NAME))
        new_owner._acquire_lock()
        new_lease = conn.execute("SELECT locked_at FROM product_sync_lock WHERE name=?", (LOCK_NAME,)).fetchone()[0]

        old_owner._release_lock()

        assert conn.execute("SELECT locked_at FROM product_sync_lock WHERE name=?", (LOCK_NAME,)).fetchone()[0] == new_lease
        new_owner._release_lock()
    finally:
        conn.close()


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
                         test_client_maps_final_rate_limit_and_bad_json_to_safe_errors,
                         test_client_maps_non_timeout_network_error_without_retry,
                         test_create_product_link_uses_real_post_id_and_keeps_full_short_urls_separate,
                         test_create_product_only_link_uses_explicit_product_sub1,
                         test_legacy_tiktok_source_keeps_its_existing_fractional_rate_contract,
                         test_factory_context_exposes_product_client],
              "service": [test_sync_paginates_and_upserts_without_duplicate,
                          test_recommendation_excludes_stockout_unavailable_and_cooldown,
                          test_local_search_filters_and_sorts_catalog_rows,
                          test_product_filters_parse_catalog_request_values,
                          test_sync_retries_recommended_once_when_commission_sort_is_rejected,
                          test_sync_lock_rejects_fresh_lock_and_releases_after_error,
                          test_stale_lock_owner_cannot_release_new_owner_lease],
              "pipeline": [test_catalog_product_creates_fresh_per_post_link_and_pending_review,
                           test_catalog_link_failure_stops_before_caption_or_post_generation,
                           test_catalog_product_publish_updates_post_metadata_once_after_success,
                           test_catalog_product_publish_failure_leaves_post_metadata_unchanged]}
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
