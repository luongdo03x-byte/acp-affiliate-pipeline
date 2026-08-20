# Threads Auto Routing Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tự động chọn account Threads theo danh mục Kênh, lấp lịch cuốn chiếu 48 giờ với quota 2–3 bài/ngày, và kiểm tra freshness trước khi publish.

**Architecture:** Giữ adapter/publisher và pipeline nội dung hiện hữu; thêm một module scheduler thuần để chọn account/slot và một lớp service mỏng để tạo bài, auto-approve hoặc giữ review. Cấu hình Auto, quota, timezone và slot nằm additive trong `channel`; worker hiện hữu là chốt publish cuối cùng.

**Tech Stack:** Python 3, Flask, SQLite, existing `core.pipeline`, `core.jobs`, `core.scoring`, `core.niche`, unittest-style tests, systemd/CLI timer.

**Spec:** `docs/superpowers/specs/2026-08-20-threads-auto-routing-scheduler-design.md`

## Global Constraints

- Chỉ routing account Threads ACTIVE/enabled có danh mục đã tick; `niches=[]` không được auto-route.
- Auto mặc định tắt; không bật `ACP_ADAPTER=live`, không publish Threads thật trong test.
- Horizon lịch là 48 giờ; target mặc định 2, cap tối đa 3 bài/account/ngày.
- Giữ nguyên attribution (`sub1`, campaign IDs, tracking URLs), idempotency và content safety hiện hữu.
- Không sửa/xóa `.env.local`, live SQLite, WAL/SHM, token hoặc khóa bí mật.
- Mọi test phải chạy với `ACP_ADAPTER=mock`, `ACP_SOURCE=mock`; release verification dùng `./manage.sh test`.

### Task 1: Add channel automation configuration migration and settings API

**Files:**
- Modify: `core/db.py` schema/migrations for `channel`
- Modify: `core/system_settings.py` only if a shared helper is needed
- Modify: `web/server.py` channel POST handling and context
- Modify: `web/templates/channels.html` automation controls
- Test: `tests/test_pipeline.py` or a focused new `tests/test_auto_scheduler.py`

**Interfaces:**
- Produces `channel.auto_schedule_enabled`, `daily_post_target`, `posting_timezone`, and `posting_slots` values on channel rows.
- Produces a validation helper such as `validate_channel_automation_config(payload) -> dict` returning normalized values or safe errors.

- [ ] **Step 1: Write failing migration/config tests** covering additive columns, legacy channel preservation, default Auto off, target/cap bounds, invalid timezone, duplicate/invalid `HH:MM` slots, and audit persistence.
- [ ] **Step 2: Run the focused tests and verify they fail because the columns/validation do not exist.**
- [ ] **Step 3: Implement idempotent schema migration and server-side normalization; preserve existing `daily_post_cap` values on upgrade and only use 3 for newly created/default channels.
- [ ] **Step 4: Add the `/kenh` form fields and CSRF-protected save path; render only for Threads and show that global publish worker remains a separate switch.
- [ ] **Step 5: Run focused migration/web tests and verify pass; inspect SQL for no secret/data output.
- [ ] **Step 6: Commit `feat: add per-channel auto scheduling settings`.

### Task 2: Implement deterministic account routing and slot ranking

**Files:**
- Create: `core/auto_scheduler.py`
- Test: `tests/test_auto_scheduler.py`

**Interfaces:**
- `route_product(conn, product, now_utc) -> dict | None` returns selected channel, reason, and slot or a skip reason.
- `candidate_channels(conn, product) -> list` filters Threads ACTIVE/enabled channels by `niche.match_reasons` and Auto state.
- `rank_slots(conn, channel_id, local_date, slots) -> list` uses same-account/hour history and configured-slot fallback.

- [ ] **Step 1: Write failing tests for exact niche match, no fallback to `niches=[]`, inactive/disabled/full-quota exclusion, duplicate-product exclusion, deterministic tie-break, and same-account/hour metric fallback.
- [ ] **Step 2: Run `python3 -m unittest tests.test_auto_scheduler -v` and verify expected failures.
- [ ] **Step 3: Implement pure helpers using existing `core.niche`, `core.scoring`, `publish_target`, and product/post status queries; keep provider calls out of the module.
- [ ] **Step 4: Add slot/timezone parsing using stdlib `zoneinfo`; use configured order when historical sample size is insufficient.
- [ ] **Step 5: Re-run focused tests and refactor only after green.
- [ ] **Step 6: Commit `feat: route products to matching threads accounts`.

