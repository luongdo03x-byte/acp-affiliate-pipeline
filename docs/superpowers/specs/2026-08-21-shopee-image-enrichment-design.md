# Shopee Image Enrichment Design

Date: 2026-08-21
Branch: `feat/shopee-image-enrichment`
Status: self-reviewed, awaiting written-spec approval

## 1. Goal

Add a safe post-import enrichment pipeline for `SHOPEE_AFFILIATE` products that were created from official Shopee Affiliate CSV files but do not yet have a product image.

The pipeline must:

1. try metadata from the public Shopee product HTML first;
2. extract the main image from JSON-LD / OpenGraph metadata when available;
3. download and validate that image into ACP-managed media storage;
4. fall back to the existing user-assisted Chrome Helper when server-side public HTML is blocked or incomplete;
5. expose batch progress and retry state to the operator;
6. never require Shopee Open API access;
7. never use cookies, authenticated session data, private Shopee endpoints, CAPTCHA bypass, or anti-bot evasion.

This phase is limited to image/metadata enrichment. It does not add ranking, content generation, posting, Open API integration, or autonomous crawling.

## 2. Current context

The CSV importer already creates canonical Products with:

- `source='manual_shopee'`
- `merchant='shopee.vn'`
- `provider='SHOPEE_AFFILIATE'`
- canonical direct `product_url`
- the official Shopee affiliate short URL
- product name, current price, sold count, shop, commission rate and commission amount when supplied by the CSV

The official CSV does not contain a product image, so imported products normally have no `image_url_original`, `image_path_local`, or `main_image_url`.

ACP already has three useful primitives:

- `ProductMetadataResolver._html_metadata()` can parse JSON-LD and OpenGraph metadata from public product HTML.
- `core.shopee_helper` validates helper metadata and verifies the observed product against the expected canonical `shop_id/item_id`.
- `core.helper_pairing` provides one-time, product-bound Chrome Helper pairing tokens.

The existing `ProductMetadataResolver.resolve()` also calls Shopee `/api/v4/...` endpoints when HTML is incomplete. This design removes that fallback from the metadata path used by ACP. Public HTML and the user-assisted Helper become the only Shopee metadata sources in this phase.

## 3. Architecture

The enrichment flow is:

```text
Official Shopee Affiliate CSV
          |
          v
Product upsert
          |
          v
SHOPEE_AFFILIATE product missing image
          |
          v
PENDING enrichment job
          |
          v
PUBLIC_FETCH
          |
          +--> public HTML contains usable image --> DOWNLOADING
          |                                      |
          |                                      +--> valid image --> READY
          |                                      +--> download/validation failure --> retry -> FAILED
          |
          +--> blocked/incomplete/no image ------> NEEDS_HELPER
                                                     |
                                                     v
                                             Chrome Helper
                                                     |
                                                     +--> returns usable image URL --> DOWNLOADING --> READY/FAILED
                                                     +--> invalid/wrong product -----> remain NEEDS_HELPER
```

Processing is operator-triggered in bounded batches. Importing creates/enqueues missing-image work, but it does not automatically crawl hundreds or thousands of product pages in the background.

Default batch controls:

- batch size: 20 products;
- concurrency: 1;
- inter-request delay: configurable, default 1.5 seconds;
- automatic public-fetch attempts per product per operator run: maximum 2;
- image-download attempts per product per operator run: maximum 2.

This keeps the feature useful for hundreds of imported products while avoiding an unbounded crawler.

## 4. Public metadata resolver

### 4.1 Public-only contract

Introduce an explicit public-only resolver boundary, for example:

```python
resolve_public_metadata(product_url) -> ProductMetadata
```

It may only request the canonical public `https://shopee.vn/...` product page and parse the returned HTML.

Allowed metadata sources inside the HTML:

- JSON-LD `Product` objects;
- `og:image`;
- `og:title`;
- standard product price meta tags already supported by ACP.

No JavaScript execution is required server-side.

### 4.2 Remove private endpoint fallback

