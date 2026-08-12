# Worker Toggle and Batch Catalog Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent system-wide publish-worker switch, a minute-based worker service, and safe bulk affiliate-link/post creation for ACCESSTRADE catalog products.

**Architecture:** Store a single fail-safe worker setting in the existing SQLite database. The external worker command will call the existing queue runner, which skips `PUBLISH_POST` jobs while disabled; the Flask operations page controls and displays the setting. Bulk catalog actions will live in a service layer and reuse the existing per-product link, image, caption, and post pipeline so attribution and review gates remain identical to single-item actions.

**Tech Stack:** Python 3, Flask, SQLite, existing `core.jobs` queue, existing ACCESSTRADE V2 client, Pillow, shell/systemd user service, focused standalone test groups in `tests/test_product_automation.py`.

## Global Constraints

- Worker default is disabled and disabling is fail-safe: no publish outside operator intent.
- Worker toggle is persisted in SQLite and protected by CSRF plus audit logging.
- Bulk default limit is 10 products per request.
- Bulk post creation always creates a fresh post-specific link and stops at `PENDING_REVIEW` or `DRAFT`.
- Product-only links use `sub_1=product:<external_product_id>` and are never reused for posts.
- Server revalidates provider, stock, detail link, image, active-post state, duplicate IDs, and batch limit.
- Provider errors and secrets must not be shown in redirects, HTML, database error fields, or logs.
- Do not modify the unrelated user-owned `core/content.py` change.

---

### Task 1: Add persistent worker setting and queue gate

**Files:**
- Modify: `core/db.py` (schema/migration declarations)
- Modify: `core/settings.py` or the existing small settings/config module; if no suitable module exists, create `core/system_settings.py`
- Modify: `core/jobs.py`
- Test: `tests/test_product_automation.py`

**Interfaces:**
- Produce `get_system_setting(conn, key, default=None) -> str | None` and `set_system_setting(conn, key, value, actor="operator") -> None`.
- Produce `publish_worker_enabled(conn) -> bool` with default `False`.
- Extend `jobs.run_once(conn, limit=10, ctx=None)` to leave `PUBLISH_POST` jobs `READY` when the setting is disabled while continuing non-publish jobs.

- [ ] **Step 1: Write failing migration and queue-gate tests.** Add tests for idempotent settings schema, default disabled, setting persistence, audit entry, disabled publish preservation, and enabled publish execution.
- [ ] **Step 2: Run the focused tests and confirm they fail for missing table/functions and ungated publish handling.**
- [ ] **Step 3: Add an idempotent `system_setting` table with `key`, `value`, `updated_at`, and a unique key constraint; implement the settings helpers and audit write.**
- [ ] **Step 4: Change queue claiming/processing so a disabled worker does not claim publish jobs, or safely returns claimed publish jobs to `READY` without incrementing attempts; keep other job types runnable.**
- [ ] **Step 5: Run the focused tests and then the client/service/pipeline groups.**
- [ ] **Step 6: Commit with `feat: add fail-safe publish worker setting`.**

### Task 2: Add worker CLI command and persistent user service

**Files:**
- Modify: `run.py`
- Modify: `manage.sh` only if a worker status/start helper belongs there
- Create: `ops/acp-worker.service` or document a generated user unit without committing secrets
- Modify: `README.md`
- Modify: `docs/ACP_RUNBOOK.md`
- Test: `tests/test_product_automation.py`

**Interfaces:**
- Add `python run.py worker-once` to run one queue pass using the active context.
- Add `python run.py worker-status` to print enabled/disabled state and safe queue counts.

- [ ] **Step 1: Write failing CLI tests for worker-once respecting disabled/enabled state and safe status output.**
- [ ] **Step 2: Run those tests and verify the commands do not exist or do not honor the setting.**
- [ ] **Step 3: Implement the commands using `jobs.run_once`, `factory.build_context()`, and the existing database connection; return nonzero only for operational failure, never provider secrets.**
- [ ] **Step 4: Add a user-level systemd service/timer example that sources the active release `.env.local`, runs once per minute, restarts on failure, and does not enable publishing by itself.**
- [ ] **Step 5: Run CLI tests, `py_compile`, and verify the service file contains no token or secret.**
- [ ] **Step 6: Commit with `feat: add scheduled publish worker command`.**

### Task 3: Add worker toggle controls to Operations UI

**Files:**
- Modify: `web/server.py`
- Modify: `web/templates/ops.html`
- Modify: `web/static/acp.css` only for the toggle/status presentation
- Test: `tests/test_product_automation.py`

