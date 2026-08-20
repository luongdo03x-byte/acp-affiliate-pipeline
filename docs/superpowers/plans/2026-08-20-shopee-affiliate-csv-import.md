# Shopee Affiliate CSV Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import one or more official Shopee Affiliate bulk-link CSV files into ACP Product Pool with read-only preview, one-time confirmation, idempotent canonical Product upsert, exact official affiliate-link preservation, and no post/publish side effects.

**Architecture:** Add a pure parser/normalizer in `core/shopee_csv_import.py`, a short-lived in-memory preview store in `core/shopee_csv_batches.py`, and an isolated Flask blueprint in `web/shopee_csv_import.py`. Reuse the existing `manual_shopee` Product namespace and canonical Shopee URL helpers; update only CSV-owned fields and record confirmed price observations with source `affiliate_csv`.

**Tech Stack:** Python 3 stdlib (`csv`, `io`, `dataclasses`, `decimal`, `secrets`, `threading`, `time`, `urllib.parse`), Flask 3, SQLite, Jinja2, existing ACP Shopee helpers, `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-20-shopee-affiliate-csv-import-design.md`

## Global Constraints

- Branch: `feat/shopee-affiliate-csv-import`, based on current `main`.
- Input is the official Shopee Affiliate `Lấy link hàng loạt` CSV.
- `Link ưu đãi` is authoritative: store unchanged; never regenerate, resolve, wrap, or append tracking during import.
- Product namespace stays `source='manual_shopee'`, `merchant='shopee.vn'`.
- Canonical identity is `(shop_id, item_id)` derived from `Link sản phẩm`; `Mã sản phẩm` must equal derived `item_id`.
- Max 20 files, max 5 MiB each, max 20,000 parsed rows per preview batch.
- Preview must not mutate Product, price history, Post, job queue, approval, scheduling, or publish state.
- Preview TTL is 900 seconds and confirmation is one-time after successful import.
- Last valid duplicate occurrence wins; earlier valid duplicates are `DUPLICATE_IN_UPLOAD`.
- Bad rows do not block valid rows.
- CSV `Doanh thu` is treated as sold-count-like numeric floor, not money.
- Price-history source is exactly `affiliate_csv`.
- No Shopee login automation, cookie/session/localStorage extraction, Open API/Product Feed calls, CAPTCHA bypass, private API reverse engineering, ACCESSTRADE wrapping, automatic post creation, approval, scheduling, or publish.
- Tests run with `ACP_ADAPTER=mock ACP_SOURCE=mock` and never publish a real Threads post.

---

## File Map

### Create

- `core/shopee_csv_import.py` — parsing, normalization, DB preview classification, idempotent import service.
- `core/shopee_csv_batches.py` — 15-minute in-memory one-time preview batches storing normalized rows only.
- `web/shopee_csv_import.py` — GET page, POST preview, POST confirm.
- `web/templates/shopee_csv_import.html` — upload, preview, summary, import result.
- `tests/test_shopee_csv_parser.py` — parser, validation, dedupe, batch-lifecycle tests.
- `tests/test_shopee_csv_import.py` — DB upsert, preservation, idempotency, price history, audit tests.
- `tests/test_shopee_csv_web.py` — auth, CSRF, multi-file preview, confirmation, replay/expiry/limits.
- `docs/SHOPEE_AFFILIATE_CSV_IMPORT.md` — operator runbook and pilot checklist.

### Modify

- `core/shopee_products.py` — add `PRICE_SOURCES` so `affiliate_csv` is valid only for price history, not metadata cache.
- `web/__init__.py` — register the isolated blueprint.
- `web/templates/base.html` — add `Shopee CSV Import` navigation.
- `tests/test_pilot.py` — importer safety/regression assertions.

---

### Task 1: Pure CSV Parser and Canonical Row Model

**Files:**
- Create: `core/shopee_csv_import.py`
- Create/Test: `tests/test_shopee_csv_parser.py`
- Reuse: `core/shopee_products.py::identity_from_url`

**Interfaces:**

