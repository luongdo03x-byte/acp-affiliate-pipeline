# Shopee Image Enrichment & Auto Publish Runbook

## Normal operation

The normal Shopee Affiliate workflow is:

```text
Official Shopee Affiliate CSV
        ↓
confirm import
        ↓
Product + shopee_image_enrichment_job
        ↓
SHOPEE_ENRICH_PRODUCT queued immediately for touched PENDING products
        ↓
existing ACP worker (normally next minute)
        ↓
READY / NEEDS_HELPER / FAILED
        ↓
Shopee Product Pool
        ↓
Shopee-only Threads Auto candidates
        ↓
niche / duplicate / cooldown / category cap / quota / slot checks
        ↓
generate + validate content
        ↓
SCHEDULED or REVIEW
        ↓
existing publish worker + publish-time preflight
```

A successful CSV confirm does **not** wait for public Shopee network/image work. It only writes Product data transactionally and queues non-publish enrichment work. The existing 60-minute enrichment stage remains a recovery/backfill path.

During normal operation, the operator does not need to press **Recovery: quét thiếu ảnh** or **Recovery: enrich 20** after importing a CSV.

## Source of truth

The official CSV remains the source of:

- Shopee product identity;
- current price;
- sold count when present;
- commission data;
- the exact affiliate URL.

Automatic post creation uses the stored `product.affiliate_url` exactly. ACP does not ask another affiliate source to generate or rewrite that Shopee URL.

Image enrichment is additive. Public HTML or Chrome Helper may fill image fields, shop name and other missing enrichment-owned metadata, but does not overwrite stronger CSV-owned commercial fields.

## Immediate enrichment trigger

After `import_rows()` commits successfully, the confirm route receives the touched Product IDs and inspects their current `shopee_image_enrichment_job` rows.

Only `PENDING` generations receive an execution job:

```text
job_type = SHOPEE_ENRICH_PRODUCT
payload  = {"product_id": "..."}
idempotency_key = shopee-enrich:<product_id>:<enrichment_updated_at>
```

The job handler reuses the existing `enrich_product()` primitive, safe HTTP client, deterministic media path and configured storage backend.

Important behavior:

- `READY` products are not re-enriched on re-import;
- `FAILED` and `NEEDS_HELPER` are not silently reset by import;
- repeated imports of the same PENDING generation do not create duplicate execution jobs;
- Retry creates a new PENDING generation, which can be queued again;
- if the immediate queue trigger fails after Product commit, the Product import remains successful and the 60-minute recovery path can rediscover it;
- the job is non-publish work, so the ACP worker may run it even while live publishing is disabled.

## Auto eligibility for `SHOPEE_AFFILIATE`

Threads Auto candidate selection is now exclusive to:

```text
provider='SHOPEE_AFFILIATE'
```

Legacy, AccessTrade and TikTok catalog products remain available to their manual workflows but do not enter the installed Auto candidate source.

A Shopee product can enter Auto only when:

- `provider='SHOPEE_AFFILIATE'`;
- `is_available=1`;
- `affiliate_link_status='READY'`;
- `affiliate_url` is a valid absolute HTTP(S) URL;
- image enrichment status is `READY` and media is usable;
- the official CSV snapshot is no older than 72 hours;
- commission meets the configured minimum;
- category is not blocked;
- product matches the target Threads channel niche;
- duplicate/cooldown and category/day checks pass;
- the channel itself remains enabled, ACTIVE and Auto-enabled;
- normal target/quota/slot checks pass later in the scheduler.

Unknown Shopee CSV inventory, rating and review fields are not replaced with fake values and do not need to satisfy legacy catalog thresholds.

Shopee freshness is 72 hours from `last_synced_at`, falling back to `last_seen_at`.

## Image enrichment lifecycle

Statuses are:

```text
PENDING
PUBLIC_FETCH
DOWNLOADING
NEEDS_HELPER
READY
FAILED
```

The existing bounded enrichment implementation remains authoritative:

- public `https://shopee.vn/...` HTML only;
- JSON-LD/OpenGraph/product metadata only;
- safe HTTP / SSRF protections;
- maximum image size 8 MiB;
- Pillow byte validation;
- deterministic local media path;
- existing storage abstraction;
- maximum 2 public metadata attempts and 2 image download attempts per product;
- maximum 20 products in each recovery batch.

A Product failure is isolated from other products.

## Chrome Helper fallback

If public Shopee HTML is blocked, incomplete or does not expose an image, the product moves to `NEEDS_HELPER`.

The existing Helper remains operator-assisted. ACP does not automate login, extract cookies/session/localStorage, solve CAPTCHA, evade anti-bot controls or call private Shopee APIs.

A `NEEDS_HELPER` product cannot enter Auto. After Helper completion safely materializes the image and moves the job to `READY`, the product can participate in a later Auto schedule pass.

