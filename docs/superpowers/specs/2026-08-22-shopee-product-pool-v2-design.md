# Shopee Product Pool v2 — Design

Date: 2026-08-22
Branch: `feat/shopee-product-pool-v2`
Status: Approved in chat; implementation pending plan review

## 1. Goal

Turn the existing Shopee Affiliate CSV flow into the single product source for Threads Auto, while making `/sanpham/shopee` a usable operational Product Pool.

The finished flow is:

`Shopee Affiliate CSV -> Product -> image-enrichment queue -> READY -> Shopee Product Pool -> Threads Auto eligibility -> schedule -> job_queue -> live publisher`

The change has three user-visible outcomes:

1. Confirming a CSV import triggers image-enrichment work immediately instead of requiring a manual click or waiting for the 60-minute recovery timer.
2. `/sanpham/shopee` gains server-side pagination, search, filters, pool-wide totals, and per-niche usage statistics.
3. Threads Auto candidates come only from `provider='SHOPEE_AFFILIATE'`; legacy/AccessTrade/TikTok catalog products remain available for manual workflows but are not Auto candidates.

## 2. Existing behavior to preserve

The design deliberately keeps the current publish pipeline authoritative:

- existing Threads channel configuration, daily target, quota and slot rules;
- duplicate/cooldown/category/day-cap safeguards;
- Shopee affiliate-link freshness and image-ready checks;
- preflight immediately before publishing;
- `publish_target` + `job_queue` delivery model;
- live worker and retry semantics;
- Chrome Helper fallback for products whose public Shopee page does not yield an image;
- the 60-minute Shopee enrichment service/timer as recovery rather than the primary trigger.

No new scheduler or second publishing queue is introduced.

## 3. Non-goals

This change does not:

- automate Shopee login, CAPTCHA, private endpoints, or browser credentials;
- replace official Shopee Affiliate CSV imports;
- change Threads OAuth or live publishing behavior;
- automatically publish a product at import time;
- remove legacy products from `/sanpham` or manual post creation;
- redesign channel configuration or posting-slot semantics;
- add a new database solely to support the Product Pool.

## 4. Architecture

### 4.1 Product Pool query/service boundary

Create a focused core module, `core/shopee_product_pool.py`, responsible for read-only Product Pool projections.

It owns:

- parsing/normalizing Product Pool filters;
- deriving usage state;
- deriving niche matches using the existing `core.niche` matcher;
- deriving exact current Auto eligibility across active Auto Threads channels;
- filtering and pagination;
- pool-wide summary counts;
- per-niche summary counts;
- stable pagination metadata.

The Flask route in `web/shopee_image_enrichment.py` becomes a thin adapter: parse query parameters, call the service, and render the template.

No routing/eligibility rules are duplicated in the template.

### 4.2 Event-driven enrichment trigger

`core.shopee_csv_import.import_rows()` already calls `enqueue_product()` for NEW, UPDATED, and UNCHANGED valid Shopee rows. That behavior remains idempotent and transaction-bound.

What is missing is an immediate executor trigger. After a successful confirmed import, the web layer will enqueue bounded enrichment work into the existing job system rather than processing all images synchronously inside the HTTP request.

Contract:

- new non-publish job type: `SHOPEE_ENRICH_PRODUCT`;
- payload contains only `product_id`;
- `import_rows()` returns an internal `touched_product_ids` collection in addition to display counters; it is not rendered or written into audit details;
- after the import transaction commits, the confirm route inspects those products and queues work only when their enrichment state is `PENDING`;
- idempotency key is `shopee-enrich:{product_id}:{enrichment_job.updated_at}` using the PENDING enrichment row observed at queue time;
- the handler processes one product using the existing `enrich_product()` primitive and normal safe HTTP/storage dependencies;
- the normal ACP worker can execute non-publish jobs even when publishing is disabled;
- existing `acp-worker.timer` therefore starts work on its next pass, normally within one minute;
- the hourly/60-minute Shopee enrichment command remains a recovery/backfill path.

This is considered “immediate” operationally because the trigger is created by the import event itself and no manual action/hourly wait is required. The HTTP request does not wait for network/image processing.

Re-importing a product that is already READY creates no enrichment execution job. Existing FAILED/NEEDS_HELPER states are not silently reset by import; operator retry/Helper remains authoritative.

### 4.3 Shopee-only Auto source

`core/shopee_auto_runtime.install()` currently merges legacy candidates and Shopee candidates. Replace that candidate composition so the installed Auto candidate source returns only `_shopee_auto_candidates(...)`.

Legacy eligibility/manual post code remains untouched. The provider boundary applies specifically to Auto scheduling.

All existing Shopee eligibility checks remain authoritative:

- provider is `SHOPEE_AFFILIATE`;
- product is available;
- affiliate URL is valid and `affiliate_link_status='READY'`;
- CSV snapshot is fresh;
- enrichment is READY and a usable image exists;
- category and minimum commission filters pass;
- channel niche matches;
- category/day cap passes;
- product is not already queued or within publish cooldown.

