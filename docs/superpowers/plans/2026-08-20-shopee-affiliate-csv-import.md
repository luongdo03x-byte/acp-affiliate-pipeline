# Shopee Affiliate CSV Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import one or more official Shopee Affiliate bulk-link CSV files into ACP Product Pool with a read-only preview, one-time confirmation, idempotent canonical Product upsert, official affiliate-link preservation, and no post/publish side effects.

**Architecture:** Add a pure parser/normalizer in `core/shopee_csv_import.py`, a short-lived in-memory preview-batch store in `core/shopee_csv_batches.py`, and an isolated Flask blueprint in `web/shopee_csv_import.py`. Reuse the existing `manual_shopee` Product namespace and Shopee canonical identity helpers; the importer updates only CSV-supplied fields, preserves richer existing metadata, and records price observations with source `affiliate_csv`.

**Tech Stack:** Python 3 stdlib (`csv`, `io`, `dataclasses`, `decimal`, `secrets`, `time`), Flask 3, SQLite, Jinja2, existing ACP Shopee helpers and migration/schema, `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-20-shopee-affiliate-csv-import-design.md`

## Global Constraints

- Branch: `feat/shopee-affiliate-csv-import`, based on current `main`.
- Input source is the official Shopee Affiliate `Lấy link hàng loạt` CSV.
- `Link ưu đãi` is authoritative; store it unchanged and never regenerate, resolve, wrap, or append `sub_id` during import.
- Canonical Product namespace remains `source='manual_shopee'`, `merchant='shopee.vn'`.
- Canonical identity is `(shop_id, item_id)` derived from `Link sản phẩm`; CSV `Mã sản phẩm` must equal derived `item_id`.
- Multi-file upload: max 20 files, max 5 MiB per file, max 20,000 parsed rows per preview batch.
- Preview must not mutate Product, price history, Post, job queue, or publish state.
- Preview batch TTL is 15 minutes and confirmation is one-time after successful import.
- Duplicate canonical products inside one upload use last valid occurrence; earlier rows become `DUPLICATE_IN_UPLOAD`.
- Bad rows do not block valid rows.
- Treat CSV `Doanh thu` as sold-count-like quantity (`units_sold`/`sold_count` numeric floor), not monetary revenue.
- Imported price-history source is exactly `affiliate_csv`.
- No Shopee login automation, cookie/session/localStorage extraction, Open API/Product Feed use, CAPTCHA bypass, private API reverse engineering, ACCESSTRADE wrapping, automatic post creation, approval, scheduling, or publish.
- Tests run with `ACP_ADAPTER=mock ACP_SOURCE=mock`; no real Threads publish.

---

## File map

### Create

- `core/shopee_csv_import.py` — pure CSV decoding, parsing, normalization, preview classification, and DB import service.
- `core/shopee_csv_batches.py` — 15-minute in-memory one-time preview batches storing normalized rows only.
- `web/shopee_csv_import.py` — isolated authenticated/CSRF-protected page, preview, and confirmation routes.
- `web/templates/shopee_csv_import.html` — upload/preview/result workspace.
- `tests/test_shopee_csv_parser.py` — parser/normalization/dedup unit tests.
- `tests/test_shopee_csv_import.py` — DB upsert/idempotency/price-history tests.
- `tests/test_shopee_csv_web.py` — auth/CSRF/multi-file/preview-batch web tests.
- `docs/SHOPEE_AFFILIATE_CSV_IMPORT.md` — operator instructions and pilot checklist.

### Modify

- `core/shopee_products.py` — allow `affiliate_csv` as a price-observation source without treating it as browser/server metadata-cache source.
- `web/__init__.py` — register the isolated CSV-import blueprint after existing Shopee modules.
- `web/templates/base.html` — add `Shopee CSV Import` navigation entry.
- `tests/test_pilot.py` — static/runtime regression assertions that importer does not create posts/publish and UI route remains protected.

