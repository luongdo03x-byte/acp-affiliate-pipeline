# Shopee Import → Auto Enrichment → Threads Auto Publish Design

## Goal

Turn the existing Shopee Affiliate CSV import into a hands-off pipeline:

`Import CSV → enqueue image enrichment → automatic enrichment → READY → Threads auto routing/scheduling → publish worker`.

The operator should not need to press **Quét sản phẩm thiếu ảnh** or **Enrich 20 sản phẩm thiếu ảnh** during the normal path. Those controls remain available as recovery tools.

## Scope

This design covers only:

- official Shopee Affiliate CSV import already supported by ACP;
- automatic public-HTML image enrichment with the existing Chrome Helper fallback;
- provider-aware inclusion of `SHOPEE_AFFILIATE` products in the existing Threads auto scheduler;
- use of the affiliate URL already imported from the CSV;
- existing content generation, validation, scheduling, pre-publish checks, and publish worker.

Out of scope:

- creating or collecting more Shopee affiliate links;
- automated Shopee login;
- cookies, sessions, localStorage, or credential extraction;
- CAPTCHA bypass or anti-bot evasion;
- private Shopee APIs;
- a second posting scheduler;
- automatic Helper interaction when public HTML is blocked.

## Existing Baseline

The feature branch is based on current `main` plus `feat/threads-auto-routing-scheduler`.

Existing Shopee CSV import already:

- validates the official 9-column CSV;
- stores `provider='SHOPEE_AFFILIATE'`;
- preserves the CSV `affiliate_url` / `affiliate_short_url`;
- stores `affiliate_link_status='READY'` and `is_available=1`;
- calls `enqueue_product()` for image enrichment.

Existing image enrichment already:

- owns `shopee_image_enrichment_job`;
- processes at most 20 products per `run_batch()` invocation;
- fetches only public Shopee HTML;
- downloads and verifies image bytes safely;
- writes deterministic local media;
- transitions to `READY`, `NEEDS_HELPER`, or `FAILED`;
- never logs in or bypasses Shopee controls.

Existing Threads auto scheduler already:

- selects ACTIVE/enabled Threads channels with Auto enabled;
- matches products against channel niches;
- prevents duplicate/recent product routing;
- enforces quota and rolling time slots;
- creates content and composited media;
- automatically approves only clean posts;
- schedules through `publish_target`;
- rechecks eligibility immediately before publish;
- publishes through the existing `PUBLISH_POST` worker.

## Core Design

### 1. Automatic enrichment is a timer stage, not part of the HTTP import transaction

CSV import must remain fast and durable. It must not wait for public Shopee requests or image downloads.

The importer continues to commit Product rows and `shopee_image_enrichment_job` rows only.

Add a CLI command:

`python3 run.py shopee-enrich`

The command runs one bounded `run_batch()` pass using the existing enrichment implementation. The current Auto systemd service invokes this command before `auto-schedule`.

Normal timer order becomes:

1. `product-sync`
2. `shopee-enrich`
3. `auto-schedule`
4. `worker-once`

The existing timer cadence remains unchanged in this feature. This avoids turning the ACCESSTRADE product sync from an hourly operation into a higher-frequency operation as a side effect of Shopee automation.

Because one enrichment batch can include network delay and retries, increase the service startup timeout enough for the additional bounded stage. The enrichment batch limit remains 20.

Manual **Quét** and **Enrich** buttons remain available for recovery but are no longer described as mandatory workflow steps.

### 2. `SHOPEE_AFFILIATE` gets provider-specific Auto eligibility

Shopee Affiliate CSV rows intentionally have incomplete commerce metadata:

- `has_inventory` is unknown rather than true;
- `rating` may be absent;
- `review_count` may be zero because the CSV does not provide it;
- `category_code` is currently `khac`;
- image readiness is represented by `shopee_image_enrichment_job.status='READY'`.

Therefore Shopee products must not be forced through the legacy hard filters that require inventory/rating/review data ACP does not possess.

A Shopee Affiliate product is Auto-eligible only when all of these hold:

- `provider == 'SHOPEE_AFFILIATE'`;
- `is_available == 1`;
- `affiliate_link_status == 'READY'`;
- `affiliate_url` is a valid absolute HTTP(S) URL;
- its image enrichment job exists and is `READY`;
- it has a usable enriched image (`image_path_local` or `main_image_url`);
- its CSV snapshot is fresh enough for Auto use;
- commission is not below the configured minimum commission filter;
- it is not in a blocked category;
- it matches the target channel niche;
- it has not already been queued, scheduled, or recently published;
- channel/day/category/slot quotas still allow it.

