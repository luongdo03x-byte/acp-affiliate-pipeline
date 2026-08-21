# Shopee Auto Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make official Shopee Affiliate CSV imports flow automatically through bounded image enrichment into the existing Threads auto scheduler and publish worker, without manual Enrich clicks.

**Architecture:** Reuse the existing `shopee_image_enrichment_job`, `fill_auto_schedule()`, `auto_scheduler.route_product()`, `approve_post(..., auto_scheduled=True)`, and `PUBLISH_POST` worker. Add a bounded CLI timer stage for enrichment, explicit `SHOPEE_AFFILIATE` candidate/eligibility/artifact branches, provider-aware 72-hour preflight freshness, and derived Auto observability on the Product Pool page. Do not introduce a second scheduler or a new mutable Auto-state table.

**Tech Stack:** Python 3, Flask, SQLite, systemd user timer/service, Pillow, existing ACP job queue and Threads publisher.

**Spec:** `docs/superpowers/specs/2026-08-21-shopee-auto-pipeline-design.md`

## Global Constraints

- Provider is exactly `SHOPEE_AFFILIATE`.
- Shopee CSV Auto freshness is exactly 72 hours from `last_synced_at`, falling back to `last_seen_at`.
- The existing Shopee enrichment batch maximum stays exactly 20 products per pass.
- The existing `acp-auto-schedule.timer` cadence stays unchanged at 60 minutes in this feature.
- Import HTTP requests must not perform public Shopee network enrichment inline.
- Do not write fake `has_inventory=1` for Shopee CSV products.
- Do not require missing Shopee CSV rating/review fields to satisfy legacy rating/review thresholds.
- Use the exact stored `product.affiliate_url`; do not call another source to generate a Shopee tracking link.
- Do not add a second scheduler, second mutable Auto-state table, or `product.auto_enabled` field.
- Preserve per-channel Auto switch, global publish-worker switch, niche checks, duplicate/cooldown protection, quota, slot, validation, and publish preflight.
- No automated Shopee login, cookie/session/localStorage extraction, private Shopee API, CAPTCHA bypass, or anti-bot evasion.

---

### Task 1: Add the bounded Shopee enrichment timer stage

**Files:**
- Modify: `run.py`
- Modify: `ops/acp-auto-schedule.service`
- Test: `tests/test_shopee_auto_pipeline.py`
- Test: `tests/test_auto_scheduler.py` only if existing service/CLI contract assertions already live there

**Interfaces:**
- Consumes: `core.shopee_image_enrichment.run_batch(connection_factory, limit=20, ...) -> dict`
- Produces: `cmd_shopee_enrich() -> int`, CLI command `python3 run.py shopee-enrich`
- Produces timer order: `product-sync -> shopee-enrich -> auto-schedule -> worker-once`

- [ ] **Step 1: Write failing tests for the CLI contract**

Create `tests/test_shopee_auto_pipeline.py` with a minimal import helper matching the repo test style and tests equivalent to:

```python
import unittest
from unittest import mock


class ShopeeAutoEnrichmentCliTests(unittest.TestCase):
    def test_shopee_enrich_runs_one_bounded_batch(self):
        import run

        fake_summary = {
            "processed": 3,
            "ready": 2,
            "needs_helper": 1,
            "failed": 0,
            "pending": 0,
        }
        with mock.patch("run.init_db"), \
             mock.patch("run.shopee_image_enrichment.run_batch", return_value=fake_summary) as batch:
            rc = run.cmd_shopee_enrich()

        self.assertEqual(rc, 0)
        self.assertEqual(batch.call_count, 1)
        self.assertEqual(batch.call_args.kwargs["limit"], 20)

    def test_shopee_enrich_hides_provider_exception_details(self):
        import run

        with mock.patch("run.init_db"), \
             mock.patch(
                 "run.shopee_image_enrichment.run_batch",
                 side_effect=RuntimeError("secret upstream body"),
             ):
            with mock.patch("builtins.print") as printer:
                rc = run.cmd_shopee_enrich()

        self.assertEqual(rc, 1)
        rendered = " ".join(str(call) for call in printer.call_args_list)
        self.assertNotIn("secret upstream body", rendered)
```

