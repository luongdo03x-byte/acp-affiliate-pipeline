# Shopee Product Intelligence Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm metadata cache theo canonical Shopee identity, canonical Product upsert ổn định, price-history có source và action làm mới giá mà không crawler/headless hoặc làm thay đổi review/publish semantics.

**Architecture:** Tái sử dụng bảng `product`, unique `(source, merchant, external_product_id)`, `pipeline.upsert_one()` và `product_price_history` hiện có. Thêm một module `core/shopee_products.py` làm boundary riêng cho cache/freshness/price observation; schema chỉ mở rộng idempotent. Luồng manual Shopee resolve ưu tiên server metadata mới → cache còn hạn → manual/helper, còn create/upsert chỉ xảy ra sau operator confirmation.

**Tech Stack:** Python 3, Flask 3, SQLite, Jinja2, stdlib + dependencies hiện có; không thêm crawler/browser automation/frontend framework.

## Global Constraints

- Base branch: `feat/shopee-metadata-helper`; implementation branch: `feat/shopee-product-intel`.
- Shopee Direct và ACCESSTRADE giữ độc lập; không wrap direct link qua ACCESSTRADE.
- Không tạo Product giả khi chưa có name + current_price > 0 + image URL đã được operator/helper/server xác nhận.
- Canonical identity là `(shop_id, item_id)`/item identity từ canonical Shopee URL, không phải affiliate URL.
- Một canonical Shopee item dùng một Product row; nhiều post vẫn được phép.
- Cache phải có source + observed timestamp; UI không gọi dữ liệu cache là realtime.
- Manual fallback luôn tồn tại.
- Price history cho Shopee confirmation/refresh chỉ append khi giá thay đổi; không làm thay đổi sampling semantics của catalog feed khác.
- `Làm mới giá` không auto-crawl; helper là ưu tiên user-assisted, server/cache/manual là fallback.
- Không tạo publish job, auto-approve hoặc publish trong Phase 3.
- Schema/migration idempotent, backward-compatible, không xóa dữ liệu legacy.
- Automated tests mock/network-free; không chạm `.env.local`, DB live, runtime `var/`, token hoặc secret.

---

## File map

### Create

- `core/shopee_products.py` — canonical Shopee identity, cache CRUD/freshness, confirmed Product upsert wrapper, price observation/source rules.
- `tests/test_shopee_product_intel.py` — focused SQLite tests cho migration/cache/upsert/history/refresh policy.
- `web/shopee_product_intel.py` — isolated Flask routes/context helpers cho cache-aware resolve và refresh endpoint, registered through `web/__init__.py`.
- `web/static/shopee_product_intel.js` — refresh button/state only; no framework.
- `docs/SHOPEE_PRODUCT_INTELLIGENCE_RUNBOOK.md` — operator semantics/freshness/refresh verification.

### Modify

- `core/db.py` — create `shopee_metadata_cache`; add nullable `source` to `product_price_history`; idempotent migrations/indexes.
- `core/pipeline.py` — let manual Shopee creation pass an explicit metadata source into Shopee-aware upsert without changing generic feed ingestion.
- `web/__init__.py` — compose Phase 3 feature module.
- `web/templates/products.html` — include metadata source/observed timestamp, cache badge and `Làm mới giá`; preserve manual inputs.
- `web/templates/base.html` — load small Phase 3 JS module if needed.
- `tests/test_pilot.py` — regression contracts only.

---

### Task 1: Metadata-cache and price-source schema

**Files:**
- Modify: `core/db.py`
- Create/Test: `tests/test_shopee_product_intel.py`

**Interfaces:**
- Table `shopee_metadata_cache` primary key `(shop_id, item_id)` with fields: `product_id`, `name`, `current_price`, `original_price`, `image_url`, `shop_name`, `source`, `observed_at`, `updated_at`.
- `product_price_history.source TEXT` nullable for backward compatibility.

- [ ] **Step 1: Write failing migration tests**

```python
with tempfile.TemporaryDirectory() as td:
    db.DB_PATH = os.path.join(td, "intel.db")
    db.init_db()
    conn = db.connect()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(shopee_metadata_cache)")}
    self.assertTrue({"shop_id","item_id","source","observed_at"} <= cols)
    pph = {r[1] for r in conn.execute("PRAGMA table_info(product_price_history)")}
    self.assertIn("source", pph)
```

