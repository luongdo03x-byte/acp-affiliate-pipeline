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
- `tests/test_shopee_csv_parser.py` — parser, validation, dedupe tests.
- `tests/test_shopee_csv_batches.py` — preview-batch lifecycle tests.
- `tests/test_shopee_csv_import.py` — DB upsert, preservation, idempotency, price history tests.
- `tests/test_shopee_csv_web.py` — auth, CSRF, multi-file preview, confirmation, replay/expiry/limits.
- `tests/test_shopee_csv_audit.py` — audit redaction and no-post/job/publish boundaries.
- `tests/test_shopee_csv_review_regressions.py` — review-found regressions: non-finite numerics, invalid-row source context, cross-shop item collision.
- `docs/SHOPEE_AFFILIATE_CSV_IMPORT.md` — operator runbook and pilot checklist.

### Modify

- `web/__init__.py` — register the isolated blueprint.
- `web/templates/base.html` — add `Shopee CSV Import` navigation.

---

## Task 1: Pure CSV Parser and Canonical Row Model

Write tests before production code for the verified nine-column CSV, UTF-8 BOM, quoted product names, Vietnamese price/commission/sold formats, exact `s.shopee.vn` affiliate host, direct HTTPS Shopee product identity, item-ID mismatch, malformed rows, and last-valid duplicate behavior.

Required parser interfaces:

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
    source_filename: str | None = None
    source_row_number: int | None = None
```

Parser requirements:

- `utf-8-sig` decode;
- exact required header contract;
- no network;
- reject URL credentials/control characters/non-HTTPS/wrong hosts;
- reject non-finite Decimal values (`NaN`, `Infinity`);
- invalid rows retain filename + line number for preview diagnostics;
- dedupe key `(shop_id, item_id)` with last valid occurrence winning.

Focused command:

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_parser -v
```

---

## Task 2: Short-Lived One-Time Preview Batches

Implement process-local preview batches with:

```python
PREVIEW_TTL_SECONDS = 900
issue_preview(rows, summary, *, now_ts=None) -> dict
peek_preview(token: str, *, now_ts=None) -> dict | None
consume_preview(token: str, *, now_ts=None) -> dict | None
reset_previews() -> None
```

Use `secrets.token_urlsafe(32)`, `time.monotonic()`, `threading.Lock()`, defensive copies, opportunistic expiry cleanup, normalized rows only, and no raw CSV bytes.

Focused command:

```bash
python -m acp.tests.test_shopee_csv_batches -v
```

---

## Task 3: Product Upsert and Price History

DB import rules:

- Product lookup namespace: `source='manual_shopee'`, `merchant='shopee.vn'`.
- Provider provenance: `SHOPEE_AFFILIATE`.
- Existing richer image/rating/review/category/original-price fields survive.
- Optional blank CSV fields do not erase existing values.
- Official short affiliate URL replaces prior one exactly and status becomes `READY`.
- First/change-only price history, source `affiliate_csv`.
- Whole confirmed batch runs inside `core.db.transaction(conn)`.
- Existing same `item_id` with a different canonical `shop_id` is a row error, not an overwrite, because the historical DB natural key is item-centric while the importer identity is `(shop_id,item_id)`.

Focused command:

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_import -v
```

---

## Task 4: Authenticated Multi-File Preview/Import Workspace

Routes:

```text
GET  /sanpham/shopee-import
POST /sanpham/shopee-import/preview
POST /sanpham/shopee-import/confirm
```

Requirements:

- reuse existing dashboard auth + CSRF guard;
- `.csv` only;
- max 20 files;
- read at most 5 MiB + 1 byte/file;
- max 20,000 parsed rows;
- preview classification is read-only for Product/history/Post/job;
- confirm form contains only CSRF + preview token;
- confirmation trusts server-side normalized rows, not browser-posted row fields;
- replay/expired token rejected;
- DB failure leaves token available for retry;
- no post/publish CTA.

Focused command:

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_web -v
```

---

## Task 5: Aggregate Audit and Operator Runbook

Audit events:

```text
shopee_csv_preview
shopee_csv_import_completed
```

Audit detail is aggregate counts only:

```json
{
  "files": 2,
  "rows": 150,
  "new": 80,
  "updated": 50,
  "unchanged": 10,
  "duplicate": 5,
  "error": 5
}
```

No raw row, full affiliate URL, product name/shop, preview token, cookie/session, or CSV body in audit detail.

Runbook must document Shopee download flow, limits, sold-count interpretation, dedupe/collision rules, idempotency, safety boundaries, verification, and controlled pilot.

Focused command:

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_audit -v
```

---

## Task 6: Review Hardening and Full Verification

Reviewer regression suite must cover:

- `NaN`/`Infinity` safe rejection;
- invalid row filename/line diagnostics;
- same item ID from another shop cannot overwrite an existing Product.

Run:

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_shopee_csv_review_regressions -v
```

Then full focused gate:

```bash
export ACP_ADAPTER=mock
export ACP_SOURCE=mock

python -m acp.tests.test_shopee_csv_parser -v
python -m acp.tests.test_shopee_csv_batches -v
python -m acp.tests.test_shopee_csv_import -v
python -m acp.tests.test_shopee_csv_web -v
python -m acp.tests.test_shopee_csv_audit -v
python -m acp.tests.test_shopee_csv_review_regressions -v
```

Existing Shopee regressions:

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

Release regressions:

```bash
python -m acp.tests.test_pipeline
python -m acp.tests.test_pilot
```

Inside package/repo root:

```bash
python tests/test_manage.py
./manage.sh test
git diff --check
python -m compileall core web tests adapters
```

Inspect:

```bash
git status --short
git diff --stat main...HEAD
git diff --check main...HEAD
git diff main...HEAD
```

Confirm no `.env.local`, DB/WAL/SHM, `var/`, generated media, real CSV upload, tokens, or runtime artifacts are tracked.

Controlled pilot uses a copy of a real Shopee bulk-link CSV against a test/non-production DB. Preview must leave Product/history/Post/job unchanged; confirm must preserve exact affiliate links; reimport must be idempotent; changed price must add one `affiliate_csv` observation; existing rich metadata must survive; no Post/job/publish action occurs.

Keep the PR Draft until all required automated gates have fresh green output and the controlled pilot status is accurately recorded.

---

## Definition of Done

- [ ] One or more official Shopee bulk-link CSV files can be previewed together.
- [ ] Real observed number formats parse correctly.
- [ ] `NaN`/`Infinity` are rejected safely.
- [ ] Invalid rows retain source file/line diagnostics.
- [ ] Invalid rows are isolated and visible.
- [ ] Last valid duplicate occurrence wins deterministically.
- [ ] Cross-shop item-ID collision is rejected rather than overwritten.
- [ ] Preview does not mutate Product/history/Post/job.
- [ ] Confirmation trusts only server-side one-time preview data.
- [ ] Existing `manual_shopee` Product is reused instead of duplicated.
- [ ] New Product has no fabricated image/rating/original price facts.
- [ ] Richer existing metadata survives CSV updates.
- [ ] Official `Link ưu đãi` is stored unchanged and marked READY.
- [ ] Price history records only first/change observations with source `affiliate_csv`.
- [ ] Import is idempotent.
- [ ] No Post/job/approve/publish side effect.
- [ ] Aggregate audit contains no raw CSV/affiliate URL/token.
- [ ] Focused tests and required regressions have fresh output.
- [ ] `./manage.sh test` has fresh output before Ready/merge.
- [ ] Draft PR documents environment/manual pilot blockers accurately.
