# Final fix report — ACCESSTRADE product automation

Date: 2026-08-12

Workspace: `/home/dluowng/Downloads/ACP/worktrees/accesstrade-product-automation`

Scope: final-review findings for `f9e44cc..0e306d3`; no production data, secrets, or `core/content.py` were touched.

## Delivered fixes

- Credential-safe catalog logging: sync, standalone-link, catalog-query, and post-link failures now log an allowlist only (`operation`, exception class, optional product ID/status). No exception message, traceback, Authorization header, token, URL, or response body is logged. Stored link failures likewise retain only the exception class.
- TikTok source compatibility: `campaign_id` is forwarded on legacy search and link creation, `AT_TIKTOK_LINK_PATH` is read at link-call time, and calls still delegate through `AccessTradeClient`. New catalog calls keep the V2 feed/create-link defaults.
- Catalog isolation: `ACCESSTRADE_TIKTOK` rows are excluded from legacy scoring/planning, with a defensive legacy `GENERATE_CONTENT` guard. Catalog selection continues to require inventory and uses a fresh per-post link.
- Auto-prepare deduplication: recommendations exclude catalog products with active SALES posts in `DRAFT`, `PENDING_REVIEW`, `APPROVED`, or `SCHEDULED`.
- UNAVAILABLE recovery: an eligible/in-stock sync row transitions `UNAVAILABLE -> NOT_CREATED` and clears the stale availability error. Other link states/errors, including `FAILED`, remain intact.
- Commission-sort fallback: only an explicit `UnsupportedSortError` can trigger the single fallback. It restarts `RECOMMENDED` with `page_token=None`; auth/network/server errors propagate without fallback.
- Malformed-row resilience: each raw row has an error boundary; failures increment `SyncResult.failed`, later rows/pages continue, and CLI/web summaries display failed counts.
- Catalog UI: added minimum commission rate/amount, minimum/maximum price, minimum units sold, affiliate state, post state, and `newest` sort controls. Existing typed parsing, parameterized SQL, CSRF, and safe external-URL handling remain in place.
- `newest` uses immutable catalog arrival (`first_seen_at`) rather than the every-sync refresh timestamp, with a deterministic ID tie-breaker.
- The malformed-row boundary catches expected row parsing/serialization errors only; SQLite/database failures and unexpected exceptions abort sync.
- Cooldown: the service fallback is now 7 days, matching `.env.example`, README/runbook guidance, and the implementation plan.
- Deferred coverage: direct tests now cover stockout `UNAVAILABLE`, observable `CREATING -> READY` timestamps, full-link fallback, and E2E publish call count/final `PUBLISHED` state.

## TDD evidence

Tests were added before production changes and run against `0e306d3` behavior.

Focused RED observations:

- Compatibility: `test_legacy_tiktok_source_forwards_campaign_and_configurable_link_path` failed because search/link calls omitted `campaign_id` and the configured link path.
- Unsupported sort: importing `UnsupportedSortError` failed; after introducing the type, the HTTP-400 regression still failed with generic `PublishError` until explicit response classification was added.
- Active-post exclusion: `test_recommendation_excludes_products_with_active_sales_posts` returned all four active-post products.
- UNAVAILABLE recovery: the recovered row remained `UNAVAILABLE` with its stale error.
- Malformed rows: the string row raised `AttributeError: 'str' object has no attribute 'get'`, aborting later rows/pages.
- Catalog isolation: the catalog product appeared in `score_candidates(..., explain=True)` and could enter legacy planning.
- Cooldown: an item posted eight days earlier was excluded because the code fallback was 30 days.
- Commission fallback: auth/provider `PublishError` caused a second `RECOMMENDED` request; the restart regression also required removal of the foreign page token.
- UI: `/sanpham` lacked the required named filter controls and `newest` option.
- Logging: catalog post-link, sync, and standalone-link records retained `exc_info` containing the injected Authorization/token/response-body secrets.
- Web summary: the expected failed count was absent.
- Review follow-up: a resynced old product incorrectly sorted ahead of a later catalog arrival, and an injected `sqlite3.OperationalError` was incorrectly counted as a malformed row instead of propagating.

Focused GREEN results after minimal fixes:

```text
client:   14 passed
service:  15 passed
pipeline:  7 passed
e2e:       1 passed
cli:       8 passed
web:       8 passed
```

The three deferred pipeline behaviors and the two new E2E assertions passed on their first direct run because their implementation paths already existed; no production change was made solely for those passing characterizations.

## Fresh final verification

All commands used the existing release virtualenv with `/tmp/acp` resolving to this worktree.

```bash
for group in migration client service pipeline e2e cli web docs; do
  PYTHONPATH=/tmp /home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python \
    /tmp/acp/tests/test_product_automation.py "$group"
done
```

Result: exit 0; `3 + 14 + 15 + 7 + 1 + 8 + 8 + 2 = 58` product-automation checks passed.

```bash
PYTHONPATH=/tmp /home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python \
  -m acp.tests.test_pipeline
```

Result: exit 0; `78 đạt, 0 hỏng`.

```bash
PYTHONPATH=/tmp /home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python \
  -m acp.tests.test_pilot
```

Result: exit 0; `290 đạt, 0 hỏng`.

```bash
PYTHONPATH=/tmp /home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python \
  -m py_compile adapters/accesstrade_client.py adapters/tiktokshop.py \
  core/products.py core/scoring.py core/pipeline.py web/server.py run.py
```

Result: exit 0.

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock PYTHONPATH=/tmp \
  /home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python /tmp/acp/run.py doctor
```

Result: exit 0 in mock mode. Doctor reported the expected non-release runtime diagnostics: dev encryption key, localhost media URL, and zero configured channels; it found 50 mock products. No live provider or publish call was made.

```bash
git diff --check
```

Result: exit 0, no whitespace errors.

## Files changed

- `adapters/accesstrade_client.py`
- `adapters/tiktokshop.py`
- `core/pipeline.py`
- `core/products.py`
- `core/scoring.py`
- `tests/test_product_automation.py`
- `web/server.py`
- `web/templates/products.html`
- `.superpowers/sdd/2026-08-11-acp-accesstrade-product-automation/final-fix-report.md`
