# Shopee Affiliate CSV Import Design

**Date:** 2026-08-20  
**Branch:** `feat/shopee-affiliate-csv-import`  
**Base:** `main`

## 1. Goal

Use the official CSV downloaded from Shopee Affiliate's **Lấy link hàng loạt** action as the primary ingestion source for Shopee products and already-created affiliate links.

ACP must not regenerate, resolve, wrap, or append tracking parameters to the CSV's `Link ưu đãi` during this flow.

```text
Shopee Affiliate
  → Hoa hồng Sản phẩm
  → select products
  → Lấy link hàng loạt
  → download CSV
  → ACP /sanpham/shopee-import
  → upload one or more CSVs
  → preview
  → explicit operator confirmation
  → idempotent Product upsert
```

This phase stops at Product data. It does not create posts, approve posts, schedule, or publish.

## 2. Verified input contract

The supplied real Shopee CSV contains exactly these columns:

```text
Mã sản phẩm
Tên sản phẩm
Giá
Doanh thu
Tên cửa hàng
Tỉ lệ hoa hồng
Hoa hồng
Link sản phẩm
Link ưu đãi
```

Observed examples include:

```text
Giá:              53,9k | 1,2tr | 100,0tr
Tỉ lệ hoa hồng:   5% | 42,5% | 4,51%
Hoa hồng:         ₫2.695 | ₫126.000 | ₫4.000.000
Doanh thu:        28 | 1k+ | 10k+ | 300k+
Link sản phẩm:    https://shopee.vn/product/<shop_id>/<item_id>
Link ưu đãi:      https://s.shopee.vn/...
```

Despite the header `Doanh thu`, the supplied values match the sold-count values shown in the Affiliate product UI. This importer therefore treats the field as a **sold-count-like quantity**, stores the numeric floor in `units_sold`/`sold_count`, and never presents it as monetary revenue.

## 3. Scope

### In scope

- New authenticated workspace: `GET /sanpham/shopee-import`.
- Multi-file `.csv` upload.
- UTF-8 and UTF-8 BOM support.
- Strict header validation.
- Pure parsing/normalization layer.
- Row-level errors without blocking valid rows.
- Read-only preview before mutation.
- Short-lived server-side preview batch.
- Explicit Import confirmation.
- Idempotent canonical Shopee Product upsert.
- Preserve official Shopee short affiliate URL unchanged.
- Update price, shop, commission and sold count.
- Preserve richer existing metadata absent from the CSV.
- Shopee price-history integration.
- Aggregate audit events without raw CSV or affiliate URLs.
- Focused parser/DB/web tests plus regression/release verification.
- Operator runbook.

### Out of scope

- Auto-watch of `~/Downloads`.
- Browser filesystem access.
- Shopee login automation.
- Cookie/session/localStorage extraction.
- Open API or Product Feed access.
- CAPTCHA/anti-bot bypass.
- Private API reverse engineering.
- Bulk-link generation from `SHOPEE_AFFILIATE_ID`.
- ACCESSTRADE wrapping for Shopee Direct.
- Automatic post creation, approval, scheduling or publishing.

A Downloads watcher may be designed later, but it must call this importer rather than implement a second ingestion path.

## 4. Existing model reuse and canonical identity

No new Product table is introduced.

The importer reuses the existing `product` row and existing Shopee conventions.

### Product namespace

Imported Shopee CSV Products use:

```text
product.source   = manual_shopee
product.merchant = shopee.vn
```

This is deliberate: the existing manual Shopee flow already uses `ManualShopeeSource.name = "manual_shopee"`, and `product` has the natural uniqueness key:

```text
UNIQUE(source, merchant, external_product_id)
```

Therefore a product previously created through the manual Shopee workflow and the same item later imported from CSV must reuse one Product row.

### Canonical identity

Derive identity from `Link sản phẩm`:

```text
(shop_id, item_id)
```

Require:

```text
CSV "Mã sản phẩm" == derived item_id
```

Mismatch is a row error and causes no mutation.

### Provider field

Do not pretend Shopee CSV is an ACCESSTRADE catalog row. If the current UI requires provider-based filtering, introduce a dedicated stable provider value for imported Shopee catalog records:

```text
SHOPEE_AFFILIATE
```

This provider value is presentation/catalog provenance only. It must not change the natural Product identity above.

## 5. Normalized parser model

Use a pure model with no DB side effects:

```python
ShopeeAffiliateCsvRow(
    item_id: str,
    shop_id: str,
    name: str,
    current_price: int,
    sold_count: int | None,
    shop_name: str | None,
    commission_rate_percent: float | None,
    commission_amount: int | None,
    product_url: str,
    affiliate_url: str,
    source_filename: str,
    source_row_number: int,
)
```

## 6. Field mapping

```text
Mã sản phẩm
  → external_product_id

Tên sản phẩm
  → name

Giá
  → current_price
  → price_min
  → price_max

Tên cửa hàng
  → shop_name

Tỉ lệ hoa hồng
  → commission_rate_percent
  → commission_rate

Hoa hồng
  → commission_amount
  → commission_value

Doanh thu
  → units_sold
  → sold_count

Link sản phẩm
  → canonical product_url
  → detail_link

Link ưu đãi
  → affiliate_url
  → affiliate_short_url
  → affiliate_link_status = READY
```

Commission semantics are explicit:

```text
42,5% → commission_rate_percent = 42.5
      → commission_rate         = 42.5
```

This matches the existing catalog convention, where `commission_rate` is populated with the percentage value rather than a 0–1 fraction.

Commission amount semantics:

```text
₫126.000 → commission_amount = 126000
          → commission_value  = 126000
```

Do not fabricate fields absent from the CSV: image, original price, rating, review count, category/facts, availability evidence, etc.

When updating an existing Product, absent CSV fields must remain unchanged.

## 7. Parsing rules

### 7.1 Price

```text
53,9k     → 53_900
300,0k    → 300_000
1,2tr     → 1_200_000
2,2tr     → 2_200_000
100,0tr   → 100_000_000
```

Rules:

- trim whitespace;
- Vietnamese decimal comma supported;
- `k` = 1,000;
- `tr` = 1,000,000;
- result must be a positive integer VND;
- malformed/zero/negative price is a row error.

### 7.2 Commission percent

```text
5%      → 5.0
42,5%   → 42.5
4,51%   → 4.51
```

Accept `0 <= rate <= 100`; otherwise row error.

### 7.3 Commission amount

```text
₫2.695       → 2_695
₫126.000     → 126_000
₫4.000.000   → 4_000_000
```

Strip the currency marker and Vietnamese thousands separators. Negative or malformed values are row errors.

### 7.4 Sold-count-like field

```text
0      → 0
28     → 28
1k+    → 1_000
10k+   → 10_000
20k+   → 20_000
300k+  → 300_000
```

The `+` value is an approximate floor. ACP stores the numeric floor only.

### 7.5 Product URL

Accept only direct HTTPS Shopee Vietnam product URLs supported by the existing canonicalizer. Persist only canonical form:

```text
https://shopee.vn/product/<shop_id>/<item_id>
```

### 7.6 Affiliate URL

For this initial contract, accept only HTTPS short links on:

```text
s.shopee.vn
```

Do not resolve the URL during preview/import.

## 8. Multi-file and deduplication behavior

One preview request can include multiple CSV files.

Deduplication key:

```text
(shop_id, item_id)
```

If a product appears more than once in the upload:

- the **last valid occurrence in upload order wins**;
- earlier occurrences are marked `DUPLICATE_IN_UPLOAD`;
- source filename + row number are retained for preview diagnostics;
- exactly one DB mutation occurs for that canonical product.

## 9. Preview-before-import

Uploading files must not mutate Product data.

```text
POST /sanpham/shopee-import/preview
  → validate files
  → parse rows
  → normalize/dedupe
  → compare with DB read-only
  → create short-lived preview batch
  → render preview
```

Statuses:

```text
NEW
UPDATED
UNCHANGED
DUPLICATE_IN_UPLOAD
ERROR
```

Preview summary example:

```text
Files: 5
Rows read: 500
Valid unique: 472
New: 320
Will update: 140
Unchanged: 12
Duplicates: 18
Errors: 10
```

