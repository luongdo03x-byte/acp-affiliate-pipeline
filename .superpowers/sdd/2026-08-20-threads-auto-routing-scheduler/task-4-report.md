# Task 4 Report: automated publish freshness preflight

## Scope delivered

- Added `core.auto_scheduler.preflight_auto_target(conn, target, post, channel, now_utc=None) -> tuple[bool, str]`.
- Added automated-target-only preflight inside `pipeline.publish_post()` before the publisher lookup/call.
- Added safe stale Auto cancellation:
  - `publish_target.status = 'CANCELLED'`
  - `publish_target.last_error = <sanitized reason code>`
  - `post.status = 'PENDING_REVIEW'`
  - `post.reject_reason = <sanitized reason code>`
  - audit action `auto_stale_cancelled`, actor `auto_scheduler`
- Manual targets keep current behavior and do not run freshness preflight.
- The publish handler does not enqueue replacement publish jobs when cancelling a stale Auto target.

## Behavior implemented

- Preflight rejects already-published targets for idempotency with `target_already_published`.
- Preflight rejects missing/unavailable products with `product_missing` / `product_unavailable`.
- Preflight rejects empty or unknown inventory unless `product.has_inventory=1` with `product_inventory_empty`.
- Preflight rejects stale product sync when `last_synced_at` or `last_seen_at` is missing/older than 120 minutes with `product_sync_stale`; exactly 120 minutes old is still accepted.
- Preflight rejects malformed post affiliate URLs and stale/bad product affiliate link status with `affiliate_link_invalid`.
- Preflight rechecks channel hard filters from current `channel.niches`; products that no longer match return `product_no_longer_matches_channel`.
- All persisted/audited reasons are short codes only; raw affiliate URLs/provider strings are not persisted by this path.

## TDD record

### Red

Commands:

```bash
python3 -m unittest tests.test_auto_scheduler.AutoSchedulerRoutingTests
python3 - <<'PY'
import importlib.util, os, sys
repo = os.getcwd()
spec = importlib.util.spec_from_file_location('acp', os.path.join(repo, '__init__.py'), submodule_search_locations=[repo])
module = importlib.util.module_from_spec(spec)
sys.modules['acp'] = module
spec.loader.exec_module(module)
from acp.tests import test_pipeline as t
conn = t.setup(); conn.close()
t.test_publish_post_cancels_stale_auto_target_without_publisher_or_replacement_job()
t.test_publish_post_keeps_manual_target_behavior_without_freshness_preflight()
print('PASS', len(t.PASS), 'FAIL', len(t.FAIL))
if t.FAIL:
    raise SystemExit(1)
PY
```

Expected failures observed:

- `preflight_auto_target(conn, target, post, channel, now_utc=None) is missing`
- stale Auto target still called publisher, became `SUCCESS`, left post `PUBLISHED`, and emitted no `auto_stale_cancelled` audit.
- manual target compatibility already passed before implementation.

### Green

Commands:

```bash
python3 -m unittest tests.test_auto_scheduler.AutoSchedulerRoutingTests
python3 - <<'PY'
import importlib.util, os, sys
repo = os.getcwd()
spec = importlib.util.spec_from_file_location('acp', os.path.join(repo, '__init__.py'), submodule_search_locations=[repo])
module = importlib.util.module_from_spec(spec)
sys.modules['acp'] = module
spec.loader.exec_module(module)
from acp.tests import test_pipeline as t
conn = t.setup(); conn.close()
for fn in (
    t.test_idempotency_and_double_post,
    t.test_publish_target_failure_semantics,
    t.test_publish_post_authorror_marks_channel,
    t.test_publish_target_cancelled_on_stale_post_status,
    t.test_retry_publish_target,
    t.test_publish_post_cancels_stale_auto_target_without_publisher_or_replacement_job,
    t.test_publish_post_keeps_manual_target_behavior_without_freshness_preflight,
):
    fn()
print('PASS', len(t.PASS), 'FAIL', len(t.FAIL))
if t.FAIL:
    print('FAILED:', t.FAIL)
    raise SystemExit(1)
PY
python3 -m py_compile core/auto_scheduler.py core/pipeline.py tests/test_auto_scheduler.py tests/test_pipeline.py
git diff --check
```

Results:

- `AutoSchedulerRoutingTests`: 16 tests passed.
- Focused publish/idempotency/rate-limit/auth/content-violation/Auto-stale/manual compatibility script: `PASS 44 FAIL 0`.
- `py_compile`: passed.
- `git diff --check`: passed.

## Notes / concerns

- Current follow-up ran `./manage.sh test` from this worktree with the mock adapter and it returned `TEST_OK`.

## Review follow-up

Review findings fixed after commit `d2c1a00`:

- Changed automated catalog sync freshness from 48 hours to exactly 120 minutes.
- Added boundary regressions: exactly 120 minutes old passes; 121 minutes old fails with `product_sync_stale`.
- Changed inventory preflight to require `has_inventory == 1`; `0` and `NULL` both fail with `product_inventory_empty`.

### Follow-up red

Command:

```bash
python3 -m unittest tests.test_auto_scheduler.AutoSchedulerRoutingTests
```

Expected failures observed before the fix:

- `test_preflight_auto_target_rejects_product_synced_after_120_minutes`: got `(True, 'ok')`, expected `(False, 'product_sync_stale')`.
- `test_preflight_auto_target_rejects_unknown_inventory`: got `(True, 'ok')`, expected `(False, 'product_inventory_empty')`.

