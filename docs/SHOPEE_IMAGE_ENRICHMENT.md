# Shopee Image Enrichment Runbook

## Purpose

This workflow adds a validated product image to `SHOPEE_AFFILIATE` Products that were imported from official Shopee Affiliate bulk-link CSV files.

The CSV remains the source of affiliate/product identity, price, sold count and commission data. Image enrichment is additive and does not replace those CSV-owned values.

## Source policy

ACP accepts Shopee metadata from only two sources in this phase:

1. public product-page HTML on `https://shopee.vn/...`;
2. rendered DOM metadata sent by the existing operator-assisted ACP Shopee Helper.

ACP does **not** use Shopee Open API for this workflow and does not call private `/api/v4/...` metadata endpoints.

The workflow does not automate Shopee login, read cookies/session/localStorage credentials, solve CAPTCHA, evade anti-bot controls, reverse engineer private APIs, run an unbounded crawler, create posts, approve posts or publish content.

## Data flow

```text
Official Shopee Affiliate CSV
        ↓
CSV Import
        ↓
SHOPEE_AFFILIATE Product
        ↓
missing usable image?
        ├─ no  → READY / no work
        └─ yes → PENDING
                   ↓
              PUBLIC_FETCH
                   ↓
          public HTML has image?
             ├─ yes → DOWNLOADING → validate bytes → local/storage → READY
             └─ no  → NEEDS_HELPER
                            ↓
                      Chrome Helper
                            ↓
                      DOWNLOADING
                            ↓
                    READY or FAILED
```

## Workspace

Open:

```text
/sanpham/shopee
```

The workspace is separate from the existing ACCESSTRADE catalog query at `/sanpham`.

Filters:

- `Tất cả`
- `Thiếu ảnh`
- `Đã có ảnh`
- `Cần Helper`
- `Lỗi`

Each row shows the image/placeholder, product name, price, sold count, commission, enrichment status, bounded error message and actions appropriate to the current state.

## Enrolling Products

New/updated/unchanged Products confirmed through the Shopee Affiliate CSV importer are enrolled idempotently when they still need an image.

For Products imported before this feature, press:

```text
Quét sản phẩm thiếu ảnh
```

This scans existing `provider='SHOPEE_AFFILIATE'` Products and creates missing enrichment jobs without re-importing CSV files.

Re-running backfill or re-importing the same CSV does not create duplicate jobs because the job key is `product_id`.

## Status meanings

### `PENDING`

Eligible for the next public enrichment attempt.

### `PUBLIC_FETCH`

ACP is reading the canonical public Shopee product HTML. It parses JSON-LD Product metadata and OpenGraph/product meta tags only.

### `DOWNLOADING`

ACP has an image URL and is downloading/validating the image.

### `NEEDS_HELPER`

The public product page was blocked/incomplete or did not expose a usable image. Use the ACP Shopee Helper from the rendered product tab.

### `READY`

ACP has a usable Product image. The Product contains the source image URL plus ACP-managed image fields.

### `FAILED`

Automatic image download, decode, local write or storage publication exhausted the bounded retry budget, or another isolated enrichment failure occurred. Use `Retry` after the underlying problem is corrected.

## Batch behavior

Press:

```text
Enrich 20 sản phẩm thiếu ảnh
```

One operator action processes at most 20 `PENDING` jobs.

Defaults:

```text
batch size:                  20
concurrency:                 1
public-request delay:        1.5 seconds
max public attempts/product: 2
max image attempts/product:  2
```

ACP does not automatically start the next batch. Run another batch when desired.

A failure on one Product is isolated; the batch continues with the remaining selected Products.

If the process dies while a job is in `PUBLIC_FETCH` or `DOWNLOADING`, a later batch can recover a transient job older than 10 minutes back to `PENDING` unless the Product already has usable media.

## Chrome Helper fallback

For a `NEEDS_HELPER` Product, press:

```text
Mở Shopee & dùng Helper
```

ACP then:

1. loads the Product server-side and issues the existing one-time Helper pairing token bound to that Product's canonical Shopee URL;
2. opens the canonical product page in a new tab;
3. waits for the operator to view the page and click ACP Shopee Helper;
4. accepts only allowlisted rendered metadata fields;
5. verifies the observed tab is the same canonical Shopee Product;
6. consumes ready Helper metadata only for the Product the token is bound to;
7. downloads the returned image URL again on the ACP server through the same safe image-validation path.