```python
class ShopeeCsvError(ValueError): pass

@dataclass(frozen=True)
class ShopeeAffiliateCsvRow:
    item_id: str
    shop_id: str
    name: str
    current_price: int
    sold_count: int | None
    shop_name: str | None
    commission_rate_percent: float | None
    commission_amount: int | None
    product_url: str
    affiliate_url: str
    source_filename: str
    source_row_number: int

@dataclass(frozen=True)
class ShopeeCsvRowResult:
    row: ShopeeAffiliateCsvRow | None
    error: str | None
    status: str

parse_price_vnd(value: str) -> int
parse_commission_percent(value: str) -> float | None
parse_commission_amount(value: str) -> int | None
parse_sold_count(value: str) -> int | None
parse_shopee_affiliate_csv(data: bytes, filename: str) -> list[ShopeeCsvRowResult]
dedupe_upload_rows(rows: list[ShopeeCsvRowResult]) -> list[ShopeeCsvRowResult]
```

- [ ] **Step 1: Write RED parser tests**

Create `tests/test_shopee_csv_parser.py`:

```python
import unittest
from acp.core.shopee_csv_import import (
    parse_price_vnd, parse_commission_percent, parse_commission_amount,
    parse_sold_count, parse_shopee_affiliate_csv,
)

HEADER = "Mã sản phẩm,Tên sản phẩm,Giá,Doanh thu,Tên cửa hàng,Tỉ lệ hoa hồng,Hoa hồng,Link sản phẩm,Link ưu đãi\n"

class ShopeeCsvParserTests(unittest.TestCase):
    def test_verified_number_formats(self):
        self.assertEqual(parse_price_vnd("53,9k"), 53900)
        self.assertEqual(parse_price_vnd("1,2tr"), 1200000)
        self.assertEqual(parse_price_vnd("100,0tr"), 100000000)
        self.assertEqual(parse_commission_percent("42,5%"), 42.5)
        self.assertEqual(parse_commission_amount("₫4.000.000"), 4000000)
        self.assertEqual(parse_sold_count("300k+"), 300000)

    def test_real_shape_row_preserves_affiliate_link(self):
        raw = (HEADER +
            '20834209498,"Cát Min, mùi thơm","53,9k",10k+,BALA PETSHOP,5%,₫2.695,'
            'https://shopee.vn/product/196194160/20834209498,'
            'https://s.shopee.vn/AUtM2b13go\n').encode("utf-8")
        result = parse_shopee_affiliate_csv(raw, "batch.csv")[0]
        self.assertIsNone(result.error)
        self.assertEqual(result.row.shop_id, "196194160")
        self.assertEqual(result.row.item_id, "20834209498")
        self.assertEqual(result.row.current_price, 53900)
        self.assertEqual(result.row.sold_count, 10000)
        self.assertEqual(result.row.affiliate_url, "https://s.shopee.vn/AUtM2b13go")

    def test_item_id_mismatch_is_row_error(self):
        raw = (HEADER +
            '999,X,100,0,Shop,5%,₫5,https://shopee.vn/product/1/123,'
            'https://s.shopee.vn/abc\n').encode("utf-8")
        result = parse_shopee_affiliate_csv(raw, "bad.csv")[0]
        self.assertIsNone(result.row)
        self.assertIn("không khớp", result.error.lower())

    def test_utf8_bom_is_supported(self):
        raw = ("\ufeff" + HEADER +
            '123,X,100,0,Shop,5%,₫5,https://shopee.vn/product/1/123,'
            'https://s.shopee.vn/abc\n').encode("utf-8")
        self.assertIsNone(parse_shopee_affiliate_csv(raw, "bom.csv")[0].error)
```

Add explicit tests for missing required columns, invalid UTF-8, quoted names containing commas, zero/negative/malformed price, invalid percent, invalid commission, non-Shopee product URL, non-HTTPS product URL, affiliate host not equal to `s.shopee.vn`, and URL credentials/control characters.

