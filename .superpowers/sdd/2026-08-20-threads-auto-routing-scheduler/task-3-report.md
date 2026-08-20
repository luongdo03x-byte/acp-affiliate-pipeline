# Task 3 Report: fill rolling auto publish schedule

## Scope delivered

- Added `pipeline.fill_auto_schedule(conn, campaign_code, now_utc=None)` returning `scheduled`, `review`, `skipped`, and `cancelled` counts.
- Added rolling slot enumeration in `core.auto_scheduler.available_slots()` while preserving `route_product()` behavior.
- Added additive `publish_target.auto_scheduled INTEGER NOT NULL DEFAULT 0` schema/migration marker.
- Extended `approve_post(..., auto_scheduled=False)` so Auto-created targets are distinguishable from operator-approved targets.
- Added `python3 run.py auto-schedule` aggregate CLI command.
- Kept publish-worker separation intact: fill enqueues scheduled `PUBLISH_POST` jobs but never runs them or toggles `publish_worker_enabled`.

## Behavior implemented

- Auto ON Threads channels fill empty configured slots across the exact rolling 48-hour UTC horizon.
- Default `daily_post_target=2` fills the first two configured slots per local day.
- `daily_post_target=3` uses the optional third slot while respecting `daily_post_cap`; malformed persisted/caller values above 3 are clamped in core routing/fill logic.
- Existing live targets occupy their slots; fill does not create a second target for the same channel/slot.
- Slot occupancy is rechecked inside the fill transaction immediately before automated approval, so a concurrent reservation of the same channel/slot is skipped instead of creating a duplicate live target or leaking Auto ON content into review-only state.
- Candidate acquisition is per channel through legacy `scoring.score_candidates(..., niches=channel_niches(...))` plus an Auto-specific provider-aware catalog path for synced `ACCESSTRADE_TIKTOK` rows. The legacy scorer still keeps its provider exclusion; catalog candidates now reuse the active scorer hard filters with channel niches, so rating, review count, commission, blocked category, cooldown, active-post, inventory, link, and availability safeguards all still apply. Category/day caps are enforced after route selection against the selected slot's local day.
- `candidate_channels()` evaluates whether the channel has an eligible slot in the rolling horizon, so quota is checked against the selected slot's local day instead of only the current local day.
- `run.py auto-schedule` builds one context and injects the same mock catalog link client used by `product-sync` when running in mock mode, avoiding accidental live `AccessTradeClient` link creation during mock verification.
- Auto-created posts reuse the existing attribution, image composition, caption generation, validation, and approval paths.
- Tracking-link creation, image composition, and storage upload are prepared before the `BEGIN IMMEDIATE` write transaction. The transaction re-fetches product/channel rows and rechecks current Auto eligibility plus slot occupancy before inserting any `post`, `publish_target`, or job rows.
- After artifact preparation, the transaction uses the freshly re-fetched channel Auto state for eligibility, slot-collision checks, and approval. If Auto is disabled concurrently, the prepared post remains review-only and no publish target/job is created.
- The shared `pipeline.current_auto_product_eligibility(...)` recheck covers current channel status/Auto state, product availability, catalog inventory/link fields, `affiliate_link_status != UNAVAILABLE`, blocked category config, current channel niche match, category/day cap, and cooldown/active-post idempotency. Scorer hard filters are normalized to the `/kenh` channel niches before `_reasons()` so global scoring-config niches do not override channel checkbox routing.
- Auto ON posts are approved with `actor='auto_scheduler'`, scheduled at the selected slot, and create `auto_scheduled=1` publish targets/jobs.
- Auto OFF channels create review-only posts up to their review target and do not create publish targets or publish jobs.
- Re-running fill is idempotent for already queued/scheduled products and does not reuse products already in active post states.

## TDD record

### Red

Commands:

```bash
python3 -m unittest tests.test_auto_scheduler.AutoScheduleFillTests -v
test_parent=$(mktemp -d); ln -s "$(pwd)" "$test_parent/acp"; cd "$test_parent"; python3 - <<'PY'
from acp.tests import test_pipeline
conn = test_pipeline.setup(); conn.close()
test_pipeline.test_auto_schedule_cli_prints_only_aggregate_counts()
PY
```

Expected failures observed:

- `AttributeError: module 'acp.core.pipeline' has no attribute 'fill_auto_schedule'`
- `AttributeError: module 'acp.run' has no attribute 'cmd_auto_schedule'`

Follow-up review regression red run:

```bash
python3 -m unittest tests.test_auto_scheduler.AutoScheduleFillTests -v
```

Expected failures observed before the fix:

- exact 48-hour horizon test created only current/next local-date slots instead of including eligible third-date slots.
- transaction collision test allowed duplicate live `publish_target` rows for the same channel/slot.
- tightened collision test also exposed a leaked Auto ON `PENDING_REVIEW` post after a skipped collided slot.
- malformed target/cap test scheduled 5 slots per day instead of enforcing the core maximum of 3.

Second follow-up review regression red run:

```bash
python3 -m unittest tests.test_auto_scheduler.AutoScheduleFillTests -v
```

Expected failures observed before the fix:

- synced `ACCESSTRADE_TIKTOK` catalog products were not scheduled because Auto fill used only the legacy scorer, which intentionally excludes that provider.
- tracking-link creation, image composition, and storage upload ran while the write transaction was held.

Third follow-up review regression red run:

```bash
python3 -m unittest tests.test_auto_scheduler.AutoScheduleFillTests.test_fill_auto_schedule_rechecks_current_catalog_eligibility_inside_transaction -v
```

Expected failures observed before the fix:

- after artifact preparation, concurrent mutations to `affiliate_link_status`, blocked category config, channel niche config, or category/day occupancy still allowed a scheduled Auto post/target/job to be persisted.

Final review regression red run:

```bash
python3 -m unittest \
  tests.test_auto_scheduler.AutoSchedulerRoutingTests.test_route_product_keeps_channel_when_today_full_but_future_slot_open \
  tests.test_auto_scheduler.AutoScheduleFillTests.test_auto_catalog_candidates_and_preflight_apply_active_quality_filters \
  -v
python3 -m unittest tests.test_auto_scheduler.AutoSchedulerRoutingTests tests.test_auto_scheduler.AutoScheduleFillTests -v
```

Expected failures observed before the fix:

- synced catalog rows with `rating=0`, `review_count=0`, and `commission_value=0` were still returned by the Auto catalog candidate path, and preflight did not reject the same hard-quality failure.
- a channel full on the current local day was excluded even when it had a valid future slot inside the 48-hour horizon.
- after switching candidates to slot-horizon quota, two older current-day quota tests exposed stale expectations and were updated to assert all-slot-day fullness and selected-slot local-day routing.

Residual race regression red run:

```bash
python3 -m unittest tests.test_auto_scheduler.AutoScheduleFillTests.test_fill_auto_schedule_uses_fresh_channel_auto_state_after_artifact_prep -v
```

Expected failure observed before the fix:

- when Auto was disabled after artifact preparation, the write-transaction path still used stale channel state and skipped instead of applying the fresh Auto OFF review-only decision.

Final review v3 regression red runs:

```bash
python3 -m unittest \
  tests.test_auto_scheduler.AutoScheduleFillTests.test_fill_auto_schedule_skips_legacy_products_with_unknown_inventory \
  tests.test_auto_scheduler.AutoScheduleFillTests.test_fill_auto_schedule_category_cap_uses_selected_slot_local_day \
  -v
python3 -m acp.tests.test_product_automation cli
python3 - <<'PY'
from acp.tests import test_pipeline
conn = test_pipeline.setup(); conn.close()
test_pipeline.test_sibling_target_not_cancelled_after_first_target_publishes()
if test_pipeline.FAIL:
    raise SystemExit(1)
PY
```

Expected failures observed before the fix:

- legacy products with `has_inventory=NULL` were selected and scheduled instead of being excluded before Auto approval.
- category/day cap used the current local day and blocked a valid tomorrow slot.
- `run.py auto-schedule` did not pass a prepared context/product client into `fill_auto_schedule`.
- the sibling-target pipeline test could select polluted `PENDING_REVIEW` fixtures instead of the post it generated.

Final routing source-of-truth regression red run:

```bash
python3 -m unittest tests.test_auto_scheduler.AutoScheduleFillTests.test_auto_preflight_uses_channel_niches_not_global_scoring_niches -v
```

Expected failure observed before the fix:

- with global scoring niches set to `gia-dung` and channel niches set to `my-pham`, a matching `my-pham` Auto product was rejected as `product_quality_filter`.

### Green

Commands:

```bash
python3 -m unittest tests.test_auto_scheduler.AutoSchedulerRoutingTests tests.test_auto_scheduler.AutoScheduleFillTests -v
test_parent=$(mktemp -d); ln -s "$(pwd)" "$test_parent/acp"; cd "$test_parent"; python3 - <<'PY'
from acp.tests import test_pipeline
conn = test_pipeline.setup(); conn.close()
test_pipeline.test_auto_schedule_cli_prints_only_aggregate_counts()
if test_pipeline.FAIL:
    raise SystemExit(1)
PY
test_parent=$(mktemp -d); ln -s "$(pwd)" "$test_parent/acp"; cd "$test_parent"; ACP_ADAPTER=mock ACP_SOURCE=mock python3 -m acp.run auto-schedule
./manage.sh test
git diff --check
```

Results:

- focused routing/fill tests: 35 tests passed.
- focused CLI contract: passed.
- product automation CLI group: 14 tests passed.
- targeted sibling-target pipeline regression: passed.
- CLI smoke with mock through package symlink: `Auto schedule: scheduled=0, review=0, skipped=0, cancelled=0`.
- `./manage.sh test`: passed with `TEST_OK`.
- `git diff --check`: passed.

## Notes / concerns

- Raw `python3 -m unittest tests.test_auto_scheduler -v` still fails on the existing web tests because system Python lacks `flask`; the focused non-web scheduler/fill classes pass.
- Raw `python3 -m acp.tests.test_pipeline` through a temporary package symlink progressed through the changed pipeline/publish-target areas and later stopped on missing `google.genai`; `./manage.sh test` uses the managed environment and passed.
- Direct `python3 run.py auto-schedule` from this worktree path fails before this task's code runs because the worktree directory is not literally named `acp`; the package-symlink smoke command exercises the same command against this worktree. The release layout used by `manage.sh` does not have that issue.
- Because artifacts are now created before the DB transaction, a late stale-product or slot-collision recheck can safely leave an external link/media artifact orphan. No DB rows are inserted in that case; cleanup is limited to provider/storage retention policies because those external calls are not reversible through this code path.