Do **not** write fake `has_inventory=1` values.

Do **not** require rating/review thresholds for this provider while those fields are absent from the official import source.

### 3. Shopee CSV freshness is provider-aware

The current generic Auto preflight uses a 120-minute product-sync freshness window. That is appropriate for continuously synced catalog products but would make a CSV product scheduled several hours later fail immediately before publishing.

Define a Shopee CSV Auto freshness window of **72 hours** from `last_synced_at` (falling back to `last_seen_at` where necessary).

The same 72-hour rule must be used by:

- Shopee candidate selection;
- mutable eligibility recheck before scheduling;
- publish preflight.

A stale Shopee product is skipped/fails closed. The operator must re-import a current Shopee Affiliate CSV to refresh it.

This keeps the rolling 48-hour scheduler usable while leaving a safety buffer before the source snapshot is considered stale.

### 4. Shopee candidate selection is explicit and deterministic

Add a dedicated Shopee candidate selector beside `_catalog_auto_candidates()`.

It selects only `SHOPEE_AFFILIATE` rows with image job `READY` and the provider-specific eligibility fields above.

The existing channel niche matcher remains the source of truth. `category_code='khac'` does not bypass niche checks because niche matching also inspects the product name.

Candidate score stays within the scheduler's existing 0..1 scale. For Shopee CSV rows, use the existing product `score` when present; otherwise derive a bounded commission-based score so Shopee candidates can be merged with legacy/catalog candidates without raw currency values dominating the ranking.

The candidate selector must not create posts or mutate Product rows.

### 5. Routing and slot selection are reused unchanged

Do not build a second router.

Shopee candidates pass into the existing `auto_scheduler.route_product()` flow, which continues to own:

- candidate Threads channels;
- niche matching;
- duplicate/cooldown checks;
- slot availability;
- channel/day quota;
- deterministic channel ranking.

Provider-specific differences belong in candidate eligibility and artifact preparation, not in duplicate routing implementations.

### 6. Use the affiliate link already imported from CSV

`_prepare_auto_sales_post_artifacts()` currently has provider-specific handling for the ACCESSTRADE catalog and a legacy fallback that calls `ctx['source'].create_tracking_link()`.

Add an explicit `SHOPEE_AFFILIATE` branch.

For Shopee:

- use `product['affiliate_url']` directly;
- do not call `ctx['source'].create_tracking_link()`;
- keep attribution payload local to ACP without changing the Shopee URL;
- use the enriched `image_path_local` as input to the existing `imaging.compose()` path;
- upload the composed image through existing storage;
- generate caption through existing content generation;
- validate against the routed channel niche.

The imported Shopee affiliate URL is never rewritten during automatic post creation.

### 7. Existing review and publish safety remains authoritative

Post creation behavior remains:

- valid generated content → `PENDING_REVIEW`, then existing Auto flow may `approve_post(..., auto_scheduled=True)` and create a scheduled publish target;
- content validation problems → `DRAFT`/review path, never automatic publish;
- failed eligibility recheck → skip;
- occupied slot/race → skip;
- publish-time stale/unavailable/invalid product → preflight fails closed;
- platform content rejection → existing worker behavior returns non-published content to review as applicable.

The feature must not bypass the existing global publish-worker switch.

### 8. Product Pool becomes an observability surface

The Product Pool should make the automatic behavior understandable without introducing per-product Auto toggles.

For each row, derive an Auto state such as:

- `WAITING_IMAGE` — image is not READY;
- `ELIGIBLE` — image READY and no active post/target yet;
- `SCHEDULED` — an active auto-scheduled target exists;
- `REVIEW` — a DRAFT/PENDING_REVIEW post exists;
- `PUBLISHED` — a recent successful/published post exists;
- `STALE` — Shopee CSV snapshot exceeds the 72-hour freshness rule.

When scheduled, show the target channel and scheduled time when available.

This status is derived from existing Product/Post/PublishTarget data; do not add a second mutable Auto-state table.

The page copy should state that READY products automatically participate in Threads Auto when eligible. Manual image-enrichment buttons are labelled/positioned as recovery actions.

## Failure Handling

### Import succeeds, enrichment fails

The Product remains imported. The enrichment job becomes `NEEDS_HELPER` or `FAILED`. The scheduler never sees it as READY and therefore cannot publish it.

### Public Shopee HTML is blocked

Transition to `NEEDS_HELPER`. No automated browser interaction is added. The operator can use the existing Chrome Helper flow.