---

### Task 1: Pure Shopee Affiliate CSV parser and canonical row normalization

**Files:**
- Create: `core/shopee_csv_import.py`
- Create/Test: `tests/test_shopee_csv_parser.py`
- Reuse: `adapters/shopee_affiliate.py` (`canonical_product_url`, existing Shopee URL parsing behavior)
- Reuse: `core/shopee_products.py` (`identity_from_url`)

**Interfaces:**
- Produces:
  - `ShopeeCsvError(ValueError)`
  - `ShopeeAffiliateCsvRow` dataclass
  - `ShopeeCsvRowResult` dataclass
  - `parse_price_vnd(value: str) -> int`
  - `parse_commission_percent(value: str) -> float | None`
  - `parse_commission_amount(value: str) -> int | None`
  - `parse_sold_count(value: str) -> int | None`
  - `parse_shopee_affiliate_csv(data: bytes, filename: str) -> list[ShopeeCsvRowResult]`
  - `dedupe_upload_rows(rows: list[ShopeeCsvRowResult]) -> list[ShopeeCsvRowResult]`

- [ ] **Step 1: Write RED tests for the verified CSV formats**

Create `tests/test_shopee_csv_parser.py` with focused cases like:

```python
import unittest

from acp.core.shopee_csv_import import (
    ShopeeCsvError,
    dedupe_upload_rows,
    parse_commission_amount,
    parse_commission_percent,
    parse_price_vnd,
    parse_shopee_affiliate_csv,
    parse_sold_count,
)


HEADER = "Mã sản phẩm,Tên sản phẩm,Giá,Doanh thu,Tên cửa hàng,Tỉ lệ hoa hồng,Hoa hồng,Link sản phẩm,Link ưu đãi\n"


class ShopeeCsvParserTests(unittest.TestCase):
    def test_vietnamese_numeric_formats(self):
        self.assertEqual(parse_price_vnd("53,9k"), 53900)
        self.assertEqual(parse_price_vnd("1,2tr"), 1200000)
        self.assertEqual(parse_price_vnd("100,0tr"), 100000000)
        self.assertEqual(parse_commission_percent("42,5%"), 42.5)
        self.assertEqual(parse_commission_amount("₫4.000.000"), 4000000)
        self.assertEqual(parse_sold_count("300k+"), 300000)

    def test_real_shape_row_is_normalized_and_affiliate_link_preserved(self):
        raw = (HEADER +
            '20834209498,"Cát Min, mùi thơm","53,9k",10k+,BALA PETSHOP,5%,₫2.695,'
            'https://shopee.vn/product/196194160/20834209498,'
            'https://s.shopee.vn/AUtM2b13go\n').encode("utf-8")
        results = parse_shopee_affiliate_csv(raw, "batch.csv")
        self.assertEqual(len(results), 1)
        row = results[0].row
        self.assertIsNone(results[0].error)
        self.assertEqual(row.shop_id, "196194160")
        self.assertEqual(row.item_id, "20834209498")
        self.assertEqual(row.current_price, 53900)
        self.assertEqual(row.sold_count, 10000)
        self.assertEqual(row.affiliate_url, "https://s.shopee.vn/AUtM2b13go")

    def test_item_id_mismatch_is_row_error(self):
        raw = (HEADER +
            '999,"X",100,0,Shop,5%,₫5,https://shopee.vn/product/1/123,'
            'https://s.shopee.vn/abc\n').encode("utf-8")
        result = parse_shopee_affiliate_csv(raw, "bad.csv")[0]
        self.assertIsNotNone(result.error)
        self.assertIsNone(result.row)

    def test_utf8_bom_is_supported(self):
        raw = ("\ufeff" + HEADER +
            '123,"X",100,0,Shop,5%,₫5,https://shopee.vn/product/1/123,'
            'https://s.shopee.vn/abc\n').encode("utf-8")
        self.assertIsNone(parse_shopee_affiliate_csv(raw, "bom.csv")[0].error)
```