Also call `db.migrate(conn)` twice and assert no duplicate/exception.

- [ ] **Step 2: Run focused test RED**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_product_intel -v
```

Expected: missing cache table / price source column.

- [ ] **Step 3: Implement idempotent schema/migration**

Add `CREATE TABLE IF NOT EXISTS shopee_metadata_cache (...)` to `SCHEMA`; add an index on `product_id`; add migration tuple for `product_price_history.source`. Do not rebuild/drop existing tables.

- [ ] **Step 4: Re-run focused test GREEN**

- [ ] **Step 5: Commit**

```bash
git add core/db.py tests/test_shopee_product_intel.py
git commit -m "feat: add Shopee metadata cache schema"
```

---

### Task 2: Cache service with explicit freshness/source

**Files:**
- Create: `core/shopee_products.py`
- Test: `tests/test_shopee_product_intel.py`
- Reuse: `core/shopee_helper.py`, `adapters/shopee_affiliate.py`

**Interfaces:**
- `ShopeeProductError(ValueError)`
- `ShopeeIdentity(shop_id: str, item_id: str, canonical_url: str)`
- `CachedShopeeMetadata(..., source: str, observed_at: str, is_fresh: bool)`
- `identity_from_url(url: str) -> ShopeeIdentity`
- `put_metadata_cache(conn, product_url: str, metadata: ProductMetadata, source: str, *, product_id: str|None=None, observed_at: str|None=None) -> CachedShopeeMetadata`
- `get_metadata_cache(conn, product_url: str, *, max_age_seconds: int=86400, now_dt=None) -> CachedShopeeMetadata|None`

Allowed cache sources: `server`, `helper`, `manual`. Cache only stores usable values after validation; it may be partial but must never fabricate missing fields.

- [ ] **Step 1: Write failing cache tests** covering canonical slug/product URL equivalence, source preservation, overwrite/upsert, fresh/stale at 86400 seconds, partial metadata, invalid source rejection.
- [ ] **Step 2: Run RED** on missing module.
- [ ] **Step 3: Implement minimal cache service** using `canonical_helper_product()` for identity and SQLite UPSERT on `(shop_id,item_id)`.
- [ ] **Step 4: Run GREEN**.
- [ ] **Step 5: Commit** `feat: cache confirmed Shopee metadata`.

---

### Task 3: Canonical confirmed-product upsert and Shopee price history

**Files:**
- Modify: `core/shopee_products.py`
- Modify: `core/pipeline.py`
- Test: `tests/test_shopee_product_intel.py`

**Interfaces:**
- `upsert_confirmed_product(conn, source, raw, *, metadata_source: str, observed_at: str|None=None) -> str`
- `record_price_observation(conn, product_id: str, price: int, *, source: str, observed_at: str|None=None, previous_price: int|None=None) -> bool`
- `pipeline.create_post_from_manual_affiliate_product(..., metadata_source: str="manual", ...)`

Rules:
- lookup by existing manual Shopee natural key (`source.name`, `merchant`, `external_product_id`);
- same item returns same `product.id`;
- create/update requires existing confirmed raw validation path;
- write `product_price_history` on insert and on changed price only for this Shopee-aware path;
- history row includes source;
- do not change `ingest_datafeed()` sampling/history behavior for other providers;
- after confirmed upsert, link cache row to `product_id` and cache confirmed metadata/source.

- [ ] **Step 1: Write failing tests**: two confirmations same item => one Product; changed price => second history row; unchanged price => no extra history; source `helper/manual/server` preserved; multiple post creation still allowed by existing state machine.
- [ ] **Step 2: Run RED**.
- [ ] **Step 3: Implement Shopee-aware upsert wrapper and wire manual create path**.
- [ ] **Step 4: Run focused + pipeline tests**.
- [ ] **Step 5: Commit** `feat: upsert canonical Shopee products`.

---

### Task 4: Cache-aware metadata resolution without Product creation

**Files:**
- Create/Modify: `web/shopee_product_intel.py`
- Modify: `web/__init__.py`
- Test: `tests/test_shopee_product_intel.py`
- Regression: `tests/test_pilot.py`

**Interfaces:**
- Existing `/sanpham/affiliate/resolve` remains the operator route.
- New composition layer may intercept/augment rendering but must not create a Product/Post.
- Resolution policy:

```text
server metadata with usable fields -> display source=server and cache it
server blocked/empty -> fresh cache -> display source=cache:<original_source>
stale/no cache -> existing BROWSER_HELPER_REQUIRED/manual flow
```

Do not suppress server metadata with stale cache. Never label cache as realtime.

- [ ] **Step 1: Add route/service tests** for fresh cache fallback, stale cache not treated fresh, resolve alone creates zero Product/Post rows.
- [ ] **Step 2: Run RED**.
- [ ] **Step 3: Implement smallest composition hook/context helper** following existing `web/__init__.py` feature pattern; avoid duplicating entire `server.py` route.
- [ ] **Step 4: Run focused + pilot regressions**.
- [ ] **Step 5: Commit** `feat: reuse cached Shopee metadata on resolve`.

---

### Task 5: Helper/manual cache updates and `Làm mới giá`

**Files:**
- Modify: `web/shopee_product_intel.py`
- Modify: `web/templates/products.html`
- Create: `web/static/shopee_product_intel.js`
- Modify: `web/templates/base.html`
- Test: `tests/test_shopee_product_intel.py`

**Interfaces:**
- `POST /sanpham/affiliate/refresh-price`
- Fields: `_csrf`, `product_url`.
- Response JSON states: `helper_required`, `cache`, `manual_required`; if a fresh server value is safely obtainable by existing resolver in controlled request, state may be `server`.
- This action never publishes, never auto-crawls, and never creates a product before confirmation.

UI shows:
- `Nguồn metadata: server/helper/manual/cache`
- `Cập nhật lúc: <timestamp>`
- `Dữ liệu cache — không phải realtime` when applicable
- button `Làm mới giá`
- existing manual price input remains editable

Helper success/manual confirmation should update cache on the confirmed/create path, not merely on arbitrary browser submit.

- [ ] **Step 1: Add failing UI/route contract tests**.
- [ ] **Step 2: Run RED**.
- [ ] **Step 3: Implement refresh decision endpoint + lightweight JS**; helper-required response directs operator to existing Phase 2 button instead of starting automation.
- [ ] **Step 4: Run focused/UI/pilot tests**.
- [ ] **Step 5: Commit** `feat: add Shopee price refresh workflow`.

---

### Task 6: Docs, regression and stacked PR

**Files:**
- Create: `docs/SHOPEE_PRODUCT_INTELLIGENCE_RUNBOOK.md`
- Test all Phase 3 touched paths.

- [ ] **Step 1: Document** cache TTL/source semantics, canonical identity, changed-price history, refresh priority, manual fallback, no crawler/no publish.
- [ ] **Step 2: Run**:

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_product_intel -v
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_helper -v
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_helper_ui -v
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_pipeline
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_pilot
python tests/test_manage.py
./manage.sh test
git diff --check
```

- [ ] **Step 3: Secret/runtime scan**; no `.env.local`, DB, `var/`, cache or token files.
- [ ] **Step 4: Create Draft stacked PR** base `feat/shopee-metadata-helper`, head `feat/shopee-product-intel`, with exact verification results/blockers.

## Phase 3 Acceptance Criteria

- [ ] Cache keyed by canonical Shopee item identity and carries source/timestamp.
- [ ] Fresh cache can reduce repeated Shopee fetch dependency; stale cache is visibly stale/not realtime.
- [ ] Same confirmed Shopee item reuses one Product row.
- [ ] No Product created from unresolved/insufficient metadata.
- [ ] Shopee confirmation/refresh history writes on price change with source; other provider ingestion semantics remain unchanged.
- [ ] `Làm mới giá` is operator-triggered, no crawler/headless automation.
- [ ] Helper/cache/manual fallback remain available.
- [ ] No publish job before approval.
- [ ] Required focused/regression/release gates are reported truthfully.