### Follow-up green

Commands:

```bash
python3 -m unittest tests.test_auto_scheduler.AutoSchedulerRoutingTests
python3 - <<'PY'
import importlib.util, os, sys
repo = os.getcwd()
spec = importlib.util.spec_from_file_location('acp', os.path.join(repo, '__init__.py'), submodule_search_locations=[repo])
module = importlib.util.module_from_spec(spec)
sys.modules['acp'] = module
spec.loader.exec_module(module)
from acp.tests import test_pipeline as t
conn = t.setup(); conn.close()
for fn in (
    t.test_idempotency_and_double_post,
    t.test_publish_target_failure_semantics,
    t.test_publish_post_authorror_marks_channel,
    t.test_publish_target_cancelled_on_stale_post_status,
    t.test_retry_publish_target,
    t.test_publish_post_cancels_stale_auto_target_without_publisher_or_replacement_job,
    t.test_publish_post_keeps_manual_target_behavior_without_freshness_preflight,
):
    fn()
print('PASS', len(t.PASS), 'FAIL', len(t.FAIL))
if t.FAIL:
    print('FAILED:', t.FAIL)
    raise SystemExit(1)
PY
python3 -m py_compile core/auto_scheduler.py tests/test_auto_scheduler.py
git diff --check
```

Results:

- `AutoSchedulerRoutingTests`: 19 tests passed.
- Focused publish/idempotency/rate-limit/auth/content-violation/Auto-stale/manual compatibility script: `PASS 44 FAIL 0`.
- `py_compile`: passed.
- `git diff --check`: passed.

## Whole-branch review follow-up

Review findings fixed after Task 3 follow-up commit `e866ea7`:

- `publish_post()` now calls `preflight_auto_target()` with the shared `current_auto_product_eligibility()` checker before publisher lookup/call for automated targets only.
- `preflight_auto_target()` keeps the freshness/link/idempotency checks, then delegates mutable hard-filter/product quality decisions to the shared checker instead of duplicating the routing rules.
- The shared checker can exclude the target's own post from cooldown and category/day saturation checks, so a scheduled target does not reject itself while still catching competing active/recent posts.
- `_published_today()` now counts `publish_target.SUCCESS` rows by the channel `posting_timezone` local date; the publish handler passes a single `now_utc` and the channel timezone into the quota check.

### Whole-branch follow-up red

Command:

```bash
python3 - <<'PY'
import importlib.util, os, sys
repo = os.getcwd()
spec = importlib.util.spec_from_file_location('acp', os.path.join(repo, '__init__.py'), submodule_search_locations=[repo])
module = importlib.util.module_from_spec(spec)
sys.modules['acp'] = module
spec.loader.exec_module(module)
from acp.tests import test_pipeline as t
conn = t.setup(); conn.close()
for name in [
    'test_published_today_counts_channel_local_date_at_midnight_boundary',
    'test_publish_post_cancels_auto_target_when_product_drops_below_quality_threshold',
    'test_publish_post_cancels_auto_target_when_product_category_becomes_blocked',
]:
    getattr(t, name)()
print('PASS', len(t.PASS), 'FAIL', len(t.FAIL))
if t.FAIL:
    raise SystemExit(1)
PY
```

Expected failures observed before the fix:

- `_published_today()` did not accept `now_utc` / `posting_timezone`, proving the quota path was still UTC-date based.
- After wiring the API, stale automated targets cancelled before publisher, but the fixture initially exposed `channel_auto_disabled`; after enabling Auto on the fixture, the low-rating and blocked-category regressions exercised the intended hard-filter branches.

### Whole-branch follow-up green

Commands:

```bash
python3 -m unittest tests.test_auto_scheduler.AutoSchedulerRoutingTests
python3 - <<'PY'
import importlib.util, os, sys
repo = os.getcwd()
spec = importlib.util.spec_from_file_location('acp', os.path.join(repo, '__init__.py'), submodule_search_locations=[repo])
module = importlib.util.module_from_spec(spec)
sys.modules['acp'] = module
spec.loader.exec_module(module)
from acp.tests import test_pipeline as t
conn = t.setup(); conn.close()
for name in [
    'test_idempotency_and_double_post',
    'test_publish_target_failure_semantics',
    'test_publish_post_authorror_marks_channel',
    'test_publish_target_cancelled_on_stale_post_status',
    'test_retry_publish_target',
    'test_publish_post_cancels_stale_auto_target_without_publisher_or_replacement_job',
    'test_publish_post_cancels_auto_target_when_product_drops_below_quality_threshold',
    'test_publish_post_cancels_auto_target_when_product_category_becomes_blocked',
    'test_publish_post_keeps_manual_target_behavior_without_freshness_preflight',
    'test_next_slot_and_daily_cap_scoped_per_channel_via_publish_target',
    'test_published_today_counts_channel_local_date_at_midnight_boundary',
]:
    getattr(t, name)()
print('PASS', len(t.PASS), 'FAIL', len(t.FAIL))
if t.FAIL:
    raise SystemExit(1)
PY
python3 -m py_compile core/auto_scheduler.py core/pipeline.py tests/test_auto_scheduler.py tests/test_pipeline.py
./manage.sh test
```

Results:

- `AutoSchedulerRoutingTests`: 19 tests passed.
- Focused publish/idempotency/rate-limit/auth/content-violation/Auto-stale/manual/quota script: `PASS 57 FAIL 0`.
- `py_compile`: passed.
- `./manage.sh test`: passed with `TEST_OK`.