`ProductMetadataResolver.resolve()` must no longer call `_api_metadata()` or request `/api/v4/pdp/get_pc`, `/api/v4/item/get`, or equivalent private/internal Shopee endpoints.

The existing private-endpoint parser code should be deleted when practical. If temporary parser helpers must remain for migration/test reasons, no production metadata flow may invoke them.

A regression test must prove that resolving metadata never sends a request to a URL containing `/api/v4/`.

### 4.3 Metadata merge policy

CSV data remains authoritative for fields it already supplied. Public HTML enrichment is additive.

Rules:

- blank/invalid public metadata never erases existing Product values;
- `image_url_original` may be filled when currently blank;
- `name` may be filled only when current Product name is blank;
- `shop_name` may be filled only when current Product shop is blank;
- `original_price` may be filled only when current Product value is blank and the parsed value is valid;
- CSV-owned current price, sold count, commission rate, commission amount, affiliate URL and provider identity are not overwritten by public enrichment;
- helper metadata follows the same non-destructive merge rule.

## 5. Enrichment job model

Register a Shopee-specific table through the existing `core.shopee_schema` migration mechanism.

Proposed table:

```sql
CREATE TABLE IF NOT EXISTS shopee_image_enrichment_job (
    product_id             TEXT PRIMARY KEY REFERENCES product(id),
    status                 TEXT NOT NULL,
    attempt_count          INTEGER NOT NULL DEFAULT 0,
    download_attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error_code        TEXT,
    last_error             TEXT,
    last_attempt_at        TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
```

Allowed statuses:

- `PENDING`
- `PUBLIC_FETCH`
- `DOWNLOADING`
- `NEEDS_HELPER`
- `READY`
- `FAILED`

The service layer, not route/database callers, owns state transitions.

### 5.1 Enqueue rules

Create or refresh a job when all are true:

- Product provider is `SHOPEE_AFFILIATE`;
- Product has a canonical direct Shopee product URL;
- Product has no usable local/main image.

The CSV confirm/import path should enqueue newly imported missing-image Products idempotently.

The UI must also provide an idempotent scan/backfill action so products imported before this phase can be enrolled without re-importing their CSV.

If a Product already has a valid local/main image, its job is `READY` or no new work is created.

### 5.2 Retry counters

`attempt_count` and `download_attempt_count` are counters for the current automatic attempt cycle. An explicit operator `Retry` resets the relevant counters to zero, clears the previous bounded error, and starts a new finite cycle.

No automatic path resets these counters or loops indefinitely.

### 5.3 Crash recovery

`PUBLIC_FETCH` and `DOWNLOADING` are transient states. If one is older than 10 minutes when the next operator batch starts, the service returns it to `PENDING` unless the Product already has a usable image, in which case it becomes `READY`.

This prevents a process restart from leaving work permanently stuck.

## 6. Image download and storage

### 6.1 Source validation

The source image URL must:

- use HTTPS;
- contain no credentials;
- use a normal HTTPS port;
- pass existing safe HTTP / SSRF protections;
- remain within the configured maximum download size.

The image response must be validated from actual bytes using Pillow. Do not trust filename extensions alone.

Supported decoded formats should match current media-library behavior: JPEG, PNG, WEBP and GIF.

HTML, JSON, corrupt bytes or oversized responses are rejected.

### 6.2 Local materialization

Use a deterministic flat filename under the existing ACP media root so it remains compatible with the current `LocalStorage.put()` implementation and `/media/<path:name>` route:

```text
var/media/shopee_<shop_id>_<item_id>.<verified-ext>
```

The extension comes from the decoded image format, never from untrusted URL text.

A valid existing file for the same canonical product is reused rather than downloaded again.

Use atomic write semantics: download and verify bytes first, write a temporary file inside the media root, then atomically replace/move to the deterministic target.

### 6.3 Product fields

On successful materialization:

- `image_url_original` = original Shopee image URL;
- `image_path_local` = deterministic ACP local file path;
- `main_image_url` = URL returned by the configured ACP storage backend for that local file.

