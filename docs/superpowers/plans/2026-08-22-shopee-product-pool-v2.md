# Shopee Product Pool v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Follow TDD: write the failing test first, run it and confirm the expected failure, implement the minimum production code, then rerun the focused test before moving on.

**Goal:** Complete the approved Shopee product workflow so a confirmed official Shopee Affiliate CSV immediately creates bounded image-enrichment work, `/sanpham/shopee` becomes a paginated/filterable operational Product Pool with accurate global and per-niche statistics, and Threads Auto candidates come only from `provider='SHOPEE_AFFILIATE'`.

**Architecture:** Keep the existing Product, `shopee_image_enrichment_job`, Auto scheduler, `publish_target`, `job_queue`, preflight and live publisher authoritative. Add one non-publish queue job for import-triggered enrichment, add a read-only Product Pool projection service, and narrow the installed Auto candidate provider boundary to Shopee-only. Do not add a second scheduler, second publish queue, mutable Product Pool state table, or synchronous Shopee network work inside the CSV import HTTP request.

**Tech Stack:** Python 3, Flask/Jinja, SQLite, existing ACP `job_queue`, unittest, existing Shopee safe HTTP/image enrichment and Threads scheduler/publisher.

**Spec:** `docs/superpowers/specs/2026-08-22-shopee-product-pool-v2-design.md`

## Global constraints

- Auto candidate provider is exactly `SHOPEE_AFFILIATE`.
- Official CSV remains source of truth for product identity, price, sold count, commission and affiliate URL.
- Import HTTP requests must not perform public Shopee HTML/image network requests inline.
- Immediate enrichment means: successful confirm creates `SHOPEE_ENRICH_PRODUCT` queue work in the same operator action, and the existing minute worker can execute it on the next pass.
- `READY` enrichment products are not re-enriched; `FAILED` and `NEEDS_HELPER` remain explicit retry/helper states.
- The existing 60-minute enrichment stage remains recovery/backfill, not the primary trigger.
- Product Pool niche classification uses `core.niche.NICHES` and its current match semantics.
- Product Pool `Auto Eligible` means an UNUSED product passes the existing Shopee Auto eligibility function for at least one ACTIVE, enabled, Auto-enabled Threads channel; do not replace this with an approximation.
- Usage-state precedence is `PUBLISHED > SCHEDULED > REVIEW > UNUSED` and is independent from image freshness/health.
- Per-niche counts may overlap because one product can match multiple niches.
- No automated verification may publish another live Threads post. Use mock/test adapters only.

---

### Task 1: Make the installed Auto candidate boundary Shopee-only

**Files:**
- Modify: `tests/test_shopee_auto_pipeline.py`
- Modify: `core/shopee_auto_runtime.py`

**Interfaces:**
- Existing: `pipeline._candidate_products_for_channel(conn, channel, limit, now_utc=None) -> list[dict]`
- Existing: `_shopee_auto_candidates(conn, channel, limit, now_utc) -> list[dict]`
- New contract after `shopee_auto_runtime.install()`: `_candidate_products_for_channel(...)` returns only Shopee Affiliate products for Auto scheduling.

- [ ] **Step 1: Add the failing provider-boundary test**

In `tests/test_shopee_auto_pipeline.py`, extend `ShopeeAutoPipelineTests` with a test that creates one valid Shopee candidate and one valid legacy candidate, then calls the installed `pipeline._candidate_products_for_channel(...)` and asserts every returned product is Shopee:

```python
def test_installed_auto_candidate_source_is_shopee_only(self):
    self._insert_product(product_id="sp1", provider="SHOPEE_AFFILIATE")
    self._insert_product(
        product_id="legacy1",
        provider="LEGACY",
        name="Tai nghe bluetooth sạc nhanh legacy",
        has_inventory=1,
        main_image_url="https://cdn.example/legacy.jpg",
        image_path_local="/tmp/legacy.jpg",
    )

    with mock.patch.object(
        pipeline,
        "_original_candidate_products_for_channel_for_test",
        create=True,
    ):
        rows = pipeline._candidate_products_for_channel(
            self.conn, self._channel(), 20, self.now
        )

    self.assertTrue(rows)
    self.assertEqual(
        {row["product"]["provider"] for row in rows},
        {"SHOPEE_AFFILIATE"},
    )
```

Use the existing fixture helpers rather than inventing a separate schema. If the legacy fixture is rejected earlier by current legacy rules, patch the saved original candidate function at the `shopee_auto_runtime.install()` seam so it returns a synthetic legacy candidate plus the Shopee candidate; the assertion must prove the installed wrapper does not merge legacy rows.

- [ ] **Step 2: Run the focused test and confirm RED**

Run from the repository parent so canonical `acp.*` imports are used:

```bash
cd ~/Downloads/ACP/releases/2.0
source acp/.venv/bin/activate
export PYTHONPATH="$PWD"
python -m unittest \
  acp.tests.test_shopee_auto_pipeline.ShopeeAutoPipelineTests.test_installed_auto_candidate_source_is_shopee_only -v
```

Expected: FAIL because current `install().candidates()` keeps `original_candidates(...)` and extends them with Shopee rows.

- [ ] **Step 3: Make the minimal production change**

In `core/shopee_auto_runtime.py`, replace the current merge wrapper:

```python
def candidates(conn, channel, limit: int, now_utc=None):
    current = now_utc or datetime.now(timezone.utc)
    base = [
        item for item in original_candidates(conn, channel, limit, current)
        if str(_row_get(item.get("product"), "provider") or "") != SHOPEE_PROVIDER
    ]
    base.extend(_shopee_auto_candidates(conn, channel, limit, current))
    base.sort(key=lambda item: -float(item.get("score") or 0.0))
    return base[:limit]
```

with:

```python
def candidates(conn, channel, limit: int, now_utc=None):
    current = now_utc or datetime.now(timezone.utc)
    return _shopee_auto_candidates(conn, channel, limit, current)
```

Keep `original_candidates` available only if another installed wrapper still needs it; otherwise remove the unused local assignment. Do not change manual catalog flows or non-Shopee eligibility/preflight behavior.

- [ ] **Step 4: Verify GREEN and regress Shopee scheduler behavior**

Run:

```bash
python -m unittest \
  acp.tests.test_shopee_auto_pipeline \
  acp.tests.test_auto_scheduler \
  acp.tests.test_auto_scheduler_safety -v
```

Expected: PASS; no network/live publisher calls.

- [ ] **Step 5: Commit**

```bash
cd ~/Downloads/ACP/releases/2.0/acp
git add core/shopee_auto_runtime.py tests/test_shopee_auto_pipeline.py
git diff --cached --check
git commit -m "feat: restrict Threads auto candidates to Shopee"
```

---

### Task 2: Return exact imported Product IDs for post-commit enrichment triggering

**Files:**
- Modify: `tests/test_shopee_csv_enrichment.py`
- Modify: `core/shopee_csv_import.py`

**Interfaces:**
- Existing: `import_rows(conn, row_results) -> dict`
- Extended result: existing public numeric summary keys remain unchanged; add internal key `product_ids: list[str]` containing unique valid Shopee Product IDs touched by this confirmed batch.

- [ ] **Step 1: Write failing tests for batch Product IDs**

Add tests to `tests/test_shopee_csv_enrichment.py`:

```python
def test_import_rows_returns_unique_touched_product_ids(self):
    summary = import_rows(self.conn, [result("123"), result("124")])
    rows = self.conn.execute(
        "SELECT id FROM product WHERE provider='SHOPEE_AFFILIATE' ORDER BY external_product_id"
    ).fetchall()
    self.assertEqual(summary["product_ids"], [row["id"] for row in rows])


def test_reimport_returns_same_product_id_without_duplicate(self):
    first = import_rows(self.conn, [result("123")])
    second = import_rows(self.conn, [result("123")])
    self.assertEqual(first["product_ids"], second["product_ids"])
    self.assertEqual(len(second["product_ids"]), 1)
```

Also assert invalid and `DUPLICATE_IN_UPLOAD` rows do not appear in `product_ids`.

- [ ] **Step 2: Run and confirm RED**

```bash
cd ~/Downloads/ACP/releases/2.0
export PYTHONPATH="$PWD"
python -m unittest acp.tests.test_shopee_csv_enrichment -v
```

Expected: new assertions fail because `import_rows()` currently returns only numeric counts.

- [ ] **Step 3: Extend `import_rows()` without changing transaction semantics**

In `core/shopee_csv_import.py`, initialize:

```python
summary = {
    "total": len(row_results or []),
    "new": 0,
    "updated": 0,
    "unchanged": 0,
    "duplicate": 0,
    "error": 0,
    "product_ids": [],
}
```

After NEW/UPDATED/UNCHANGED resolves a Product ID, append it once:

```python
if product_id not in summary["product_ids"]:
    summary["product_ids"].append(product_id)
```

Do this inside the existing transaction after `enqueue_product()` succeeds. Do not put affiliate URLs or raw CSV content in the returned internal list. Existing `_audit_detail()` in the web layer already allowlists numeric audit keys, so `product_ids` must remain out of audit payloads.

- [ ] **Step 4: Verify GREEN and existing CSV contracts**

```bash
python -m unittest \
  acp.tests.test_shopee_csv_import \
  acp.tests.test_shopee_csv_enrichment -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/Downloads/ACP/releases/2.0/acp
git add core/shopee_csv_import.py tests/test_shopee_csv_enrichment.py
git diff --cached --check
git commit -m "feat: expose touched Shopee product ids after import"
```

