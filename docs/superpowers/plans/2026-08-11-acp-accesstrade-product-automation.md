# ACP ACCESSTRADE Product Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Đồng bộ catalog TikTok Shop từ ACCESSTRADE, tạo bài từ Product DB với affiliate link theo từng post, và dừng tại PENDING_REVIEW.

**Architecture:** Mở rộng bảng product hiện có thành catalog trung tâm. AccessTradeClient cô lập HTTP Product Search V2/Create Link V2; ProductService quản lý parse, upsert, ranking, sync và candidate. Content pipeline hiện có tiếp tục tạo post, nhưng lấy Product đã lưu và tạo affiliate link có sub1=post_id trước khi sinh caption.

**Tech Stack:** Python 3, Flask server-rendered templates, SQLite, requests, custom test runners (tests/test_pipeline.py, tests/test_pilot.py), manage.sh mock-mode verification.

## Global Constraints

- Không hard-code, expose frontend, commit, hoặc log token ACCESSTRADE.
- Không thay đổi adapter live/publishing hay tự publish Threads; mọi post mới dừng ở PENDING_REVIEW.
- Không fallback detail_link nếu Create Link thất bại.
- Giữ semantics attribution: sub1 là post_id; utm_content là external_product_id.
- Product sync không bulk-create affiliate link.
- Dùng product hiện hữu, migration idempotent, không xoá dữ liệu cũ.
- Tests luôn mock; chỉ dùng ./manage.sh test để xác minh release.
- Không chạm thay đổi đang có trong core/content.py.

---

## File structure

| File | Responsibility |
|---|---|
| adapters/accesstrade_client.py | HTTP API V2, retry, response/error mapping |
| adapters/tiktokshop.py | Compatibility ContentSource adapter delegating HTTP calls to client |
| adapters/factory.py | Supplies the same Product API client to CLI, web, and pipeline contexts |
| core/products.py | Parsing, upsert, sync lock, ranking, query/filter/recommendation, link state |
| core/db.py | Additive migration/indexes for product catalog and sync lock |
| core/pipeline.py | Generate content from stored product; create per-post affiliate link; update posting metadata |
| run.py | product-sync CLI command for cron/systemd |
| web/server.py | Safe sanpham catalog routes and POST actions |
| web/templates/products.html | Catalog filters, sync state, product cards/actions, preserving Shopee-direct tab |
| web/static/acp.css | Responsive catalog cards/stats/badges |
| tests/test_product_automation.py | Isolated client/service/pipeline/web regression runner |
| tests/fixtures/accesstrade_*.json | Sanitized Product Search/Create Link fixtures |
| .env.example, README.md, docs/ACP_RUNBOOK.md | Configuration and operator documentation |

### Task 1: Add additive schema migration and catalog model contract

**Files:**
- Modify: core/db.py
- Create: tests/test_product_automation.py

**Consumes:** existing product table and db.init_db() migration mechanism.

**Produces:** Product catalog columns: provider, shop_name, detail_link, main_image_url, sale_region, currency, price_min, price_max, original_price_min, original_price_max, commission_rate_raw, commission_rate_percent, commission_amount, commission_currency, units_sold, has_inventory, category_data, score, affiliate_url, affiliate_short_url, affiliate_link_status, affiliate_link_error, first_seen_at, last_seen_at, last_synced_at, affiliate_link_created_at, last_posted_at, post_count; table product_sync_lock; unique index idx_product_provider_external.

- [ ] **Step 1: Write the failing migration tests**

    def test_product_catalog_migration_is_idempotent():
        db.init_db(); db.init_db()
        conn = db.connect()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(product)")}
        assert {"provider", "commission_rate_raw", "affiliate_link_status", "post_count"} <= columns
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(product)")}
        assert "idx_product_provider_external" in indexes

    def test_migration_preserves_existing_product_and_backfills_provider():
        # Open an old-schema fixture database, run init_db, then retain the row and post.
        assert row["provider"] and row["first_seen_at"]

- [ ] **Step 2: Run test to verify it fails**

Run: python3 tests/test_product_automation.py migration

Expected: FAIL because the new columns/index do not exist.

