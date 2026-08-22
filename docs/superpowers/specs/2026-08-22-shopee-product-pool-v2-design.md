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

Create a focused core module, tentatively `core/shopee_product_pool.py`, responsible for read-only Product Pool projections.

It owns:

- parsing/normalizing Product Pool filters;
- deriving usage state;
- deriving niche matches using the existing `core.niche` matcher;
- filtering and pagination;
- pool-wide summary counts;
- per-niche summary counts;
- stable pagination metadata.

The Flask route in `web/shopee_image_enrichment.py` becomes a thin adapter: parse query parameters, call the service, and render the template.

No routing/eligibility rules are duplicated in the template.

### 4.2 Event-driven enrichment trigger

`core.shopee_csv_import.import_rows()` already calls `enqueue_product()` for NEW, UPDATED, and UNCHANGED valid Shopee rows. That behavior remains idempotent and transaction-bound.

What is missing is an immediate executor trigger. After a successful confirmed import, the web layer will enqueue bounded enrichment work into the existing job system rather than processing all images synchronously inside the HTTP request.

Preferred contract:

- new non-publish job type: `SHOPEE_ENRICH_PRODUCT`;
- payload contains only `product_id`;
- import confirmation enqueues work for products whose enrichment state is `PENDING`;
- the normal ACP worker can execute non-publish jobs even when publish is disabled;
- existing `acp-worker.timer` therefore starts work on its next pass, normally within one minute;
- the hourly/60-minute Shopee enrichment command remains a recovery/backfill path.

This is considered “immediate” operationally because the trigger is created by the import event itself and no manual action/hourly wait is required. The HTTP request does not wait for network/image processing.

Idempotency is enforced by the enrichment job state plus a queue idempotency key that represents the current pending enrichment generation. Re-importing a product that is already READY does not create unnecessary work; FAILED/NEEDS_HELPER remains operator-controlled unless the existing retry contract permits a reset.

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

The UI may continue to expose operational states such as:

- `ELIGIBLE`
- `WAITING_IMAGE`
- `STALE`
- `REVIEW`
- `SCHEDULED`
- `PUBLISHED`

For `UNUSED` products, `ELIGIBLE` means the current Shopee Auto eligibility checks can admit the product for at least one active Auto Threads channel matching its niche. If evaluating against all active channels would be too expensive for a pool-wide request, the first implementation may use the existing product-level readiness projection for the card and keep channel-specific eligibility authoritative in the scheduler. Tests must document whichever contract is chosen; the UI must not claim a product is eligible when it clearly fails image/freshness/link/provider requirements.

## 6. Niche classification

Use `core.niche.NICHES` and its existing match logic. Do not create a second category dictionary for Product Pool UI.

A product may match more than one niche. Therefore per-niche totals are intentionally overlapping and must not be summed to derive the global product total.

Each niche summary exposes:

- niche code;
- display name;
- total matching products;
- unused;
- scheduled;
- published;
- optionally eligible if it can be computed without duplicating scheduler logic.

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
- `auto`: operational Auto state or all;
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

When filters reduce the result set below the requested page, clamp to the final valid page.

### 7.3 Global summary

Display pool-wide counts independent of the current table page:

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
6. After the transaction succeeds, queue `SHOPEE_ENRICH_PRODUCT` work for current PENDING products from that confirmed batch.
7. The ACP worker processes enrichment jobs without blocking the import response.
8. READY products become visible as eligible candidates to Auto on the next scheduler pass.

If the immediate queue trigger fails after the Product transaction has committed, the import is still successful; the existing recovery/backfill timer can rediscover PENDING products. The UI should report the import as imported and, when possible, note that enrichment recovery remains pending.

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

The first implementation may scan Shopee Product rows in application code to apply the existing Python niche matcher, then paginate the matched projection. Search and simple DB-level constraints should be applied as early as practical.

No niche-materialization table is introduced in this phase. If the pool later grows enough for request latency to become material, niche matches can be cached/materialized behind the same `shopee_product_pool` service API without changing routes/templates.

## 11. Testing strategy

Implementation follows TDD.

Required tests:

### Shopee-only Auto

- legacy/non-Shopee product is never returned by the installed Auto candidate source;
- eligible Shopee product is returned;
- existing Shopee eligibility/preflight tests continue to pass.

### Import-triggered enrichment

- successful confirm/import creates enrichment execution work for PENDING imported products;
- READY products are not needlessly re-enriched;
- repeated imports do not create duplicate concurrent enrichment work;
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
- per-niche totals may overlap but each niche's usage breakdown is internally consistent;
- global summaries are independent of current page.

### Regression

Run focused Shopee CSV, image enrichment, Auto scheduler, publish worker, and web tests plus compile/diff checks. Do not perform additional live Threads posts as part of automated verification.

## 12. Files expected to change

Likely files:

- `core/shopee_product_pool.py` — new read/query projection service;
- `core/shopee_auto_runtime.py` — Shopee-only candidate boundary;
- `core/shopee_csv_import.py` — expose/return imported product IDs if needed for event trigger without weakening transactional behavior;
- `core/shopee_image_enrichment.py` — reuse existing primitives; minimal changes only if needed for worker-safe execution;
- `core/jobs.py` or a focused Shopee job-handler module — enrichment job registration/execution;
- `web/shopee_csv_import.py` — queue immediate enrichment work after successful confirm;
- `web/shopee_image_enrichment.py` — delegate filtering/pagination/stats to Product Pool service;
- `web/shopee_auto_state.py` — adjust projection responsibilities if needed, preserving compatibility;
- `web/templates/shopee_image_enrichment.html` — filters, pagination, niche statistics, usage state;
- focused tests for all three changes.

Changes to `web/server.py` should be avoided unless wiring requires it.

## 13. Acceptance criteria

The feature is complete when all of the following are true:

1. Importing and confirming a valid Shopee Affiliate CSV automatically creates image-enrichment execution work without a manual “Enrich” click or waiting for the hourly timer.
2. `/sanpham/shopee` supports search, niche/Auto/image/usage filters and 20/50/100 pagination.
3. The Product Pool shows global usage/health totals and per-niche total/unused/scheduled/published statistics.
4. Auto scheduler candidates are exclusively Shopee Affiliate products.
5. Legacy catalog/manual workflows remain functional.
6. Existing duplicate/quota/slot/preflight/live worker safeguards remain active.
7. Focused and regression tests pass without generating another live Threads post.