---

### Task 3: Add import-triggered `SHOPEE_ENRICH_PRODUCT` queue work

**Files:**
- Create: `core/shopee_enrichment_jobs.py`
- Modify: `core/__init__.py`
- Modify: `web/shopee_csv_import.py`
- Modify: `tests/test_shopee_csv_web.py`
- Modify: `tests/test_shopee_image_enrichment_flow.py`

**Interfaces:**
- New job type: `SHOPEE_ENRICH_PRODUCT`
- New helper: `enqueue_pending_product(conn, product_id: str) -> int`
- New handler: `handle_shopee_enrich_product(conn, payload, ctx) -> None`
- Queue payload: `{"product_id": "..."}` only
- Idempotency key: `shopee-enrich:{product_id}:{shopee_image_enrichment_job.updated_at}`

- [ ] **Step 1: Write failing queue-trigger tests**

In `tests/test_shopee_csv_web.py`, replace the old expectation that confirm leaves `job_queue` empty. Keep the assertion that preview never mutates it, but after confirm assert exactly one enrichment execution job exists:

```python
row = conn.execute(
    "SELECT * FROM job_queue WHERE job_type='SHOPEE_ENRICH_PRODUCT'"
).fetchone()
self.assertIsNotNone(row)
payload = json.loads(row["payload"])
self.assertEqual(payload, {"product_id": product["id"]})
```

Add:

```python
def test_reimport_does_not_create_duplicate_concurrent_enrichment_work(self):
    # confirm first CSV, then confirm the same product from a new preview
    # while enrichment state generation has not changed
    # assert COUNT(job_queue WHERE job_type='SHOPEE_ENRICH_PRODUCT') == 1
```

Add a queue-trigger failure test by injecting a configured enqueue function or patching the helper so it raises `sqlite3.DatabaseError`; assert Product import still exists, preview is consumed after successful Product commit, and the response remains successful with bounded recovery messaging.

- [ ] **Step 2: Write failing worker-handler tests**

In `tests/test_shopee_image_enrichment_flow.py`, add a class that initializes canonical `acp.core` so job handlers are registered. Test:

```python
def test_enrichment_job_handler_processes_pending_product(self):
    # insert PENDING Shopee Product
    # inject fake resolver/http/storage through ctx
    # enqueue_pending_product(...)
    # jobs.run_once(conn, limit=1, ctx=ctx)
    # assert job_queue DONE and shopee_image_enrichment_job READY
```

Also add:

```python
def test_publish_disabled_worker_still_runs_shopee_enrichment_job(self):
    # set PUBLISH_WORKER_ENABLED false via existing system_settings helper
    # enqueue SHOPEE_ENRICH_PRODUCT
    # run jobs.run_once(...)
    # assert done == 1, skipped == 0
```

This proves the existing `skip_publish` filter only suppresses `PUBLISH_POST`, which is the required behavior.

- [ ] **Step 3: Run focused tests and confirm RED**

```bash
cd ~/Downloads/ACP/releases/2.0
export PYTHONPATH="$PWD"
python -m unittest \
  acp.tests.test_shopee_csv_web \
  acp.tests.test_shopee_image_enrichment_flow -v
```

Expected: failures because `SHOPEE_ENRICH_PRODUCT` helper/handler does not exist and confirm creates no execution job.

- [ ] **Step 4: Implement a focused job-registration module**

Create `core/shopee_enrichment_jobs.py`:

```python
from __future__ import annotations

from ..adapters.safe_http import SafeHttpClient
from ..adapters.shopee_affiliate import ProductMetadataResolver
from . import storage
from .jobs import enqueue, handler
from .shopee_image_enrichment import (
    MAX_IMAGE_BYTES,
    PENDING,
    enrich_product,
    get_job,
)

JOB_TYPE = "SHOPEE_ENRICH_PRODUCT"


def enqueue_pending_product(conn, product_id: str) -> int:
    state = get_job(conn, product_id)
    if not state or state["status"] != PENDING:
        return 0
    generation = str(state["updated_at"])
    return enqueue(
        conn,
        JOB_TYPE,
        {"product_id": str(product_id)},
        idempotency_key=f"shopee-enrich:{product_id}:{generation}",
    )


@handler(JOB_TYPE)
def handle_shopee_enrich_product(conn, payload, ctx):
    product_id = str(payload.get("product_id") or "").strip()
    if not product_id:
        raise ValueError("Thiếu product_id cho Shopee enrichment")

    state = get_job(conn, product_id)
    if not state or state["status"] != PENDING:
        return

    resolver = ctx.get("shopee_metadata_resolver") or ProductMetadataResolver()
    image_http = ctx.get("shopee_image_http") or SafeHttpClient(max_bytes=MAX_IMAGE_BYTES)
    media_dir = ctx.get("shopee_media_dir")
    backend = ctx.get("storage") or storage.get_storage()

    kwargs = {
        "metadata_resolver": resolver,
        "storage_backend": backend,
        "image_http": image_http,
    }
    if media_dir:
        kwargs["media_dir"] = media_dir
    else:
        from .shopee_image_enrichment import _default_media_dir
        kwargs["media_dir"] = _default_media_dir()

    enrich_product(conn, product_id, **kwargs)
```