- [ ] **Step 3: Write minimal migration implementation**

    PRODUCT_MIGRATIONS = [
        ("product", "provider", "ALTER TABLE product ADD COLUMN provider TEXT"),
        ("product", "commission_rate_raw", "ALTER TABLE product ADD COLUMN commission_rate_raw INTEGER"),
        ("product", "affiliate_link_status",
         "ALTER TABLE product ADD COLUMN affiliate_link_status TEXT NOT NULL DEFAULT 'NOT_CREATED'"),
        ("product", "post_count", "ALTER TABLE product ADD COLUMN post_count INTEGER NOT NULL DEFAULT 0"),
    ]
    conn.execute("UPDATE product SET provider=COALESCE(provider, 'LEGACY_' || source)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_product_provider_external "
                 "ON product(provider, external_product_id)")

Add every listed catalog column in the migration loop; initialize first_seen_at from created_at. Create product_sync_lock(name TEXT PRIMARY KEY, locked_at TEXT NOT NULL). Do not rebuild, delete, or alter existing post/conversion data.

- [ ] **Step 4: Run test to verify it passes**

Run: python3 tests/test_product_automation.py migration && git diff --check

Expected: PASS; two init_db calls create no duplicate columns/indexes.

- [ ] **Step 5: Commit**

    git add core/db.py tests/test_product_automation.py
    git commit -m "feat: extend product catalog schema"

### Task 2: Implement resilient ACCESSTRADE V2 client and raw-product parser

**Files:**
- Create: adapters/accesstrade_client.py
- Modify: adapters/tiktokshop.py
- Modify: adapters/factory.py
- Modify: tests/test_product_automation.py
- Create: tests/fixtures/accesstrade_product_search_v2.json

**Consumes:** requests.Session, env ACCESSTRADE_API_BASE_URL and ACCESSTRADE_API_TOKEN, existing RateLimitError and PublishError.

**Produces:** AccessTradeClient.search_products(...) returning tuple[list[dict], str|None]; create_product_link(detail_link, post_id, external_product_id) returning LinkResult; normalize_accesstrade_product(raw); factory.build_context() with product_client key.

- [ ] **Step 1: Write failing client/parser tests**

    def test_parser_keeps_missing_units_sold_as_none():
        product = normalize_accesstrade_product(
            {"id": "p1", "title": "A", "sales_price": {"minimum_amount": 1}})
        assert product.units_sold is None

    def test_commission_raw_is_scaled_correctly():
        assert normalize_commission_rate_percent(1000) == 10.0
        assert normalize_commission_rate_percent(3587) == 35.87

    def test_client_retries_429_then_returns_page(fake_session):
        products, token = AccessTradeClient(session=fake_session).search_products(limit=50)
        assert len(products) == 2 and token == "NEXT"
        assert fake_session.calls == 2

    def test_client_status_false_and_401_are_domain_errors(fake_session):
        assert_error("Token ACCESSTRADE không hợp lệ",
                     lambda: AccessTradeClient(session=fake_session).search_products())

- [ ] **Step 2: Run test to verify it fails**

Run: python3 tests/test_product_automation.py client

Expected: FAIL because client/parser imports are absent.

- [ ] **Step 3: Write minimal client implementation**

    class AccessTradeClient:
        PRODUCT_FEED_PATH = "/v2/tiktokshop_product_feeds"
        CREATE_LINK_PATH = "/v2/tiktokshop_product_feeds/create_link"

        def search_products(self, *, sort_field="RECOMMENDED", limit=50,
                            title_keywords=None, page_token=None, product_ids=None):
            params = {"sort_field": sort_field, "limit": min(int(limit), 50)}
            if title_keywords: params["title_keywords"] = title_keywords
            if page_token: params["page_token"] = page_token
            if product_ids: params["product_ids"] = ",".join(map(str, product_ids))
            payload = self._request("GET", self.PRODUCT_FEED_PATH, params=params)
            data = payload.get("data") or {}
            return data.get("products") or [], data.get("next_page_token")

Use Authorization: Token only on Session headers. _request uses connect/read timeouts (5, 20), retries exactly twice after 1s/2s only for timeout, 429, 500, 502, 503, 504, and maps 401, 429, network failure, invalid JSON, and status:false into redacted domain errors. create_product_link sends utm_source=threads, utm_medium=social, utm_campaign=acp, utm_content=external_product_id, sub1=post_id, and returns full/short URLs separately.

- [ ] **Step 4: Delegate old TikTok adapter to client**