### Task 3: Fill the rolling 48-hour schedule and integrate Auto mode

**Files:**
- Modify: `core/pipeline.py` or create a small service module that calls existing content-generation functions
- Modify: `core/jobs.py` only where Auto metadata/preflight must be carried safely
- Modify: `run.py` add `auto-schedule`
- Test: `tests/test_auto_scheduler.py`, `tests/test_pipeline.py`

**Interfaces:**
- `fill_auto_schedule(conn, campaign_code, now_utc=None) -> dict` returns aggregate `scheduled`, `review`, `skipped`, `cancelled` counts.
- Auto-generated posts record enough metadata to distinguish automated targets from manually approved targets without changing attribution.

- [ ] **Step 1: Write failing tests for 48-hour filling, two-target default, optional third target, one target per slot, no duplicate product, no over-quota, Auto ON auto-approval/job creation, Auto OFF review-only behavior, and aggregate CLI output.
- [ ] **Step 2: Run focused tests and verify failures are due to missing fill/API behavior.
- [ ] **Step 3: Implement candidate acquisition through `scoring.score_candidates(..., niches=channel_niches)` and call the existing post/link/image/caption pipeline; make insertion/idempotency transaction-safe.
- [ ] **Step 4: Implement Auto ON schedule creation with `approve_post(..., scheduled_at=slot)` and `actor='auto_scheduler'`; keep Auto OFF at `PENDING_REVIEW`/`DRAFT` with no publish target.
- [ ] **Step 5: Add `python3 run.py auto-schedule` with sanitized aggregate output and no live adapter changes.
- [ ] **Step 6: Run focused tests and CLI smoke test with mock; commit `feat: fill rolling auto publish schedule`.

### Task 4: Add freshness preflight before automated publish

**Files:**
- Modify: `core/pipeline.py` publish handler or extract preflight to `core/auto_scheduler.py`
- Modify: `core/jobs.py` only for safe stale-target handling
- Test: `tests/test_auto_scheduler.py`, `tests/test_pipeline.py`

**Interfaces:**
- `preflight_auto_target(conn, target, post, channel, now_utc=None) -> tuple[bool, str]` returns pass/fail and sanitized reason.

- [ ] **Step 1: Write failing tests for unavailable product, stale sync, invalid affiliate URL, changed hard-filter result, already-published target, cancellation without publisher call, and manual-target compatibility.
- [ ] **Step 2: Run focused tests and verify the stale cases fail before implementation.
- [ ] **Step 3: Implement preflight before `publisher.publish`; mark Auto target `CANCELLED`, return post to review, audit `auto_stale_cancelled`, and never enqueue replacement from inside publish handler.
- [ ] **Step 4: Verify retry/idempotency behavior for valid targets and existing rate-limit/auth/content-violation branches.
- [ ] **Step 5: Run focused pipeline tests and commit `feat: validate automated posts before publish`.

### Task 5: Operations UI, timer documentation, and release verification

**Files:**
- Modify: `web/server.py` and `web/templates/ops.html` for rolling schedule summary
- Modify: `README.md` and `docs/ACP_RUNBOOK.md` for timer order and safety gates
- Modify: `ops/` timer examples if required
- Test: `tests/test_product_automation.py` or focused web tests; `tests/test_manage.py` only if `manage.sh` changes

**Interfaces:**
- Ops summary exposes only aggregate counts, upcoming slots, and sanitized stale reasons.
- Timer order is catalog sync -> `auto-schedule` -> `worker-once`; neither timer enables global publishing.

- [ ] **Step 1: Write failing web/documentation contract tests for Auto controls, upcoming 48-hour summary, CLI command/help, and explicit global worker separation.
- [ ] **Step 2: Implement the minimal routes/templates/docs and add timer invocation without embedding secrets.
- [ ] **Step 3: Run focused web tests and inspect rendered labels for clear Auto ON/OFF semantics.
- [ ] **Step 4: Run `./manage.sh test` from the release layout with mock adapters; run `git diff --check`, `git status`, and review all changed files.
- [ ] **Step 5: Commit `docs: document rolling auto scheduling operations`.

## Final verification checklist

- [ ] `python3 -m unittest tests.test_auto_scheduler -v` passes.
- [ ] Relevant existing pipeline/product/web tests pass.
- [ ] `./manage.sh test` passes with `ACP_ADAPTER=mock` and `ACP_SOURCE=mock`.
- [ ] No command enabled live publishing or touched production credentials/data.
- [ ] `git diff --check`, `git status`, and branch history reviewed before merge.
