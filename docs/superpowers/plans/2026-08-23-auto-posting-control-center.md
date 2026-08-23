# Auto Posting Control Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Dynamic Topics, background Enrich All, a 48-hour Auto Posting Control Center, and the approved navigation/favicon changes without adding a second scheduler or publishing live during verification.

**Architecture:** Add schema/runtime extensions in focused modules, compose new Flask blueprints through `web/__init__.py`, and preserve the current scheduler/publisher as the execution backbone. Existing static `core/niche.py` remains the safety source for system-topic rules; the new DB topic tree handles dynamic routing and UI inheritance.

**Tech Stack:** Python 3, Flask/Jinja, SQLite, existing `job_queue`, existing ACP scheduler/publisher.

**Spec:** `docs/superpowers/specs/2026-08-23-auto-posting-control-center-design.md`

## Global Constraints

- Keep existing scheduler/timer; do not create a second scheduler service.
- No real Threads publish in tests or verification.
- Keep `ACP_ADAPTER=mock` for tests.
- Preserve `/sanpham` and `/sanpham/shopee-bulk` routes; hide only their sidebar entries.
- Dynamic Topics must not replace `core/niche.py` safety rules.
- Auto-create topic threshold: cluster size >= 5 and confidence >= 0.80.
- Auto-merge topic similarity threshold: >= 0.92 and retain alias.
- Parent include inherits future descendants; explicit EXCLUDE wins.
- Auto plans cover the existing 48-hour scheduling horizon.

---

### Task 1: Schema + Dynamic Topic Engine

**Files:**
- Create: `core/control_center_schema.py`
- Create: `core/topic_engine.py`
- Create: `core/topic_jobs.py`
- Modify: `core/__init__.py`
- Test: `tests/test_dynamic_topics.py`

**Interfaces:**
- Produces `topic_engine.ensure_system_topics(conn)`, `topic_engine.topic_tree(conn)`, `topic_engine.channel_rules(conn, channel_id)`, `topic_engine.set_channel_rules(conn, channel_id, includes, excludes)`, `topic_engine.product_topic_codes(conn, product_id)`, `topic_engine.channel_accepts_product(conn, channel_id, product_id)`.
- Produces job `SHOPEE_DISCOVER_TOPICS` and `topic_jobs.queue_discovery(conn, product_ids)`.

- [ ] **Step 1: Write failing schema/topic tests**

Cover:
- System topics mirror `niche.NICHES` idempotently.
- Parent INCLUDE matches descendants created later.
- Child EXCLUDE beats parent INCLUDE.
- No INCLUDE means all topics.
- 4-product cluster does not auto-create; 5-product cluster at confidence >=0.80 does.
- Similarity >=0.92 reuses canonical topic and stores alias.

- [ ] **Step 2: Verify RED**

Run focused unit test and confirm failure because topic tables/API do not exist.

- [ ] **Step 3: Implement schema registration and topic engine**

Use `core.db` as central migration engine, like `core/shopee_schema.py`. Tables: `topic`, `topic_alias`, `product_topic`, `channel_topic_rule`.

- [ ] **Step 4: Implement background discovery job**

Deterministic/offline classifier:
- mirror matching system root using `niche.match_reasons`;
- tokenize normalized product titles;
- build candidate 1-3 token phrases after stopword removal;
- aggregate across Shopee products under the root;
- confidence = matched_cluster_count / products containing candidate root vocabulary, clamped 0..1;
- only create cluster >=5 and confidence >=0.80;
- `difflib.SequenceMatcher` + token similarity for canonical merge >=0.92;
- alias merged candidate names.

- [ ] **Step 5: Verify GREEN**

Run `tests/test_dynamic_topics.py`.

- [ ] **Step 6: Commit**

`feat: add dynamic topic engine`

---

### Task 2: Channel Topic Tree + Product Pool Topic Filter

**Files:**
- Create: `core/topic_runtime.py`
- Create: `web/topic_ui.py`
- Modify: `core/__init__.py`
- Modify: `web/__init__.py`
- Modify: `web/templates/channels.html`
- Modify: `core/shopee_product_pool.py`
- Modify: `web/templates/shopee_image_enrichment.html`
- Test: `tests/test_topic_routing_ui.py`

