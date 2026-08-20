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

- `python3 -m unittest tests.test_auto_scheduler` under system Python still fails in existing web validation/web UI tests because `flask` is not installed in this worktree environment; the changed non-web scheduler class passes.
- Full `test_pipeline` through a temporary `acp` package symlink reached and passed the changed publish sections, then stopped later on existing `google.genai` dependency absence under system Python.
- `./manage.sh test` was not used for this worktree because `manage.sh` resolves the active release symlink (`$ACP_BASE/acp`) and there is no worktree-local deployment layout/venv here; running it would not verify these uncommitted worktree changes.

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