Also add tests for quoted names containing commas, missing required header, malformed/non-UTF-8 input, zero/negative/malformed price, invalid percent, invalid commission amount, non-Shopee product URL, non-HTTPS URL, and affiliate host other than `s.shopee.vn`.

- [ ] **Step 2: Run parser tests and verify RED**

Run from the directory containing package `acp/`:

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_parser -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'acp.core.shopee_csv_import'`.

- [ ] **Step 3: Implement minimal parser and normalization**

Use these constants and dataclasses:

```python
REQUIRED_COLUMNS = (
    "Mã sản phẩm", "Tên sản phẩm", "Giá", "Doanh thu", "Tên cửa hàng",
    "Tỉ lệ hoa hồng", "Hoa hồng", "Link sản phẩm", "Link ưu đãi",
)
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES = 20
MAX_ROWS = 20_000
AFFILIATE_HOST = "s.shopee.vn"

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
    status: str = "VALID"
```

Parsing rules:

```python
def parse_price_vnd(value):
    text = str(value or "").strip().lower().replace(" ", "")
    multiplier = 1
    if text.endswith("tr"):
        multiplier, text = 1_000_000, text[:-2]
    elif text.endswith("k"):
        multiplier, text = 1_000, text[:-1]
    number = Decimal(text.replace(".", "").replace(",", "."))
    amount = int(number * multiplier)
    if amount <= 0:
        raise ShopeeCsvError("Giá sản phẩm phải lớn hơn 0")
    return amount
```

For `parse_commission_amount()`, strip `₫`, spaces and `.` thousands separators. For `parse_sold_count()`, support plain integer and suffix `k+`; store the numeric floor. Validate product identity with `identity_from_url()` and require the CSV item ID to match. Validate affiliate URL with `urlsplit`: scheme exactly `https`, hostname exactly `s.shopee.vn`, no username/password, no control chars.

Decode bytes with `utf-8-sig`; `csv.DictReader` handles quoted names/commas. Missing required columns raises batch-level `ShopeeCsvError`; row validation returns `ShopeeCsvRowResult(row=None, error=...)` without aborting the rest of the file.

- [ ] **Step 4: Implement deterministic last-valid-occurrence dedupe**

`dedupe_upload_rows()` must keep source order for display, but mark earlier valid occurrences with `status="DUPLICATE_IN_UPLOAD"` and leave only the last valid occurrence eligible for DB mutation.

Use canonical key:

```python
key = (row.shop_id, row.item_id)
```

Invalid rows are never considered duplicates and keep their own row error.

- [ ] **Step 5: Run parser tests GREEN**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_parser -v
```

Expected: all parser tests PASS.

- [ ] **Step 6: Commit parser slice**

```bash
git add core/shopee_csv_import.py tests/test_shopee_csv_parser.py
git commit -m "feat: parse Shopee affiliate CSV batches"
```

---

### Task 2: One-time 15-minute preview batch store

**Files:**
- Create: `core/shopee_csv_batches.py`
- Test: `tests/test_shopee_csv_parser.py`

**Interfaces:**
- Consumes: `list[ShopeeCsvRowResult]` from Task 1.
- Produces:
  - `PREVIEW_TTL_SECONDS = 900`
  - `issue_preview(rows, summary) -> dict` returning `{token, expires_in}`
  - `peek_preview(token: str) -> dict | None`
  - `consume_preview(token: str) -> dict | None`
  - `reset_previews() -> None`

- [ ] **Step 1: Add RED lifecycle tests**

Append:

```python
from acp.core import shopee_csv_batches

class PreviewBatchTests(unittest.TestCase):
    def setUp(self):
        shopee_csv_batches.reset_previews()

    def test_preview_is_peekable_then_one_time_consumable(self):
        issued = shopee_csv_batches.issue_preview([{"x": 1}], {"rows": 1})
        self.assertEqual(shopee_csv_batches.peek_preview(issued["token"])["summary"]["rows"], 1)
        self.assertIsNotNone(shopee_csv_batches.consume_preview(issued["token"]))
        self.assertIsNone(shopee_csv_batches.consume_preview(issued["token"]))

    def test_expired_preview_disappears(self):
        issued = shopee_csv_batches.issue_preview([], {}, now_ts=100.0)
        self.assertIsNone(shopee_csv_batches.peek_preview(issued["token"], now_ts=1001.0))
```

- [ ] **Step 2: Run focused tests RED**

```bash
python -m acp.tests.test_shopee_csv_parser -v
```

Expected: missing batch module/functions.

- [ ] **Step 3: Implement in-memory TTL store**

Use `secrets.token_urlsafe(32)`, `time.monotonic()`, a module-level dict, and a lock if the existing runtime may serve concurrent requests. Store normalized rows + safe summary only; never raw CSV bytes. `consume_preview()` removes only a valid non-expired batch and returns its data exactly once.

- [ ] **Step 4: Run focused tests GREEN and commit**

```bash
python -m acp.tests.test_shopee_csv_parser -v
git add core/shopee_csv_batches.py tests/test_shopee_csv_parser.py
git commit -m "feat: add Shopee CSV preview batches"
```

---

### Task 3: Idempotent Product Pool upsert and Shopee price-history integration

**Files:**
- Modify: `core/shopee_products.py`
- Modify: `core/shopee_csv_import.py`
- Create/Test: `tests/test_shopee_csv_import.py`

**Interfaces:**
- Consumes: `ShopeeAffiliateCsvRow` from Task 1.
- Produces:
  - `PRICE_SOURCES = CACHE_SOURCES | {"affiliate_csv"}` in `core/shopee_products.py`
  - `classify_row_against_db(conn, row) -> str` returning `NEW | UPDATED | UNCHANGED`
  - `preview_rows_against_db(conn, row_results) -> list[ShopeeCsvRowResult]`
  - `import_rows(conn, row_results) -> dict`
  - provider label exactly `SHOPEE_AFFILIATE`

- [ ] **Step 1: Write RED DB tests**

Create `tests/test_shopee_csv_import.py` using a temp SQLite DB initialized through `db.init_db()` and rows constructed directly.

Required cases:

```python
class ShopeeCsvImportDbTests(unittest.TestCase):
    def test_new_csv_row_inserts_manual_shopee_product_with_ready_link(self):
        result = import_rows(self.conn, [valid_result(item_id="123", price=100000)])
        self.assertEqual(result["new"], 1)
        row = self.conn.execute("SELECT * FROM product WHERE source='manual_shopee' AND external_product_id='123'").fetchone()
        self.assertEqual(row["merchant"], "shopee.vn")
        self.assertEqual(row["provider"], "SHOPEE_AFFILIATE")
        self.assertEqual(row["affiliate_url"], "https://s.shopee.vn/abc")
        self.assertEqual(row["affiliate_link_status"], "READY")

    def test_existing_richer_product_is_updated_without_erasing_image_rating_or_category(self):
        # seed existing manual_shopee row with image/rating/category
        # import same item with changed price/commission/link
        # assert image/rating/category survive, CSV fields change
        ...

    def test_reimport_same_row_is_unchanged_and_keeps_single_product(self):
        import_rows(self.conn, [valid_result(item_id="123", price=100000)])
        result = import_rows(self.conn, [valid_result(item_id="123", price=100000)])
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM product WHERE external_product_id='123' AND source='manual_shopee'").fetchone()[0], 1)

    def test_changed_price_adds_one_history_observation_with_affiliate_csv_source(self):
        import_rows(self.conn, [valid_result(item_id="123", price=100000)])
        import_rows(self.conn, [valid_result(item_id="123", price=120000)])
        rows = self.conn.execute("SELECT price, source FROM product_price_history WHERE product_id=(SELECT id FROM product WHERE source='manual_shopee' AND external_product_id='123') ORDER BY id").fetchall()
        self.assertEqual([(r["price"], r["source"]) for r in rows], [(100000, "affiliate_csv"), (120000, "affiliate_csv")])
```

