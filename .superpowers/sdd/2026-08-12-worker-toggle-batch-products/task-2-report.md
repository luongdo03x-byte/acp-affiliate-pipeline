# Task 2 report — worker CLI and user timer

## Delivered

- Added `python run.py worker-once`, which builds the active adapter context and
  performs one `jobs.run_once` pass. It reports only aggregate results and
  returns `1` for operational setup/runtime failures without printing exception
  text.
- Added `python run.py worker-status`, which reports the durable publish-worker
  switch and aggregate queue counts only.
- Added secret-free user-systemd templates:
  - `ops/acp-worker.service`
  - `ops/acp-worker.timer`
  They source the active release `.env.local`, run every minute, and do not
  change the persisted default-disabled publish setting.
- Documented installation, status, logs, and stopping the timer in `README.md`
  and `docs/ACP_RUNBOOK.md`.
- Added CLI and unit-safety tests to the product automation CLI group.

## Verification

Passed from the ACP release directory:

```text
ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= .venv/bin/python tests/test_product_automation.py cli
# 12 passed

ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= .venv/bin/python tests/test_product_automation.py worker
# 6 passed

ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= .venv/bin/python tests/test_product_automation.py pipeline
# 8 passed

.venv/bin/python -m py_compile run.py
git diff --check
```

`systemd-analyze verify ops/acp-worker.service ops/acp-worker.timer` completed
without errors for the new units. A machine-wide pre-existing TeamViewer legacy
PID path warning was emitted by the verifier and is unrelated to ACP.

An explicit `rg` scan confirmed the worker unit files contain none of
`ACCESSTRADE_API_TOKEN=`, `AT_ACCESS_KEY=`, or `ACP_MASTER_KEY=`.

## Scope note

No UI toggle, batch catalog implementation, live worker installation, or
publishing action was performed. The user-owned `core/content.py` change was
not modified.