**Interfaces:**
- `topic_runtime.install()` patches `pipeline.set_channel_niches` for backward-compatible form handling while storing full include rules; legacy `channel.niches` retains system-root includes only.
- `web/topic_ui.py` injects `topic_tree` and per-channel include/exclude state into Jinja.
- Product Pool keeps query name `niche` for compatibility but accepts any topic code.

- [ ] **Step 1: Write failing routing/UI tests**

Assert Channel page renders nested dynamic topics and Product Pool can filter by dynamic topic code.

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Implement runtime compatibility layer**

`set_channel_niches` receives include values from existing form name `niches`, plus exclusions from `topic_excludes`; writes `channel_topic_rule`, mirrors system includes to `channel.niches`, audits sanitized codes.

- [ ] **Step 4: Implement recursive tree UI**

Add a Jinja partial or recursive macro. Checking a parent submits only parent INCLUDE; explicit unchecked descendant exclusion is represented by a dedicated exclude checkbox/action to avoid storing all descendants.

- [ ] **Step 5: Update Product Pool projection**

Attach `topic_codes`, `topic_paths`; compute topic stats from DB; filter any valid active topic code, while static-system fallback still works before discovery jobs run.

- [ ] **Step 6: Verify GREEN + regression**

Run topic UI tests plus existing Product Pool tests.

- [ ] **Step 7: Commit**

`feat: route channels with dynamic topics`

---

### Task 3: Background Enrich All Controller

**Files:**
- Create: `core/shopee_bulk_enrichment.py`
- Modify: `web/shopee_image_enrichment.py`
- Modify: `web/templates/shopee_image_enrichment.html`
- Test: `tests/test_shopee_bulk_enrichment.py`

**Interfaces:**
- `start(conn)`, `pause(conn)`, `resume(conn)`, `retry_failed(conn)`, `status(conn)`, `pump(conn, limit=20)`.
- Persistent state stored in `system_setting` keys prefixed `shopee_enrich_all.*`; no new scheduler.

- [ ] **Step 1: Write failing state-machine tests**

Cover start, pause, resume, retry failed, progress counts, idempotent start, and no queueing while paused.

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Implement controller using existing enrichment job table + `queue_pending_products`**

`pump()` queues at most 20 pending items each invocation. Existing worker/job mechanism performs network work.

- [ ] **Step 4: Add Product Pool POST routes and controls**

`/sanpham/shopee/enrichment/all/start|pause|resume|retry-failed` and progress block.

- [ ] **Step 5: Hook pump into safe existing web/worker passes**

Use job completion handler or an idempotent `SHOPEE_ENRICH_ALL_PUMP` queue job so each completed batch schedules the next pump; never keep HTTP request open.

- [ ] **Step 6: Verify GREEN + existing enrichment tests**

- [ ] **Step 7: Commit**

`feat: add background enrich all controls`

---

### Task 4: Auto Post Plan + Reconciliation Runtime

**Files:**
- Create: `core/auto_post_plans.py`
- Create: `core/auto_post_runtime.py`
- Modify: `core/control_center_schema.py`
- Modify: `core/__init__.py`
- Test: `tests/test_auto_post_plans.py`

**Interfaces:**
- `auto_post_plans.upsert_from_target(conn, post_id, target_id, reason='scheduled')`
- `list_window(conn, now_utc, hours=48)`
- `edit_caption`, `move_slot`, `replace_product`, `cancel_plan`, `reconcile_plan`.
- Runtime wraps `pipeline.approve_post` to capture `auto_scheduled=True` targets and wraps `pipeline.publish_post` preflight to reconcile before provider publish.

- [ ] **Step 1: Write failing lifecycle tests**

Cover plan creation from auto approve, unique channel/slot, caption edit revision, move slot updating target/job `run_after`, cancellation, published synchronization.

- [ ] **Step 2: Write failing replacement tests**

When product becomes stale/unavailable, `reconcile_plan` selects another eligible product for the same channel/topic and same slot, regenerates artifacts, increments replacement count, keeps target id/slot.

- [ ] **Step 3: Verify RED**

- [ ] **Step 4: Implement plan storage + lifecycle**