### Image download/storage fails

Use the existing bounded retry/error behavior. The product does not become Auto-eligible until the enrichment state is READY.

### Affiliate link is missing/invalid

The product is excluded from candidate selection and fails preflight if a stale scheduled post somehow reaches publish time.

### CSV snapshot becomes stale

Candidate selection and publish preflight fail closed after 72 hours. Re-import refreshes `last_synced_at` and preserves the normal image-enrichment idempotency behavior.

### Scheduler or worker is disabled

No special override is introduced. Channel Auto settings and the global publish-worker switch continue to control automatic scheduling/publishing.

## Data Model

No new database table is required.

Reuse:

- `product`
- `shopee_image_enrichment_job`
- `post`
- `publish_target`
- `job_queue`
- existing channel Auto scheduling fields.

No `product.auto_enabled` field is added because the chosen product policy is: every eligible Shopee image-READY product automatically participates in Auto.

## Files Expected to Change

- `run.py` — add the bounded `shopee-enrich` CLI command.
- `core/pipeline.py` — Shopee candidate selector, provider-specific eligibility, direct affiliate-link artifact preparation.
- `core/auto_scheduler.py` — provider-aware publish preflight freshness/inventory behavior.
- `web/shopee_image_enrichment.py` — derive Auto status for Product Pool rows/summary.
- `web/templates/shopee_image_enrichment.html` — show Auto pipeline status and clarify recovery controls.
- `ops/acp-auto-schedule.service` — run bounded Shopee enrichment before Auto scheduling and increase timeout if required.
- tests covering every new provider-specific contract.
- operator runbook documentation for the new normal flow.

The CSV importer should not need a behavior change because it already enrolls imported products into the enrichment queue.

## Testing Strategy

### Enrichment automation

Verify `run.py shopee-enrich`:

- calls the existing bounded batch runner;
- does not publish;
- returns safe aggregate output;
- handles failures without leaking provider details.

### Candidate selection

Cover Shopee rows that:

- are READY and valid → candidate;
- are PENDING/NEEDS_HELPER/FAILED → excluded;
- have missing/invalid affiliate URL → excluded;
- are stale >72h → excluded;
- are below minimum commission → excluded;
- do not match channel niche → excluded;
- already have active/recent posts → excluded;
- have unknown inventory/rating/review → still allowed when all Shopee-specific requirements pass.

### Artifact preparation

Verify a Shopee candidate:

- uses the exact stored CSV affiliate URL;
- never calls the source tracking-link creator;
- requires enriched local media;
- creates caption/composited image through existing functions.

### Scheduling

Verify READY Shopee products can flow through `fill_auto_schedule()` to an auto-scheduled target while preserving:

- daily target/cap;
- slot occupation checks;
- duplicate/cooldown behavior;
- content validation review fallback.

### Publish preflight

Verify:

- unknown Shopee inventory does not fail by itself;
- <=72h Shopee CSV freshness passes;
- >72h freshness fails;
- invalid affiliate link fails;
- non-Shopee catalog behavior keeps its existing 120-minute/inventory semantics.

### Web

Verify Product Pool reports WAITING_IMAGE, ELIGIBLE, SCHEDULED, REVIEW, PUBLISHED, and STALE from existing records without creating mutable Auto state.

### Regression

Run existing focused suites for:

- Shopee CSV import;
- Shopee image enrichment;
- Shopee Helper;
- auto scheduler;
- pipeline;
- publish worker;
- web routes;
- manage/release verification where affected.

## Acceptance Criteria

1. Importing a valid official Shopee Affiliate CSV continues to succeed without network enrichment inside the HTTP transaction.
2. Imported missing-image products are automatically picked up by the existing timer flow; normal operation requires no manual Enrich click.
3. Publicly enrichable products transition to READY automatically.
4. READY Shopee products can be selected by the existing Threads Auto scheduler despite unknown inventory/rating/review fields.
5. Scheduler uses the exact affiliate URL imported from the CSV and never asks another source to create a tracking link.
6. Scheduler still enforces niche, duplicate/cooldown, quota, slot, content validation, and publish safety.
7. Shopee CSV data older than 72 hours fails closed for Auto.
8. Products requiring Chrome Helper never auto-publish until Helper completion makes them READY.
9. The existing global publish-worker switch and per-channel Auto switch remain authoritative.
10. Product Pool visibly explains each product's Auto state.
11. No Shopee login automation, credential extraction, private API call, CAPTCHA bypass, or anti-bot evasion is introduced.