Replace the `...` in the actual test file with explicit insert/setup code; do not leave placeholders in committed code.

Also test: latest affiliate URL replaces old valid one; unchanged price does not add history; duplicate/error rows do not mutate DB; `commission_rate_percent=42.5` remains 42.5; `commission_value` and `commission_amount` both reflect CSV commission; `units_sold` and legacy `sold_count` receive the parsed sold-count floor.

- [ ] **Step 2: Run DB tests RED**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_import -v
```

Expected: missing DB import functions and `affiliate_csv` rejected as a price source.

- [ ] **Step 3: Separate metadata-cache sources from price-observation sources**

In `core/shopee_products.py`:

```python
CACHE_SOURCES = frozenset({"server", "helper", "manual"})
PRICE_SOURCES = CACHE_SOURCES | frozenset({"affiliate_csv"})
```

Keep `put_metadata_cache()` and confirmed metadata validation on `CACHE_SOURCES`. Change only `record_price_observation()` to validate against `PRICE_SOURCES`. This avoids calling CSV import browser/server metadata cache while still recording price provenance.

- [ ] **Step 4: Implement DB classification and upsert**

Lookup existing canonical Product exactly by:

```sql
SELECT * FROM product
WHERE source='manual_shopee'
  AND merchant='shopee.vn'
  AND external_product_id=?
```

For existing rows, compare only CSV-owned fields:

```text
name
current_price
shop_name
sold_count
units_sold
commission_rate
commission_rate_percent
commission_value
commission_amount
product_url
detail_link
affiliate_url
affiliate_short_url
affiliate_link_status
```

Do not consider timestamps when deciding `UNCHANGED`.

Update only CSV-owned fields plus safe lifecycle timestamps/status. Preserve `description`, `original_price`, `image_url_original`, `image_path_local`, `main_image_url`, `rating`, `review_count`, `category_code`, `category_data`, `score`, post/history fields.

For a new Product, insert safe non-fabricated defaults required by schema:

```text
source = manual_shopee
merchant = shopee.vn
provider = SHOPEE_AFFILIATE
description = ""
original_price = NULL
commission_value = CSV commission amount or 0
commission_rate = CSV percent
category_code = "khac"
rating = NULL
review_count = 0
sold_count = parsed sold count or 0
image_url_original = NULL
image_path_local = NULL
product_url = canonical URL
is_available = 1
shop_name = CSV shop
detail_link = canonical URL
currency = VND
price_min = current_price
price_max = current_price
commission_rate_percent = CSV percent
commission_amount = CSV amount
commission_currency = VND
units_sold = parsed sold count
has_inventory = NULL
affiliate_url = exact CSV link
affiliate_short_url = exact CSV link
affiliate_link_status = READY
affiliate_link_error = NULL
```

Set first/last timestamps consistently with existing `db.now()`.

Call `record_price_observation(conn, product_id, row.current_price, source="affiliate_csv")` only for rows actually imported. `UNCHANGED` must remain idempotent and not create a new history point.

- [ ] **Step 5: Wrap one import confirmation in one transaction**

`import_rows()` must use the existing `core.db.transaction(conn)` so a database exception cannot leave a half-imported confirmed batch. Row validation errors were already removed before this point; a genuine DB error aborts the transaction and keeps the preview token unconsumed at the web layer until a successful commit.

- [ ] **Step 6: Run DB tests GREEN and commit**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_import -v
git add core/shopee_products.py core/shopee_csv_import.py tests/test_shopee_csv_import.py
git commit -m "feat: import Shopee CSV products idempotently"
```