Also add a service-file assertion:

```python
def test_auto_schedule_service_runs_shopee_enrichment_before_scheduler(self):
    text = open("ops/acp-auto-schedule.service", encoding="utf-8").read()
    self.assertLess(text.index("run.py\" shopee-enrich"), text.index("run.py\" auto-schedule"))
    self.assertIn("run.py\" worker-once", text)
```

- [ ] **Step 2: Run the focused test and confirm red**

Run:

```bash
python3 -m unittest tests.test_shopee_auto_pipeline -v
```

Expected: FAIL because `run.cmd_shopee_enrich` and/or the service stage do not exist.

- [ ] **Step 3: Implement the CLI command using the existing batch runner**

In `run.py`, import the module rather than duplicating enrichment logic:

```python
from acp.core import attribution, crypto, jobs, pipeline, scoring, shopee_image_enrichment
```

Add:

```python
def cmd_shopee_enrich():
    """Run one bounded Shopee image-enrichment pass; never publish posts."""
    try:
        init_db()
        summary = shopee_image_enrichment.run_batch(
            connect,
            limit=shopee_image_enrichment.MAX_BATCH_SIZE,
        )
    except Exception:
        print("Shopee enrichment failed. Check local service logs.")
        return 1

    print(
        "Shopee enrichment: "
        + ", ".join(
            f"{key}={int(summary.get(key, 0))}"
            for key in ("processed", "ready", "needs_helper", "failed", "pending")
        )
    )
    return 0
```

Register `shopee-enrich` in the command dispatcher and add it to the CLI help comment near `auto-schedule`.

- [ ] **Step 4: Insert the timer stage without changing cadence**

Change `ops/acp-auto-schedule.service` so the commands run in this order:

```ini
ExecStartPre=/bin/bash -lc '... run.py" product-sync'
ExecStartPre=/bin/bash -lc '... run.py" shopee-enrich'
ExecStart=/bin/bash -lc '... run.py" auto-schedule'
ExecStartPost=/bin/bash -lc '... run.py" worker-once'
TimeoutStartSec=120s
```

Do not modify `ops/acp-auto-schedule.timer` cadence.

- [ ] **Step 5: Run focused tests**

Run:

```bash
python3 -m unittest tests.test_shopee_auto_pipeline -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add run.py ops/acp-auto-schedule.service tests/test_shopee_auto_pipeline.py
git commit -m "feat: automate Shopee image enrichment stage"
```

---

### Task 2: Add provider-aware Shopee Auto eligibility and candidate selection

**Files:**
- Modify: `core/pipeline.py`
- Test: `tests/test_shopee_auto_pipeline.py`

**Interfaces:**
- Produces: `SHOPEE_PROVIDER = "SHOPEE_AFFILIATE"`
- Produces: `SHOPEE_AUTO_FRESHNESS = timedelta(hours=72)`
- Produces: `_shopee_auto_candidates(conn, channel, limit, now_utc) -> list[dict]`
- Produces: `_shopee_product_auto_eligibility(conn, product, channel, now_utc, *, exclude_post_id=None, slot_at=None) -> tuple[bool, str]`
- Extends: `_candidate_products_for_channel(...)` to merge Shopee candidates with legacy/catalog candidates

- [ ] **Step 1: Write failing candidate tests with explicit Shopee fixtures**

Add helpers in `tests/test_shopee_auto_pipeline.py` that create a `SHOPEE_AFFILIATE` Product and matching `shopee_image_enrichment_job`. Cover at least these contracts:

```python
def test_ready_shopee_product_with_unknown_inventory_rating_review_is_candidate(self):
    product_id = self.insert_shopee_product(
        has_inventory=None,
        rating=None,
        review_count=0,
        commission_value=12000,
        affiliate_url="https://s.shopee.vn/example",
        last_synced_at=self.now.isoformat(),
    )
    self.insert_enrichment_job(product_id, status="READY")
    channel = self.insert_auto_channel(niches=["cong-nghe"])

    rows = pipeline._shopee_auto_candidates(self.conn, channel, 20, self.now)

    self.assertEqual([row["product"]["id"] for row in rows], [product_id])


def test_shopee_candidate_requires_ready_image_job(self):
    product_id = self.insert_shopee_product(...)
    self.insert_enrichment_job(product_id, status="NEEDS_HELPER")
    channel = self.insert_auto_channel(niches=["cong-nghe"])
    self.assertEqual(pipeline._shopee_auto_candidates(self.conn, channel, 20, self.now), [])


def test_shopee_candidate_rejects_stale_csv(self):
    product_id = self.insert_shopee_product(
        last_synced_at=(self.now - timedelta(hours=73)).isoformat(),
        ...,
    )
    self.insert_enrichment_job(product_id, status="READY")
    channel = self.insert_auto_channel(niches=["cong-nghe"])
    self.assertEqual(pipeline._shopee_auto_candidates(self.conn, channel, 20, self.now), [])
```

Also cover: missing/invalid `affiliate_url`, commission below configured minimum, niche mismatch, active/recent post, and a product with no usable enriched image.

- [ ] **Step 2: Run the candidate tests and confirm red**

Run:

```bash
python3 -m unittest \
  tests.test_shopee_auto_pipeline.ShopeeAutoCandidateTests -v
```

Expected: FAIL because `_shopee_auto_candidates` does not exist.

- [ ] **Step 3: Add shared provider/freshness helpers**

In `core/pipeline.py` add near catalog provider constants:

```python
SHOPEE_PROVIDER = "SHOPEE_AFFILIATE"
SHOPEE_AUTO_FRESHNESS = timedelta(hours=72)


def _valid_absolute_http_url(value: str) -> bool:
    try:
        parsed = urlparse(str(value or "").strip())
    except (TypeError, ValueError):
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _shopee_snapshot_is_fresh(product, now_utc: datetime) -> bool:
    raw = product["last_synced_at"] or product["last_seen_at"]
    parsed = auto_scheduler._parse_iso_datetime(raw)
    return bool(parsed and now_utc - parsed.astimezone(timezone.utc) <= SHOPEE_AUTO_FRESHNESS)
```

Import `urlparse` from `urllib.parse`.

- [ ] **Step 4: Implement Shopee-specific mutable eligibility**

Implement `_shopee_product_auto_eligibility()` so it checks, in stable-code order:

```python
if not product or product["provider"] != SHOPEE_PROVIDER:
    return False, "product_provider_invalid"
if not channel or not channel["enabled"] or channel["status"] != "ACTIVE":
    return False, "channel_ineligible"
if not channel["auto_schedule_enabled"]:
    return False, "channel_auto_disabled"
if not product["is_available"]:
    return False, "product_unavailable"
if not _valid_absolute_http_url(product["affiliate_url"]):
    return False, "affiliate_link_invalid"
if str(product["affiliate_link_status"] or "").upper() != "READY":
    return False, "affiliate_link_invalid"
if not _shopee_snapshot_is_fresh(product, now_utc):
    return False, "product_sync_stale"
```

Then require the enrichment row to be READY and usable media to exist:

```sql
SELECT status
FROM shopee_image_enrichment_job
WHERE product_id=?
```

Use `image_path_local` or `main_image_url` as the media readiness contract; do not set inventory.

Reuse existing configured filters only for:

- `blocked_categories`;
- channel niche matching;
- `min_commission_value`;
- category/day cap;
- duplicate/cooldown via `_queued_or_recently_published_product_exists()`.

Do not call `scoring._reasons()` for Shopee because it enforces rating/review thresholds absent from the CSV.

- [ ] **Step 5: Implement deterministic Shopee candidate selection**

Query only Shopee rows joined to READY enrichment jobs:

```sql
SELECT p.*
FROM product p
JOIN shopee_image_enrichment_job j ON j.product_id=p.id
WHERE p.provider=?
  AND p.is_available=1
  AND j.status='READY'
ORDER BY COALESCE(p.score, 0) DESC,
         COALESCE(p.commission_value, 0) DESC,
         p.last_synced_at DESC,
         p.id
LIMIT ?
```

For every row, call `_shopee_product_auto_eligibility(...)`. Produce scheduler items shaped like existing candidates:

```python
{
    "product": row,
    "score": bounded_score,
    "rejected": [],
    "breakdown": {"shopee_commission": product["commission_value"] or 0},
}
```

Use `product.score / 100.0` when a score exists. Otherwise use a bounded commission score such as:

```python
commission = max(0.0, float(product["commission_value"] or 0))
bounded_score = min(1.0, commission / 100_000.0)
```

- [ ] **Step 6: Merge Shopee candidates into the existing candidate list**

Change `_candidate_products_for_channel()` from:

```python
combined = legacy + catalog
```

to:

```python
shopee = _shopee_auto_candidates(conn, channel, limit, now_utc)
combined = legacy + catalog + shopee
```

Keep the existing descending score sort and limit.

- [ ] **Step 7: Run focused candidate tests and existing routing tests**

Run:

```bash
python3 -m unittest \
  tests.test_shopee_auto_pipeline.ShopeeAutoCandidateTests \
  tests.test_auto_scheduler -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add core/pipeline.py tests/test_shopee_auto_pipeline.py
git commit -m "feat: route eligible Shopee products automatically"
```

---

### Task 3: Prepare Shopee sales posts with the exact imported affiliate URL

**Files:**
- Modify: `core/pipeline.py`
- Test: `tests/test_shopee_auto_pipeline.py`

**Interfaces:**
- Extends: `_prepare_auto_sales_post_artifacts(...) -> dict`
- Shopee input contract: `product.provider == "SHOPEE_AFFILIATE"`, enriched local media exists, `affiliate_url` valid
- Shopee output contract: exact imported URL in `prepared["affiliate_link"]`; no call to `ctx["source"].create_tracking_link()`

- [ ] **Step 1: Write failing artifact tests**

Add:

```python
def test_shopee_artifacts_use_exact_csv_affiliate_url(self):
    stored = "https://s.shopee.vn/9example"
    product = self.insert_ready_shopee_product(affiliate_url=stored)
    source = mock.Mock()
    ctx = {
        "source": source,
        "storage": self.fake_storage,
    }

    prepared = pipeline._prepare_auto_sales_post_artifacts(
        self.conn,
        ctx,
        product,
        self.campaign,
        self.channel,
        self.template,
        "hook-a",
        score=0.7,
    )

    self.assertTrue(prepared["ok"])
    self.assertEqual(prepared["affiliate_link"], stored)
    source.create_tracking_link.assert_not_called()
```

Add a second test proving missing enriched local media fails safely rather than falling back to another image/link source.

- [ ] **Step 2: Run artifact tests and confirm red**

Run:

```bash
python3 -m unittest \
  tests.test_shopee_auto_pipeline.ShopeeAutoArtifactTests -v
```

Expected: FAIL because Shopee still enters the legacy tracking-link branch.

- [ ] **Step 3: Add the explicit Shopee branch**

In `_prepare_auto_sales_post_artifacts()` keep the ACCESSTRADE catalog branch first, then add:

```python
elif product["provider"] == SHOPEE_PROVIDER:
    link = str(product["affiliate_url"] or "").strip()
    if not _valid_absolute_http_url(link):
        return {"ok": False, "error": "Affiliate link Shopee không hợp lệ"}
    if not str(product["image_path_local"] or "").strip():
        return {"ok": False, "error": "Ảnh Shopee chưa sẵn sàng"}
    attribution_payload = {
        "provider": "shopee_affiliate_csv",
        "link_mode": "imported",
        "product_id": product["id"],
        "post_id": post_id,
    }
else:
    subs = attribution.encode_sub_ids(...)
    link = _tracking_link_from_result(ctx["source"].create_tracking_link(...))
```