Replace direct Session calls in AccessTradeTikTokShopSource with the client. Preserve its ContentSource methods for existing CLI/search tests. ProductService, not the adapter, counts malformed product rows.

- [ ] **Step 5: Add client to the shared factory context**

    def build_context(source_name=None):
        return {
            "source": get_source(source_name),
            "product_client": AccessTradeClient.from_env(),
            "channel": get_channel(),
            "storage": storage.get_storage(),
        }

Update factory tests to assert product_client is present. Do not instantiate a client in a route or template.

- [ ] **Step 6: Run test to verify it passes**

Run: python3 tests/test_product_automation.py client && python3 tests/test_pilot.py

Expected: PASS including empty page, 401, 429, 500, timeout, invalid JSON, status:false, missing shop/image/price/commission.

- [ ] **Step 7: Commit**

    git add adapters/accesstrade_client.py adapters/tiktokshop.py tests
    git commit -m "feat: add accesstrade product API client"

### Task 3: Build ProductService upsert, pagination, ranking and recommendation

**Files:**
- Create: core/products.py
- Modify: tests/test_product_automation.py

**Consumes:** db.now, db.ulid, transaction, AccessTradeClient, catalog columns from Task 1.

**Produces:** ProductService.sync(...), search_local(filters), recommended(limit), get(product_id), ProductFilters and SyncResult.

- [ ] **Step 1: Write failing service tests**

    def test_sync_paginates_and_upserts_without_duplicate(fake_client, conn):
        result = ProductService(conn, fake_client).sync(max_pages=10)
        assert (result.fetched, result.inserted, result.updated) == (3, 2, 1)
        assert conn.execute(
            "SELECT COUNT(*) FROM product WHERE provider='ACCESSTRADE_TIKTOK'").fetchone()[0] == 2
        assert first_seen_before == first_seen_after

    def test_recommendation_excludes_stockout_unavailable_and_cooldown(conn):
        ids = [r["id"] for r in ProductService(conn, fake_client).recommended(20)]
        assert ids == [eligible_id]

- [ ] **Step 2: Run test to verify it fails**

Run: python3 tests/test_product_automation.py service

Expected: FAIL because core.products does not exist.

- [ ] **Step 3: Write minimal sync/upsert implementation**

    def sync(self, *, title_keywords=None, sort_field="RECOMMENDED", max_pages=None):
        max_pages = max_pages or env_int("ACP_PRODUCT_SYNC_MAX_PAGES", 10)
        token = None
        result = SyncResult()
        for page in range(max_pages):
            rows, token = self.client.search_products(
                sort_field=sort_field, limit=50, title_keywords=title_keywords, page_token=token)
            result.pages += 1
            result.fetched += len(rows)
            for raw in rows:
                self._upsert(normalize_accesstrade_product(raw), result)
            if not token:
                break
        self.recalculate_scores()
        return result

Use provider ACCESSTRADE_TIKTOK. Preserve first_seen_at; update price/commission/last_seen_at/last_synced_at. Store absent units_sold as NULL. If commission sort is rejected, make a single RECOMMENDED retry and record SyncResult.warning. Never mark other providers unavailable.

Implement all settings in this module so later tasks use one contract:

    def env_int(name, default):
        try:
            return int(os.environ.get(name, default))
        except ValueError:
            return default

    def env_bool(name, default=False):
        return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")

- [ ] **Step 4: Implement ranking/query contract**

    score = sales_percentile * 45 + commission_rate_percentile * 35 + commission_amount_percentile * 20

    WHERE has_inventory=1
      AND detail_link <> ''
      AND external_product_id <> ''
      AND affiliate_link_status <> 'UNAVAILABLE'
      AND (last_posted_at IS NULL OR last_posted_at < :cooldown)
    ORDER BY score DESC, last_seen_at DESC
    LIMIT :limit

Use percentiles among eligible catalog values, persist scores 0 through 100, and parameterize local filters: title/shop keyword, inventory, commission/price/units thresholds, affiliate state, post state, and sorts recommended, sold, commission, price_asc, price_desc, newest, score.

- [ ] **Step 5: Add lock and test concurrent behavior**

Acquire product_sync_lock for accesstrade_tiktok within BEGIN IMMEDIATE. Raise SyncAlreadyRunning when a fresh lock exists; always release in finally. Test the second service call returns a friendly busy error and the first releases lock after error.