## Product Pool v2

Open:

```text
/sanpham/shopee
```

Supported query parameters:

```text
q=<product/shop text>
niche=<niche code|all>
auto=all|eligible|waiting_image|stale|ineligible|review|scheduled|published
image=all|ready|missing|needs_helper|failed|pending
usage=all|unused|scheduled|review|published
page=<1-based page>
per_page=20|50|100
```

Invalid values degrade to safe defaults. Default page size is 20 and an out-of-range page is clamped to the final valid page.

The global summary is independent of table filters/pages and shows:

- total products;
- unused;
- exact Auto eligible;
- scheduled;
- published;
- review;
- image READY;
- needs Helper;
- image failed;
- stale official CSV.

Per-niche statistics use the same `core.niche.NICHES` matcher as the scheduler. A product may match multiple niches, so niche totals intentionally overlap and must not be summed into the global total.

### Usage state

Usage is mutually exclusive with this precedence:

```text
PUBLISHED > SCHEDULED > REVIEW > UNUSED
```

- `PUBLISHED`: post is PUBLISHED or any target is SUCCESS;
- `SCHEDULED`: an Auto target is SCHEDULED/PENDING/RUNNING;
- `REVIEW`: post is DRAFT/PENDING_REVIEW;
- `UNUSED`: none of the above.

A stale CSV or missing image does not hide historical usage.

### Auto/health state

For already-used products, Auto state mirrors `PUBLISHED`, `SCHEDULED` or `REVIEW`.

For UNUSED products:

- `WAITING_IMAGE`: enrichment is not READY;
- `STALE`: image is READY but CSV snapshot is older than 72 hours;
- `ELIGIBLE`: at least one currently active, enabled, Auto-enabled Threads channel accepts the product through the real `_shopee_product_auto_eligibility()` function;
- `INELIGIBLE`: READY/fresh but every active Auto channel rejects it.

This makes `Auto Eligible` an exact current value rather than a UI approximation.

## Recovery controls

These remain available as secondary controls:

```text
Recovery: quét thiếu ảnh
Recovery: enrich 20
Retry
Mở Shopee & dùng Helper
```

The 60-minute timer remains unchanged. It is recovery/backfill, not the primary post-import trigger.

## Scheduler and publish safety

The Shopee integration reuses the existing Threads scheduler, `publish_target`, `job_queue`, publisher and preflight. It does not introduce another router or publisher.

Existing controls remain authoritative:

- per-channel Auto switch;
- channel ACTIVE/enabled state;
- niche matching;
- duplicate/cooldown protection;
- daily target/cap;
- category/day cap;
- posting slots/min gap;
- content validation;
- publish-time preflight;
- global publish-worker switch.

No automated verification for this feature should publish another live Threads post.

## Timer service

The recovery service still runs on its existing 60-minute cadence:

```text
1. run.py product-sync
2. shopee_auto_enrich.py
3. run.py auto-schedule
4. run.py worker-once
```

The separate existing `acp-worker.timer` remains the normal minute-level worker that can process `SHOPEE_ENRICH_PRODUCT` work.

## Verification

Use mock adapters only.

From the directory that contains the `acp` package:

```bash
export ACP_ENV=test
export ACP_ADAPTER=mock
export ACP_SOURCE=mock
export ACP_CAPTION_LLM=
export ACP_CONTENT_ENGINE_LLM=
export PYTHONPATH="$PWD"

python -m unittest \
  acp.tests.test_shopee_product_pool_v2 \
  acp.tests.test_shopee_product_pool_v2_edges \
  acp.tests.test_shopee_import_trigger_resilience \
  acp.tests.test_shopee_auto_pipeline \
  acp.tests.test_shopee_csv_web \
  acp.tests.test_shopee_csv_enrichment \
  acp.tests.test_shopee_image_enrichment_flow \
  acp.tests.test_shopee_image_enrichment_web \
  acp.tests.test_auto_scheduler \
  acp.tests.test_auto_scheduler_safety -v
```

Project release gate from inside the repository:

```bash
./manage.sh test
git diff --check
python -m compileall core web tests adapters run.py shopee_auto_enrich.py >/dev/null
```

Do not mark the branch release-ready until focused tests, project tests, compile and diff checks are green on a fresh checkout/worktree.

## Controlled pilot

After the mock/release gate is green:

1. import a small official Shopee Affiliate CSV;
2. confirm Product rows and `SHOPEE_ENRICH_PRODUCT` work are created without manual Enrich;
3. keep live publishing disabled for the first observation pass;
4. confirm publicly enrichable products move to READY;
5. confirm blocked products move to NEEDS_HELPER and do not enter Auto;
6. verify Product Pool global/per-niche counts and filters;
7. verify the Auto candidate source contains Shopee Affiliate only;
8. enable real publishing only through the normal operator-controlled production procedure.
