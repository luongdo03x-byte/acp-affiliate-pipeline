# Shopee Image Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich imported `SHOPEE_AFFILIATE` Products with a validated product image using public Shopee HTML first and the existing operator-assisted Chrome Helper as fallback, while materializing a safe ACP-owned local/public image.

**Architecture:** Add a focused Shopee image-enrichment service that owns job state, public-only metadata lookup, safe deterministic image materialization, non-destructive Product updates, retry limits, and helper completion. Register its schema through `core.shopee_schema`, enqueue missing-image jobs from the CSV importer, and expose a separate `/sanpham/shopee` workspace through an isolated Blueprint. The existing metadata path is hardened so production code no longer calls Shopee `/api/v4/...` endpoints.

**Tech Stack:** Python 3, Flask, SQLite, Pillow, existing `SafeHttpClient`, existing ACP storage abstraction, `unittest`-style repository tests.

**Spec:** `docs/superpowers/specs/2026-08-21-shopee-image-enrichment-design.md`

## Global Constraints

- Only public Shopee product-page HTML and operator-assisted rendered DOM metadata are allowed Shopee metadata sources.
- No Open API requirement, cookie/session/localStorage extraction, automated login, CAPTCHA bypass, anti-bot evasion, private `/api/v4/...` endpoint use, reverse engineering, autonomous unbounded crawling, or automatic publishing.
- Default batch size is 20, concurrency is 1, default inter-request delay is 1.5 seconds.
- Automatic public-fetch attempts per product are capped at 2; automatic image-download attempts are capped at 2.
- CSV-owned current price, sold count, commission fields, affiliate URL, and provider identity are never overwritten by enrichment.
- Image bytes must be downloaded through existing safe HTTP protections and decoded by Pillow before becoming Product media.
- Persist no raw HTML, response bodies, cookies, auth headers, helper pairing tokens, or stack traces.

---

## File Structure

- Create `core/shopee_image_enrichment.py` — service/state machine, public fetch orchestration, deterministic image materialization, Product merge, retries, helper completion, batch selection.
- Modify `adapters/shopee_affiliate.py` — expose/route through public-HTML-only metadata resolution and remove production `/api/v4/...` fallback.
- Modify `core/shopee_schema.py` — register `shopee_image_enrichment_job` table and indexes.
- Modify `core/shopee_csv_import.py` — idempotently enqueue imported/updated missing-image Shopee Products inside the confirmed import transaction.
- Create `web/shopee_image_enrichment.py` — isolated authenticated/CSRF-protected Shopee Product Pool workspace and actions.
- Modify `web/__init__.py` — register the new Blueprint.
- Create `web/templates/shopee_image_enrichment.html` — status/filter/batch UI.
- Modify `web/templates/base.html` — add navigation link to Shopee Product Pool without changing the ACCESSTRADE catalog query.
- Create `tests/test_shopee_public_metadata.py` — public-only resolver regression tests.
- Create `tests/test_shopee_image_enrichment.py` — service, storage, state machine, retry, merge, helper-completion tests.
- Create `tests/test_shopee_image_enrichment_web.py` — workspace/auth/CSRF/filter/batch/action tests.
- Modify `tests/test_shopee_csv_import.py` — importer enqueue regression coverage.
- Create `docs/SHOPEE_IMAGE_ENRICHMENT.md` — operator runbook and verification steps.

---

### Task 1: Make Shopee metadata resolution public-HTML-only

**Files:**
- Modify: `adapters/shopee_affiliate.py`
- Create: `tests/test_shopee_public_metadata.py`

**Interfaces:**
- Produces: `ProductMetadataResolver.resolve_public(product_url: str) -> ProductMetadata`
- Produces: `ProductMetadataResolver.resolve(product_url: str) -> ProductMetadata` as a compatibility alias/entry point that uses public HTML only.
- Existing `ManualShopeeSource.metadata(product_url)` continues to call the resolver and therefore becomes public-only.

- [ ] **Step 1: Write failing tests for JSON-LD/OpenGraph metadata and private-endpoint prohibition**

Create tests using a fake HTTP client that records requested URLs. Assert:

```python
metadata = ProductMetadataResolver(http=fake).resolve("https://shopee.vn/product/1/2")
assert metadata.image_url == "https://down-vn.img.susercontent.com/file/example"
assert all("/api/v4/" not in url for url in fake.urls)
```

Include cases for JSON-LD image, `og:image`, no image, and an HTML request failure.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
python -m acp.tests.test_shopee_public_metadata -v
```

Expected: failure because `resolve_public()` does not exist and current `resolve()` may call `/api/v4/...`.

- [ ] **Step 3: Implement the minimal public-only resolver**

In `ProductMetadataResolver`:

```python
def resolve_public(self, product_url: str) -> ProductMetadata:
    product_url = canonical_product_url(product_url)
    try:
        return self._html_metadata(product_url)
    except (SafeHttpError, OSError) as exc:
        raise AffiliateImportError("Không thể tải thông tin sản phẩm Shopee.") from exc


