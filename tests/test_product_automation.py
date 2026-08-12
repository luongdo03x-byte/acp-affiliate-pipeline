"""Catalog-schema migration checks.

Run a focused migration check with:
    python3 tests/test_product_automation.py migration
"""
import os
import sqlite3
import sys
import tempfile
import json
import contextlib
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def test_env_example_has_required_safe_defaults():
    """Operators receive bounded catalog defaults without any token placeholder."""
    text = Path(".env.example").read_text()
    assert "ACCESSTRADE_API_TOKEN=" in text
    assert "ACP_PRODUCT_SYNC_MAX_PAGES=10" in text
    assert "ACP_AUTO_PREPARE_CONTENT=false" in text
    assert "REDACTED" not in text


def test_catalog_schedule_docs_source_active_release_env():
    """Scheduled sync must export the active release env before running Python."""
    for path in (Path("README.md"), Path("docs/ACP_RUNBOOK.md")):
        text = path.read_text()
        assert "/bin/bash -lc" in text
        assert "set -a; . /home/operator/Downloads/ACP/acp/.env.local; set +a" in text
        assert ("exec /home/operator/Downloads/ACP/acp/.venv/bin/python "
                "/home/operator/Downloads/ACP/acp/run.py product-sync") in text


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


def test_create_product_link_uses_v2_provider_body_and_keeps_full_short_urls_separate():
    """TikTok Shop V2 requires product_url/product_id and underscore sub-ID fields."""
    from acp.adapters.accesstrade_client import AccessTradeClient

    fake_session = _FakeSession([_Response(200, {
        "status": True,
        "data": {
            "aff_url": "https://tracking.example/full",
            "aff_short_url": "https://short.example/post",
        },
    })])

    link = AccessTradeClient(session=fake_session).create_product_link(
        "https://vt.tiktok.com/product", post_id="POST-123", external_product_id="product-42")

    assert link.full_url == "https://tracking.example/full"
    assert link.short_url == "https://short.example/post"
    body = fake_session.requests[0][2]["json"]
    assert body["product_url"] == "https://vt.tiktok.com/product"
    assert body["product_id"] == "product-42"
    assert body["utm_source"] == "threads"
    assert body["utm_medium"] == "social"
    assert body["utm_campaign"] == "acp"
    assert body["utm_content"] == "product-42"
    assert body["sub_1"] == "POST-123"
    assert "urls" not in body
    assert "url_enc" not in body


def test_create_product_only_link_uses_explicit_product_sub1():
    """The standalone operator link is deliberately marked so content can never reuse it."""
    from acp.adapters.accesstrade_client import AccessTradeClient

    fake_session = _FakeSession([_Response(200, {
        "status": True,
        "data": {"success_link": [{"aff_link": "https://tracking.example/full"}]},
    })])

    AccessTradeClient(session=fake_session).create_product_link(
        "https://vt.tiktok.com/product", post_id="product:product-42", external_product_id="product-42")

    assert fake_session.requests[0][2]["json"]["sub_1"] == "product:product-42"


def test_legacy_tiktok_source_forwards_campaign_and_configurable_link_path():
    """Delegating HTTP must preserve the legacy campaign and endpoint contract."""
    from acp.adapters.accesstrade_client import LinkResult
    from acp.adapters.tiktokshop import AccessTradeTikTokShopSource

    class _Client:
        def __init__(self):
            self.search_calls = []
            self.link_calls = []

        def search_products(self, **kwargs):
            self.search_calls.append(kwargs)
            return [], None

        def create_tracking_link(self, detail_link, sub_ids, **kwargs):
            self.link_calls.append((detail_link, sub_ids, kwargs))
            return LinkResult("https://tracking.example/legacy")

    old_path = os.environ.get("AT_TIKTOK_LINK_PATH")
    try:
        os.environ["AT_TIKTOK_LINK_PATH"] = "/v1/custom-tiktok-link"
        client = _Client()
        source = AccessTradeTikTokShopSource(
            campaign_id="legacy-campaign", client=client)
        source.search_products(query="váy", limit=12, cursor="CURSOR")
        link = source.create_tracking_link(
            "https://example.test/product", {"sub1": "post-1"})
    finally:
        if old_path is None:
            os.environ.pop("AT_TIKTOK_LINK_PATH", None)
        else:
            os.environ["AT_TIKTOK_LINK_PATH"] = old_path

    assert client.search_calls == [{
        "limit": 12, "title_keywords": "váy", "page_token": "CURSOR",
        "campaign_id": "legacy-campaign",
    }]
    assert link == "https://tracking.example/legacy"
    assert client.link_calls[0][2] == {
        "campaign_id": "legacy-campaign", "link_path": "/v1/custom-tiktok-link",
    }