---

### Task 4: Authenticated multi-file preview/import web workspace

**Files:**
- Create: `web/shopee_csv_import.py`
- Create: `web/templates/shopee_csv_import.html`
- Modify: `web/__init__.py`
- Modify: `web/templates/base.html`
- Create/Test: `tests/test_shopee_csv_web.py`

**Interfaces:**
- Routes:
  - `GET /sanpham/shopee-import`
  - `POST /sanpham/shopee-import/preview`
  - `POST /sanpham/shopee-import/confirm`
- Reuse existing global dashboard auth/CSRF guard from `web.server.create_app()`.
- Config seams:
  - `SHOPEE_CSV_MAX_FILES = 20`
  - `SHOPEE_CSV_MAX_FILE_BYTES = 5 * 1024 * 1024`
  - total parsed rows `<= 20_000`

- [ ] **Step 1: Write RED web tests**

Create `tests/test_shopee_csv_web.py` following existing Flask test-client login/CSRF conventions. Cover:

```python
def test_preview_does_not_mutate_product_db(self):
    before = self.conn.execute("SELECT COUNT(*) FROM product").fetchone()[0]
    response = self.client.post(
        "/sanpham/shopee-import/preview",
        data={"_csrf": self.csrf, "files": (io.BytesIO(self.csv_bytes), "batch.csv")},
        content_type="multipart/form-data",
    )
    self.assertEqual(response.status_code, 200)
    after = self.conn.execute("SELECT COUNT(*) FROM product").fetchone()[0]
    self.assertEqual(after, before)
    self.assertIn(b"Import v\xc3\xa0o Product Pool", response.data)


def test_confirm_imports_exactly_previewed_server_side_rows(self):
    # preview valid row; obtain batch token from returned HTML
    # submit confirm with only token + csrf, not row values
    # assert Product inserted and exact affiliate URL preserved
    ...


def test_replayed_preview_token_is_rejected(self):
    # confirm once succeeds; second confirm returns 400/410 and does not mutate again
    ...
```

In the actual test file replace `...` with explicit code. Also test no file selected, non-CSV extension, >20 files, oversized file, missing columns, mixed valid/error rows, multi-file duplicate summary, expired token, login redirect when auth enabled, and CSRF rejection on both POST routes.

- [ ] **Step 2: Run web tests RED**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_web -v
```

Expected: route/module not found.

- [ ] **Step 3: Implement isolated blueprint**

Structure:

```python
bp = Blueprint("shopee_csv_import", __name__)

@bp.get("/sanpham/shopee-import")
def page():
    return _render()

@bp.post("/sanpham/shopee-import/preview")
def preview():
    files = request.files.getlist("files")
    # validate count/name/size, parse bytes, enforce total row cap
    # dedupe, DB read-only classification, issue_preview(...)
    # render token + rows + summary

@bp.post("/sanpham/shopee-import/confirm")
def confirm():
    token = request.form.get("preview_token", "")
    batch = peek_preview(token)
    if not batch:
        return _render(err="Phiên preview đã hết hạn hoặc không hợp lệ", status=410)
    conn = connect()
    try:
        summary = import_rows(conn, batch["rows"])
    except Exception:
        # safe operator error, leave preview token available for retry
        ...
    else:
        consume_preview(token)
        return _render(import_summary=summary)
```

Actual code must catch expected safe importer/database exceptions explicitly; do not swallow arbitrary exceptions into a fake success. Never reconstruct rows from hidden browser form fields.

For file-size enforcement, read at most `MAX_FILE_BYTES + 1`; reject if length exceeds max. Aggregate all files and stop before exceeding `MAX_ROWS`.

- [ ] **Step 4: Register blueprint in package composition**

In `web/__init__.py`:

```python
from .shopee_csv_import import register_shopee_csv_import_routes
...
register_shopee_csv_import_routes(app)
```

Keep existing route registrations and public import compatibility intact.

- [ ] **Step 5: Implement UI and navigation**

`web/templates/shopee_csv_import.html` must include:

```html
<form method="post" action="/sanpham/shopee-import/preview" enctype="multipart/form-data">
  <input type="hidden" name="_csrf" value="{{ csrf_token }}">
  <input type="file" name="files" accept=".csv,text/csv" multiple required>
  <button class="btn btn--primary" type="submit">Preview</button>