- [ ] **Step 2: Run RED**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_parser -v
```

Expected: import failure because `acp.core.shopee_csv_import` does not exist.

- [ ] **Step 3: Implement parser constants and numeric functions**

```python
REQUIRED_COLUMNS = (
    "Mã sản phẩm", "Tên sản phẩm", "Giá", "Doanh thu", "Tên cửa hàng",
    "Tỉ lệ hoa hồng", "Hoa hồng", "Link sản phẩm", "Link ưu đãi",
)
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES = 20
MAX_ROWS = 20_000
AFFILIATE_HOST = "s.shopee.vn"
```

`parse_price_vnd()` uses `Decimal`, decimal comma, suffix `k=1_000`, `tr=1_000_000`, and rejects non-positive values. `parse_commission_percent()` removes `%`, converts decimal comma, permits `0 <= value <= 100`, and returns `None` only for blank input. `parse_commission_amount()` removes `₫`, whitespace, and Vietnamese `.` thousands separators and rejects negative values. `parse_sold_count()` supports integer, `k`, `k+`, and returns numeric floor.

- [ ] **Step 4: Implement CSV decoding, row validation, and canonical identity**

Decode with `utf-8-sig`, parse with `csv.DictReader`, require every member of `REQUIRED_COLUMNS`, call `identity_from_url(product_url)`, require CSV item ID to equal identity item ID, and validate affiliate URL with `urlsplit`:

```python
parsed.scheme == "https"
parsed.hostname == "s.shopee.vn"
parsed.username is None
parsed.password is None
```

Invalid row returns `ShopeeCsvRowResult(row=None, error=<safe message>, status="ERROR")` and parsing continues.

- [ ] **Step 5: Implement deterministic dedupe**

For valid rows use key `(row.shop_id, row.item_id)`. Find the final valid index for each key, mark earlier valid indices `DUPLICATE_IN_UPLOAD`, and keep the final one `VALID`. Invalid rows remain `ERROR` and are not part of dedupe.

- [ ] **Step 6: Run GREEN and commit**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_parser -v
git add core/shopee_csv_import.py tests/test_shopee_csv_parser.py
git commit -m "feat: parse Shopee affiliate CSV batches"
```

---

### Task 2: Short-Lived One-Time Preview Batches

**Files:**
- Create: `core/shopee_csv_batches.py`
- Modify/Test: `tests/test_shopee_csv_parser.py`

**Interfaces:**

```python
PREVIEW_TTL_SECONDS = 900
issue_preview(rows, summary, *, now_ts=None) -> dict
peek_preview(token: str, *, now_ts=None) -> dict | None
consume_preview(token: str, *, now_ts=None) -> dict | None
reset_previews() -> None
```

- [ ] **Step 1: Add RED lifecycle tests**

```python
from acp.core import shopee_csv_batches

class ShopeeCsvPreviewBatchTests(unittest.TestCase):
    def setUp(self):
        shopee_csv_batches.reset_previews()

    def test_peek_then_consume_once(self):
        issued = shopee_csv_batches.issue_preview([{"id": 1}], {"rows": 1}, now_ts=100.0)
        token = issued["token"]
        self.assertEqual(shopee_csv_batches.peek_preview(token, now_ts=101.0)["summary"], {"rows": 1})
        self.assertIsNotNone(shopee_csv_batches.consume_preview(token, now_ts=102.0))
        self.assertIsNone(shopee_csv_batches.consume_preview(token, now_ts=103.0))

    def test_expired_batch_is_unavailable(self):
        issued = shopee_csv_batches.issue_preview([], {}, now_ts=100.0)
        self.assertIsNone(shopee_csv_batches.peek_preview(issued["token"], now_ts=1001.0))
```

- [ ] **Step 2: Run RED**

```bash
python -m acp.tests.test_shopee_csv_parser -v
```

- [ ] **Step 3: Implement batch store**

Use `secrets.token_urlsafe(32)`, `time.monotonic()`, `threading.Lock()`, and a module-level dict. Store only normalized row objects plus aggregate summary and `expires_at`. Never store raw CSV bytes or browser form rows. Clean expired entries opportunistically on issue/peek/consume.

- [ ] **Step 4: Run GREEN and commit**

