# ACP — Shopee Product Intelligence Phase 3 Runbook

Date: 2026-08-17
Scope: metadata cache, canonical Product reuse, sourced price history and manual price refresh.

## Data semantics

### Canonical identity

Shopee product identity is the canonical pair:

```text
(shop_id, item_id)
```

A slug URL, `/product/<shop>/<item>` and supported OPA landing shapes resolve to the same canonical URL. Affiliate URL is **not** the Product natural key.

The existing Product unique key remains in force for manual Shopee:

```text
source = manual_shopee
merchant = shopee.vn
external_product_id = item_id
```

Repeated confirmation of the same item therefore reuses the Product row; multiple posts may still reference that Product.

## Metadata cache

Table: `shopee_metadata_cache`.

Stores only bounded product metadata plus:

- source: `server`, `helper`, or `manual`;
- `observed_at`;
- `updated_at`;
- optional linked `product_id` after operator-confirmed Product creation.

Default freshness window is 24 hours. Cache is a dependency-reduction fallback, **not realtime truth**. UI explicitly labels cached data as `không phải realtime`.

Resolution behavior:

```text
server metadata usable
→ cache as source=server
→ use server result

server blocked/empty
→ fresh cache exists
→ use cache and show original source/timestamp

stale/missing cache
→ Chrome Helper or manual fallback
```

A resolve/cache read never creates Product/Post rows.

## Confirmed Product and price history

The legacy pipeline still owns Product upsert. Phase 3 normalizes the Shopee history after confirmation:

- first confirmed price: one history row;
- same confirmed price again: no extra history row;
- changed price: append exactly one row;
- surviving history row records source when Phase 3 knows it.

This Shopee-specific normalization does **not** alter ACCESSTRADE/feed sampling behavior.

## Metadata provenance in confirmation UI

`metadata_source` is carried as presentation/provenance state:

- server metadata → `server`;
- Chrome Helper success → `helper`;
- trusted operator edit → `manual`;
- fresh cache preserves its original `server/helper/manual` source and shows observation time.

Manual inputs remain editable at all times.

## Làm mới giá

`Làm mới giá` is an explicit operator action.

It tries:

```text
existing server metadata path
→ fresh cache
→ helper_required
→ operator chooses Chrome Helper or manual entry
```

The endpoint never starts Selenium/Playwright, never clicks Chrome Helper automatically, never loops/crawls products, and never publishes.

A refresh before confirmation does not create a Product. Confirmed data is linked/cached only after the existing create-draft action succeeds and redirects to `/duyet`.

## Database upgrade gate

Before running Phase 3 against an existing ACP database, use the normal upgrade path so central `db.init_db()` applies the registered schema/migration:

- create `shopee_metadata_cache` idempotently;
- add nullable `product_price_history.source` idempotently;
- no table drop/rebuild for these Phase 3 changes.

Do not run ad-hoc destructive SQL against the live DB.

## Verification commands on ACP Ubuntu

From the directory containing `acp/`:

```bash
export ACP_ADAPTER=mock
export ACP_SOURCE=mock

python -m acp.tests.test_shopee_product_intel -v
python -m acp.tests.test_shopee_product_upsert -v
python -m acp.tests.test_shopee_product_intel_web -v
python -m acp.tests.test_shopee_product_intel_ui -v
python -m acp.tests.test_shopee_helper -v
python -m acp.tests.test_shopee_helper_ui -v
python -m acp.tests.test_pipeline
python -m acp.tests.test_pilot
cd acp
python tests/test_manage.py
./manage.sh test
git diff --check
```

Do not deploy if an actual regression fails. Record missing dependency/CI/billing failures as blockers, not passes.

## Manual pilot

1. Resolve one Shopee product.
2. Check metadata provenance text and timestamp.
3. If a fresh cache is used, confirm UI says it is not realtime.
4. Click **Làm mới giá** once.
5. If server works, verify new price against visible Shopee page before confirmation.
6. If `helper_required`, choose Chrome Helper manually or enter the price manually.
7. Create a draft only if you want to verify the normal draft path; stop at `/duyet`.
8. Re-confirm the same product with unchanged price in a test DB and verify no duplicate unchanged-price history row.
9. Change the test price, confirm again, and verify one new sourced history row.
10. Do not approve/publish as part of this Phase 3 pilot.

## Non-goals

- continuous Shopee crawler;
- headless browser refresh;
- CAPTCHA bypass;
- cookie/session extraction;
- private API reverse engineering;
- automatic draft creation from cache/refresh;
- automatic approval/publish;
- changing ACCESSTRADE attribution or price-history sampling semantics.