</form>
```

When preview exists show summary cards and a table with status, item ID, name, price, sold count, shop, commission %, commission amount, canonical product URL, truncated affiliate URL, source file/row, and row error. Only valid unique `NEW/UPDATED/UNCHANGED` rows participate in confirmation.

Confirmation form contains only:

```html
<input type="hidden" name="_csrf" value="{{ csrf_token }}">
<input type="hidden" name="preview_token" value="{{ preview_token }}">
<button class="btn btn--primary" type="submit">Import vào Product Pool</button>
```

No post/publish CTA.

In `base.html`, add a separate link:

```html
<a href="/sanpham/shopee-import" class="nav-item {{ 'nav-item--active' if page=='shopee-csv-import' }}">
  <span class="nav-icon">⇩</span><span class="nav-label">Shopee CSV Import</span>
</a>
```

- [ ] **Step 6: Run web + parser + DB suites GREEN and commit**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_parser -v
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_import -v
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_web -v

git add web/shopee_csv_import.py web/templates/shopee_csv_import.html web/__init__.py web/templates/base.html tests/test_shopee_csv_web.py
git commit -m "feat: add Shopee CSV import workspace"
```

---

### Task 5: Aggregate audit, operator docs, and regression boundaries

**Files:**
- Modify: `core/shopee_csv_import.py`
- Create: `docs/SHOPEE_AFFILIATE_CSV_IMPORT.md`
- Modify: `tests/test_shopee_csv_import.py`
- Modify: `tests/test_pilot.py`

**Interfaces:**
- Audit actions:
  - `shopee_csv_preview`
  - `shopee_csv_import_completed`
- Audit detail only aggregate counts; no raw rows, full affiliate URLs, or CSV bodies.

- [ ] **Step 1: Add RED audit-sanitization tests**

Verify preview/import audit details contain counts only and that serialized detail does not contain `s.shopee.vn`, product names, raw CSV text, or preview token.

Use an aggregate payload exactly shaped like:

```python
{
    "files": 2,
    "rows": 150,
    "new": 80,
    "updated": 50,
    "unchanged": 10,
    "duplicate": 5,
    "error": 5,
}
```

- [ ] **Step 2: Implement aggregate audit calls**

Use existing `core.db.audit()`. Preview audit occurs only after successful parse/classification and stores aggregate numbers. Import audit occurs after successful DB transaction. Use an entity identifier such as `"batch"` plus a non-secret short batch identifier; never use the one-time preview token itself as audit entity ID.

- [ ] **Step 3: Add pilot/static safety assertions**

Extend `tests/test_pilot.py` to assert the importer routes exist and that importer source/template do not contain calls to approve/publish/job enqueue APIs. Keep this as a regression guard, not the sole behavioral test.

- [ ] **Step 4: Write operator runbook**

`docs/SHOPEE_AFFILIATE_CSV_IMPORT.md` must document:

```text
Shopee Affiliate → Hoa hồng Sản phẩm
→ chọn tối đa 100 SP
→ Lấy link hàng loạt
→ optional Sub_id
→ Lấy link
→ download CSV
→ ACP /sanpham/shopee-import
→ chọn 1..N CSV
→ Preview
→ kiểm tra NEW/UPDATED/UNCHANGED/ERROR
→ Import vào Product Pool
```

Document supported numeric formats, max files/size/rows, exact dedupe rule, that `Doanh thu` is treated as sold-count-like, and that importer stops at Product Pool.

