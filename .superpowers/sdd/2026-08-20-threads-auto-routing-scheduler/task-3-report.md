# Task 3 Report: fill rolling auto publish schedule

## Scope delivered

- Added `pipeline.fill_auto_schedule(conn, campaign_code, now_utc=None)` returning `scheduled`, `review`, `skipped`, and `cancelled` counts.
- Added rolling slot enumeration in `core.auto_scheduler.available_slots()` while preserving `route_product()` behavior.
- Added additive `publish_target.auto_scheduled INTEGER NOT NULL DEFAULT 0` schema/migration marker.
- Extended `approve_post(..., auto_scheduled=False)` so Auto-created targets are distinguishable from operator-approved targets.
- Added `python3 run.py auto-schedule` aggregate CLI command.
- Kept publish-worker separation intact: fill enqueues scheduled `PUBLISH_POST` jobs but never runs them or toggles `publish_worker_enabled`.

## Behavior implemented

- Auto ON Threads channels fill empty configured slots across the next two local dates.
- Default `daily_post_target=2` fills the first two configured slots per local day.
- `daily_post_target=3` uses the optional third slot while respecting `daily_post_cap`.
- Existing live targets occupy their slots; fill does not create a second target for the same channel/slot.
- Candidate acquisition is per channel through `scoring.score_candidates(..., niches=channel_niches(...))`.
- Auto-created posts reuse the existing attribution, image composition, caption generation, validation, and approval paths.
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

- focused routing/fill tests: 12 tests passed.
- focused CLI contract: passed.
- CLI smoke with mock through package symlink: `Auto schedule: scheduled=0, review=0, skipped=0, cancelled=0`.
- `./manage.sh test`: passed with `TEST_OK`.
- `git diff --check`: passed.

## Notes / concerns

- Raw `python3 -m unittest tests.test_auto_scheduler -v` still fails on the existing web tests because system Python lacks `flask`; the focused non-web scheduler/fill classes pass.
- Raw `python3 -m acp.tests.test_pipeline` through a temporary package symlink progressed through the changed pipeline/publish-target areas and later stopped on missing `google.genai`; `./manage.sh test` uses the managed environment and passed.
- Direct `python3 run.py auto-schedule` from this worktree path fails before this task's code runs because the worktree directory is not literally named `acp`; the package-symlink smoke command exercises the same command against this worktree. The release layout used by `manage.sh` does not have that issue.