If exposing `_default_media_dir` is undesirable, promote it to a public `default_media_dir()` in `core/shopee_image_enrichment.py` with a direct unit test; do not duplicate the path calculation.

- [ ] **Step 5: Register the handler only in canonical `acp.core` imports**

In `core/__init__.py`, alongside the existing canonical namespace runtime install:

```python
if __name__ == "acp.core":
    from . import shopee_enrichment_jobs as _shopee_enrichment_jobs  # noqa: F401,E402
    from . import shopee_auto_runtime as _shopee_auto_runtime  # noqa: E402
    _shopee_auto_runtime.install()
```

This preserves the existing protection for legacy top-level `core` imports.

- [ ] **Step 6: Trigger queue work after successful confirm**

In `web/shopee_csv_import.py`, import only the helper:

```python
from ..core.shopee_enrichment_jobs import enqueue_pending_product
```

After `import_rows()` succeeds and before closing `conn`, attempt to enqueue each touched Product ID:

```python
enqueued = 0
try:
    for product_id in result.get("product_ids", []):
        if enqueue_pending_product(conn, product_id):
            enqueued += 1
except sqlite3.DatabaseError:
    current_app.logger.warning("Shopee import enrichment trigger failed")
    result["enrichment_trigger_failed"] = True
else:
    result["enrichment_queued"] = enqueued
```

Do not let this best-effort trigger roll back a successful Product import. Keep `_audit_detail()` unchanged so Product IDs and queue internals never enter audit logs. The HTML result may display only numeric `enrichment_queued` or a generic recovery message.

- [ ] **Step 7: Verify GREEN**

```bash
python -m unittest \
  acp.tests.test_shopee_csv_web \
  acp.tests.test_shopee_csv_enrichment \
  acp.tests.test_shopee_image_enrichment_flow -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
cd ~/Downloads/ACP/releases/2.0/acp
git add \
  core/__init__.py \
  core/shopee_enrichment_jobs.py \
  web/shopee_csv_import.py \
  tests/test_shopee_csv_web.py \
  tests/test_shopee_image_enrichment_flow.py
git diff --cached --check
git commit -m "feat: trigger Shopee enrichment after CSV import"
```

---

### Task 4: Build the read-only Shopee Product Pool projection service

**Files:**
- Create: `core/shopee_product_pool.py`
- Create: `tests/test_shopee_product_pool.py`
- Modify only if necessary for compatibility: `web/shopee_auto_state.py`

**Interfaces:**
- New dataclass: `ProductPoolFilters`
- New function: `parse_filters(mapping) -> ProductPoolFilters`
- New function: `build_product_pool(conn, filters, *, now_utc=None) -> dict`
- Returned shape:

```python
{
    "items": [...],
    "summary": {...},
    "niches": [...],
    "pagination": {...},
    "filters": filters,
}
```

- Item fields include existing Product/enrichment columns plus `usage_state`, `auto_state`, `matched_niches`, `auto_channel_handle`, `auto_scheduled_at`.

- [ ] **Step 1: Create a failing provider/pagination test file**

Create `tests/test_shopee_product_pool.py` with a real temporary ACP DB, fixture helpers for Product/channel/post/target/enrichment rows, and tests covering:

```python
def test_pool_excludes_non_shopee_products(self): ...
def test_default_page_size_is_twenty(self): ...
def test_allowed_page_sizes_are_20_50_100(self): ...
def test_invalid_page_and_page_size_fall_back_safely(self): ...
def test_requested_page_is_clamped_to_last_page(self): ...
```

Insert 25+ Shopee rows in the pagination test so page boundaries are real.

- [ ] **Step 2: Add failing search/image/usage/niche tests**

Cover:

```python
def test_search_matches_product_name_or_shop_name(self): ...
def test_image_filter_ready_and_missing(self): ...
def test_usage_state_precedence_published_over_scheduled_over_review(self): ...
def test_usage_filter_unused_scheduled_review_published(self): ...
def test_niche_filter_uses_core_niche_matcher(self): ...
```

For niche semantics, use a product name that clearly matches `thoi-trang-nu` such as `"Đầm maxi nữ dự tiệc"` and one that clearly matches `cong-nghe` such as `"Tai nghe bluetooth sạc nhanh"`; assert the service uses `niche.match_reasons`/existing matcher rather than `category_code == niche`.