- [ ] **Step 6: Run test to verify it passes**

Run: python3 tests/test_product_automation.py service

Expected: PASS for new/update/no-duplicate, pages A/B/null, null handling, scores, filters, fallback and lock.

- [ ] **Step 7: Commit**

    git add core/products.py tests/test_product_automation.py
    git commit -m "feat: sync and rank accesstrade products"

### Task 4: Integrate catalog product content generation and post lifecycle

**Files:**
- Modify: core/pipeline.py
- Modify: core/jobs.py only if publish handler needs ProductService
- Modify: tests/test_product_automation.py

**Consumes:** ProductService.get, AccessTradeClient.create_product_link, existing image/content validation.

**Produces:** create_post_for_catalog_product(conn, ctx, product_id, campaign_code, channel_code=None) and mark_product_posted(conn, product_id, published_at).

- [ ] **Step 1: Write failing pipeline tests**

    def test_catalog_product_creates_per_post_link_and_pending_review(conn, ctx):
        result = pipeline.create_post_for_catalog_product(conn, ctx, product_id, "gd2026", "ch1")
        post = conn.execute("SELECT * FROM post WHERE id=?", (result["post_id"],)).fetchone()
        assert result["status"] == "PENDING_REVIEW"
        assert post["affiliate_link"] == "https://short.example/post"
        assert product_detail_link not in post["caption_final"]
        assert ctx["product_client"].last_body["sub1"] == post["id"]

    def test_link_failure_stops_before_caption_generation(conn, failing_ctx):
        result = pipeline.create_post_for_catalog_product(conn, failing_ctx, product_id, "gd2026")
        assert not result["ok"]
        assert conn.execute("SELECT COUNT(*) FROM post").fetchone()[0] == 0

- [ ] **Step 2: Run test to verify it fails**

Run: python3 tests/test_product_automation.py pipeline

Expected: FAIL because catalog pipeline entry point is absent.

- [ ] **Step 3: Write guarded per-post link flow**

    def create_post_for_catalog_product(conn, ctx, product_id, campaign_code, channel_code=None):
        product = ProductService(conn, ctx["product_client"]).get(product_id)
        if not product or not product["has_inventory"] or not product["detail_link"]:
            return {"ok": False, "error": "Sản phẩm không đủ điều kiện tạo nội dung"}
        post_id = ulid()
        link = ctx["product_client"].create_product_link(
            product["detail_link"], post_id=post_id, external_product_id=product["external_product_id"])
        return _create_post_from_catalog_product(
            conn, ctx, product, post_id, link, campaign_code, channel_code)

Set product CREATING then READY, FAILED, or UNAVAILABLE with redacted error/timestamps. Persist latest full/short link for display, but persist the post-specific URL in post.affiliate_link. Caption receives short URL if present else full URL. Do not enqueue PUBLISH_POST.

The separate Product-card affiliate-link action calls `create_product_link` with
`post_id="product:" + external_product_id` solely for operator copy. Mark that
stored link `product-only`; it must never flow into `_create_post_from_catalog_product`
or `post.affiliate_link`. Add a regression test proving content creation calls
the API again with the newly allocated real post ID.

- [ ] **Step 4: Update successful publish metadata and add tests**

After current channel publish succeeds and marks a post published, execute:

    UPDATE product
    SET last_posted_at=?, post_count=post_count+1, updated_at=?
    WHERE id=?

Test failed publishing leaves both product fields unchanged and existing idempotency protection still passes.

- [ ] **Step 5: Run test to verify it passes**

Run: python3 tests/test_product_automation.py pipeline && python3 tests/test_pipeline.py && python3 tests/test_pilot.py

Expected: PASS; old single-product and manual Shopee flows remain unchanged.

- [ ] **Step 6: Commit**

    git add core/pipeline.py core/jobs.py tests/test_product_automation.py
    git commit -m "feat: create review posts from catalog products"

### Task 5: Add cron-safe CLI sync and optional auto-prepare

**Files:**
- Modify: run.py
- Modify: tests/test_product_automation.py

**Consumes:** ProductService.sync, ProductService.recommended, Task 4 pipeline entry point.

**Produces:** python3 run.py product-sync [keyword] and python3 run.py product-sync --auto-prepare.