```bash
python -m acp.tests.test_shopee_csv_parser -v
git add core/shopee_csv_batches.py tests/test_shopee_csv_parser.py
git commit -m "feat: add Shopee CSV preview batches"
```

---

### Task 3: Product Upsert and Price History

**Files:**
- Modify: `core/shopee_products.py`
- Modify: `core/shopee_csv_import.py`
- Create/Test: `tests/test_shopee_csv_import.py`

**Interfaces:**

```python
PRICE_SOURCES = CACHE_SOURCES | frozenset({"affiliate_csv"})
PROVIDER = "SHOPEE_AFFILIATE"
classify_row_against_db(conn, row) -> str
preview_rows_against_db(conn, row_results) -> list[ShopeeCsvRowResult]
import_rows(conn, row_results) -> dict
```

Statuses are exactly `NEW`, `UPDATED`, `UNCHANGED`, `DUPLICATE_IN_UPLOAD`, `ERROR`.

- [ ] **Step 1: Write RED DB tests with explicit fixtures**

Create a helper:

```python
def valid_result(item_id="123", shop_id="1", price=100000,
                 affiliate="https://s.shopee.vn/abc"):
    row = ShopeeAffiliateCsvRow(
        item_id=item_id, shop_id=shop_id, name="Sản phẩm CSV",
        current_price=price, sold_count=1000, shop_name="Shop CSV",
        commission_rate_percent=42.5, commission_amount=42000,
        product_url=f"https://shopee.vn/product/{shop_id}/{item_id}",
        affiliate_url=affiliate, source_filename="batch.csv", source_row_number=2,
    )
    return ShopeeCsvRowResult(row=row, error=None, status="NEW")
```

New-row test:

```python
result = import_rows(self.conn, [valid_result()])
self.assertEqual(result["new"], 1)
row = self.conn.execute(
    "SELECT * FROM product WHERE source='manual_shopee' AND merchant='shopee.vn' AND external_product_id='123'"
).fetchone()
self.assertEqual(row["provider"], "SHOPEE_AFFILIATE")
self.assertEqual(row["affiliate_url"], "https://s.shopee.vn/abc")
self.assertEqual(row["affiliate_link_status"], "READY")
self.assertEqual(row["commission_rate_percent"], 42.5)
self.assertEqual(row["commission_amount"], 42000)
self.assertEqual(row["units_sold"], 1000)
```

Richer-existing-row preservation test must explicitly seed a manual Shopee Product with:

```text
image_url_original=https://img.example/product.jpg
image_path_local=/tmp/existing.jpg
rating=4.9
review_count=321
category_code=pet
original_price=150000
```

Import the same item at price `120000` and assert all five richer fields above are unchanged while price, shop, commission, sold count, canonical URL, and affiliate URL update.

Idempotency test imports the same normalized row twice and asserts one Product row and one unchanged-price history point. Price-change test imports `100000` then `120000` and asserts history equals `[(100000, "affiliate_csv"), (120000, "affiliate_csv")]`.

Also test newer official affiliate URL replaces old valid URL; `commission_value == commission_amount`; legacy `sold_count == units_sold`; rows with `ERROR` or `DUPLICATE_IN_UPLOAD` never mutate DB.

- [ ] **Step 2: Run RED**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_import -v
```

- [ ] **Step 3: Separate metadata-cache and price sources**

In `core/shopee_products.py`:

```python
CACHE_SOURCES = frozenset({"server", "helper", "manual"})
PRICE_SOURCES = CACHE_SOURCES | frozenset({"affiliate_csv"})
```

`put_metadata_cache()` and confirmed metadata validation continue using `CACHE_SOURCES`. Only `record_price_observation()` switches validation to `PRICE_SOURCES`.

- [ ] **Step 4: Implement read-only DB classification**

Lookup:

```sql
SELECT * FROM product
WHERE source='manual_shopee'
  AND merchant='shopee.vn'
  AND external_product_id=?
