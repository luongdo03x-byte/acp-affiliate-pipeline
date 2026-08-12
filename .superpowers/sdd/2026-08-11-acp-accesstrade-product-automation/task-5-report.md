# Task 5 report — cron-safe catalog sync CLI

## Scope delivered

Modified only `run.py` and `tests/test_product_automation.py`.

- Added `python3 run.py product-sync [keyword] [--auto-prepare]` to CLI help and dispatch through a testable `main(argv)` entry point.
- Added a catalog sync wrapper that performs schema-only initialization, calls `ProductService.sync(title_keywords=...)`, and prints a redacted `Fetched / New / Updated / Skipped / Failed` summary.
- `ACP_PRODUCT_SYNC_ENABLED=false` exits successfully before database work.
- Busy, provider, and network failures return exit status 1; unexpected errors are redacted rather than exposing request/token details.
- Auto-prepare runs only when both the CLI flag and `ACP_AUTO_PREPARE_CONTENT=true` are present. It calls the Task 4 catalog-post entry point only; it does not approve, schedule, or publish posts.
- Explicit mock adapter/source mode uses the repository seed catalog through the same Product Search V2-shaped interface, avoiding live ACCESSTRADE HTTP during local verification.

## TDD evidence

1. RED: added the `cli` test group and ran:

   ```bash
   PYTHONPATH=/tmp /home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python /tmp/acp/tests/test_product_automation.py cli
   ```

   Observed the intended missing-feature failure:

   ```text
   AttributeError: module 'acp.run' has no attribute 'main'
   ```

2. GREEN: implemented `main`, command parsing, gated sync, and the summary. The focused group passed (initially 3 tests).

3. RED: added an offline mock-mode regression. Before the mock catalog boundary existed, the focused group failed because `run.main(["product-sync"])` returned 1 after trying the live client.

4. GREEN: added `_MockCatalogClient` selected only when both `ACP_ADAPTER=mock` and `ACP_SOURCE=mock`; the focused group passed (4 tests).

5. RED: added a fresh-database regression. Before schema-only initialization, the command returned 1 because `product_sync_lock` did not exist.

6. GREEN: added `db.init_db()` after the enabled gate and before the session; the final focused suite passed 6 tests.

## Final verification

Passed:

```bash
PYTHONPATH=/tmp /home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python /tmp/acp/tests/test_product_automation.py cli
PYTHONPATH=/tmp /home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python -m py_compile /tmp/acp/run.py
ACP_ADAPTER=mock ACP_SOURCE=mock PYTHONPATH=/tmp /home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python /tmp/acp/run.py product-sync
git diff --check
```

Focused suite result: `6 passed`.

Mock CLI result: `Fetched: 50 | New: 0 | Updated: 50 | Skipped: 0 | Failed: 0` (the existing local catalog had already been populated by the preceding mock verification). No credential or token value appeared in output.

## Files changed

- `run.py`
- `tests/test_product_automation.py`
- `.superpowers/sdd/2026-08-11-acp-accesstrade-product-automation/task-5-report.md`

## Review follow-up

Two important review findings were fixed in the same task scope.

1. Auto-prepare now constructs the Task 4 context once and replaces its
   `product_client` with the exact client used for catalog synchronization. In
   explicit mock mode, that is `_MockCatalogClient`, which now supplies a
   deterministic mock affiliate link. The mock path therefore cannot call the
   factory-created live ACCESSTRADE client for its link POST. The pipeline entry
   point remains the only content action and the regression asserts its
   `PENDING_REVIEW` result.
2. Each `{ok: false}` preparation is counted. A nonzero count prints only
   `Preparation failed: <count>` and exits 1, so cron retries the run without
   exposing the provider's error text.

### Follow-up TDD evidence

- RED: `test_mock_auto_prepare_uses_mock_client_and_keeps_post_pending_review`
  enabled `ACP_ADAPTER=mock`, `ACP_SOURCE=mock`, and both auto-prepare gates.
  It failed because the factory context still held the sentinel live client;
  invoking it took the generic failure path and returned 1.
- GREEN: injecting the sync client into the content context and implementing a
  mock-only `create_product_link` made that test pass with a mock link and a
  `PENDING_REVIEW` result; the sentinel live client recorded zero calls.
- RED: `test_auto_prepare_failure_is_redacted_and_returns_nonzero` expected an
  exit status of 1 and a redacted failure count for a Task 4 `{ok: false}`
  result. The old command returned 0.
- GREEN: counting failed preparations and returning 1 made the regression pass
  without printing `provider token=must-not-print`.

Final follow-up focused verification:

```bash
PYTHONPATH=/tmp /home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python /tmp/acp/tests/test_product_automation.py cli
```

Result: `8 passed`.
