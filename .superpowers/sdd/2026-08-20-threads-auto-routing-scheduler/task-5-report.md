# Task 5 Report

Implemented Task 5 in `/home/dluowng/Downloads/ACP/worktrees/threads-auto-routing-scheduler`.

## Scope delivered

- Added `/vanhanh` Auto operations summary with:
  - aggregate upcoming Auto target count,
  - aggregate open-slot count for the next 48 hours,
  - sanitized stale-reason counts from `auto_stale_cancelled` audit rows,
  - explicit copy separating channel Auto scheduling from the global publish worker.
- Added deterministic web coverage for the new summary and sanitized rendering.
- Updated CLI/help text so `auto-schedule` is described as schedule-fill only and documented timer order is `sync catalog -> auto-schedule -> worker-once`.
- Updated `README.md` and `docs/ACP_RUNBOOK.md` with timer order, safety gates, and user-systemd install steps for the new Auto schedule timer.
- Added `ops/acp-auto-schedule.service` and `ops/acp-auto-schedule.timer` examples that source active release env without embedding secrets.
- Follow-up: revised the concrete systemd chain so `acp-auto-schedule.service` runs `product-sync` -> `auto-schedule` -> `worker-once`, while `acp-worker.service` stays `worker-once` only as a fallback queue sweep.

## Files changed

- `web/server.py`
- `web/templates/ops.html`
- `run.py`
- `README.md`
- `docs/ACP_RUNBOOK.md`
- `ops/acp-auto-schedule.service`
- `ops/acp-auto-schedule.timer`
- `tests/test_auto_scheduler.py`
- `tests/test_product_automation.py`

## Verification

- `ACP_ADAPTER=mock ACP_SOURCE=mock /home/dluowng/Downloads/ACP/releases/2.0/acp/.venv/bin/python -m unittest tests.test_auto_scheduler -v`
  - PASS (`Ran 37 tests in 1.350s`, `OK`)
- Focused product/web contracts via direct module load of `tests/test_product_automation.py`
  - PASS for `cli` group (`13 passed`)
  - PASS for `web` group (`19 passed`)
- `ACP_BASE=<temporary release-layout copy> ACP_ADAPTER=mock ACP_SOURCE=mock ./manage.sh test`
  - PASS (`TEST_OK`)
- `git diff --check`
  - PASS (no output)
- `git status --short --branch`
  - Reviewed expected tracked changes plus new `ops/acp-auto-schedule.*`

## Safety notes

- No live adapter enablement.
- No production secrets, `.env.local`, live DB, or live publish actions were modified.
- `manage.sh` was not changed.

## Concerns

- `manage.sh test` cannot execute directly from this worktree path because the script expects a release directory literally named `acp`; verification used a temporary release-layout copy of the worktree with the shared virtualenv and mock env.