Preflight continues to re-check the product immediately before publish.

## 5. Product Pool states

Separate “usage state” from “eligibility/health state” so statistics remain intuitive.

### 5.1 Usage state

Each Shopee product has one mutually exclusive usage state, with this precedence:

1. `PUBLISHED`: any associated post is PUBLISHED or any publish target is SUCCESS.
2. `SCHEDULED`: an associated auto publish target is in SCHEDULED/PENDING/RUNNING.
3. `REVIEW`: an associated post is DRAFT/PENDING_REVIEW.
4. `UNUSED`: none of the above.

This state powers “Chưa dùng / Đã lên lịch / Đã đăng” counts. A stale CSV or missing image must not hide the historical fact that a product was already published.

### 5.2 Auto/health state

The Product Pool exposes these operational states:

- `PUBLISHED` when usage state is PUBLISHED;
- `SCHEDULED` when usage state is SCHEDULED;
- `REVIEW` when usage state is REVIEW;
- `WAITING_IMAGE` when UNUSED but enrichment is not READY;
- `STALE` when UNUSED, image is READY, but the Shopee CSV snapshot is stale;
- `ELIGIBLE` when UNUSED and at least one currently active, enabled, Auto-enabled Threads channel accepts the product through the same `_shopee_product_auto_eligibility()` rules used by the scheduler;
- `INELIGIBLE` for remaining UNUSED products that are READY/fresh but fail all active Auto Threads channels.

`Auto Eligible` summary is therefore exact for the current channel configuration, not an approximation. The service evaluates eligible UNUSED products against active Auto Threads channels using the existing scheduler eligibility function; it does not duplicate its business rules.

## 6. Niche classification

Use `core.niche.NICHES` and its existing match logic. Do not create a second category dictionary for Product Pool UI.

A product may match more than one niche. Therefore per-niche totals are intentionally overlapping and must not be summed to derive the global product total.

Each niche summary exposes:

- niche code;
- display name;
- total matching products;
- unused;
- scheduled;
- published.

Example:

```text
Thời trang nữ 327
  Chưa dùng 280
  Đã lên lịch 31
  Đã đăng 16
```

Selecting a niche card or filter restricts the table to products that match that same matcher.

## 7. `/sanpham/shopee` UI contract

### 7.1 Query parameters

Supported parameters:

- `q`: product/shop search text;
- `niche`: niche code or empty/all;
- `auto`: all/eligible/waiting_image/stale/ineligible/review/scheduled/published;
- `image`: all/ready/missing/needs_helper/failed/pending;
- `usage`: all/unused/scheduled/review/published;
- `page`: 1-based page;
- `per_page`: 20, 50, or 100.

Invalid values fall back to safe defaults instead of causing a 500.

### 7.2 Pagination

Default `per_page=20`. Allowed values are 20/50/100.

Pagination metadata includes:

- current page;
- total filtered rows;
- total pages;
- has previous/next;
- URLs preserve all current filters.

When filters reduce the result set below the requested page, clamp to the final valid page. With zero results, page is 1 and total pages is 1 so template logic remains simple.

### 7.3 Global summary

Display pool-wide counts independent of the current table page and current filters:

- Tổng sản phẩm
- Chưa dùng
- Auto Eligible
- Đã lên lịch
- Đã đăng
- Chờ duyệt
- Ảnh READY
- Cần Helper
- Lỗi ảnh
- CSV quá hạn

The existing enrichment recovery controls remain available but are visually secondary because normal imports now trigger enrichment automatically.

### 7.4 Table

Retain the useful existing columns and actions:

- image;
- product + shop + Shopee link;
- price;
- sold count;
- commission;
- image status;
- usage/Auto status;
- manual recovery action.

Add enough context to distinguish `UNUSED`, `SCHEDULED`, and `PUBLISHED` without forcing the operator to inspect `/vanhanh`.

## 8. Data flow

### 8.1 CSV import

1. Operator previews one or more official Shopee Affiliate CSV files.
2. Existing validation/dedupe logic classifies rows.
3. Operator confirms.
4. Product rows are inserted/updated in one transaction.
5. `enqueue_product()` leaves image jobs READY or PENDING idempotently.
6. `import_rows()` returns display counters plus internal touched product IDs.
7. After the transaction succeeds, the web route queues `SHOPEE_ENRICH_PRODUCT` for touched products whose enrichment state is currently PENDING.
8. The ACP worker processes enrichment jobs without blocking the import response.
9. READY products become visible as eligible candidates to Auto on the next scheduler pass.

If the immediate queue trigger fails after the Product transaction has committed, the import is still successful; the existing recovery/backfill timer can rediscover PENDING products. The UI reports the import as imported and may report that enrichment recovery remains pending.

### 8.2 Auto schedule