Preview table shows:

```text
status
item_id
name
price
sold count
shop
commission %
commission amount
product URL
affiliate URL (truncated visually, full copy value)
source filename + row
error message
```

## 10. Preview batch and tamper resistance

The browser must not post arbitrary normalized Product rows back to the import endpoint.

Use an in-memory short-TTL batch store, matching the existing helper-pairing pattern.

Batch contents:

```text
batch_id
created_at / expires_at
normalized unique valid rows
preview statuses
aggregate counts
```

Rules:

- raw CSV bytes are not retained;
- batch stores normalized values only;
- TTL = 15 minutes;
- import consumes a batch once after successful completion;
- expired/replayed/unknown batch ID is rejected;
- process restart may invalidate pending previews; that is acceptable for this phase.

If persistent preview batches become necessary later, add a staging table in a separate change.

## 11. Product upsert semantics

### Existing Product

Find using:

```text
source='manual_shopee'
merchant='shopee.vn'
external_product_id=item_id
```

Update only CSV-owned fields:

```text
name
current_price
price_min
price_max
shop_name
sold_count
units_sold
commission_rate
commission_rate_percent
commission_amount
commission_value
product_url
detail_link
affiliate_url
affiliate_short_url
affiliate_link_status
affiliate_link_error
affiliate_link_created_at
last_seen_at
last_synced_at
updated_at
```

Preserve fields not provided by CSV, including:

```text
image_url_original
main_image_url
image_path_local
original_price
rating
review_count
category_code/category_data
facts/content metadata
post history
first_seen_at
created_at
```

### New Product

Insert with:

```text
source              = manual_shopee
merchant            = shopee.vn
provider            = SHOPEE_AFFILIATE
external_product_id = item_id
name                = CSV name
current_price       = parsed price
price_min/max       = parsed price
commission_*        = parsed values
sold_count          = parsed sold count or 0
units_sold          = parsed sold count
product_url         = canonical product URL
detail_link         = canonical product URL
affiliate_url       = official Link ưu đãi
affiliate_short_url = official Link ưu đãi
affiliate_link_status = READY
is_available        = 1
category_code       = khac
```

For new rows only, `category_code='khac'` is the existing generic safe fallback and is not presented as a Shopee-provided business fact.

No image is fabricated. A later content action may require metadata/helper completion before creating a post.

## 12. Affiliate semantics

`Link ưu đãi` is authoritative for this importer.

Rules:

- persist unchanged;
- do not append or rewrite Sub_ID;
- do not call the Phase 1 generator;
- do not resolve the short URL;
- set `affiliate_link_status='READY'`;
- clear `affiliate_link_error`;
- set/update `affiliate_link_created_at` at confirmed import time;
- if a later confirmed CSV supplies a different valid affiliate URL for the same item, replace the stored affiliate URL with the newer one.

## 13. Price history and metadata source

CSV import is an operator-confirmed price observation.

Extend Shopee metadata/price source allowlist with exactly:

```text
affiliate_csv
```

The source allowlist becomes conceptually:

```text
server
helper
manual
affiliate_csv
```

Reuse existing Shopee price-history semantics:

- first observation → one history row;
- unchanged price → no duplicate history point;
- changed price → exactly one new history row;
- source = `affiliate_csv`.

Do not change ACCESSTRADE price-history behavior.

The CSV may also refresh Shopee metadata cache with the fields it actually owns (`name`, `current_price`, `shop`) using source `affiliate_csv`; partial-cache merge must preserve richer existing cache fields such as image/original price.

## 14. Product Pool visibility

CSV-imported Shopee products must be visible to the operator after import.

The implementation must not rely exclusively on the current ACCESSTRADE-specific `ProductService.search_local(provider=ACCESSTRADE_TIKTOK)` query.

Use one of the existing source-aware `/sanpham` patterns or add a small Shopee catalog query for `provider='SHOPEE_AFFILIATE'` so the operator can verify imported rows, filter by commission/sold count, and later start the existing Shopee draft workflow.

This phase does not redesign the whole Product Service abstraction.

## 15. UI

Add navigation entry:

```text
Shopee CSV Import
```

Page:

```text
Shopee Affiliate CSV Import

[ Chọn file CSV ... ]   multiple
[ Preview ]

Nguồn hỗ trợ:
Shopee Affiliate → Hoa hồng Sản phẩm → Lấy link hàng loạt
```

After preview:

```text
[ summary cards ]
[ all | new | update | unchanged | duplicate | error ]
[ preview table ]

[ Hủy ] [ Import vào Product Pool ]
```

No CTA creates a Post or publishes.

## 16. File/request safety

Initial limits:

```text
max files:             20
max file size:         5 MiB each
max total parsed rows: 20,000
preview TTL:           15 minutes
```

Rules:

- `.csv` only;
- reject binary/corrupt content;
- treat CSV as text only; no formulas/macros are executed;
- do not persist raw uploaded files;
- do not log CSV bodies;
- dashboard authentication + CSRF required for preview and import;
- no secret/token/session logging.

## 17. Error handling

### Batch-level

- no file;
- unsupported extension;
- file/request too large;
- missing required columns;
- invalid encoding/CSV structure;
- row cap exceeded.

### Row-level

- missing item ID/name/price/product URL/affiliate URL;
- invalid numeric formats;
- product URL invalid;
- CSV item ID != URL item ID;
- affiliate host invalid.

A bad row does not block valid rows.

## 18. Audit

Record only aggregate events:

```text
shopee_csv_preview
shopee_csv_import_completed
```

Safe detail:

```json
{
  "files": 5,
  "rows": 500,
  "new": 320,
  "updated": 140,
  "unchanged": 12,
  "duplicate": 18,
  "error": 10
}
```

Never put raw rows, full affiliate URLs or CSV contents in audit detail.

## 19. Testing

### Parser tests

Cover:

- exact 9-column contract;
- UTF-8 BOM;
- quoted names containing commas;
- `53,9k`, `1,2tr`, `100,0tr`;
- `42,5%`;
- `₫4.000.000`;
- `300k+`;
- canonical identity;
- item-ID mismatch;
- invalid affiliate host;
- missing columns;
- duplicate rows across multiple files.

### DB tests

Cover:

- new Product insert;
- existing manual Shopee Product reuse;
- repeated import remains one Product;
- richer metadata preserved;
- latest affiliate URL replaces prior one;
- READY state/error clearing/timestamp;
- unchanged price no duplicate history;
- changed price one new `affiliate_csv` history row;
- malformed row cannot mutate DB.

### Web tests

Cover:

- authentication;
- CSRF;
- multi-file preview;
- preview is read-only;
- 15-minute batch lifecycle;
- confirmation imports only stored normalized batch;
- replay/expired batch rejection;
- request/file/row caps;
- deterministic summary rendering.

### Regression/release

Run relevant Shopee tests plus:

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_pipeline
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_pilot
python tests/test_manage.py
./manage.sh test
git diff --check
```

No test publishes a real Threads post.

## 20. Rollout

1. Parser + format normalization.
2. Preview batch lifecycle.
3. Product upsert + `affiliate_csv` price/cache source.
4. Authenticated multi-file preview/import UI.
5. Shopee imported-product visibility.
6. Docs/tests.
7. Full mock regression/release gate.
8. Controlled pilot with a copy of the real Shopee CSV against a non-production/test DB.
9. Merge to `main` only after verification.

## 21. Definition of done

- Multiple official Shopee Affiliate CSVs upload in one action.
- Real Vietnamese display formats parse correctly.
- `Doanh thu` is handled as sold-count-like data, never monetary revenue.
- Preview performs no Product mutation.
- Import requires explicit operator confirmation.
- Duplicate canonical items remain one Product.
- Existing manual Shopee Product is reused.
- Richer existing metadata survives CSV refresh.
- Official `Link ưu đãi` is preserved unchanged and marked READY.
- Price history follows unchanged/change semantics with source `affiliate_csv`.
- Imported products are visible to the operator after import.
- No bulk-link generator is called.
- No Post/job/approval/publish is created by import.
- Row errors are isolated and visible.
- Focused/regression/release tests pass with actual output.
- No raw CSV/runtime DB/secrets are committed.