```

Return `NEW` if absent. If present, compare CSV-owned values only: name, current price, shop, legacy/new sold count, legacy/new commission, canonical product/detail URL, exact affiliate URL/short URL, and link status. Ignore timestamps. Return `UPDATED` if any CSV-owned value differs, otherwise `UNCHANGED`.

- [ ] **Step 5: Implement import transaction**

Use `core.db.transaction(conn)` for the whole confirmed batch.

Existing Product: update only CSV-owned values and lifecycle timestamps; preserve description, original price, image URLs/path, rating, review count, category, category data, score, facts, post history.

New Product safe fields:

```text
source=manual_shopee
merchant=shopee.vn
provider=SHOPEE_AFFILIATE
description=""
current_price=CSV price
original_price=NULL
commission_value=CSV commission amount or 0
commission_rate=CSV commission percent
category_code=khac
rating=NULL
review_count=0
sold_count=CSV sold floor or 0
image_url_original=NULL
image_path_local=NULL
product_url=canonical URL
is_available=1
shop_name=CSV shop
detail_link=canonical URL
currency=VND
price_min=current price
price_max=current price
commission_rate_percent=CSV percent
commission_amount=CSV amount
commission_currency=VND
units_sold=CSV sold floor
has_inventory=NULL
affiliate_url=exact CSV link
affiliate_short_url=exact CSV link
affiliate_link_status=READY
affiliate_link_error=NULL
```

Set `first_seen_at` only on insert, and update `last_seen_at`, `last_synced_at`, `affiliate_link_created_at`, `updated_at` on actual import. Call `record_price_observation(..., source="affiliate_csv")` only for `NEW` or changed-price `UPDATED`; unchanged price must not create another point.

- [ ] **Step 6: Run GREEN and commit**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_import -v
git add core/shopee_products.py core/shopee_csv_import.py tests/test_shopee_csv_import.py
git commit -m "feat: import Shopee CSV products idempotently"
```

---

### Task 4: Authenticated Multi-File Web Workspace

**Files:**
- Create: `web/shopee_csv_import.py`
- Create: `web/templates/shopee_csv_import.html`
- Modify: `web/__init__.py`
- Modify: `web/templates/base.html`
- Create/Test: `tests/test_shopee_csv_web.py`

**Routes:**

```text
GET  /sanpham/shopee-import
POST /sanpham/shopee-import/preview
POST /sanpham/shopee-import/confirm
```

Existing global dashboard auth and CSRF guard protect all three because these paths are not public prefixes.

- [ ] **Step 1: Write RED web tests**

Use the existing Flask test client and a temporary DB. Include exact checks:

```python
before = self.db_count("product")
response = self.client.post(
    "/sanpham/shopee-import/preview",
    data={"_csrf": self.csrf, "files": (io.BytesIO(self.csv_bytes), "batch.csv")},
    content_type="multipart/form-data",
)
self.assertEqual(response.status_code, 200)
self.assertEqual(self.db_count("product"), before)
self.assertIn("Import vào Product Pool", response.get_data(as_text=True))
```

For confirmation, parse the `preview_token` from returned HTML, submit only `_csrf` + `preview_token`, assert the exact affiliate URL is persisted, then submit the same token again and assert HTTP 410 with no extra mutation.

Add tests for login redirect with auth enabled, invalid/missing CSRF, no files, non-CSV extension, 21 files, one file larger than 5 MiB, missing headers, mixed valid/error rows, duplicate products across two files, >20,000 rows, and expired preview token.

- [ ] **Step 2: Run RED**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_web -v
```

- [ ] **Step 3: Implement blueprint preview route**

`request.files.getlist("files")`; require 1–20 entries; filename must end `.csv`; read each stream with `MAX_FILE_BYTES + 1` cap; parse in upload order; enforce cumulative row cap; dedupe; open DB only for read-only classification; create a preview batch storing classified normalized rows + aggregate summary; render preview.

Summary keys:

```python
{"files": 0, "rows": 0, "new": 0, "updated": 0,
 "unchanged": 0, "duplicate": 0, "error": 0}
```

- [ ] **Step 4: Implement confirm route with retry-safe token semantics**

Flow:

```python
batch = peek_preview(token)
if batch is None:
    return _render(err="Phiên preview đã hết hạn hoặc không hợp lệ", status=410)