def test_client_marks_only_explicitly_unsupported_commission_sort():
    """Auth/server/provider failures must not masquerade as unsupported sorting."""
    from acp.adapters.accesstrade_client import AccessTradeClient, UnsupportedSortError
    from acp.adapters.base import PublishError

    unsupported = _Response(400, {
        "status": False,
        "code": "UNSUPPORTED_SORT_FIELD",
        "message": "sort_field COMMISSION is not supported",
    })
    _assert_error(
        "ACCESSTRADE không hỗ trợ sắp xếp hoa hồng",
        lambda: AccessTradeClient(session=_FakeSession([unsupported])).search_products(
            sort_field="COMMISSION"),
        UnsupportedSortError,
    )
    _assert_error(
        "ACCESSTRADE không phản hồi thành công (HTTP 400)",
        lambda: AccessTradeClient(session=_FakeSession([unsupported])).search_products(
            sort_field="RECOMMENDED"),
        PublishError,
    )


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

    def __init__(self, *, error=None, link_result=None, on_create=None):
        super().__init__()
        self.error = error
        self.link_result = link_result
        self.on_create = on_create
        self.link_calls = []
        self.last_body = None

    def create_product_link(self, detail_link, *, post_id, external_product_id):
        from acp.adapters.accesstrade_client import LinkResult

        self.last_body = {"detail_link": detail_link, "sub1": post_id,
                          "external_product_id": external_product_id}
        self.link_calls.append(self.last_body)
        if self.on_create:
            self.on_create()
        if self.error:
            raise self.error
        return self.link_result or LinkResult(full_url="https://tracking.example/post",
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


def _insert_catalog_post(conn, *, post_id, product_id, status):
    timestamp = "2026-08-11T10:00:00+00:00"
    conn.execute("""INSERT INTO post (
                    id, product_id, channel_id, campaign_id, caption_template_id,
                    variant_code, caption_body, disclosure_text, caption_final,
                    status, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (post_id, product_id, "channel-1", "campaign-1", "template-1",
                  "H1", "body", "Có chứa link tiếp thị liên kết.", "caption",
                  status, timestamp, timestamp))


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


def test_catalog_post_materializes_product_image_before_creating_a_link():
    """A real catalog image must be composed instead of silently using the placeholder."""
    from io import BytesIO

    from PIL import Image
    from acp.adapters.safe_http import SafeHttpResponse
    from acp.core import pipeline

    class _ImageHttp:
        def get(self, url, allowed_hosts=None, expected_content_prefix=None):
            assert url == "https://img.example/catalog.jpg"
            assert expected_content_prefix == "image/"
            buffer = BytesIO()
            Image.new("RGB", (3, 2), "red").save(buffer, format="JPEG")
            return SafeHttpResponse(url, buffer.getvalue(), "image/jpeg")

    conn = _catalog_pipeline_conn()
    conn.execute("UPDATE product SET main_image_url=? WHERE id='catalog-product'",
                 ("https://img.example/catalog.jpg",))
    client = _CatalogPipelineClient()
    old_media_dir = pipeline.MEDIA_DIR
    try:
        with tempfile.TemporaryDirectory() as media_dir:
            pipeline.MEDIA_DIR = media_dir
            result = pipeline.create_post_for_catalog_product(
                conn, {"product_client": client, "storage": _CatalogStorage(),
                       "catalog_image_http": _ImageHttp()},
                "catalog-product", "gd2026", "ch1")
            product = conn.execute(
                "SELECT image_path_local FROM product WHERE id='catalog-product'").fetchone()
            assert os.path.isfile(product["image_path_local"])

        assert result["ok"]
        assert len(client.link_calls) == 1
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


def test_catalog_stockout_sets_unavailable_without_requesting_a_link():
    """A stockout must become UNAVAILABLE before any provider link request."""
    from acp.core import pipeline

    conn = _catalog_pipeline_conn()
    client = _CatalogPipelineClient()
    try:
        conn.execute("UPDATE product SET has_inventory=0 WHERE id='catalog-product'")
        result = pipeline.create_post_for_catalog_product(
            conn, {"product_client": client, "storage": _CatalogStorage()},
            "catalog-product", "gd2026", "ch1")
        product = conn.execute(
            "SELECT affiliate_link_status, updated_at FROM product WHERE id='catalog-product'"
        ).fetchone()

        assert not result["ok"]
        assert product["affiliate_link_status"] == "UNAVAILABLE"
        assert product["updated_at"]
        assert client.link_calls == []
        assert conn.execute("SELECT COUNT(*) FROM post").fetchone()[0] == 0
    finally:
        conn.close()


def test_catalog_link_state_is_creating_during_request_and_records_timestamp():
    """The observable link lifecycle must transition CREATING -> READY with a timestamp."""
    from acp.core import pipeline
    import tempfile

    conn = _catalog_pipeline_conn()
    observed = []
    client = _CatalogPipelineClient(on_create=lambda: observed.append(
        conn.execute("SELECT affiliate_link_status FROM product WHERE id='catalog-product'").fetchone()[0]))
    old_media_dir = pipeline.MEDIA_DIR
    try:
        with tempfile.TemporaryDirectory() as media_dir:
            pipeline.MEDIA_DIR = media_dir
            result = pipeline.create_post_for_catalog_product(
                conn, {"product_client": client, "storage": _CatalogStorage()},
                "catalog-product", "gd2026", "ch1")
        product = conn.execute("""SELECT affiliate_link_status, affiliate_link_created_at, updated_at
                                  FROM product WHERE id='catalog-product'""").fetchone()

        assert result["ok"]
        assert observed == ["CREATING"]
        assert product["affiliate_link_status"] == "READY"
        assert product["affiliate_link_created_at"]
        assert product["updated_at"] == product["affiliate_link_created_at"]
    finally:
        pipeline.MEDIA_DIR = old_media_dir
        conn.close()


def test_catalog_post_uses_full_link_when_short_link_is_absent():
    """A valid full affiliate URL is the required fallback when no short URL exists."""
    from acp.adapters.accesstrade_client import LinkResult
    from acp.core import pipeline
    import tempfile

    conn = _catalog_pipeline_conn()
    client = _CatalogPipelineClient(
        link_result=LinkResult(full_url="https://tracking.example/full-only"))
    old_media_dir = pipeline.MEDIA_DIR
    try:
        with tempfile.TemporaryDirectory() as media_dir:
            pipeline.MEDIA_DIR = media_dir
            result = pipeline.create_post_for_catalog_product(
                conn, {"product_client": client, "storage": _CatalogStorage()},
                "catalog-product", "gd2026", "ch1")
        post = conn.execute("SELECT affiliate_link, caption_final FROM post WHERE id=?",
                            (result["post_id"],)).fetchone()

        assert post["affiliate_link"] == "https://tracking.example/full-only"
        assert "https://tracking.example/full-only" in post["caption_final"]
    finally:
        pipeline.MEDIA_DIR = old_media_dir
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


def test_end_to_end_catalog_product_to_review_and_repost_cooldown():
    """A catalog item must not be duplicated, posted without review, or immediately recommended again."""
    from io import BytesIO

    from PIL import Image
    from acp.adapters.safe_http import SafeHttpResponse
    from acp.core import pipeline
    from acp.core.products import ProductService

    class _ImageHttp:
        def get(self, url, allowed_hosts=None, expected_content_prefix=None):
            buffer = BytesIO()
            Image.new("RGB", (3, 2), "blue").save(buffer, format="JPEG")
            return SafeHttpResponse(url, buffer.getvalue(), "image/jpeg")

    conn = _catalog_pipeline_conn()
    client = _CatalogPipelineClient()
    fixture_rows = _fixture("accesstrade_product_search_v2.json")["data"]["products"]
    old_media_dir = pipeline.MEDIA_DIR
    try:
        client.pages.append((fixture_rows, None))
        ProductService(conn, client).sync(max_pages=1)
        product = conn.execute("""SELECT * FROM product
                                  WHERE provider=? AND external_product_id=?""",
                               ("ACCESSTRADE_TIKTOK", "1729384756102938475")).fetchone()
        assert product is not None

        with tempfile.TemporaryDirectory() as media_dir:
            pipeline.MEDIA_DIR = media_dir
            result = pipeline.create_post_for_catalog_product(
                conn, {"product_client": client, "storage": _CatalogStorage(),
                       "catalog_image_http": _ImageHttp()},
                product["id"], "gd2026", "ch1")

        assert result["status"] == "PENDING_REVIEW"
        assert result["affiliate_link"] == "https://short.example/post"
        assert client.last_body["sub1"] == result["post_id"]
        assert client.last_body["sub1"] != f"product:{product['external_product_id']}"

        client.pages.append((fixture_rows, None))
        ProductService(conn, client).sync(max_pages=1)
        assert conn.execute("""SELECT COUNT(*) FROM product
                               WHERE provider=? AND external_product_id=?""",
                            ("ACCESSTRADE_TIKTOK", product["external_product_id"])).fetchone()[0] == 1

        assert pipeline.approve_post(conn, result["post_id"])["ok"]
        channel = _PublishingChannel()
        pipeline.publish_post(conn, {"post_id": result["post_id"], "channel_id": "channel-1"},
                              {"channel": channel})
        published = conn.execute("SELECT status FROM post WHERE id=?", (result["post_id"],)).fetchone()
        assert channel.calls == 1
        assert published["status"] == "PUBLISHED"
        recommended_ids = {row["id"] for row in ProductService(conn, client).recommended(20)}
        assert product["id"] not in recommended_ids
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


def test_recommendation_excludes_products_with_active_sales_posts():
    """Auto-prepare must not create another post while an active sales post exists."""
    from acp.core.products import ProductService

    conn = _catalog_pipeline_conn()
    try:
        active_statuses = ("DRAFT", "PENDING_REVIEW", "APPROVED", "SCHEDULED")
        for index, status in enumerate(active_statuses):
            product_id = f"active-{index}"
            _insert_catalog_product(conn, product_id=product_id, external_id=product_id)
            _insert_catalog_post(conn, post_id=f"post-{index}", product_id=product_id, status=status)
        _insert_catalog_product(conn, product_id="eligible-no-post", external_id="eligible-no-post")

        recommended = {row["id"] for row in ProductService(conn, _ProductClient()).recommended(20)}

        assert "eligible-no-post" in recommended
        assert not set(f"active-{index}" for index in range(4)) & recommended
    finally:
        conn.close()


def test_sync_recovers_only_eligible_unavailable_rows_and_preserves_failed_errors():
    """Availability recovery clears UNAVAILABLE but leaves unrelated FAILED diagnostics intact."""
    from acp.core.products import ProductService

    conn = _catalog_conn()
    try:
        _insert_catalog_product(conn, product_id="recover", external_id="recover",
                                affiliate_status="UNAVAILABLE", has_inventory=0)
        _insert_catalog_product(conn, product_id="failed", external_id="failed",
                                affiliate_status="FAILED")
        conn.execute("UPDATE product SET affiliate_link_error='stockout' WHERE id='recover'")
        conn.execute("UPDATE product SET affiliate_link_error='safe failure' WHERE id='failed'")
        rows = [
            {"id": "recover", "title": "Recovered", "detail_link": "https://example.test/recover",
             "sales_price": {"minimum_amount": 10000}, "has_inventory": True},
            {"id": "failed", "title": "Still failed", "detail_link": "https://example.test/failed",
             "sales_price": {"minimum_amount": 10000}, "has_inventory": True},
        ]

        ProductService(conn, _ProductClient([(rows, None)])).sync(max_pages=1)
        recovered = conn.execute(
            "SELECT affiliate_link_status, affiliate_link_error FROM product WHERE id='recover'"
        ).fetchone()
        failed = conn.execute(
            "SELECT affiliate_link_status, affiliate_link_error FROM product WHERE id='failed'"
        ).fetchone()

        assert (recovered["affiliate_link_status"], recovered["affiliate_link_error"]) == (
            "NOT_CREATED", None)
        assert (failed["affiliate_link_status"], failed["affiliate_link_error"]) == (
            "FAILED", "safe failure")
    finally:
        conn.close()


def test_sync_counts_malformed_rows_and_continues_later_rows_and_pages():
    """One malformed provider row must not discard valid rows or later pages."""
    from acp.core.products import ProductService

    valid_1 = {"id": "valid-1", "title": "One", "detail_link": "https://example.test/1",
               "sales_price": {"minimum_amount": 10000}, "has_inventory": True}
    valid_2 = {"id": "valid-2", "title": "Two", "detail_link": "https://example.test/2",
               "sales_price": {"minimum_amount": 20000}, "has_inventory": True}
    conn = _catalog_conn()
    try:
        result = ProductService(conn, _ProductClient([
            ([valid_1, "malformed-row"], "NEXT"),
            ([valid_2], None),
        ])).sync(max_pages=2)

        assert (result.fetched, result.inserted, result.failed, result.pages) == (3, 2, 1, 2)
        assert conn.execute("SELECT COUNT(*) FROM product WHERE provider='ACCESSTRADE_TIKTOK'").fetchone()[0] == 2
        assert "Failed: 1" in result.operator_summary()
    finally:
        conn.close()


def test_sync_propagates_database_failures_instead_of_counting_them_as_bad_rows():
    """Infrastructure/data-integrity failures must abort sync rather than report partial success."""
    import sqlite3
    from acp.core.products import ProductService

    class _DatabaseFailingService(ProductService):
        def _upsert(self, product, result):
            raise sqlite3.OperationalError("database write failed")

    row = {"id": "valid", "title": "Valid", "detail_link": "https://example.test/valid",
           "sales_price": {"minimum_amount": 10000}, "has_inventory": True}
    conn = _catalog_conn()
    try:
        try:
            _DatabaseFailingService(conn, _ProductClient([([row], None)])).sync(max_pages=1)
        except sqlite3.OperationalError as error:
            assert str(error) == "database write failed"
        else:
            raise AssertionError("Expected database failure to propagate")
    finally:
        conn.close()


def test_catalog_rows_never_enter_legacy_scoring_or_generate_content():
    """Catalog posts require per-post Product API links, never the legacy ctx.source path."""
    from acp.core import pipeline, scoring

    conn = _catalog_pipeline_conn()
    try:
        conn.execute("""UPDATE product SET rating=5, review_count=500,
                        commission_value=50000, sold_count=5000, is_available=1
                        WHERE id='catalog-product'""")
        explained_ids = {
            item["product"]["id"] for item in scoring.score_candidates(conn, limit=20, explain=True)
        }
        assert "catalog-product" not in explained_ids
        assert pipeline.plan_content(conn, "gd2026", limit=10) == []

        class _LegacySource:
            calls = 0

            def create_tracking_link(self, *_args, **_kwargs):
                self.calls += 1
                raise AssertionError("catalog row reached legacy source")

        source = _LegacySource()
        payload = {
            "product_id": "catalog-product", "channel_id": "channel-1",
            "campaign_id": "campaign-1", "template_id": "template-1",
            "variant_code": "H1", "score": 1,
        }
        try:
            pipeline.generate_content(conn, payload, {"source": source})
        except ValueError as error:
            assert "catalog" in str(error).lower()
        else:
            raise AssertionError("Expected catalog legacy-pipeline guard")
        assert source.calls == 0
    finally:
        conn.close()


def test_recommendation_default_cooldown_is_seven_days():
    """The service fallback must match the documented seven-day repost cooldown."""
    from acp.core.products import ProductService

    old_value = os.environ.pop("ACP_PRODUCT_REPOST_COOLDOWN_DAYS", None)
    conn = _catalog_conn()
    try:
        _insert_catalog_product(
            conn, product_id="posted-eight-days", external_id="posted-eight-days",
            last_posted_at=(datetime.now(timezone.utc) - timedelta(days=8)).isoformat(timespec="seconds"))
        assert [row["id"] for row in ProductService(conn, _ProductClient()).recommended(20)] == [
            "posted-eight-days"]
    finally:
        conn.close()
        if old_value is not None:
            os.environ["ACP_PRODUCT_REPOST_COOLDOWN_DAYS"] = old_value


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


def test_newest_sort_uses_first_seen_not_last_sync_refresh():
    """Resyncing an old product must not make it newer than a later catalog arrival."""
    from acp.core.products import ProductFilters, ProductService

    conn = _catalog_conn()
    try:
        _insert_catalog_product(conn, product_id="older", external_id="older")
        _insert_catalog_product(conn, product_id="newer", external_id="newer")
        conn.execute("""UPDATE product SET first_seen_at='2026-08-01T00:00:00+00:00',
                        last_seen_at='2026-08-12T00:00:00+00:00' WHERE id='older'""")
        conn.execute("""UPDATE product SET first_seen_at='2026-08-10T00:00:00+00:00',
                        last_seen_at='2026-08-10T00:00:00+00:00' WHERE id='newer'""")

        rows = ProductService(conn, _ProductClient()).search_local(ProductFilters(sort="newest"))

        assert [row["id"] for row in rows] == ["newer", "older"]
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
    """Unsupported commission sorting restarts RECOMMENDED at page one exactly once."""
    from acp.adapters.accesstrade_client import UnsupportedSortError
    from acp.core.products import ProductService

    class _CommissionRejectingClient(_ProductClient):
        def search_products(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return ([{"id": "commission-page", "title": "Commission",
                          "detail_link": "https://example.test/commission",
                          "sales_price": {"minimum_amount": 10000}, "has_inventory": True}],
                        "FOREIGN-TOKEN")
            if len(self.calls) == 2:
                raise UnsupportedSortError("ACCESSTRADE không hỗ trợ sắp xếp hoa hồng")
            return ([{"id": "recommended-page", "title": "Recommended",
                      "detail_link": "https://example.test/recommended",
                      "sales_price": {"minimum_amount": 20000}, "has_inventory": True}], None)

    conn = _catalog_conn()
    try:
        client = _CommissionRejectingClient()
        result = ProductService(conn, client).sync(sort_field="COMMISSION", max_pages=2)

        assert result.warning
        assert [(call["sort_field"], call["page_token"]) for call in client.calls] == [
            ("COMMISSION", None), ("COMMISSION", "FOREIGN-TOKEN"), ("RECOMMENDED", None)]
    finally:
        conn.close()


def test_sync_does_not_fallback_commission_sort_on_auth_network_or_server_errors():
    """Only UnsupportedSortError is eligible for the one-time RECOMMENDED fallback."""
    from acp.adapters.base import PublishError
    from acp.core.products import ProductService

    conn = _catalog_conn()
    client = _ProductClient(error=PublishError("Token ACCESSTRADE không hợp lệ"))
    try:
        try:
            ProductService(conn, client).sync(sort_field="COMMISSION", max_pages=2)
        except PublishError:
            pass
        else:
            raise AssertionError("Expected auth/provider error")
        assert len(client.calls) == 1
        assert client.calls[0]["sort_field"] == "COMMISSION"
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


def test_product_sync_command_runs_catalog_sync_and_prints_summary():
    """Removing the CLI catalog sync path would leave a scheduled run invisible to operators."""
    from acp import run

    class _Session:
        def __enter__(self):
            return object()

        def __exit__(self, *_):
            return False

    class _Result:
        fetched = 2
        inserted = 1
        updated = 1
        skipped = 0

    class _Service:
        def __init__(self, conn, client):
            self.conn = conn
            self.client = client

        def sync(self, **kwargs):
            assert kwargs == {"title_keywords": None}
            return _Result()

    original = {name: getattr(run, name, None)
                for name in ("db", "ProductService", "AccessTradeClient")}
    try:
        run.db = type("_Db", (), {"init_db": staticmethod(lambda: None),
                                    "session": staticmethod(lambda: _Session())})
        run.ProductService = _Service
        run.AccessTradeClient = type("_Client", (), {"from_env": staticmethod(object)})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert run.main(["product-sync"]) == 0
        assert "Fetched: 2" in output.getvalue()
        assert "New: 1" in output.getvalue()
        assert "Updated: 1" in output.getvalue()
        assert "Skipped: 0" in output.getvalue()
        assert "Failed: 0" in output.getvalue()
    finally:
        for name, value in original.items():
            if value is None:
                delattr(run, name)
            else:
                setattr(run, name, value)


def test_product_sync_auto_prepare_requires_flag_and_environment():
    """A scheduler must never create review posts when either explicit safety gate is absent."""
    from acp import run

    class _Session:
        def __enter__(self):
            return "connection"

        def __exit__(self, *_):
            return False

    class _Result:
        fetched = inserted = updated = skipped = 0

    class _Service:
        def __init__(self, *_):
            pass

        def sync(self, **_):
            return _Result()

        def recommended(self, limit):
            assert limit == 3
            return [{"id": "catalog-product"}]

    class _Pipeline:
        calls = []

        @classmethod
        def create_post_for_catalog_product(cls, *args):
            cls.calls.append(args)
            return {"ok": True, "status": "PENDING_REVIEW"}

    original = {name: getattr(run, name, None)
                for name in ("db", "ProductService", "AccessTradeClient", "factory", "pipeline")}
    old_auto_prepare = os.environ.pop("ACP_AUTO_PREPARE_CONTENT", None)
    try:
        run.db = type("_Db", (), {"init_db": staticmethod(lambda: None),
                                    "session": staticmethod(lambda: _Session())})
        run.ProductService = _Service
        run.AccessTradeClient = type("_Client", (), {"from_env": staticmethod(object)})
        run.factory = type("_Factory", (), {"build_context": staticmethod(lambda: {"ctx": "value"})})
        run.pipeline = _Pipeline
        run.cmd_product_sync(auto_prepare=False)
        run.cmd_product_sync(auto_prepare=True)
        assert _Pipeline.calls == []

        os.environ["ACP_AUTO_PREPARE_CONTENT"] = "true"
        run.cmd_product_sync(auto_prepare=True)
        assert len(_Pipeline.calls) == 1
        _, context, product_id, campaign_code = _Pipeline.calls[0]
        assert context["ctx"] == "value"
        assert "product_client" in context
        assert (product_id, campaign_code) == ("catalog-product", run.CAMPAIGN_CODE)
    finally:
        if old_auto_prepare is None:
            os.environ.pop("ACP_AUTO_PREPARE_CONTENT", None)
        else:
            os.environ["ACP_AUTO_PREPARE_CONTENT"] = old_auto_prepare
        for name, value in original.items():
            if value is None:
                delattr(run, name)
            else:
                setattr(run, name, value)


def test_product_sync_skip_and_errors_have_cron_safe_exit_codes():
    """Disabled cron jobs must skip cleanly, while a busy catalog lock must be retryable."""
    from acp import run

    old_enabled = os.environ.get("ACP_PRODUCT_SYNC_ENABLED")
    try:
        os.environ["ACP_PRODUCT_SYNC_ENABLED"] = "false"
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert run.main(["product-sync"]) == 0
        assert "disabled" in output.getvalue().lower()
    finally:
        if old_enabled is None:
            os.environ.pop("ACP_PRODUCT_SYNC_ENABLED", None)
        else:
            os.environ["ACP_PRODUCT_SYNC_ENABLED"] = old_enabled


def test_product_sync_returns_nonzero_without_leaking_provider_errors():
    """Cron must retry a busy or provider-failed sync without printing request secrets."""
    from acp import run
    from acp.adapters.base import PublishError

    class _Session:
        def __enter__(self):
            return object()

        def __exit__(self, *_):
            return False

    errors = [run.SyncAlreadyRunning(), PublishError("token=must-not-print")]

    class _Service:
        def __init__(self, *_):
            pass

        def sync(self, **_):
            raise errors.pop(0)

    original = {name: getattr(run, name, None)
                for name in ("db", "ProductService", "AccessTradeClient")}
    old_enabled = os.environ.get("ACP_PRODUCT_SYNC_ENABLED")
    try:
        os.environ["ACP_PRODUCT_SYNC_ENABLED"] = "true"
        run.db = type("_Db", (), {"init_db": staticmethod(lambda: None),
                                    "session": staticmethod(lambda: _Session())})
        run.ProductService = _Service
        run.AccessTradeClient = type("_Client", (), {"from_env": staticmethod(object)})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert run.main(["product-sync"]) == 1
            assert run.main(["product-sync"]) == 1
        assert "token=must-not-print" not in output.getvalue()
    finally:
        if old_enabled is None:
            os.environ.pop("ACP_PRODUCT_SYNC_ENABLED", None)
        else:
            os.environ["ACP_PRODUCT_SYNC_ENABLED"] = old_enabled
        for name, value in original.items():
            if value is None:
                delattr(run, name)
            else:
                setattr(run, name, value)


def test_product_sync_uses_seed_catalog_in_mock_mode():
    """Mock-mode verification must not make an ACCESSTRADE network request."""
    from acp import run

    previous_db_path = db.DB_PATH
    previous_adapter = os.environ.get("ACP_ADAPTER")
    previous_source = os.environ.get("ACP_SOURCE")
    previous_enabled = os.environ.get("ACP_PRODUCT_SYNC_ENABLED")
    with tempfile.TemporaryDirectory() as directory:
        db.DB_PATH = os.path.join(directory, "catalog.db")
        try:
            db.init_db()
            os.environ["ACP_ADAPTER"] = "mock"
            os.environ["ACP_SOURCE"] = "mock"
            os.environ["ACP_PRODUCT_SYNC_ENABLED"] = "true"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                assert run.main(["product-sync"]) == 0
            assert "Fetched: " in output.getvalue()
            assert "Failed: 0" in output.getvalue()
        finally:
            db.DB_PATH = previous_db_path
            for name, value in (("ACP_ADAPTER", previous_adapter),
                                ("ACP_SOURCE", previous_source),
                                ("ACP_PRODUCT_SYNC_ENABLED", previous_enabled)):
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def test_product_sync_initializes_its_catalog_schema_before_syncing():
    """A fresh cron database must receive the catalog migration before acquiring its sync lock."""
    from acp import run

    previous_db_path = db.DB_PATH
    previous_adapter = os.environ.get("ACP_ADAPTER")
    previous_source = os.environ.get("ACP_SOURCE")
    with tempfile.TemporaryDirectory() as directory:
        db.DB_PATH = os.path.join(directory, "fresh-catalog.db")
        try:
            os.environ["ACP_ADAPTER"] = "mock"
            os.environ["ACP_SOURCE"] = "mock"
            assert run.main(["product-sync"]) == 0
        finally:
            db.DB_PATH = previous_db_path
            for name, value in (("ACP_ADAPTER", previous_adapter), ("ACP_SOURCE", previous_source)):
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def test_mock_auto_prepare_uses_mock_client_and_keeps_post_pending_review():
    """Mock auto-prepare must not replace its safe catalog client with a live link client."""
    from acp import run

    class _Session:
        def __enter__(self):
            return "connection"

        def __exit__(self, *_):
            return False

    class _Result:
        fetched = inserted = updated = skipped = 0

    class _Service:
        def __init__(self, _, client):
            self.client = client

        def sync(self, **_):
            return _Result()

        def recommended(self, _):
            return [{"id": "catalog-product"}]

    class _LiveClient:
        calls = []

        def create_product_link(self, *_args, **_kwargs):
            self.calls.append((_args, _kwargs))
            raise AssertionError("mock auto-prepare must not call the live client")

    class _Pipeline:
        results = []

        @classmethod
        def create_post_for_catalog_product(cls, _, ctx, product_id, campaign_code):
            link = ctx["product_client"].create_product_link(
                "https://example.test/product", post_id="post-1", external_product_id=product_id)
            cls.results.append((link.full_url, campaign_code, "PENDING_REVIEW"))
            return {"ok": True, "status": "PENDING_REVIEW"}

    original = {name: getattr(run, name, None)
                for name in ("db", "ProductService", "factory", "pipeline")}
    old_values = {name: os.environ.get(name) for name in (
        "ACP_ADAPTER", "ACP_SOURCE", "ACP_PRODUCT_SYNC_ENABLED", "ACP_AUTO_PREPARE_CONTENT")}
    try:
        os.environ.update({"ACP_ADAPTER": "mock", "ACP_SOURCE": "mock",
                           "ACP_PRODUCT_SYNC_ENABLED": "true", "ACP_AUTO_PREPARE_CONTENT": "true"})
        run.db = type("_Db", (), {"init_db": staticmethod(lambda: None),
                                    "session": staticmethod(lambda: _Session())})
        run.ProductService = _Service
        run.factory = type("_Factory", (), {"build_context": staticmethod(
            lambda: {"product_client": _LiveClient()})})
        run.pipeline = _Pipeline

        assert run.cmd_product_sync(auto_prepare=True) == 0
        assert _LiveClient.calls == []
        assert _Pipeline.results == [("https://mock.acp/product/catalog-product?post_id=post-1",
                                      run.CAMPAIGN_CODE, "PENDING_REVIEW")]
    finally:
        for name, value in old_values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for name, value in original.items():
            if value is None:
                delattr(run, name)
            else:
                setattr(run, name, value)


def test_auto_prepare_failure_is_redacted_and_returns_nonzero():
    """A failed affiliate-link preparation must make cron retry without leaking the provider error."""
    from acp import run

    class _Session:
        def __enter__(self):
            return "connection"

        def __exit__(self, *_):
            return False

    class _Result:
        fetched = inserted = updated = skipped = 0

    class _Service:
        def __init__(self, *_):
            pass

        def sync(self, **_):
            return _Result()

        def recommended(self, _):
            return [{"id": "catalog-product"}]

    class _Pipeline:
        @staticmethod
        def create_post_for_catalog_product(*_):
            return {"ok": False, "error": "provider token=must-not-print"}

    original = {name: getattr(run, name, None)
                for name in ("db", "ProductService", "AccessTradeClient", "factory", "pipeline")}
    old_auto_prepare = os.environ.get("ACP_AUTO_PREPARE_CONTENT")
    try:
        os.environ["ACP_AUTO_PREPARE_CONTENT"] = "true"
        run.db = type("_Db", (), {"init_db": staticmethod(lambda: None),
                                    "session": staticmethod(lambda: _Session())})
        run.ProductService = _Service
        run.AccessTradeClient = type("_Client", (), {"from_env": staticmethod(object)})
        run.factory = type("_Factory", (), {"build_context": staticmethod(lambda: {})})
        run.pipeline = _Pipeline
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert run.cmd_product_sync(auto_prepare=True) == 1
        assert "Preparation failed: 1" in output.getvalue()
        assert "token=must-not-print" not in output.getvalue()
    finally:
        if old_auto_prepare is None:
            os.environ.pop("ACP_AUTO_PREPARE_CONTENT", None)
        else:
            os.environ["ACP_AUTO_PREPARE_CONTENT"] = old_auto_prepare
        for name, value in original.items():
            if value is None:
                delattr(run, name)
            else:
                setattr(run, name, value)


@contextlib.contextmanager
def _catalog_web_app(*, require_auth=False):
    """Create an isolated catalog-backed web client without touching production data."""
    from acp.web import server

    previous_db_path = db.DB_PATH
    previous_password = os.environ.get("ACP_ADMIN_PASSWORD")
    with tempfile.TemporaryDirectory() as directory:
        db.DB_PATH = os.path.join(directory, "catalog-web.db")
        if require_auth:
            os.environ["ACP_ADMIN_PASSWORD"] = "catalog-password"
        else:
            os.environ.pop("ACP_ADMIN_PASSWORD", None)
        try:
            db.init_db()
            conn = db.connect()
            try:
                conn.execute("INSERT INTO campaign (id, code, name, created_at) VALUES (?,?,?,?)",
                             ("campaign-web", "gd2026", "Catalog web", db.now()))
                conn.execute("INSERT INTO caption_template (id, code, name, body) VALUES (?,?,?,?)",
                             ("template-web", "price_drop", "Price drop", "price_drop"))
                conn.execute("""INSERT INTO channel (id, code, platform, handle, status,
                                                       daily_post_cap, min_gap_minutes, created_at)
                                VALUES (?,?, 'threads', ?, 'ACTIVE', 12, 90, ?)""",
                             ("channel-web", "ch1", "@catalog-web", db.now()))
                _insert_catalog_product(
                    conn, product_id="catalog-web-product", external_id="external-web-product",
                    name="Váy test", shop="Shop test", price_min=125000,
                    commission_amount=12500, commission_rate_percent=10, units_sold=25)
            finally:
                conn.close()
            app = server.create_app()
            app.config["TESTING"] = True
            app.logger.disabled = True
            yield app, server, "catalog-web-product"
        finally:
            db.DB_PATH = previous_db_path
            if previous_password is None:
                os.environ.pop("ACP_ADMIN_PASSWORD", None)
            else:
                os.environ["ACP_ADMIN_PASSWORD"] = previous_password


def _login_catalog_web(client):
    response = client.post("/dangnhap", data={"password": "catalog-password"})
    assert response.status_code == 302
    with client.session_transaction() as session_data:
        return session_data["csrf"]


def test_products_page_is_local_and_renders_filters():
    """The default workspace must read the local catalog, not call live search on GET."""
    with _catalog_web_app() as (app, _server, _product_id):
        client = app.test_client()
        response = client.get("/sanpham?q=váy&sort=score&inventory=1")

    assert response.status_code == 200
    assert "Đồng bộ sản phẩm" in response.text
    assert "ACP Score" in response.text
    assert "Váy test" in response.text
    assert "Nhập link affiliate" in response.text
    for field in ("min_commission_rate", "min_commission_amount", "min_price", "max_price",
                  "min_units_sold", "affiliate_status", "post_state"):
        assert f'name="{field}"' in response.text
    assert '<option value="newest"' in response.text


def test_catalog_routes_require_csrf_and_hide_api_errors():
    """Catalog mutation routes retain CSRF and never render provider credentials."""
    with _catalog_web_app(require_auth=True) as (app, server, product_id):
        client = app.test_client()
        csrf = _login_catalog_web(client)
        assert client.post("/sanpham/sync").status_code == 400

        original_service = server.ProductService

        class _FailingService:
            def __init__(self, *_):
                pass

            def sync(self, **_):
                raise RuntimeError("Authorization: Token catalog-secret")

        server.ProductService = _FailingService
        try:
            response = client.post("/sanpham/sync", data={"_csrf": csrf})
        finally:
            server.ProductService = original_service
        assert response.status_code == 302
        page = client.get(response.headers["Location"])
        assert "Không thể tiếp tục. Vui lòng thử lại." in page.text
        assert "catalog-secret" not in page.text
        assert "Authorization" not in page.text

        original_create = server.pipeline.create_post_for_catalog_product
        server.pipeline.create_post_for_catalog_product = lambda *_args, **_kwargs: {"ok": True}
        try:
            response = client.post(f"/sanpham/{product_id}/tao-bai", data={"_csrf": csrf})
        finally:
            server.pipeline.create_post_for_catalog_product = original_create
        assert response.status_code == 302
        assert "/duyet" in response.headers["Location"]


def test_catalog_publish_error_is_redacted_from_redirect_and_page():
    """A provider error is diagnostic-only; its credentials must never enter a redirect or HTML."""
    from acp.adapters.base import PublishError

    with _catalog_web_app(require_auth=True) as (app, server, _product_id):
        client = app.test_client()
        csrf = _login_catalog_web(client)
        original_service = server.ProductService

        class _FailingService:
            def __init__(self, *_):
                pass

            def sync(self, **_):
                raise PublishError("Authorization: Token provider-secret; response body=private")

        server.ProductService = _FailingService
        try:
            response = client.post("/sanpham/sync", data={"_csrf": csrf})
        finally:
            server.ProductService = original_service
        page = client.get(response.headers["Location"])

    assert response.status_code == 302
    assert "provider-secret" not in response.headers["Location"]
    assert "Authorization" not in response.headers["Location"]
    assert "provider-secret" not in page.text
    assert "Authorization" not in page.text
    assert "Không thể tiếp tục. Vui lòng thử lại." in page.text


def test_catalog_omits_unsafe_provider_urls_from_html():
    """Only absolute HTTP(S) detail and image URLs may cross the catalog template boundary."""
    with _catalog_web_app() as (app, _server, product_id):
        conn = db.connect()
        try:
            conn.execute("""UPDATE product SET detail_link=?, main_image_url=? WHERE id=?""",
                         ("javascript:alert('catalog-xss')", "not-a-url", product_id))
        finally:
            conn.close()
        response = app.test_client().get("/sanpham")

    assert response.status_code == 200
    assert "javascript:alert" not in response.text
    assert "not-a-url" not in response.text
    assert '<a href="javascript:' not in response.text
    assert '<img src="not-a-url"' not in response.text


def test_catalog_create_post_logs_only_safe_diagnostic_context():
    """Link logs retain operation/type/product context without provider exception data."""
    from acp.adapters.base import PublishError
    from acp.adapters import factory
    import logging

    with _catalog_web_app(require_auth=True) as (app, _server, product_id):
        client = app.test_client()
        csrf = _login_catalog_web(client)
        captured = []
        app.logger.disabled = False

        class _Capture(logging.Handler):
            def emit(self, record):
                captured.append(record)

        handler = _Capture()
        previous_handlers = list(app.logger.handlers)
        previous_propagate = app.logger.propagate
        app.logger.handlers[:] = [handler]
        app.logger.propagate = False
        app.logger.setLevel(logging.ERROR)
        original_context = factory.build_context

        class _FailingClient:
            def create_product_link(self, *_args, **_kwargs):
                raise PublishError("Authorization: Token post-provider-secret")

        factory.build_context = lambda: {"product_client": _FailingClient(), "storage": _CatalogStorage()}
        try:
            response = client.post(f"/sanpham/{product_id}/tao-bai", data={"_csrf": csrf})
        finally:
            factory.build_context = original_context
            app.logger.handlers[:] = previous_handlers
            app.logger.propagate = previous_propagate
            app.logger.disabled = True
        page = client.get(response.headers["Location"])

    assert response.status_code == 302
    assert "post-provider-secret" not in response.headers["Location"]
    assert "post-provider-secret" not in page.text
    assert "Không thể tạo link affiliate cho sản phẩm" in page.text
    rendered = "\n".join(record.getMessage() for record in captured)
    assert "operation=create_post_link" in rendered
    assert "error_type=PublishError" in rendered
    assert f"product_id={product_id}" in rendered
    assert "post-provider-secret" not in rendered
    assert "Authorization" not in rendered
    assert all(record.exc_info is None for record in captured)


def test_catalog_sync_and_standalone_link_logs_never_contain_provider_secrets():
    """Sync/link failures may log safe context only, never exception messages or bodies."""
    from acp.adapters.base import PublishError
    import logging

    with _catalog_web_app(require_auth=True) as (app, server, product_id):
        client = app.test_client()
        csrf = _login_catalog_web(client)
        captured = []
        app.logger.disabled = False

        class _Capture(logging.Handler):
            def emit(self, record):
                captured.append(record)

        class _FailingService:
            def __init__(self, *_):
                pass

            def sync(self, **_):
                raise PublishError("Authorization: Token sync-secret; response body=sync-private")

        class _FailingLinkClient:
            def create_product_link(self, *_args, **_kwargs):
                raise PublishError("Authorization: Token link-secret; response body=link-private")

        handler = _Capture()
        previous_handlers = list(app.logger.handlers)
        previous_propagate = app.logger.propagate
        original_service = server.ProductService
        original_client = server.AccessTradeClient
        app.logger.handlers[:] = [handler]
        app.logger.propagate = False
        app.logger.setLevel(logging.ERROR)
        server.ProductService = _FailingService
        try:
            client.post("/sanpham/sync", data={"_csrf": csrf})
            server.ProductService = original_service
            server.AccessTradeClient = type(
                "_Client", (), {"from_env": staticmethod(lambda: _FailingLinkClient())})
            client.post(f"/sanpham/{product_id}/affiliate-link", data={"_csrf": csrf})
        finally:
            server.ProductService = original_service
            server.AccessTradeClient = original_client
            app.logger.handlers[:] = previous_handlers
            app.logger.propagate = previous_propagate
            app.logger.disabled = True

    rendered = "\n".join(record.getMessage() for record in captured)
    assert "operation=sync" in rendered
    assert "operation=create_product_link" in rendered
    assert "error_type=PublishError" in rendered
    assert f"product_id={product_id}" in rendered
    for secret in ("sync-secret", "link-secret", "Authorization", "response body", "sync-private",
                   "link-private"):
        assert secret not in rendered
    assert all(record.exc_info is None for record in captured)


def test_catalog_sync_redirects_with_operator_summary():
    """A successful local sync returns its safe operator summary to the catalog page."""
    with _catalog_web_app(require_auth=True) as (app, server, _product_id):
        client = app.test_client()
        csrf = _login_catalog_web(client)
        original_service = server.ProductService

        class _SyncService:
            def __init__(self, *_):
                pass

            def sync(self, **_):
                return type("_Result", (), {
                    "fetched": 4, "inserted": 1, "updated": 2, "failed": 1,
                })()

        server.ProductService = _SyncService
        try:
            response = client.post("/sanpham/sync", data={"_csrf": csrf, "q": "váy"})
        finally:
            server.ProductService = original_service
        page = client.get(response.headers["Location"])

    assert response.status_code == 302
    assert "Đã đồng bộ 4 sản phẩm (1 mới, 2 cập nhật, 1 lỗi)." in page.text


def test_catalog_standalone_link_uses_product_marker():
    """Copied catalog links carry the product marker; post creation gets a fresh post marker elsewhere."""
    with _catalog_web_app(require_auth=True) as (app, server, product_id):
        client = app.test_client()
        csrf = _login_catalog_web(client)
        calls = []

        class _LinkClient:
            def create_product_link(self, detail_link, *, post_id, external_product_id):
                from acp.adapters.accesstrade_client import LinkResult
                calls.append((detail_link, post_id, external_product_id))
                return LinkResult("https://tracking.example/product", "https://short.example/product")

        original_client = server.AccessTradeClient
        server.AccessTradeClient = type("_Client", (), {"from_env": staticmethod(lambda: _LinkClient())})
        try:
            response = client.post(f"/sanpham/{product_id}/affiliate-link", data={"_csrf": csrf})
        finally:
            server.AccessTradeClient = original_client

    assert response.status_code == 302
    assert calls == [("https://example.test/product", "product:external-web-product", "external-web-product")]


def test_system_setting_schema_is_idempotent_and_unique():
    """A repeated schema upgrade must keep one durable value per setting key."""
    with tempfile.TemporaryDirectory() as directory:
        previous_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(directory, "system-settings.db")
        try:
            db.init_db()
            db.init_db()
            conn = db.connect()
            try:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(system_setting)")}
                assert columns == {"key", "value", "updated_at"}
                conn.execute(
                    "INSERT INTO system_setting (key, value, updated_at) VALUES (?,?,?)",
                    ("publish_worker_enabled", "0", db.now()),
                )
                try:
                    conn.execute(
                        "INSERT INTO system_setting (key, value, updated_at) VALUES (?,?,?)",
                        ("publish_worker_enabled", "1", db.now()),
                    )
                except sqlite3.IntegrityError:
                    pass
                else:
                    raise AssertionError("a duplicate system setting key must be rejected")
            finally:
                conn.close()
        finally:
            db.DB_PATH = previous_db_path


def test_publish_worker_setting_defaults_persists_and_audits():
    """Removing the fail-safe default, persistence, or audit write breaks this contract."""
    from acp.core import system_settings

    with tempfile.TemporaryDirectory() as directory:
        previous_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(directory, "worker-setting.db")
        try:
            db.init_db()
            conn = db.connect()
            try:
                assert system_settings.get_system_setting(conn, "missing", "fallback") == "fallback"
                assert system_settings.publish_worker_enabled(conn) is False

                system_settings.set_system_setting(
                    conn, "publish_worker_enabled", "1", actor="reviewer"
                )

                assert system_settings.get_system_setting(conn, "publish_worker_enabled") == "1"
                assert system_settings.publish_worker_enabled(conn) is True
                audit_row = conn.execute(
                    """SELECT entity, entity_id, action, actor, detail
                       FROM audit_log WHERE entity='system_setting'
                       ORDER BY id DESC LIMIT 1"""
                ).fetchone()
                assert dict(audit_row) == {
                    "entity": "system_setting",
                    "entity_id": "publish_worker_enabled",
                    "action": "set",
                    "actor": "reviewer",
                    "detail": '{"value": "1"}',
                }
            finally:
                conn.close()
        finally:
            db.DB_PATH = previous_db_path


def test_disabled_publish_worker_keeps_publish_ready_and_runs_other_jobs():
    """A disabled worker must not consume due publish jobs, but may run other work."""
    from acp.core import jobs

    calls = []
    previous_handler = jobs._handlers.get("WORKER_TOGGLE_NON_PUBLISH")

    @jobs.handler("WORKER_TOGGLE_NON_PUBLISH")
    def non_publish_handler(conn, payload, ctx):
        calls.append(payload["name"])

    with tempfile.TemporaryDirectory() as directory:
        previous_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(directory, "disabled-worker.db")
        try:
            db.init_db()
            conn = db.connect()
            try:
                publish_id = jobs.enqueue(conn, "PUBLISH_POST", {"post_id": "post-1"})
                other_id = jobs.enqueue(conn, "WORKER_TOGGLE_NON_PUBLISH", {"name": "catalog"})

                stats = jobs.run_once(conn, limit=2, ctx={})

                publish = conn.execute(
                    "SELECT status, attempt_count, locked_by FROM job_queue WHERE id=?", (publish_id,)
                ).fetchone()
                other = conn.execute("SELECT status FROM job_queue WHERE id=?", (other_id,)).fetchone()
                assert dict(publish) == {"status": "READY", "attempt_count": 0, "locked_by": None}
                assert other["status"] == "DONE"
                assert calls == ["catalog"]
                assert stats["done"] == 1
            finally:
                conn.close()
        finally:
            db.DB_PATH = previous_db_path
            if previous_handler is None:
                jobs._handlers.pop("WORKER_TOGGLE_NON_PUBLISH", None)
            else:
                jobs._handlers["WORKER_TOGGLE_NON_PUBLISH"] = previous_handler


def test_enabled_publish_worker_executes_due_publish_job():
    """Changing the enabled setting must allow a due publish job to execute once."""
    from acp.core import jobs, system_settings

    calls = []
    previous_handler = jobs._handlers.get("PUBLISH_POST")

    @jobs.handler("PUBLISH_POST")
    def publish_handler(conn, payload, ctx):
        calls.append(payload["post_id"])

    with tempfile.TemporaryDirectory() as directory:
        previous_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(directory, "enabled-worker.db")
        try:
            db.init_db()
            conn = db.connect()
            try:
                system_settings.set_system_setting(conn, "publish_worker_enabled", "1")
                job_id = jobs.enqueue(conn, "PUBLISH_POST", {"post_id": "post-2"})

                stats = jobs.run_once(conn, limit=1, ctx={})

                job = conn.execute(
                    "SELECT status, attempt_count FROM job_queue WHERE id=?", (job_id,)
                ).fetchone()
                assert dict(job) == {"status": "DONE", "attempt_count": 0}
                assert calls == ["post-2"]
                assert stats["done"] == 1
            finally:
                conn.close()
        finally:
            db.DB_PATH = previous_db_path
            if previous_handler is None:
                jobs._handlers.pop("PUBLISH_POST", None)
            else:
                jobs._handlers["PUBLISH_POST"] = previous_handler


def test_system_setting_audit_redacts_non_toggle_value():
    """An arbitrary setting value must never be copied into the audit trail."""
    from acp.core import system_settings

    with tempfile.TemporaryDirectory() as directory:
        previous_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(directory, "redacted-setting.db")
        try:
            db.init_db()
            conn = db.connect()
            try:
                secret_value = "credential-that-must-not-appear-in-audit"
                system_settings.set_system_setting(conn, "unrelated_setting", secret_value)

                audit_detail = conn.execute(
                    "SELECT detail FROM audit_log ORDER BY id DESC LIMIT 1"
                ).fetchone()["detail"]
                assert secret_value not in audit_detail
                assert audit_detail == '{"value": "[redacted]"}'
            finally:
                conn.close()
        finally:
            db.DB_PATH = previous_db_path


def test_publish_worker_releases_claimed_job_if_disabled_before_handler():
    """A toggle flipped after claim must still stop the publish handler from running."""
    from acp.core import jobs, system_settings

    calls = []
    previous_handler = jobs._handlers.get("PUBLISH_POST")
    original_claim = jobs.claim

    @jobs.handler("PUBLISH_POST")
    def publish_handler(conn, payload, ctx):
        calls.append(payload["post_id"])

    def claim_then_disable(conn, *args, **kwargs):
        claimed = original_claim(conn, *args, **kwargs)
        system_settings.set_system_setting(conn, "publish_worker_enabled", "0")
        return claimed

    with tempfile.TemporaryDirectory() as directory:
        previous_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(directory, "worker-race.db")
        try:
            db.init_db()
            conn = db.connect()
            try:
                system_settings.set_system_setting(conn, "publish_worker_enabled", "1")
                job_id = jobs.enqueue(conn, "PUBLISH_POST", {"post_id": "post-race"})
                jobs.claim = claim_then_disable

                stats = jobs.run_once(conn, limit=1, ctx={})

                job = conn.execute(
                    "SELECT status, attempt_count, locked_at, locked_by FROM job_queue WHERE id=?", (job_id,)
                ).fetchone()
                assert dict(job) == {
                    "status": "READY", "attempt_count": 0, "locked_at": None, "locked_by": None,
                }
                assert calls == []
                assert stats["skipped"] == 1
            finally:
                conn.close()
        finally:
            db.DB_PATH = previous_db_path
            jobs.claim = original_claim
            if previous_handler is None:
                jobs._handlers.pop("PUBLISH_POST", None)
            else:
                jobs._handlers["PUBLISH_POST"] = previous_handler


def main():
    groups = {"docs": [test_env_example_has_required_safe_defaults,
                        test_catalog_schedule_docs_source_active_release_env],
              "migration": [test_product_catalog_migration_is_idempotent,
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
                         test_create_product_link_uses_v2_provider_body_and_keeps_full_short_urls_separate,
                         test_create_product_only_link_uses_explicit_product_sub1,
                         test_legacy_tiktok_source_forwards_campaign_and_configurable_link_path,
                         test_client_marks_only_explicitly_unsupported_commission_sort,
                         test_legacy_tiktok_source_keeps_its_existing_fractional_rate_contract,
                         test_factory_context_exposes_product_client],
              "service": [test_sync_paginates_and_upserts_without_duplicate,
                          test_recommendation_excludes_stockout_unavailable_and_cooldown,
                          test_recommendation_excludes_products_with_active_sales_posts,
                          test_sync_recovers_only_eligible_unavailable_rows_and_preserves_failed_errors,
                          test_sync_counts_malformed_rows_and_continues_later_rows_and_pages,
                          test_sync_propagates_database_failures_instead_of_counting_them_as_bad_rows,
                          test_catalog_rows_never_enter_legacy_scoring_or_generate_content,
                          test_recommendation_default_cooldown_is_seven_days,
                          test_local_search_filters_and_sorts_catalog_rows,
                          test_newest_sort_uses_first_seen_not_last_sync_refresh,
                          test_product_filters_parse_catalog_request_values,
                          test_sync_retries_recommended_once_when_commission_sort_is_rejected,
                          test_sync_does_not_fallback_commission_sort_on_auth_network_or_server_errors,
                          test_sync_lock_rejects_fresh_lock_and_releases_after_error,
                          test_stale_lock_owner_cannot_release_new_owner_lease],
              "pipeline": [test_catalog_product_creates_fresh_per_post_link_and_pending_review,
                           test_catalog_post_materializes_product_image_before_creating_a_link,
                           test_catalog_link_failure_stops_before_caption_or_post_generation,
                           test_catalog_stockout_sets_unavailable_without_requesting_a_link,
                           test_catalog_link_state_is_creating_during_request_and_records_timestamp,
                           test_catalog_post_uses_full_link_when_short_link_is_absent,
                           test_catalog_product_publish_updates_post_metadata_once_after_success,
                           test_catalog_product_publish_failure_leaves_post_metadata_unchanged],
              "e2e": [test_end_to_end_catalog_product_to_review_and_repost_cooldown],
              "cli": [test_product_sync_command_runs_catalog_sync_and_prints_summary,
                      test_product_sync_auto_prepare_requires_flag_and_environment,
                      test_product_sync_skip_and_errors_have_cron_safe_exit_codes,
                      test_product_sync_returns_nonzero_without_leaking_provider_errors,
                      test_product_sync_uses_seed_catalog_in_mock_mode,
                      test_product_sync_initializes_its_catalog_schema_before_syncing,
                      test_mock_auto_prepare_uses_mock_client_and_keeps_post_pending_review,
                      test_auto_prepare_failure_is_redacted_and_returns_nonzero],
              "web": [test_products_page_is_local_and_renders_filters,
                      test_catalog_routes_require_csrf_and_hide_api_errors,
                      test_catalog_publish_error_is_redacted_from_redirect_and_page,
                      test_catalog_omits_unsafe_provider_urls_from_html,
                      test_catalog_create_post_logs_only_safe_diagnostic_context,
                      test_catalog_sync_and_standalone_link_logs_never_contain_provider_secrets,
                      test_catalog_sync_redirects_with_operator_summary,
                      test_catalog_standalone_link_uses_product_marker],
              "worker": [test_system_setting_schema_is_idempotent_and_unique,
                         test_publish_worker_setting_defaults_persists_and_audits,
                         test_disabled_publish_worker_keeps_publish_ready_and_runs_other_jobs,
                         test_enabled_publish_worker_executes_due_publish_job,
                         test_system_setting_audit_redacts_non_toggle_value,
                         test_publish_worker_releases_claimed_job_if_disabled_before_handler]}
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