Add controlled pilot steps using a copy of a real Shopee CSV against non-production/test DB first; explicitly verify no Post/job/publish side effect.

- [ ] **Step 5: Run focused tests and commit**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_import -v
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_pilot

git add core/shopee_csv_import.py tests/test_shopee_csv_import.py tests/test_pilot.py docs/SHOPEE_AFFILIATE_CSV_IMPORT.md
git commit -m "docs: add Shopee CSV import runbook and audit"
```

---

### Task 6: Full verification, diff review, and Draft PR

**Files:**
- No production feature expansion.
- Review every file changed by Tasks 1–5.

**Interfaces:**
- Deliverable: verified branch + Draft PR to `main`.

- [ ] **Step 1: Run complete focused Shopee CSV gate**

```bash
export ACP_ADAPTER=mock
export ACP_SOURCE=mock

python -m acp.tests.test_shopee_csv_parser -v
python -m acp.tests.test_shopee_csv_import -v
python -m acp.tests.test_shopee_csv_web -v
```

Expected: PASS with exact output recorded.

- [ ] **Step 2: Run existing Shopee regressions**

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

If a named suite no longer exists on current `main`, record that exact fact and use the current equivalent suite; do not invent a passing result.

- [ ] **Step 3: Run pipeline/release regressions**

From the parent directory containing `acp/`:

```bash
python -m acp.tests.test_pipeline
python -m acp.tests.test_pilot
```

Then inside `acp/`:

```bash
python tests/test_manage.py
./manage.sh test
git diff --check
python -m compileall core web tests adapters
```

No production publish.

- [ ] **Step 4: Inspect diff/status and secret/runtime safety**

```bash
git status --short
git diff --stat main...HEAD
git diff --check main...HEAD
git diff main...HEAD
```

Confirm no `.env.local`, DB/WAL/SHM, `var/`, generated media, tokens, real CSV upload, or runtime artifacts are tracked. The user-provided real CSV is a design/test reference only; do not commit it.

- [ ] **Step 5: Controlled local pilot**

Use a copy of a real Shopee bulk-link CSV against a test/non-production DB. Verify:

```text
preview does not change DB
→ confirm import inserts/updates expected rows
→ exact short affiliate URLs preserved
→ reimport is idempotent
→ richer existing metadata survives
→ changed price adds one affiliate_csv observation
→ no post created
→ no job enqueued
→ no publish action
```

If a browser/manual action cannot be completed in the execution environment, mark pilot `NOT RUN` with reason; never claim PASS.

- [ ] **Step 6: Open Draft PR**

Title:

```text
feat: import Shopee affiliate CSV batches
```

PR body must summarize scope, safety boundaries, exact verification commands/results, and any remaining pilot/environment blocker. Keep Draft until required gates are green.

---

## Definition of Done

- [ ] One or more official Shopee bulk-link CSV files can be previewed together.
- [ ] Real observed number formats parse correctly.
- [ ] Invalid rows are isolated and visible.
- [ ] Last valid duplicate occurrence wins deterministically.
- [ ] Preview does not mutate DB.
- [ ] Confirmation trusts only server-side one-time preview data.
- [ ] Existing `manual_shopee` Product is reused instead of duplicated.
- [ ] New Product has no fabricated image/rating/original price/category facts beyond safe schema default `khac`.
- [ ] Richer existing metadata survives CSV updates.
- [ ] Official `Link ưu đãi` is stored unchanged and marked READY.
- [ ] Price history records only first/change observations with source `affiliate_csv`.
- [ ] Import is idempotent.
- [ ] No Post/job/approve/publish side effect.
- [ ] Aggregate audit contains no raw CSV/affiliate URL/token.
- [ ] Focused tests and required regressions have fresh output.
- [ ] `./manage.sh test` has fresh output before Ready/merge.
- [ ] Draft PR documents any environment/manual pilot blocker accurately.