The one-time token is never rendered as a static Product-table value and is not stored in the database/audit data.

A token ready for Product A cannot be completed against Product B.

## Image validation and storage

Image URL requirements include HTTPS, no embedded credentials, a normal HTTPS port and existing ACP safe HTTP / SSRF validation.

Download size is capped at 8 MiB.

ACP validates actual bytes with Pillow. Supported decoded formats:

- JPEG
- PNG
- WEBP
- GIF

HTML/JSON masquerading as an image, corrupt image bytes and unsupported image formats are rejected.

The local deterministic filename is:

```text
var/media/shopee_<shop_id>_<item_id>.<verified-extension>
```

Examples:

```text
var/media/shopee_196194160_20834209498.jpg
var/media/shopee_1240355310_24676329852.webp
```

A valid deterministic local file is reused rather than downloaded again. Temporary files from interrupted writes are not accepted as final cached media.

After local validation, ACP calls the existing storage abstraction:

- local storage → public ACP `/media/...` URL through `ACP_MEDIA_BASE_URL`;
- S3/R2 → existing configured storage backend/public URL.

Product fields after success:

```text
image_url_original = original Shopee image URL
image_path_local    = verified ACP local file
main_image_url      = public URL produced by ACP storage
```

If storage publication fails, the verified local file may remain for retry, but the job is not marked `READY` until Product media is consistent.

## Non-destructive metadata policy

Public HTML / Helper enrichment may fill blank enrichment fields such as:

- `image_url_original`
- `image_path_local`
- `main_image_url`
- `shop_name`
- `original_price`
- product name only if blank

It does not overwrite CSV-owned current price, sold count, commission fields, affiliate URL, provider identity or nonblank stronger Product metadata.

## Error codes

Operator-facing failures use bounded messages and stable internal codes such as:

```text
PUBLIC_BLOCKED
PUBLIC_NO_IMAGE
IMAGE_DOWNLOAD_FAILED
IMAGE_TOO_LARGE
IMAGE_INVALID_CONTENT
IMAGE_DECODE_FAILED
STORAGE_FAILED
PRODUCT_IDENTITY_INVALID
HELPER_REQUIRED
ENRICHMENT_FAILED
```

Raw HTML, response bodies, cookies, auth headers, pairing tokens and stack traces are not persisted as job errors.

## Verification

Run from the parent directory containing package `acp/`:

```bash
export ACP_ADAPTER=mock
export ACP_SOURCE=mock

python -m acp.tests.test_shopee_public_metadata -v
python -m acp.tests.test_shopee_image_enrichment -v
python -m acp.tests.test_shopee_image_enrichment_flow -v
python -m acp.tests.test_shopee_image_enrichment_review -v
python -m acp.tests.test_shopee_helper_pairing_enrichment -v
python -m acp.tests.test_shopee_csv_enrichment -v
python -m acp.tests.test_shopee_image_enrichment_web -v

python -m acp.tests.test_shopee_csv_import -v
python -m acp.tests.test_shopee_csv_web -v
python -m acp.tests.test_shopee_helper -v
python -m acp.tests.test_shopee_helper_ui -v
python -m acp.tests.test_shopee_product_intel -v
python -m acp.tests.test_shopee_product_upsert -v
python -m acp.tests.test_shopee_product_intel_web -v
```

Then from the repo/package root:

```bash
./manage.sh test
git diff --check
python -m compileall core web tests adapters >/dev/null
```

Do not mark the feature release-ready until the fresh checkout/worktree gate above is green.

## Controlled pilot

After tests are green, use a small real CSV batch already exported from the official Shopee Affiliate UI:

1. import a few Products;
2. open `/sanpham/shopee`;
3. confirm they appear as missing/PENDING;
4. run one 20-or-smaller public enrichment batch;
5. verify `READY` images are real product images and local files exist;
6. verify blocked/no-image Products move to `NEEDS_HELPER`;
7. complete one Product with Chrome Helper;
8. verify the Helper Product becomes `READY` and the original affiliate URL/CSV price/commission values did not change;
9. do not create/publish posts as part of this pilot.