1. Auto scheduler asks for candidates for an active Threads channel.
2. Candidate provider boundary returns Shopee Affiliate only.
3. Shopee eligibility evaluates image/link/freshness/category/commission/niche/duplicate/cap rules.
4. Existing slot/quota routing creates post + publish_target + PUBLISH_POST job.
5. Existing preflight checks the current product state again at publish time.
6. Existing worker publishes to Threads.

## 9. Error handling

- CSV import remains transactional; DB failure does not consume the preview token.
- Immediate enrichment queue failure never rolls back an already successful Product import.
- Network/provider image failures remain bounded to one product and use existing `NEEDS_HELPER`/`FAILED` states.
- Product Pool filters must never expose raw tokens, provider response bodies, stack traces, or affiliate secrets.
- Pagination/filter parse errors degrade to defaults.
- Legacy products are rejected at the candidate-source boundary, not late in publish.
- Existing publish preflight remains fail-closed.

## 10. Performance

Current pool size is small, so correctness and shared niche semantics are prioritized over premature schema changes.

The first implementation scans Shopee Product rows in application code to apply the existing Python niche matcher and derive usage/Auto state, then paginates the matched projection. Search text and simple image constraints may be applied in SQL before projection when doing so does not change semantics.

Exact Auto eligibility is evaluated only for UNUSED products and stops at the first active Auto Threads channel that accepts the product.

No niche-materialization table is introduced in this phase. If the pool later grows enough for request latency to become material, niche matches/state projections can be cached/materialized behind the same `shopee_product_pool` service API without changing routes/templates.

## 11. Testing strategy

Implementation follows TDD.

Required tests:

### Shopee-only Auto

- legacy/non-Shopee product is never returned by the installed Auto candidate source;
- eligible Shopee product is returned;
- existing Shopee eligibility/preflight tests continue to pass.

### Import-triggered enrichment

- successful confirm/import creates enrichment execution work for PENDING touched products;
- READY products are not needlessly re-enriched;
- repeated imports with the same PENDING enrichment generation do not create duplicate concurrent work;
- a new PENDING generation can be queued after the previous generation changes;
- a queue-trigger failure does not corrupt/rollback a successful Product import;
- worker handler changes PENDING product toward READY/NEEDS_HELPER/FAILED using existing enrichment primitives;
- publish-disabled mode still permits non-publish enrichment jobs.

### Product Pool

- provider boundary always excludes non-Shopee rows;
- default page is 20;
- 20/50/100 page sizes work;
- page count and filter preservation are correct;
- search matches name/shop;
- image/usage/Auto filters work;
- niche filter uses the same matcher as scheduler;
- usage-state precedence produces stable mutually exclusive counts;
- published products remain counted as published even when CSV becomes stale;
- Auto Eligible is exact against at least one active Auto Threads channel;
- per-niche totals may overlap but each niche's usage breakdown is internally consistent;
- global summaries are independent of current page and filters.

### Regression

Run focused Shopee CSV, image enrichment, Auto scheduler, publish worker, and web tests plus compile/diff checks. Do not perform additional live Threads posts as part of automated verification.

## 12. Files expected to change

Likely files:

- `core/shopee_product_pool.py` — new read/query projection service;
- `core/shopee_auto_runtime.py` — Shopee-only candidate boundary;
- `core/shopee_csv_import.py` — return touched product IDs without weakening transactional behavior;
- `core/shopee_image_enrichment.py` — reuse existing primitives; minimal changes only if needed for worker-safe execution;
- `core/jobs.py` or a focused Shopee job-handler module — enrichment job registration/execution;
- `web/shopee_csv_import.py` — queue immediate enrichment work after successful confirm;
- `web/shopee_image_enrichment.py` — delegate filtering/pagination/stats to Product Pool service;
- `web/shopee_auto_state.py` — reduce/adjust projection responsibilities if needed, preserving compatibility;
- `web/templates/shopee_image_enrichment.html` — filters, pagination, niche statistics, usage state;
- focused tests for all three changes.

Changes to `web/server.py` should be avoided unless wiring requires it.

## 13. Acceptance criteria

The feature is complete when all of the following are true:

1. Importing and confirming a valid Shopee Affiliate CSV automatically creates image-enrichment execution work without a manual “Enrich” click or waiting for the hourly timer.
2. `/sanpham/shopee` supports search, niche/Auto/image/usage filters and 20/50/100 pagination.
3. The Product Pool shows global usage/health totals and per-niche total/unused/scheduled/published statistics.
4. `Auto Eligible` reflects at least one real active Auto Threads channel accepting the product through existing Shopee eligibility logic.
5. Auto scheduler candidates are exclusively Shopee Affiliate products.
6. Legacy catalog/manual workflows remain functional.
7. Existing duplicate/quota/slot/preflight/live worker safeguards remain active.
8. Focused and regression tests pass without generating another live Threads post.