def resolve(self, product_url: str) -> ProductMetadata:
    return self.resolve_public(product_url)
```

Remove the production call path to `_api_metadata()`. Delete dead `/api/v4/...` helpers if no remaining repository code needs them; otherwise leave them unreachable and cover non-use with the regression test.

- [ ] **Step 4: Run focused and legacy Shopee resolver/helper tests**

Run:

```bash
python -m acp.tests.test_shopee_public_metadata -v
python -m acp.tests.test_shopee_helper -v
python -m acp.tests.test_shopee_product_intel -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add adapters/shopee_affiliate.py tests/test_shopee_public_metadata.py
git commit -m "refactor: use public Shopee metadata only"
```

---

### Task 2: Add enrichment job schema and core state machine

**Files:**
- Modify: `core/shopee_schema.py`
- Create: `core/shopee_image_enrichment.py`
- Create: `tests/test_shopee_image_enrichment.py`

**Interfaces:**
- Produces: `enqueue_product(conn, product_id: str) -> str | None`
- Produces: `backfill_missing(conn, limit: int | None = None) -> int`
- Produces: `get_job(conn, product_id: str) -> dict | None`
- Produces: `list_products(conn, status: str | None = None, limit: int = 100) -> list[dict]`
- Produces stable constants `PENDING`, `PUBLIC_FETCH`, `DOWNLOADING`, `NEEDS_HELPER`, `READY`, `FAILED`.

- [ ] **Step 1: Write RED tests for table registration and idempotent enqueue**

Test that a `SHOPEE_AFFILIATE` Product with no usable image receives one job, repeated enqueue does not duplicate it, non-Shopee Products are rejected/skipped, and an already-imaged Product is `READY` or not re-enqueued.

- [ ] **Step 2: Run the focused test and confirm RED**

```bash
python -m acp.tests.test_shopee_image_enrichment -v
```

Expected: missing table/module/functions.

- [ ] **Step 3: Register the table**

Append through existing `core.shopee_schema.register()`:

```sql
CREATE TABLE IF NOT EXISTS shopee_image_enrichment_job (
    product_id TEXT PRIMARY KEY REFERENCES product(id),
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    download_attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error_code TEXT,
    last_error TEXT,
    last_attempt_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shopee_image_enrichment_status
    ON shopee_image_enrichment_job(status, updated_at);
```

- [ ] **Step 4: Implement identity-safe enqueue/backfill/list primitives**

Only Products matching `provider='SHOPEE_AFFILIATE'` with canonical direct Shopee identity are eligible. Treat nonblank `main_image_url` or a valid local image path as already ready. Use Product ID as the natural job key.

- [ ] **Step 5: Add stale transient recovery tests and implementation**

When `PUBLIC_FETCH`/`DOWNLOADING` is older than 10 minutes, recover to `PENDING` at the next batch selection unless Product media is already usable, in which case set `READY`.

- [ ] **Step 6: Run focused tests**

```bash
python -m acp.tests.test_shopee_image_enrichment -v
```

Expected: PASS for schema/enqueue/backfill/list/recovery cases.

- [ ] **Step 7: Commit**

```bash
git add core/shopee_schema.py core/shopee_image_enrichment.py tests/test_shopee_image_enrichment.py
git commit -m "feat: add Shopee image enrichment jobs"
```

---

### Task 3: Implement safe deterministic image materialization and Product merge

**Files:**
- Modify: `core/shopee_image_enrichment.py`
- Modify: `tests/test_shopee_image_enrichment.py`

**Interfaces:**
- Produces: `materialize_product_image(product_url: str, image_url: str, media_dir: str, storage_backend, http_client=None) -> dict`
- Produces result keys: `image_url_original`, `image_path_local`, `main_image_url`.
- Produces: `merge_metadata_into_product(conn, product_id: str, metadata: ProductMetadata, materialized: dict | None) -> None`.

- [ ] **Step 1: Write RED tests for valid/corrupt/oversized/masquerading images**

Use in-memory JPEG/PNG/WEBP fixtures and fake HTTP responses. Verify decoded format controls extension and that invalid content never creates the deterministic target.

- [ ] **Step 2: Add deterministic filename behavior**

Use canonical identity only:

```text
shopee_<shop_id>_<item_id>.<verified-ext>
```

Write verified bytes to a temporary sibling and `os.replace()` into the final path. Reuse an existing valid deterministic file without another download.

- [ ] **Step 3: Publish through the existing storage abstraction**

After local validation, call `storage_backend.put(local_path)` and use its return value for `main_image_url`. A storage error leaves the verified local file intact but does not mark the job `READY`.

- [ ] **Step 4: Implement non-destructive Product metadata merge**

Only fill blank `image_url_original`, `image_path_local`, `main_image_url`, `name`, `shop_name`, and valid `original_price`. Never overwrite CSV-owned current price, sold count, commission fields, affiliate URL, provider, or nonblank stronger metadata.

- [ ] **Step 5: Run focused tests**

```bash
python -m acp.tests.test_shopee_image_enrichment -v
```

Expected: PASS including deterministic reuse, atomic failure, storage failure, and non-destructive merge.

- [ ] **Step 6: Commit**

```bash
git add core/shopee_image_enrichment.py tests/test_shopee_image_enrichment.py
git commit -m "feat: materialize Shopee product images safely"
```

---

### Task 4: Implement public enrichment, retries, batch execution, and helper completion

**Files:**
- Modify: `core/shopee_image_enrichment.py`
- Modify: `tests/test_shopee_image_enrichment.py`

**Interfaces:**
- Produces: `enrich_product(conn, product_id: str, *, metadata_resolver, media_dir, storage_backend, image_http=None) -> dict`
- Produces: `run_batch(connection_factory, *, limit: int = 20, delay_seconds: float = 1.5, metadata_resolver_factory=None, image_http_factory=None, media_dir=None, storage_backend=None, sleep_fn=time.sleep) -> dict`
- Produces: `complete_from_helper(conn, product_id: str, metadata: dict, *, media_dir, storage_backend, image_http=None) -> dict`.

- [ ] **Step 1: Write RED state-transition tests**

Cover:

```text
PENDING -> PUBLIC_FETCH -> DOWNLOADING -> READY
PENDING -> PUBLIC_FETCH -> NEEDS_HELPER
DOWNLOADING -> retry -> FAILED
wrong helper product / invalid helper metadata -> remain NEEDS_HELPER
helper usable image -> DOWNLOADING -> READY
```

- [ ] **Step 2: Implement bounded error classification**

Use stable codes: `PUBLIC_TIMEOUT`, `PUBLIC_BLOCKED`, `PUBLIC_NO_IMAGE`, `IMAGE_DOWNLOAD_FAILED`, `IMAGE_TOO_LARGE`, `IMAGE_INVALID_CONTENT`, `IMAGE_DECODE_FAILED`, `STORAGE_FAILED`, `PRODUCT_IDENTITY_INVALID`, `HELPER_REQUIRED`.

Persist only bounded safe operator messages.

- [ ] **Step 3: Implement automatic retry budgets**

Increment `attempt_count` only for public metadata attempts and `download_attempt_count` only for image materialization attempts. Do not exceed 2 automatically. `Retry` resets a new bounded attempt cycle; it does not create an infinite loop.

- [ ] **Step 4: Implement batch selection and sequential processing**

Select at most `limit`, hard-cap public web usage to 20 per operator batch, process one Product at a time, commit per Product, call `sleep_fn(delay_seconds)` only between actual public page requests, and return aggregate counts by final status.

- [ ] **Step 5: Implement helper completion through existing validation boundary**

`complete_from_helper()` receives metadata only after existing `core.shopee_helper` / pairing validation. Sanitize again defensively, require a usable image URL, materialize it through the same image path, and apply the same non-destructive Product merge.

- [ ] **Step 6: Run focused tests**

```bash
python -m acp.tests.test_shopee_image_enrichment -v
python -m acp.tests.test_shopee_helper -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add core/shopee_image_enrichment.py tests/test_shopee_image_enrichment.py
git commit -m "feat: run bounded Shopee image enrichment"
```

---

### Task 5: Enqueue missing-image Products from confirmed CSV imports

**Files:**
- Modify: `core/shopee_csv_import.py`
- Modify: `tests/test_shopee_csv_import.py`

**Interfaces:**
- Consumes: `enqueue_product(conn, product_id)` from Task 2.
- Import summary remains backward compatible; optional `enrichment_queued` may be added only if all existing callers/tests tolerate the field.

- [ ] **Step 1: Write RED importer tests**

Assert that NEW and UPDATED `SHOPEE_AFFILIATE` Products without media have exactly one enrichment job after `import_rows()`, repeated import remains idempotent, and a Product with existing media is not moved back to pending.

- [ ] **Step 2: Wire enqueue inside the existing import transaction**

After `_insert_product()` or `_update_product()` returns Product ID, call `enqueue_product(conn, product_id)`. For `UNCHANGED`, resolve the existing Product and enqueue idempotently so products imported before this feature can become eligible without data mutation.

- [ ] **Step 3: Run importer + enrichment tests**

```bash
python -m acp.tests.test_shopee_csv_import -v
python -m acp.tests.test_shopee_image_enrichment -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add core/shopee_csv_import.py tests/test_shopee_csv_import.py
git commit -m "feat: queue Shopee images after CSV import"
```

---

### Task 6: Add the Shopee Product Pool enrichment workspace

**Files:**
- Create: `web/shopee_image_enrichment.py`
- Create: `web/templates/shopee_image_enrichment.html`
- Modify: `web/__init__.py`
- Modify: `web/templates/base.html`
- Create: `tests/test_shopee_image_enrichment_web.py`

**Interfaces:**
- Routes:
  - `GET /sanpham/shopee`
  - `POST /sanpham/shopee/enrichment/backfill`
  - `POST /sanpham/shopee/enrichment/run`
  - `POST /sanpham/shopee/<product_id>/enrich`
  - `POST /sanpham/shopee/<product_id>/retry`
- Reuse existing helper pairing route/workflow for `NEEDS_HELPER`; do not create a second token system.

- [ ] **Step 1: Write RED web tests for auth/CSRF/list/filter limits**

Verify dashboard authentication and global CSRF behavior still applies, status filters return only Shopee Products, batch actions never select more than 20, and product actions reject ACCESSTRADE/non-Shopee Product IDs.

- [ ] **Step 2: Implement isolated Blueprint and dependency seams**

Add config seams for tests where useful, e.g. `SHOPEE_ENRICHMENT_RUNNER`, without changing production defaults. Route handlers delegate all business logic to `core.shopee_image_enrichment`.

- [ ] **Step 3: Implement workspace template**

Show image/placeholder, name, price, sold count, commission rate/amount, status, bounded error, and relevant actions. Include filters `all`, `missing`, `ready`, `needs_helper`, `failed` and controls `Enrich 20 sản phẩm thiếu ảnh`, per-product `Enrich ảnh`, and `Retry`.

- [ ] **Step 4: Integrate helper fallback without exposing tokens**

For `NEEDS_HELPER`, link into the existing product/helper pairing flow rather than rendering the one-time token in HTML. After a validated helper result is available, invoke `complete_from_helper()` before showing `READY`.

- [ ] **Step 5: Register Blueprint and navigation**

Register in `web/__init__.py`; add a `Shopee Affiliate` navigation entry without altering the existing ACCESSTRADE-only catalog SQL.

- [ ] **Step 6: Run web and legacy Shopee tests**

```bash
python -m acp.tests.test_shopee_image_enrichment_web -v
python -m acp.tests.test_shopee_csv_web -v
python -m acp.tests.test_shopee_helper_ui -v
python -m acp.tests.test_shopee_product_intel_web -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add web/shopee_image_enrichment.py web/templates/shopee_image_enrichment.html web/__init__.py web/templates/base.html tests/test_shopee_image_enrichment_web.py
git commit -m "feat: add Shopee image enrichment workspace"
```

---

### Task 7: Runbook, safety regression, and full verification

**Files:**
- Create: `docs/SHOPEE_IMAGE_ENRICHMENT.md`
- Modify tests only if verification reveals a genuine uncovered regression.

**Interfaces:**
- Operator workflow: import CSV -> backfill/queue -> run bounded public enrichment -> handle `NEEDS_HELPER` -> verify local/public image.

- [ ] **Step 1: Write the operator runbook**

Document statuses, batch cap 20, public-HTML-only rule, helper fallback, deterministic local image naming, retry behavior, and exact safety non-goals.

- [ ] **Step 2: Run focused feature suite**

```bash
python -m acp.tests.test_shopee_public_metadata -v
python -m acp.tests.test_shopee_image_enrichment -v
python -m acp.tests.test_shopee_image_enrichment_web -v
python -m acp.tests.test_shopee_csv_import -v
python -m acp.tests.test_shopee_csv_web -v
python -m acp.tests.test_shopee_helper -v
python -m acp.tests.test_shopee_helper_ui -v
python -m acp.tests.test_shopee_product_intel -v
python -m acp.tests.test_shopee_product_intel_web -v
```

Expected: PASS.

- [ ] **Step 3: Run repository gate**

From repo/package root with mock adapter:

```bash
export ACP_ADAPTER=mock
export ACP_SOURCE=mock
./manage.sh test
git diff --check
python -m compileall core web tests adapters >/dev/null
```

Expected: `TEST_OK`, no diff-check output, compileall exit 0.

- [ ] **Step 4: Inspect final diff and status**

```bash
git status --short
git diff --stat main...HEAD
git diff --check main...HEAD
```

Expected: only planned feature/docs/tests changes; no secrets, DB files, media files, CSVs, or runtime artifacts committed.

- [ ] **Step 5: Commit docs/final adjustments**

```bash
git add docs/SHOPEE_IMAGE_ENRICHMENT.md
git commit -m "docs: add Shopee image enrichment runbook"
```