**Interfaces:**
- Add POST route `/vanhanh/worker-toggle` with CSRF, form field `enabled`, and redirect summary.
- Pass `worker_enabled`, `worker_updated_at`, and safe queue counts to `ops.html`.

- [ ] **Step 1: Write failing web tests for GET display, CSRF rejection, enable/disable persistence, audit logging, and safe confirmation copy.**
- [ ] **Step 2: Run the web tests and confirm the route/context/control are absent.**
- [ ] **Step 3: Implement the route using `set_system_setting`, accept only `0`/`1`, and audit the action without logging credentials.**
- [ ] **Step 4: Add a clearly labeled global switch with current state and warning that it controls scheduled publishing only.**
- [ ] **Step 5: Run the web group and inspect rendered HTML for secret leakage.**
- [ ] **Step 6: Commit with `feat: add global worker toggle UI`.**

### Task 4: Implement bounded bulk catalog service

**Files:**
- Modify: `core/products.py` or create `core/product_batch.py` if service responsibilities become too large
- Modify: `core/pipeline.py` to expose a reusable single-item standalone-link operation if needed
- Test: `tests/test_product_automation.py`

**Interfaces:**
- Produce `ProductBatchResult(successes, skipped, failures)` with per-product safe messages.
- Produce `ProductService.create_product_links(product_ids, max_items=10) -> ProductBatchResult`.
- Produce `ProductService.create_posts(product_ids, ctx, campaign_code, channel_code=None, max_items=10) -> ProductBatchResult`.

- [ ] **Step 1: Write failing service tests for limit, duplicate IDs, wrong provider, stockout, missing detail/image, active post, per-item failure isolation, product marker links, post marker links, and no publish jobs.**
- [ ] **Step 2: Run the service tests and verify the batch methods are missing.**
- [ ] **Step 3: Implement server-side selection and deduplication; preserve input order; cap at 10; re-read each product from the database.**
- [ ] **Step 4: Implement bulk link creation with `post_id="product:<external_product_id>"`, safe status/error persistence, and one provider call per eligible product.**
- [ ] **Step 5: Implement bulk post creation by calling the existing catalog post pipeline, including image materialization and fresh post-specific links; continue after an individual failure.**
- [ ] **Step 6: Run client, service, pipeline, and E2E groups.**
- [ ] **Step 7: Commit with `feat: add bounded catalog batch service`.**

### Task 5: Add catalog selection and bulk-action UI/routes

**Files:**
- Modify: `web/server.py`
- Modify: `web/templates/products.html`
- Modify: `web/static/acp.css`
- Test: `tests/test_product_automation.py`

**Interfaces:**
- Add POST `/sanpham/batch/affiliate-link` and `/sanpham/batch/tao-bai`, both CSRF-protected and accepting repeated `product_id` fields plus optional `q`/filter context.
- Render a safe result summary with success/skipped/failure counts.

- [ ] **Step 1: Write failing web tests for checkbox rendering, select-current-page behavior, CSRF, empty/over-limit requests, summary rendering, and no provider error leakage.**
- [ ] **Step 2: Run web tests and confirm controls/routes are absent.**
- [ ] **Step 3: Add checkbox selection and client-side select-all-current-page behavior without trusting it for authorization.**
- [ ] **Step 4: Add separate bulk-action forms/buttons and server routes that call the batch service and preserve filter context.**
- [ ] **Step 5: Add result summary styling and explicit copy that bulk post creation stops at review.**
- [ ] **Step 6: Run the web group and manually inspect one rendered catalog page locally.**
- [ ] **Step 7: Commit with `feat: add catalog bulk actions`.**

### Task 6: Install and verify runtime worker, then run release validation

**Files:**
- Modify: `docs/ACP_RUNBOOK.md`
- Modify: `README.md`
- Test: all existing release test groups and runtime smoke checks

- [ ] **Step 1: Run all product automation groups: `docs migration client service pipeline e2e cli web`.**
- [ ] **Step 2: Run `python -m py_compile` for changed Python files and `git diff --check`.**
- [ ] **Step 3: Install/update the user worker service for the active release without copying secrets into the unit file; keep the persistent toggle disabled.**
- [ ] **Step 4: Verify worker status, queue behavior with toggle off, then toggle on only if the operator explicitly requests a live publish test.**
- [ ] **Step 5: Run `./manage.sh test` and verify ACP/worker service status plus local HTTP health.**
- [ ] **Step 6: Commit documentation/runtime changes with `docs: document publish worker and batch operations`.**