try:
    summary = import_rows(conn, batch["rows"])
except (ShopeeCsvError, sqlite3.DatabaseError) as exc:
    return _render(err=safe_import_error(exc), preview=batch, status=500)
consume_preview(token)
return _render(import_summary=summary)
```

Do not consume token before DB success. Do not accept posted row fields. Do not catch arbitrary exceptions and report success.

- [ ] **Step 5: Register blueprint and add navigation**

In `web/__init__.py` import and call `register_shopee_csv_import_routes(app)` after existing Shopee registrations.

In `base.html` add:

```html
<a href="/sanpham/shopee-import" class="nav-item {{ 'nav-item--active' if page=='shopee-csv-import' }}">
  <span class="nav-icon">⇩</span><span class="nav-label">Shopee CSV Import</span>
</a>
```

- [ ] **Step 6: Implement template**

Upload form:

```html
<form method="post" action="/sanpham/shopee-import/preview" enctype="multipart/form-data">
  <input type="hidden" name="_csrf" value="{{ csrf_token }}">
  <input type="file" name="files" accept=".csv,text/csv" multiple required>
  <button class="btn btn--primary" type="submit">Preview</button>
</form>
```

Preview shows summary cards and table columns: status, item ID, name, price, sold count, shop, commission %, commission amount, product URL, truncated affiliate URL with copyable full value, source file/row, error. Confirm form contains only CSRF + preview token + `Import vào Product Pool`. No post/publish CTA.

- [ ] **Step 7: Run GREEN and commit**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_parser -v
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_import -v
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_web -v
git add web/shopee_csv_import.py web/templates/shopee_csv_import.html web/__init__.py web/templates/base.html tests/test_shopee_csv_web.py
git commit -m "feat: add Shopee CSV import workspace"
```

---

### Task 5: Aggregate Audit, Runbook, and Safety Regressions

**Files:**
- Modify: `core/shopee_csv_import.py`
- Modify: `web/shopee_csv_import.py`
- Modify/Test: `tests/test_shopee_csv_import.py`
- Modify: `tests/test_pilot.py`
- Create: `docs/SHOPEE_AFFILIATE_CSV_IMPORT.md`

**Audit actions:** `shopee_csv_preview`, `shopee_csv_import_completed`.

- [ ] **Step 1: Write RED audit tests**

After preview/import, load matching `audit_log.detail`, parse JSON, and assert keys are only aggregate counters plus a non-secret batch label. Explicitly assert serialized audit detail contains none of:

```text
s.shopee.vn
product name
preview token
raw CSV header
```

Expected aggregate shape:

```python
{"files": 2, "rows": 150, "new": 80, "updated": 50,
 "unchanged": 10, "duplicate": 5, "error": 5}
```

- [ ] **Step 2: Implement sanitized audit**

Use `core.db.audit()`. Preview audit occurs after successful parse/classification. Import audit occurs after successful transaction. Use a random non-secret short batch label separate from preview token. Do not put raw rows, full affiliate links, file bodies, or token in `audit_log.detail`.

- [ ] **Step 3: Add pilot safety assertions**

Extend `tests/test_pilot.py` to assert CSV routes are registered and a successful import changes Product data but leaves counts of `post` and `job_queue` unchanged. Add static assertions that the importer module does not import or call `approve_post`, `publish_post`, or `enqueue`.

- [ ] **Step 4: Write operator runbook**

Document exact flow:

```text
Shopee Affiliate → Hoa hồng Sản phẩm
→ select products → Lấy link hàng loạt
→ optional Sub_id → Lấy link → download CSV
→ ACP /sanpham/shopee-import
→ choose 1–20 CSV files → Preview
→ inspect NEW/UPDATED/UNCHANGED/DUPLICATE/ERROR
→ Import vào Product Pool
```

Document number formats, 5 MiB/file, 20-file cap, 20,000-row cap, last-valid duplicate rule, `Doanh thu` interpretation as sold-count-like, official link preservation, and that this phase stops at Product Pool.