For local storage, `storage.get_storage().put(local_path)` produces a URL compatible with the existing `/media/<filename>` route. For S3/R2, the same storage abstraction uploads and returns the public URL. Do not construct S3/R2 URLs manually.

If storage publication fails after local validation, keep the verified local file but do not mark the job `READY` until Product fields are internally consistent.

## 7. Chrome Helper fallback

Move a Product to `NEEDS_HELPER` when public product HTML is blocked, times out, is incomplete, or contains no usable image after the public-fetch retry budget.

A Product that already yielded an image URL but fails image download/validation/storage follows its image retry budget and then becomes `FAILED`; it is not automatically sent to Helper because Helper cannot repair a local download/storage failure. The operator may still explicitly choose Helper if they want to obtain a different rendered image URL.

Reuse the existing Helper security model:

- one-time product-bound pairing token;
- short TTL;
- expected and observed canonical Shopee product identities must match;
- allowlisted metadata fields only;
- no cookie/session/localStorage extraction;
- helper reads only the rendered product DOM after the operator invokes it.

When Helper metadata contains an image URL, run the same safe image materialization path as public HTML. Do not trust Helper image bytes directly and do not bypass server-side validation.

A wrong product tab must remain rejected without consuming a still-valid pairing token, preserving current behavior.

## 8. Web UI

Expose a Shopee Affiliate area under the `/sanpham` namespace without mixing Shopee rows into the existing ACCESSTRADE-only query implementation.

Recommended routes:

```text
GET  /sanpham/shopee
POST /sanpham/shopee/enrichment/backfill
POST /sanpham/shopee/enrichment/run
POST /sanpham/shopee/<product_id>/enrich
POST /sanpham/shopee/<product_id>/retry
```

The page is a Shopee tab/workspace linked from `/sanpham` navigation.

Filters:

- All
- Missing image
- Ready
- Needs Helper
- Failed

Each row shows:

- product image/placeholder;
- product name;
- current price;
- sold count;
- commission rate / amount;
- enrichment status;
- last short error, if any;
- actions relevant to current state.

Primary controls:

- `Enrich 20 sản phẩm thiếu ảnh`
- per-product `Enrich ảnh`
- `Retry`
- for `NEEDS_HELPER`: `Mở sản phẩm tiếp theo` / pair with Chrome Helper using the existing helper workflow.

Batch completion should summarize, for example:

```text
20 processed
14 READY
5 NEEDS_HELPER
1 FAILED
```

The UI must not expose internal stack traces, raw HTML, pairing tokens, cookies or session material.

## 9. Batch execution behavior

Phase 1 uses synchronous operator-triggered bounded batches rather than introducing a background worker subsystem.

For each batch:

1. select up to 20 eligible `PENDING`/retryable jobs in deterministic order;
2. process one Product at a time;
3. persist state/Product updates per product so one failure does not roll back the whole batch;
4. wait for the configured delay between public product-page requests;
5. stop after the selected batch; do not automatically start another batch;
6. return an aggregate summary to the UI.

A product-level action uses the same service function as batch execution; route handlers must not contain a second enrichment implementation.

If observed request duration becomes unsuitable for the deployed reverse proxy, the implementation may use a small browser-side sequential coordinator while keeping the exact same server-side product-level service and 20-item bound. It must not introduce an autonomous background crawler as part of this phase.

## 10. Error handling

Use stable internal error codes plus bounded operator-facing messages.

Suggested categories:

- `PUBLIC_TIMEOUT`
- `PUBLIC_BLOCKED`
- `PUBLIC_NO_IMAGE`
- `IMAGE_DOWNLOAD_FAILED`
- `IMAGE_TOO_LARGE`
- `IMAGE_INVALID_CONTENT`
- `IMAGE_DECODE_FAILED`
- `STORAGE_FAILED`
- `PRODUCT_IDENTITY_INVALID`
- `HELPER_REQUIRED`

Do not persist:

- raw HTML;
- response bodies;
- cookies;
- headers containing authentication data;
- helper pairing tokens;
- stack traces.

Automatic processing respects the per-cycle attempt limits. Only an explicit operator `Retry` starts another finite attempt cycle.

## 11. Security and policy boundaries

This feature explicitly prohibits:

- automated Shopee login;
- cookie/session/localStorage credential extraction;
- CAPTCHA solving or bypass;
- anti-bot evasion;
- authenticated hidden endpoint scraping;
- Shopee `/api/v4/...` private endpoint use;
- reverse engineering of private APIs;
- unbounded autonomous crawling;
- automatic posting/publishing.

Only public product-page HTML and operator-assisted rendered DOM metadata are accepted as Shopee metadata sources.

Existing SSRF protections and canonical Shopee identity checks remain mandatory.

## 12. Idempotency

Running enrichment repeatedly must be safe.

- A Product already `READY` with a valid local image is skipped.
- A valid deterministic local image is reused.
- Existing nonblank CSV metadata is preserved.
- Job upsert is keyed by Product ID.
- Helper submissions remain one-time and product-bound.
- Re-importing the same CSV does not create duplicate enrichment jobs.

## 13. Testing

Add focused tests for at least:

### Public metadata

- JSON-LD image -> usable metadata;
- `og:image` -> usable metadata;
- HTML with no image -> `NEEDS_HELPER`;
- timeout/403 -> `NEEDS_HELPER`;
- no production request URL contains `/api/v4/`;
- public metadata does not overwrite stronger existing CSV data.

### Image safety/storage

- valid JPEG/PNG/WEBP image -> materialized successfully;
- oversized response -> rejected;
- wrong content type / HTML masquerading as image -> rejected;
- corrupt image -> rejected;
- deterministic filename uses canonical shop/item only;
- existing valid local file -> no duplicate download;
- atomic write leaves no partial target on failure;
- storage backend failure does not mark job `READY`.

### Job state machine

- enqueue is idempotent;
- already imported missing-image Product is backfilled;
- automatic attempts respect limits;
- explicit retry resets only the current attempt cycle;
- stale transient jobs recover;
- successful public fetch -> `READY`;
- incomplete public fetch -> `NEEDS_HELPER`;
- image materialization failure after retry -> `FAILED`;
- permanent validation/config error -> `FAILED`;
- retry uses the same service path.

### Helper fallback

- correct product Helper submission -> image materialization -> `READY`;
- wrong shop/item -> rejected;
- invalid image URL -> rejected;
- invalid submission does not consume valid pairing token;
- helper metadata does not erase stronger Product metadata.

### Web

- authentication and CSRF remain enforced;
- Shopee list filters are correct;
- batch action selects at most 20;
- per-product action cannot enrich non-Shopee Product;
- batch summary counts are correct;
- no secrets/tokens/raw HTML appear in rendered errors.

### Regression gate

Run:

```bash
./manage.sh test
git diff --check
python -m compileall core web tests adapters
```

Tests must use mock/fake HTTP clients and must not hit live Shopee during automated verification.

## 14. Rollout

1. Ship schema + service + tests first.
2. Backfill only a small controlled set of imported Shopee products.
3. Run one 20-product public batch and inspect READY / NEEDS_HELPER / FAILED distribution.
4. Validate that local images render through current ACP storage configuration.
5. Validate Helper fallback on a few blocked products.
6. Only then process additional 20-product batches.

No production publish action is part of this rollout.

## 15. Success criteria

The phase is complete when:

- imported `SHOPEE_AFFILIATE` products with missing images can be enrolled without CSV re-import;
- public HTML successfully enriches products when Shopee exposes `og:image` or JSON-LD image metadata;
- ACP stores a verified local copy and a usable `main_image_url`;
- blocked/incomplete products are clearly routed to the existing Chrome Helper workflow;
- no Shopee private API is called;
- batch processing is bounded and retry behavior is finite;
- all new tests and the repository regression gate pass.