- [ ] **Step 1: Write failing CLI tests**

    def test_product_sync_command_uses_product_service(monkeypatch, capsys):
        assert run.main(["product-sync"]) == 0
        assert "Fetched: 2" in capsys.readouterr().out

    def test_auto_prepare_is_disabled_unless_env_true(monkeypatch):
        monkeypatch.delenv("ACP_AUTO_PREPARE_CONTENT", raising=False)
        run.cmd_product_sync(auto_prepare=True)
        assert fake_pipeline.calls == []

- [ ] **Step 2: Run test to verify it fails**

Run: python3 tests/test_product_automation.py cli

Expected: FAIL because the command is unrecognized.

- [ ] **Step 3: Write minimal CLI implementation**

    def cmd_product_sync(keyword=None, auto_prepare=False):
        with db.session() as conn:
            service = ProductService(conn, AccessTradeClient.from_env())
            result = service.sync(title_keywords=keyword)
            print(result.operator_summary())
            if auto_prepare and env_bool("ACP_AUTO_PREPARE_CONTENT", False):
                for product in service.recommended(env_int("ACP_AUTO_PREPARE_CONTENT_COUNT", 3)):
                    pipeline.create_post_for_catalog_product(
                        conn, factory.build_context(), product["id"], CAMPAIGN_CODE)

Register product-sync in usage and dispatch. Scheduled calls skip when ACP_PRODUCT_SYNC_ENABLED=false. Return non-zero for auth/network/busy errors. Never create content unless both CLI flag and env are true.

- [ ] **Step 4: Run test to verify it passes**

Run: python3 tests/test_product_automation.py cli && ACP_ADAPTER=mock ACP_SOURCE=mock python3 run.py product-sync

Expected: PASS; output lists fetched/new/updated/skipped/failed and no credentials.

- [ ] **Step 5: Commit**

    git add run.py tests/test_product_automation.py
    git commit -m "feat: add catalog sync CLI"

### Task 6: Replace live product search UI with Product DB workspace

**Files:**
- Modify: web/server.py
- Modify: web/templates/products.html
- Modify: web/static/acp.css
- Modify: tests/test_product_automation.py

**Consumes:** ProductService, existing CSRF/auth/error rendering, current Shopee-direct mode.

**Produces:** local catalog GET /sanpham, POST /sanpham/sync, POST /sanpham/<product_id>/affiliate-link, POST /sanpham/<product_id>/tao-bai.

- [ ] **Step 1: Write failing route/UI tests**

    def test_products_page_is_local_and_renders_filters(client, seeded_catalog):
        response = client.get("/sanpham?q=váy&sort=score&inventory=1")
        assert response.status_code == 200
        assert "Đồng bộ sản phẩm" in response.text and "ACP Score" in response.text
        assert "Váy test" in response.text

    def test_sync_and_generate_routes_require_csrf_and_hide_api_errors(client):
        assert client.post("/sanpham/sync").status_code == 400
        response = post_with_csrf(client, "/sanpham/%s/tao-bai" % product_id)
        assert response.status_code == 302 and "/duyet" in response.headers["Location"]

- [ ] **Step 2: Run test to verify it fails**

Run: python3 tests/test_product_automation.py web

Expected: FAIL because catalog routes/context do not exist.

- [ ] **Step 3: Implement safe route layer**

    @app.route("/sanpham/sync", methods=["POST"])
    def sync_products():
        try:
            result = ProductService(connect(), AccessTradeClient.from_env()).sync(
                title_keywords=request.form.get("q") or None)
        except ProductUserError as exc:
            return redirect(url_for("products", err=exc.user_message))
        return redirect(url_for("products", synced=result.operator_summary()))

Use parameterized ProductFilters.from_request. Map domain failures to the specified friendly Vietnamese text. Unexpected exceptions are logged server-side and render only “Không thể tiếp tục. Vui lòng thử lại.” Keep Shopee-direct routes and template block untouched.

- [ ] **Step 4: Render catalog controls and cards**

Add sync summary (last sync/count/in stock/ready), keyword plus “Tìm từ ACCESSTRADE”, filters, sort select, cards with escaped title/shop/image, null-safe em dash, price/commission/sold/score/badges, product detail anchor with rel=noopener, copy button, and create-content form. Disable sync only during form submission; server lock stays authoritative. Scope CSS under catalog-*.

- [ ] **Step 5: Run test to verify it passes**