Pilot must use a test/non-production DB first and explicitly verify no Post, job, approval, scheduling, or publish side effect.

- [ ] **Step 5: Run focused regression and commit**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_import -v
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_web -v
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_pilot
git add core/shopee_csv_import.py web/shopee_csv_import.py tests/test_shopee_csv_import.py tests/test_pilot.py docs/SHOPEE_AFFILIATE_CSV_IMPORT.md
git commit -m "docs: add Shopee CSV import runbook and audit"
```

---

### Task 6: Full Verification and Draft PR

**Files:** no new feature scope.

- [ ] **Step 1: Run all new focused suites**

```bash
export ACP_ADAPTER=mock
export ACP_SOURCE=mock
python -m acp.tests.test_shopee_csv_parser -v
python -m acp.tests.test_shopee_csv_import -v
python -m acp.tests.test_shopee_csv_web -v
```

Record exact pass/fail output.

- [ ] **Step 2: Run current Shopee regressions**

```bash
python -m acp.tests.test_shopee_bulk_affiliate
python -m acp.tests.test_shopee_helper -v
python -m acp.tests.test_shopee_helper_ui -v
python -m acp.tests.test_shopee_product_intel -v
python -m acp.tests.test_shopee_product_upsert -v
python -m acp.tests.test_shopee_product_intel_web -v
python -m acp.tests.test_shopee_product_intel_ui -v
python -m acp.tests.test_shopee_observability -v
python -m acp.tests.test_shopee_instrumentation -v
python -m acp.tests.test_shopee_preview -v
python -m acp.tests.test_shopee_polish_ui -v
```

If a named suite is absent on current `main`, report `NOT FOUND` and identify the current equivalent; never count it as PASS.

- [ ] **Step 3: Run release regressions**

From parent directory containing `acp/`:

```bash
python -m acp.tests.test_pipeline
python -m acp.tests.test_pilot
```

Inside `acp/`:

```bash
python tests/test_manage.py
./manage.sh test
python -m compileall core web tests adapters
git diff --check
```

- [ ] **Step 4: Review diff/status and tracked-file safety**

```bash
git status --short
git diff --stat main..HEAD
git diff --check main..HEAD
git diff main..HEAD
```

Confirm no `.env.local`, SQLite DB/WAL/SHM, `var/`, generated media, tokens, browser secrets, or the real uploaded CSV are tracked.

- [ ] **Step 5: Controlled pilot**

Against a test/non-production DB, use a copy of a real Shopee bulk-link CSV and verify in order:

```text
preview leaves DB unchanged
confirm inserts/updates expected Product rows
exact short affiliate URLs are preserved
re-import is idempotent
richer existing metadata survives
changed price adds exactly one affiliate_csv observation
post count unchanged
job_queue count unchanged
no publish action occurs
```

If the environment cannot complete the pilot, report `NOT RUN` plus reason.

- [ ] **Step 6: Open Draft PR**

Title:

```text
feat: import Shopee affiliate CSV batches
```

PR body includes scope, safety boundaries, exact verification commands/results, and pilot status. Keep Draft until required gates are green.

---

## Definition of Done

- [ ] Multi-file Shopee CSV preview works.
- [ ] Verified Vietnamese number formats parse correctly.
- [ ] Invalid rows remain isolated and visible.
- [ ] Last valid duplicate occurrence wins deterministically.
- [ ] Preview performs no DB mutation.
- [ ] Confirmation trusts server-side one-time normalized data only.
- [ ] Existing `manual_shopee` Product is reused.
- [ ] New Product contains no fabricated image/rating/original-price facts.
- [ ] Existing richer metadata survives update.
- [ ] Official affiliate short link is stored unchanged and marked READY.
- [ ] Price history adds only first/change observations with `affiliate_csv` source.
- [ ] Import is idempotent.
- [ ] No Post/job/approve/schedule/publish side effect.
- [ ] Audit contains aggregates only.
- [ ] Focused tests and required regressions have fresh output.
- [ ] `./manage.sh test` has fresh output before Ready/merge.
- [ ] Draft PR accurately records any remaining environment/pilot blocker.