Update `post`, `publish_target`, and matching `PUBLISH_POST` job atomically for operator actions.

- [ ] **Step 5: Implement reconciliation**

Reuse `pipeline._candidate_products_for_channel`, `pipeline.current_auto_product_eligibility`, `pipeline._prepare_auto_sales_post_artifacts`; never call publisher during reconciliation.

- [ ] **Step 6: Install runtime wrappers after Shopee/caption runtimes**

No second scheduler. Scheduler-created auto targets become plans automatically.

- [ ] **Step 7: Verify GREEN + auto scheduler/publisher regressions**

- [ ] **Step 8: Commit**

`feat: add 48 hour auto post plans`

---

### Task 5: Auto Posting Control Center Web UI

**Files:**
- Create: `web/auto_posting.py`
- Create: `web/templates/auto_posting.html`
- Modify: `web/__init__.py`
- Test: `tests/test_auto_posting_web.py`

**Interfaces:**
- GET `/auto-posting`
- POST `/auto-posting/<plan_id>/caption`
- POST `/auto-posting/<plan_id>/time`
- POST `/auto-posting/<plan_id>/product`
- POST `/auto-posting/<plan_id>/cancel`

- [ ] **Step 1: Write failing route/render tests**

Assert 48h plans show channel, local scheduled time, product, topic, image, exact caption/link, state/revision and action forms.

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Implement read model + routes**

All writes require existing CSRF guard. Product replacement endpoint accepts only Product IDs currently eligible for that channel/slot.

- [ ] **Step 4: Implement responsive timeline/card template**

Group by channel; show state badges and `last_change_reason`.

- [ ] **Step 5: Verify GREEN**

- [ ] **Step 6: Commit**

`feat: add auto posting control center`

---

### Task 6: Navigation, Favicon, Import Trigger, Documentation + Verification

**Files:**
- Create: `web/static/favicon.svg`
- Modify: `web/templates/base.html`
- Modify: `web/shopee_csv_import.py`
- Modify: `web/__init__.py`
- Modify: `README.md`
- Test: relevant focused suites

**Interfaces:**
- CSV confirm queues both enrichment and topic discovery after successful DB import.

- [ ] **Step 1: Write failing nav/import tests**

Assert sidebar omits `Sản phẩm` and `Shopee Affiliate`, includes `Auto Posting`, includes favicon, and CSV confirm queues topic discovery without rolling back successful import if discovery trigger fails.

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Update base navigation + favicon**

Keep hidden routes intact.

- [ ] **Step 4: Add topic job trigger after CSV confirmation**

Follow existing enrichment trigger safety: import is already committed; discovery queue failure is logged and shown as non-destructive trigger status, never asks operator to repeat import.

- [ ] **Step 5: Update README operator behavior**

Document Auto Posting page, Dynamic Topics, and Enrich All controls.

- [ ] **Step 6: Run focused tests and compile check**

Expected commands in repository worktree:

```bash
python -m unittest \
  acp.tests.test_dynamic_topics \
  acp.tests.test_topic_routing_ui \
  acp.tests.test_shopee_bulk_enrichment \
  acp.tests.test_auto_post_plans \
  acp.tests.test_auto_posting_web -v
python -m unittest acp.tests.test_shopee_product_pool_v2 acp.tests.test_shopee_auto_pipeline acp.tests.test_auto_scheduler -v
python -m compileall acp/core acp/web acp/tests
```

- [ ] **Step 7: Run release verification if deployment layout is available**

`./manage.sh test` with mock adapter/source. If unavailable, report that limitation explicitly.

- [ ] **Step 8: Review diff + request code review**

Inspect scope, no secret files, no live publish path added, no second timer/scheduler.

- [ ] **Step 9: Commit final docs/fixes and open Draft PR**

`docs: document auto posting control center`

## Self-review

- Spec coverage: all approved choices map to Tasks 1-6.
- No second scheduler: all background work uses `job_queue`/existing worker.
- TDD: every behavior task starts with failing tests.
- Backward compatibility: legacy route/sidebar behavior is changed only visually; static safety topics stay authoritative; `channel.niches` remains compatible.
- Verification claims must cite fresh command output; GitHub Actions pre-step failures are not test evidence.