- [ ] **Step 3: Add failing global/per-niche summary tests**

Cover page-independent counts:

```python
def test_global_summary_counts_full_filtered_pool_not_current_page(self): ...
def test_published_stale_product_still_counts_as_published(self): ...
def test_per_niche_breakdown_total_unused_scheduled_published(self): ...
def test_one_product_can_contribute_to_more_than_one_niche(self): ...
```

`summary` must include:

```python
{
    "total": 0,
    "unused": 0,
    "auto_eligible": 0,
    "scheduled": 0,
    "published": 0,
    "review": 0,
    "ready": 0,
    "needs_helper": 0,
    "failed": 0,
    "stale": 0,
}
```

- [ ] **Step 4: Add the exact Auto Eligible tests**

Test all of these:

```python
def test_unused_product_is_auto_eligible_when_one_active_auto_channel_accepts_it(self): ...
def test_no_active_auto_channel_means_not_auto_eligible(self): ...
def test_ready_but_niche_mismatched_product_is_not_auto_eligible(self): ...
def test_scheduled_or_published_product_is_not_counted_auto_eligible(self): ...
```

Patch only expensive media file existence where necessary; call the real `shopee_auto_runtime._shopee_product_auto_eligibility(...)` for correctness.

- [ ] **Step 5: Run the new test module and confirm RED**

```bash
cd ~/Downloads/ACP/releases/2.0
export PYTHONPATH="$PWD"
python -m unittest acp.tests.test_shopee_product_pool -v
```

Expected: import failure because `core/shopee_product_pool.py` does not exist.

- [ ] **Step 6: Implement filter normalization**

Create `core/shopee_product_pool.py` with:

```python
from dataclasses import dataclass
from datetime import datetime, timezone
import math

from . import niche
from .shopee_auto_runtime import (
    SHOPEE_PROVIDER,
    _shopee_product_auto_eligibility,
    _shopee_snapshot_is_fresh,
)

ALLOWED_PER_PAGE = (20, 50, 100)
IMAGE_FILTERS = {"all", "ready", "missing", "needs_helper", "failed", "pending"}
USAGE_FILTERS = {"all", "unused", "scheduled", "review", "published"}
AUTO_FILTERS = {
    "all", "eligible", "waiting_image", "stale", "review", "scheduled", "published"
}


@dataclass(frozen=True)
class ProductPoolFilters:
    q: str = ""
    niche: str = ""
    auto: str = "all"
    image: str = "all"
    usage: str = "all"
    page: int = 1
    per_page: int = 20
```

`parse_filters()` must trim `q`, accept niche only when in `niche.NICHES`, normalize enum filters to lowercase, clamp page to `>=1`, and fall back `per_page` to 20 unless it is exactly 20/50/100.

- [ ] **Step 7: Implement one-pass Product projection**

Load only Shopee rows, joined to enrichment status:

```sql
SELECT p.*, j.status AS enrichment_status,
       j.last_error_code, j.last_error,
       j.attempt_count, j.download_attempt_count
FROM product p
LEFT JOIN shopee_image_enrichment_job j ON j.product_id=p.id
WHERE p.provider=?
ORDER BY p.updated_at DESC, p.id DESC
```

Load active Auto Threads channels once:

```sql
SELECT * FROM channel
WHERE platform='threads'
  AND status='ACTIVE'
  AND COALESCE(enabled,1)=1
  AND COALESCE(auto_schedule_enabled,0)=1
ORDER BY code
```

For each Product:

1. derive `matched_niches` using the existing matcher;
2. derive usage state with precedence `PUBLISHED > SCHEDULED > REVIEW > UNUSED`;
3. preserve scheduled channel/timestamp if a live auto target exists;
4. derive health/Auto state:
   - historical `PUBLISHED`, `SCHEDULED`, `REVIEW` first;
   - otherwise `WAITING_IMAGE` if enrichment != READY;
   - otherwise `STALE` if CSV stale;
   - otherwise call `_shopee_product_auto_eligibility(...)` across active Auto channels and use `ELIGIBLE` if any returns `(True, "ok")`;
   - otherwise use a neutral non-eligible state such as `INELIGIBLE` for the service and keep it accepted by the `auto=all` filter.

Do not call one SQL query per summary card. Build one projected list, then compute filters/stats from it.

- [ ] **Step 8: Implement filtering, summaries and pagination**

Apply filters in this order to the projection: `q`, niche, image, usage, auto. The global summary and niche summary must be calculated from the full Shopee projection before table pagination; the table `total_filtered` is calculated after active filters.

Pagination contract:

```python
total_filtered = len(filtered)
total_pages = max(1, math.ceil(total_filtered / filters.per_page))
page = min(filters.page, total_pages)
start = (page - 1) * filters.per_page
items = filtered[start:start + filters.per_page]
```

Return:

```python
"pagination": {
    "page": page,
    "per_page": filters.per_page,
    "total_items": total_filtered,
    "total_pages": total_pages,
    "has_prev": page > 1,
    "has_next": page < total_pages,
}
```

Per-niche rows are ordered by `niche.NICHES` insertion order and shaped:

```python
{
    "code": code,
    "name": niche.NICHES[code]["name"],
    "total": total,
    "unused": unused,
    "scheduled": scheduled,
    "published": published,
}
```

- [ ] **Step 9: Verify GREEN**

```bash
python -m unittest \
  acp.tests.test_shopee_product_pool \
  acp.tests.test_shopee_auto_state -v
```

If `web/shopee_auto_state.py` becomes redundant, keep it as a thin compatibility wrapper over Product Pool projection helpers until existing tests/routes migrate; do not delete it in the same step unless every caller is removed and tests prove compatibility.

- [ ] **Step 10: Commit**

```bash
cd ~/Downloads/ACP/releases/2.0/acp
git add core/shopee_product_pool.py tests/test_shopee_product_pool.py web/shopee_auto_state.py
git diff --cached --check
git commit -m "feat: add Shopee Product Pool projection service"
```

---

### Task 5: Wire `/sanpham/shopee` to server-side filters, pagination and pool-wide statistics

**Files:**
- Modify: `web/shopee_image_enrichment.py`
- Modify: `web/templates/shopee_image_enrichment.html`
- Modify: `tests/test_shopee_image_enrichment_web.py`

**Interfaces:**
- Route: `GET /sanpham/shopee?q=&niche=&auto=&image=&usage=&page=&per_page=`
- Backward compatibility: old `status=` image filter URLs continue to map to `image=` for one release or redirects preserve the equivalent value.

- [ ] **Step 1: Write failing web tests for query controls**

Extend `tests/test_shopee_image_enrichment_web.py`:

```python
def test_workspace_defaults_to_twenty_products_per_page(self): ...
def test_workspace_supports_per_page_50_and_100(self): ...
def test_workspace_searches_name_and_shop(self): ...
def test_workspace_filters_by_niche_usage_auto_and_image(self): ...
def test_workspace_renders_global_summary_independent_of_page(self): ...
def test_workspace_renders_per_niche_usage_cards(self): ...
def test_pagination_links_preserve_current_filters(self): ...
```

For filter preservation, request a URL such as:

```text
/sanpham/shopee?q=dam&niche=thoi-trang-nu&usage=unused&image=ready&auto=eligible&page=2&per_page=20
```

and assert next/previous links retain every filter except the changed page.

- [ ] **Step 2: Run web tests and confirm RED**

```bash
cd ~/Downloads/ACP/releases/2.0
export PYTHONPATH="$PWD"
python -m unittest acp.tests.test_shopee_image_enrichment_web -v
```

Expected: failures because current route calls `list_products(... limit=200)` and template has only image-status tabs.

- [ ] **Step 3: Replace route-local aggregation with the Product Pool service**

In `web/shopee_image_enrichment.py`, import:

```python
from ..core.shopee_product_pool import build_product_pool, parse_filters
from ..core import niche
```

Change `page()` to:

```python
@bp.get("/sanpham/shopee")
def page():
    raw = request.args.to_dict(flat=True)
    if "image" not in raw and raw.get("status"):
        raw["image"] = raw["status"]
    filters = parse_filters(raw)

    conn = connect()
    try:
        pool = build_product_pool(conn, filters)
    finally:
        conn.close()

    return render_template(
        "shopee_image_enrichment.html",
        page="shopee-product-pool",
        items=pool["items"],
        summary=pool["summary"],
        niche_summary=pool["niches"],
        niche_options=[{"code": code, "name": data["name"]} for code, data in niche.NICHES.items()],
        filters=pool["filters"],
        pagination=pool["pagination"],
        message=request.args.get("message"),
        err=request.args.get("err"),
        pending_review=_pending_review_count(),
    )
```

Remove the route-level `list_products(... limit=200)`, per-row `derive_auto_state()` loop and `auto_summary(rows)` call. Leave enrichment action routes unchanged.

- [ ] **Step 4: Preserve filters across POST recovery actions**

Replace `_workspace_redirect()` with a helper that preserves only the allowlisted query/form fields:

```python
_FILTER_KEYS = ("q", "niche", "auto", "image", "usage", "page", "per_page")


def _workspace_values():
    values = {}
    for key in _FILTER_KEYS:
        value = request.form.get(key) or request.args.get(key)
        if value not in (None, ""):
            values[key] = value
    return values
```

Then add those values to redirects. Do not blindly forward arbitrary request args.

- [ ] **Step 5: Redesign the top controls in the existing template**

In `web/templates/shopee_image_enrichment.html`:

1. keep Import CSV link;
2. replace image-only tabs with one GET filter form containing:
   - text `q`;
   - select `niche`;
   - select `auto`;
   - select `image`;
   - select `usage`;
   - select `per_page` 20/50/100;
   - Apply and Reset buttons;
3. global cards show:
   - Tổng sản phẩm
   - Chưa dùng
   - Auto Eligible
   - Đã lên lịch
   - Đã đăng
   - Chờ duyệt
   - Ảnh READY
   - Cần Helper
   - Lỗi ảnh
   - CSV quá hạn
4. render niche summary cards using `niche_summary`;
5. keep existing recovery controls as secondary actions.

Use normal Jinja form/query rendering; no new client framework.

- [ ] **Step 6: Update table state display**

Keep current product/image/price/sold/commission/actions. In the Auto/Usage column render both stable usage and operational state:

```jinja2
<span class="status-badge">{{ item.usage_state }}</span>
<div class="note">Auto: {{ item.auto_state }}</div>
```

For `SCHEDULED`, keep channel/timestamp. For `PUBLISHED`, show published state even if `auto_state` health would otherwise be stale. For `WAITING_IMAGE`/`STALE`, keep bounded recovery guidance.

Every POST form/button in each row must include hidden filter fields from `filters`, so returning from Enrich/Retry/Helper does not lose the operator's current view.

- [ ] **Step 7: Add pagination controls**

Render previous/page/next links. Build URLs with `url_for('shopee_image_enrichment.page', ...)` and an explicit dict containing current filter values plus the target page. Do not drop `q`, `niche`, `auto`, `image`, `usage`, or `per_page`.

Display a compact line such as:

```text
Hiển thị 21–40 / 327 sản phẩm
```

based on `pagination` metadata; for zero filtered products show `0 / 0` without negative indexes.

- [ ] **Step 8: Verify GREEN**

```bash
python -m unittest \
  acp.tests.test_shopee_product_pool \
  acp.tests.test_shopee_image_enrichment_web \
  acp.tests.test_shopee_auto_state -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
cd ~/Downloads/ACP/releases/2.0/acp
git add \
  web/shopee_image_enrichment.py \
  web/templates/shopee_image_enrichment.html \
  tests/test_shopee_image_enrichment_web.py
git diff --cached --check
git commit -m "feat: add Shopee Product Pool filters and pagination"
```

---

### Task 6: Update import UI/runbook for event-driven enrichment

**Files:**
- Modify: `web/templates/shopee_csv_import.html`
- Modify: `docs/SHOPEE_IMAGE_ENRICHMENT.md`
- Modify: `tests/test_shopee_csv_web.py`

- [ ] **Step 1: Add failing UI copy assertions**

In `tests/test_shopee_csv_web.py`, after successful confirm assert the page communicates that enrichment work was queued when `enrichment_queued > 0`, and that a trigger failure produces a generic recovery notice without provider details.

Example expected copy:

```text
Đã đưa 1 sản phẩm vào hàng đợi enrich ảnh.
```

Recovery copy:

```text
Import đã hoàn tất; enrich ảnh sẽ được recovery tự động hoặc có thể chạy thủ công trong Shopee Product Pool.
```

- [ ] **Step 2: Run and confirm RED**

```bash
cd ~/Downloads/ACP/releases/2.0
export PYTHONPATH="$PWD"
python -m unittest acp.tests.test_shopee_csv_web -v
```

- [ ] **Step 3: Render bounded post-import status**

Update `web/templates/shopee_csv_import.html` to show `import_summary.enrichment_queued` and the generic failure/recovery state. Do not render Product IDs, full affiliate URLs, raw errors or queue idempotency keys.

- [ ] **Step 4: Update the runbook**

Change `docs/SHOPEE_IMAGE_ENRICHMENT.md` normal flow from “wait for hourly ACP Auto service” to:

```text
Official CSV
  -> confirm import
  -> enqueue image state + SHOPEE_ENRICH_PRODUCT execution jobs
  -> existing minute ACP worker
  -> READY / NEEDS_HELPER / FAILED
  -> next Auto schedule pass
```

Document that the 60-minute `shopee_auto_enrich.py` stage remains recovery/backfill and that Auto now accepts Shopee provider only. Update the Product Pool section with `q/niche/auto/image/usage/page/per_page`, 20/50/100 pagination, global usage cards and overlapping per-niche stats.

- [ ] **Step 5: Verify and commit**

```bash
python -m unittest acp.tests.test_shopee_csv_web -v
cd ~/Downloads/ACP/releases/2.0/acp
git add web/templates/shopee_csv_import.html docs/SHOPEE_IMAGE_ENRICHMENT.md tests/test_shopee_csv_web.py
git diff --cached --check
git commit -m "docs: describe event-driven Shopee Product Pool flow"
```

---

### Task 7: Full focused regression and release verification

