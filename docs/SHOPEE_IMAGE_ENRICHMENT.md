# Shopee Image Enrichment & Auto Publish Runbook

## Normal operation

The normal Shopee Affiliate workflow is now:

```text
Official Shopee Affiliate CSV
        ↓
Import into ACP
        ↓
enqueue image enrichment
        ↓
hourly ACP Auto service
        ↓
product-sync → Shopee enrich → auto-schedule → worker-once
        ↓
READY Shopee products become Threads Auto candidates
        ↓
niche / duplicate / cooldown / quota / slot checks
        ↓
generate + validate content
        ↓
SCHEDULED or REVIEW
        ↓
existing publish worker
```

During normal operation, the operator does **not** need to press **Quét sản phẩm thiếu ảnh** or **Enrich 20 sản phẩm thiếu ảnh** after importing the CSV. Those buttons remain recovery controls.

The timer cadence remains the existing 60 minutes. The Shopee enrichment stage is bounded to at most 20 products per timer pass.

## Source of truth

The official CSV remains the source of:

- Shopee product identity;
- current price;
- sold count when present;
- commission data;
- the affiliate URL.

Automatic post creation uses the exact `product.affiliate_url` imported from the CSV. ACP does not ask another affiliate source to generate or rewrite that Shopee URL.

Image enrichment is additive. Public HTML or Chrome Helper may fill image fields, shop name and other missing enrichment-owned metadata, but does not overwrite stronger CSV-owned commercial fields.

## Auto eligibility for `SHOPEE_AFFILIATE`

Shopee Affiliate CSV products intentionally have incomplete commerce metadata. In particular, `has_inventory`, rating and review count may be unknown. ACP does not write fake inventory values and does not require missing rating/review values to pass legacy filters.

A Shopee product can enter Auto only when:

- `provider='SHOPEE_AFFILIATE'`;
- `is_available=1`;
- `affiliate_link_status='READY'`;
- `affiliate_url` is a valid absolute HTTP(S) URL;
- image enrichment status is `READY`;
- enriched media is usable;
- the CSV snapshot is no older than 72 hours;
- commission meets the configured minimum;
- the category is not blocked;
- the product matches the target Threads channel niche;
- duplicate/cooldown, category/day, channel quota and slot checks pass.

Shopee freshness is 72 hours from `last_synced_at`, falling back to `last_seen_at`. Re-import a current official CSV when a product becomes `STALE`.

Other providers keep their existing inventory and 120-minute catalog freshness semantics.

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

The automatic stage uses the existing bounded enrichment implementation:

- public `https://shopee.vn/...` HTML only;
- JSON-LD/OpenGraph/product metadata only;
- image download through ACP safe HTTP / SSRF protections;
- max image size 8 MiB;
- Pillow byte validation;
- deterministic local media path;
- existing storage abstraction;
- at most 2 public metadata attempts and 2 image download attempts per product;
- max 20 products per batch.

A Product failure is isolated from the rest of the batch.

## Chrome Helper fallback

If public Shopee HTML is blocked, incomplete or does not expose an image, the product moves to `NEEDS_HELPER`.

Only then does the operator need to use:

```text
Mở Shopee & dùng Helper
```

The existing Helper remains operator-assisted. ACP does not automate login, extract cookies/session/localStorage, solve CAPTCHA, evade anti-bot controls or call private Shopee APIs.

A `NEEDS_HELPER` product cannot enter Auto. After Helper completion safely materializes the image and moves the job to `READY`, the product can participate in a later Auto schedule pass.

## Product Pool

Open:

```text
/sanpham/shopee
```

The table shows image enrichment state plus a derived Auto state. Auto state is read from existing Product/Post/PublishTarget records; no second mutable Auto-state table exists.

Possible Auto states:

- `WAITING_IMAGE` — image is not READY;
- `STALE` — official CSV snapshot is older than 72 hours;
- `ELIGIBLE` — READY/fresh and currently unused by an active post;
- `SCHEDULED` — an auto-scheduled publish target exists;
- `REVIEW` — generated content is in DRAFT/PENDING_REVIEW;
- `PUBLISHED` — the product already has a published/successful post.

For `SCHEDULED`, the Product Pool also shows the target channel and scheduled timestamp when available.

## Recovery controls

The following controls remain available but are not required in the normal path:

```text
Recovery: quét thiếu ảnh
Recovery: enrich 20
Retry
Mở Shopee & dùng Helper
```

Backfill is idempotent by `product_id`. Re-importing the same official CSV does not create duplicate enrichment jobs.

## Scheduler and publish safety

The Shopee integration reuses the existing Threads scheduler. It does not create a second router or publish worker.

The following existing controls remain authoritative:

- per-channel `auto_schedule_enabled`;
- channel ACTIVE/enabled state;
- niche matching;
- duplicate and cooldown protection;
- daily target/cap;
- category/day cap;
- available posting slots;
- content validation;
- publish-time preflight;
- global publish-worker switch.

Clean generated content can be auto-approved into a scheduled target. Content with validation problems remains in the review path and is not automatically published.

Publish preflight fails closed if a Shopee product has become unavailable, its affiliate URL is invalid, the image is no longer READY, it no longer matches the channel, or the official CSV snapshot is older than 72 hours.

## Timer service

`ops/acp-auto-schedule.service` runs:

```text
1. run.py product-sync
2. shopee_auto_enrich.py
3. run.py auto-schedule
4. run.py worker-once
```

`ops/acp-auto-schedule.timer` remains on the existing 60-minute cadence. The service timeout is increased to allow the bounded Shopee enrichment pass to complete.

## Image storage

Validated local files use deterministic names:

```text
var/media/shopee_<shop_id>_<item_id>.<verified-extension>
```

Supported decoded formats are JPEG, PNG, WEBP and GIF. HTML/JSON masquerading as an image, corrupt bytes and unsupported formats are rejected.

Successful enrichment fills:

```text
image_url_original
image_path_local
main_image_url
```

## Verification

Use mock adapters. Never verify this feature by publishing to a real Threads account.

Focused gate:

```bash
export ACP_ENV=test
export ACP_ADAPTER=mock
export ACP_SOURCE=mock
export ACP_CAPTION_LLM=
export ACP_CONTENT_ENGINE_LLM=

python -m unittest \
  tests.test_shopee_auto_pipeline \
  tests.test_shopee_auto_state \
  tests.test_shopee_csv_enrichment \
  tests.test_shopee_image_enrichment \
  tests.test_shopee_image_enrichment_web \
  tests.test_shopee_helper \
  tests.test_shopee_polish_resilience \
  tests.test_auto_scheduler \
  tests.test_auto_scheduler_safety -v
```

Project release gate:

```bash
./manage.sh test
git diff --check
python -m compileall core web tests adapters run.py shopee_auto_enrich.py >/dev/null
```

Do not mark the branch release-ready until these commands pass on a fresh local checkout/worktree.

## Controlled pilot

After the mock/release gate is green:

1. import a small official Shopee Affiliate CSV;
2. confirm rows appear in `/sanpham/shopee` without manually running Enrich;
3. run the bounded enrichment stage manually only if testing the timer stage in isolation;
4. verify publicly enrichable products become READY;
5. verify blocked products become NEEDS_HELPER and never enter Auto;
6. keep the global publish worker disabled for the first real-data observation pass;
7. verify eligible READY products receive the expected derived Auto state and routing candidate behavior;
8. enable real publishing only through the normal operator-controlled production procedure.