Run: python3 tests/test_product_automation.py web && python3 tests/test_pilot.py

Expected: PASS; no traceback, Authorization value, or API token appears in page; existing Shopee-direct tab still works.

- [ ] **Step 6: Commit**

    git add web/server.py web/templates/products.html web/static/acp.css tests/test_product_automation.py
    git commit -m "feat: add product catalog workspace"

### Task 7: Add configuration, operator docs and release verification

**Files:**
- Create or Modify: .env.example
- Modify: README.md
- Modify: docs/ACP_RUNBOOK.md
- Modify: tests/test_product_automation.py

**Consumes:** every public CLI/route/env introduced above.

**Produces:** documented setup and safe end-to-end acceptance coverage.

- [ ] **Step 1: Write failing settings/documentation assertion**

    def test_env_example_has_required_safe_defaults():
        text = Path(".env.example").read_text()
        assert "ACCESSTRADE_API_TOKEN=" in text
        assert "ACP_PRODUCT_SYNC_MAX_PAGES=10" in text
        assert "ACP_AUTO_PREPARE_CONTENT=false" in text
        assert "REDACTED" not in text

- [ ] **Step 2: Run test to verify it fails**

Run: python3 tests/test_product_automation.py docs

Expected: FAIL until configuration/doc sections are present.

- [ ] **Step 3: Add safe configuration and workflow**

    ACCESSTRADE_API_BASE_URL=https://api.accesstrade.vn
    ACCESSTRADE_API_TOKEN=
    ACP_PRODUCT_SYNC_ENABLED=true
    ACP_PRODUCT_SYNC_INTERVAL_MINUTES=60
    ACP_PRODUCT_SYNC_MAX_PAGES=10
    ACP_PRODUCT_REPOST_COOLDOWN_DAYS=7
    ACP_PRODUCT_RECOMMENDATION_LIMIT=20
    ACP_AUTO_PREPARE_CONTENT=false
    ACP_AUTO_PREPARE_CONTENT_COUNT=3

Document python3 run.py product-sync, cron/systemd timer every 60 minutes, manual sync on /sanpham, 401/429/unavailable troubleshooting, and mock end-to-end: sync -> catalog row -> generate -> stored short link -> PENDING_REVIEW -> repeat sync has one row -> simulated publish updates cooldown.

- [ ] **Step 4: Run all verification**

Run: python3 tests/test_product_automation.py && python3 tests/test_pipeline.py && python3 tests/test_pilot.py && ./manage.sh test && git diff --check && git status --short

Expected: every command exits 0. A pre-existing failure must be captured separately and never claimed as a feature pass.

- [ ] **Step 5: Review diff and commit**

    git diff -- core/db.py adapters core web run.py tests .env.example README.md docs/ACP_RUNBOOK.md
    git add .env.example README.md docs/ACP_RUNBOOK.md tests/test_product_automation.py
    git commit -m "docs: document accesstrade product automation"

### Task 8: Final acceptance and handoff

**Files:** none unless a test pinpoints a regression.

**Consumes:** all prior tasks.

**Produces:** evidence-based handoff.

- [ ] **Step 1: Execute end-to-end mock acceptance test**

    def test_end_to_end_catalog_product_to_review_and_repost_cooldown():
        sync_fixture_catalog()
        product = catalog_product("1729384756102938475")
        result = create_catalog_post(product)
        assert result["status"] == "PENDING_REVIEW"
        assert post_link_is_short_affiliate(result["post_id"])
        sync_fixture_catalog()
        assert catalog_count(product["external_product_id"]) == 1
        publish_successfully(result["post_id"])
        assert product_is_not_recommended(product["id"])

- [ ] **Step 2: Run acceptance and release suite**

Run: python3 tests/test_product_automation.py e2e && ./manage.sh test

Expected: PASS in mock mode; no real ACCESSTRADE/Threads call and no real publish.

- [ ] **Step 3: Inspect repository state**

Run: git diff --check && git status --short && git log --oneline -8

Expected: only intended commits plus the pre-existing core/content.py change, which remains unstaged and unmodified.

- [ ] **Step 4: Hand off**

Report architecture, every created/modified file, migration behavior, routes/env, exact test outputs, cron command, end-to-end mock command, and the remaining limitation: production API payload/path requires an operator-provided valid token and controlled live verification; automated tests make no live call.