Do not mutate the Product affiliate URL.

Continue through the existing shared path:

```python
discount = scoring.real_discount_depth(...)
image_path = imaging.compose(...)
image_url = ctx.get("storage", storage.get_storage()).put(image_path)
caption = content.generate(...)
problems = content.validate(...)
```

- [ ] **Step 4: Run focused artifact and content tests**

Run:

```bash
python3 -m unittest \
  tests.test_shopee_auto_pipeline.ShopeeAutoArtifactTests \
  tests.test_shopee_csv_enrichment \
  tests.test_shopee_image_enrichment -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/pipeline.py tests/test_shopee_auto_pipeline.py
git commit -m "feat: create Shopee auto posts from imported links"
```

---

### Task 4: Make scheduling and publish preflight provider-aware

**Files:**
- Modify: `core/pipeline.py`
- Modify: `core/auto_scheduler.py`
- Test: `tests/test_shopee_auto_pipeline.py`
- Test: `tests/test_auto_scheduler.py`

**Interfaces:**
- Extends: `current_auto_product_eligibility(...)`
- Extends: `auto_scheduler.preflight_auto_target(...)`
- Shopee freshness contract: <=72h passes, >72h fails `product_sync_stale`
- Non-Shopee contract: retain existing inventory + 120-minute freshness behavior

- [ ] **Step 1: Write failing scheduling/preflight tests**

Add tests equivalent to:

```python
def test_fill_auto_schedule_can_schedule_ready_shopee_product(self):
    product_id = self.insert_ready_shopee_product(...)
    stats = pipeline.fill_auto_schedule(
        self.conn,
        self.campaign["code"],
        now_utc=self.now,
        ctx=self.ctx,
    )
    self.assertEqual(stats["scheduled"], 1)
    target = self.conn.execute(
        "SELECT * FROM publish_target WHERE auto_scheduled=1"
    ).fetchone()
    self.assertIsNotNone(target)


def test_shopee_publish_preflight_allows_unknown_inventory_with_fresh_csv(self):
    ok, reason = auto_scheduler.preflight_auto_target(
        self.conn,
        self.target,
        self.post,
        self.channel,
        now_utc=self.now,
    )
    self.assertEqual((ok, reason), (True, "ok"))


def test_shopee_publish_preflight_rejects_csv_older_than_72_hours(self):
    ...
    self.assertEqual((ok, reason), (False, "product_sync_stale"))
```

Add a regression assertion that an ACCESSTRADE/non-Shopee product with `has_inventory=0` still returns `product_inventory_empty`, and one older than 120 minutes still returns `product_sync_stale`.

- [ ] **Step 2: Run focused tests and confirm red**

Run:

```bash
python3 -m unittest \
  tests.test_shopee_auto_pipeline.ShopeeAutoScheduleTests \
  tests.test_shopee_auto_pipeline.ShopeePublishPreflightTests -v
```

Expected: FAIL because the generic inventory/120-minute preflight blocks Shopee.

- [ ] **Step 3: Dispatch mutable eligibility by provider in `pipeline.py`**

At the top of `current_auto_product_eligibility(...)`, after the common product/channel existence checks, delegate Shopee products:

```python
if product["provider"] == SHOPEE_PROVIDER:
    return _shopee_product_auto_eligibility(
        conn,
        product,
        channel,
        now_utc,
        exclude_post_id=exclude_post_id,
        slot_at=slot_at,
    )
```

Keep existing catalog/legacy behavior unchanged for other providers.

- [ ] **Step 4: Make publish preflight provider-aware in `auto_scheduler.py`**

Add constants:

```python
SHOPEE_PROVIDER = "SHOPEE_AFFILIATE"
MAX_SHOPEE_CSV_AGE = timedelta(hours=72)
```

In `preflight_auto_target()` keep common checks (`target already published`, Product exists, `is_available`, affiliate URL). Replace unconditional inventory/freshness logic with:

```python
provider = str(_row_get(product, "provider") or "")
last_synced = _parse_iso_datetime(
    _row_get(product, "last_synced_at") or _row_get(product, "last_seen_at")
)

if provider == SHOPEE_PROVIDER:
    if not last_synced or now_utc - last_synced.astimezone(timezone.utc) > MAX_SHOPEE_CSV_AGE:
        return False, "product_sync_stale"
    image_job = conn.execute(
        "SELECT status FROM shopee_image_enrichment_job WHERE product_id=?",
        (_row_get(product, "id"),),
    ).fetchone()
    if not image_job or image_job["status"] != "READY":
        return False, "product_image_not_ready"
else:
    if int(_row_get(product, "has_inventory") or 0) != 1:
        return False, "product_inventory_empty"
    if not last_synced or now_utc - last_synced.astimezone(timezone.utc) > MAX_AUTO_PRODUCT_SYNC_AGE:
        return False, "product_sync_stale"
```

Keep `affiliate_link_status`, niche, and injected `eligibility_checker` checks afterward.

- [ ] **Step 5: Run scheduling/preflight and legacy regression tests**

Run:

```bash
python3 -m unittest \
  tests.test_shopee_auto_pipeline.ShopeeAutoScheduleTests \
  tests.test_shopee_auto_pipeline.ShopeePublishPreflightTests \
  tests.test_auto_scheduler \
  tests.test_auto_scheduler_safety -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add core/pipeline.py core/auto_scheduler.py tests/test_shopee_auto_pipeline.py tests/test_auto_scheduler.py
git commit -m "fix: enforce Shopee auto publish safety"
```

---

### Task 5: Show derived Auto state in Shopee Product Pool

**Files:**
- Modify: `web/shopee_image_enrichment.py`
- Modify: `web/templates/shopee_image_enrichment.html`
- Test: `tests/test_shopee_image_enrichment_web.py`

**Interfaces:**
- Produces: `_derive_auto_state(conn, product_row, *, now_utc=None) -> dict`
- Each item gets: `auto_state`, optional `auto_channel_handle`, optional `auto_scheduled_at`
- Summary adds counts: `auto_eligible`, `auto_scheduled`, `auto_review`, `auto_stale`

- [ ] **Step 1: Write failing web-state tests**

Extend `tests/test_shopee_image_enrichment_web.py` with fixtures for:

```python
WAITING_IMAGE
ELIGIBLE
SCHEDULED
REVIEW
PUBLISHED
STALE
```

For example:

```python
def test_ready_product_with_auto_target_renders_scheduled_state(self):
    product_id = self.insert_shopee_product(image_status="READY")
    self.insert_auto_scheduled_post(product_id, channel_handle="@tech", scheduled_at="2026-08-21T14:00:00+00:00")

    response = self.client.get("/sanpham/shopee")

    self.assertEqual(response.status_code, 200)
    self.assertIn(b"SCHEDULED", response.data)
    self.assertIn(b"@tech", response.data)
```

Also assert the page copy explains that READY products participate in Threads Auto when eligible and that manual Enrich controls are recovery actions.

- [ ] **Step 2: Run web tests and confirm red**

Run:

```bash
python3 -m unittest tests.test_shopee_image_enrichment_web -v
```

Expected: FAIL because no Auto state is derived/rendered.

- [ ] **Step 3: Implement derived Auto state without a new table**

In `web/shopee_image_enrichment.py` add `_derive_auto_state()` with precedence:

```text
WAITING_IMAGE -> STALE -> SCHEDULED -> REVIEW -> PUBLISHED -> ELIGIBLE
```

Use existing records only:

- enrichment job status;
- Product `last_synced_at`/`last_seen_at` against the same 72-hour constant;
- `publish_target.auto_scheduled=1` and live statuses for SCHEDULED;
- Product posts in `DRAFT`/`PENDING_REVIEW` for REVIEW;
- recent `PUBLISHED`/successful target for PUBLISHED;
- otherwise READY + fresh gives ELIGIBLE.

Return a small dict, e.g.:

```python
{
    "state": "SCHEDULED",
    "channel_handle": "@tech",
    "scheduled_at": "2026-08-21T14:00:00+00:00",
}
```

Attach these values to each `items` row before rendering and aggregate summary counts.

- [ ] **Step 4: Update Product Pool UI copy/status**

Add an `Auto` table column. Render state badges and, for SCHEDULED, channel + scheduled time. Change the enrichment section copy from a required workflow tone to recovery tone, for example:

```html
<p class="note">READY products automatically participate in Threads Auto when eligible. Manual enrichment controls below are for retry/recovery.</p>
```

Do not add per-product Auto toggles.

- [ ] **Step 5: Run focused web tests**

Run:

```bash
python3 -m unittest tests.test_shopee_image_enrichment_web -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web/shopee_image_enrichment.py web/templates/shopee_image_enrichment.html tests/test_shopee_image_enrichment_web.py
git commit -m "feat: show Shopee auto pipeline state"
```

---

### Task 6: Document the normal workflow and run the release gate

**Files:**
- Modify: `docs/SHOPEE_IMAGE_ENRICHMENT.md`
- Modify: `docs/superpowers/specs/2026-08-21-shopee-auto-pipeline-design.md` only if implementation uncovered a contract mismatch that must be reflected
- Verify: focused and core test suites

**Interfaces:**
- Operator normal path becomes: `Import CSV -> wait for timer -> READY -> auto schedule -> worker publish`
- Manual Quét/Enrich remains recovery only

- [ ] **Step 1: Update the operator runbook**

Document explicitly:

```text
Normal operation
1. Import the official Shopee Affiliate CSV.
2. Do not press Enrich during the normal flow.
3. The hourly ACP Auto timer runs: product-sync -> shopee-enrich -> auto-schedule -> worker-once.
4. Publicly enrichable products become READY and enter Threads Auto automatically.
5. NEEDS_HELPER products require the existing Chrome Helper before they can enter Auto.
6. Re-import a current CSV if the Shopee snapshot is older than 72 hours.
```

Document that the global publish-worker switch and per-channel Auto switch remain authoritative.

- [ ] **Step 2: Run the focused Shopee + scheduler regression gate**

Run:

```bash
python3 -m unittest \
  tests.test_shopee_auto_pipeline \
  tests.test_shopee_csv_enrichment \
  tests.test_shopee_image_enrichment \
  tests.test_shopee_image_enrichment_web \
  tests.test_shopee_helper \
  tests.test_shopee_polish_resilience \
  tests.test_auto_scheduler \
  tests.test_auto_scheduler_safety -v
```

Expected: PASS, 0 failures.

- [ ] **Step 3: Run the project release verification**

Use the repo-approved release command first:

```bash
./manage.sh test
```

Then run static sanity checks:

```bash
git diff --check
python3 -m compileall core web tests adapters >/dev/null
```

Expected: all exit 0.

- [ ] **Step 4: Inspect the final diff and status**

Run:

```bash
git status --short
git diff --stat origin/main...HEAD
git log --oneline --decorate -n 12
```

Confirm only intended files changed and no secrets/runtime data are present.

- [ ] **Step 5: Commit documentation/final adjustments**

```bash
git add docs/SHOPEE_IMAGE_ENRICHMENT.md docs/superpowers/specs/2026-08-21-shopee-auto-pipeline-design.md
git commit -m "docs: document Shopee auto publish workflow"
```

If the spec file was unchanged, omit it from `git add`.

- [ ] **Step 6: Prepare PR evidence, but do not merge automatically**

PR summary must include:

```text
- official CSV import stays network-free
- timer auto-enriches up to 20 Shopee products per pass
- READY Shopee rows enter the existing Threads scheduler
- exact imported affiliate URL is preserved
- Shopee freshness is 72h; other providers keep existing semantics
- NEEDS_HELPER never auto-publishes
- focused tests + ./manage.sh test + diff/compile checks passed
```

Do not merge to `main` until fresh verification evidence exists and the operator explicitly requests merge.