**Files:**
- No feature code unless a failing regression exposes a real defect.
- If a defect is found, add/adjust the smallest failing test in the owning test module before changing production code.

- [ ] **Step 1: Freeze live side effects**

Before running any release gate, use test/mock environment only:

```bash
cd ~/Downloads/ACP/releases/2.0
source acp/.venv/bin/activate
export PYTHONPATH="$PWD"
export ACP_ENV=test
export ACP_ADAPTER=mock
export ACP_SOURCE=mock
export ACP_CAPTION_LLM=
export ACP_CONTENT_ENGINE_LLM=
```

Do not run the production `acp-worker.timer`, `worker-once` against the shared live DB, or any live Threads publish as part of this verification.

- [ ] **Step 2: Run the focused Shopee suite**

```bash
python -m unittest \
  acp.tests.test_shopee_auto_pipeline \
  acp.tests.test_shopee_auto_state \
  acp.tests.test_shopee_csv_import \
  acp.tests.test_shopee_csv_enrichment \
  acp.tests.test_shopee_csv_web \
  acp.tests.test_shopee_image_enrichment \
  acp.tests.test_shopee_image_enrichment_flow \
  acp.tests.test_shopee_image_enrichment_review \
  acp.tests.test_shopee_image_enrichment_web \
  acp.tests.test_shopee_observability \
  acp.tests.test_shopee_product_pool \
  acp.tests.test_auto_scheduler \
  acp.tests.test_auto_scheduler_safety -v
```

Expected: all PASS.

- [ ] **Step 3: Run repository release gate**

From `~/Downloads/ACP/releases/2.0/acp`:

```bash
./manage.sh test
git diff --check
python -m compileall core web tests adapters run.py shopee_auto_enrich.py >/dev/null
```

Expected: all commands exit 0.

- [ ] **Step 4: Inspect the final diff for scope and secrets**

```bash
git status --short
git diff main...HEAD --stat
git diff main...HEAD --check
git diff main...HEAD -- \
  core/shopee_auto_runtime.py \
  core/shopee_csv_import.py \
  core/shopee_enrichment_jobs.py \
  core/shopee_product_pool.py \
  web/shopee_csv_import.py \
  web/shopee_image_enrichment.py \
  web/templates/shopee_image_enrichment.html
```

Confirm:

- no token/App Secret/affiliate raw provider response was committed;
- no second scheduler/worker was added;
- no live account IDs were hard-coded;
- Auto candidate source is Shopee-only;
- import event enqueues only non-publish enrichment work;
- Product Pool summaries are full-pool, not current-page counts;
- no production timer cadence was changed unintentionally.

- [ ] **Step 5: Final verification commit if documentation/test-only cleanup is needed**

Only if the gate required test/docs cleanup:

```bash
git add <only-the-files-changed-by-the-gate-fix>
git diff --cached --check
git commit -m "test: verify Shopee Product Pool v2"
```

- [ ] **Step 6: Request code review before merge**

Use `superpowers:requesting-code-review` against `feat/shopee-product-pool-v2`, resolve review findings with `superpowers:receiving-code-review`, rerun the exact focused/release gates, then use `superpowers:verification-before-completion` before claiming the feature is complete.

## Acceptance checklist

The implementation is ready to merge only when all are true:

- [ ] Confirming a valid official Shopee Affiliate CSV creates `SHOPEE_ENRICH_PRODUCT` execution jobs for current PENDING products without a manual Enrich click.
- [ ] The import HTTP request does not perform public Shopee network enrichment inline.
- [ ] Re-importing the same unchanged PENDING generation does not create duplicate concurrent enrichment execution work.
- [ ] READY products are skipped; FAILED/NEEDS_HELPER remain explicit operator recovery states.
- [ ] Non-publish enrichment jobs can run while publishing is disabled.
- [ ] `/sanpham/shopee` defaults to 20 rows and supports 20/50/100 pagination.
- [ ] Search, niche, Auto, image and usage filters work and survive pagination/recovery actions.
- [ ] Global cards count the full pool and include Total, Unused, Auto Eligible, Scheduled, Published, Review, Image READY, Helper, Failed and Stale.
- [ ] Per-niche cards use the existing matcher and show total/unused/scheduled/published; overlap across niches is allowed.
- [ ] Auto Eligible is exact against at least one active Auto Threads channel via existing Shopee eligibility logic.
- [ ] Published history wins over current stale/image health in usage statistics.
- [ ] Installed Threads Auto candidates contain only `provider='SHOPEE_AFFILIATE'`.
- [ ] Existing duplicate/cooldown/category cap/quota/slot/preflight/publish-worker safeguards still pass regression tests.
- [ ] Full focused suite, `./manage.sh test`, `git diff --check` and `compileall` pass with mock/test adapters.
- [ ] No additional live Threads post is generated during verification.
