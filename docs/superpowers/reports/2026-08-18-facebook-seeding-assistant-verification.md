# Facebook Seeding Assistant — Verification Report

**Date:** 2026-08-18  
**Branch:** `feat/facebook-seeding-assistant`

## Fresh verification evidence

### Domain/schema harness

Executed in isolated SQLite harness with mock/no-network configuration.

```text
9 domain tests: PASS
2 schema/settings tests: PASS
```

Covered:

- threshold floor;
- Facebook URL validation and queue order;
- template fallback;
- safe structured LLM AUTO_READY;
- complaint/refund mandatory review;
- first-person testimonial and unsupported claim review;
- recent-comment duplicate review;
- global pause override;
- terminal result idempotency.

### Shift pre-submit regression

Red/green regression was executed for the case where a shift is paused after analysis but before submit.

```text
Before fix: submit click count = 1 (FAIL)
After fix:  submit click count = 0, result = PAUSED (PASS)
```

`runner.performSingleSubmit()` now requires the server-reported `active_shift_id` to match the assignment immediately before clicking submit.

### Extension Node contracts

```text
13 tests: PASS
0 failures
```

Covered:

- text normalization;
- fail-closed composer ambiguity;
- current article context extraction;
- AUTO_READY + pause/active-shift gate;
- single-submit invariant;
- UNKNOWN after failed verification;
- Facebook checkpoint/rate restriction detection;
- Facebook host alias target matching;
- MV3 minimal permission contract;
- no cookies/debugger permission;
- orchestration markers and no anti-detection/bypass markers.

### KPI/report scenario

Synthetic isolated shift:

```text
posted_count=2
auto_posted_count=1
reviewed_posted_count=1
skipped_count=1
unknown_count=1
```

Result: PASS.

### Manager integration harness

A fake ACP release harness exercising the same `run_release_tests` ordering was run:

```text
5 tests: PASS
```

Covered:

- status;
- start/stop;
- invalid command rejection;
- `test` invokes `SEEDING_TEST_OK` + `SEEDING_WEB_TEST_OK` and reaches `TEST_OK`;
- upgrade/rollback preserves DB marker.

## Branch diff/security inspection

Compared branch to `main`. Changed paths are limited to:

- empty config documentation in `.env.example`;
- additive SQLite seeding schema;
- seeding domain/routes/dashboard/tests;
- Chrome MV3 extension/tests/docs;
- `manage.sh` release-test additions;
- navigation/server registration;
- design/plan/runbook/report docs.

No `.env.local`, SQLite DB/WAL/SHM, production token, browser profile, cookie store, generated production media, or backup file is in the branch diff.

Extension manifest uses only `storage` permission and Facebook + loopback host permissions. No `cookies`, `debugger`, `<all_urls>`, proxy rotation, CAPTCHA solver, fingerprint spoofing, webdriver bypass, or anti-detection timing logic was added.

## Verification not executable in this sandbox

These are **not claimed as passing**:

1. `python3 -m acp.tests.test_seeding_web` functional Flask test-client path — sandbox Python does not have Flask installed.
2. Exact full repository `python3 tests/test_manage.py` from a local git checkout — outbound DNS prevents cloning/materializing the GitHub branch into the container. A manager integration harness was run instead.
3. `./manage.sh test` against the normal `~/Downloads/ACP` release layout — that deployment layout/dependencies are not present in the sandbox.
4. Live Facebook DOM validation — intentionally not executed because production/live posting requires explicit operator approval and a controlled target.

## Required Ubuntu gate before enabling auto-submit

On the operator Ubuntu machine:

```bash
cd ~/Downloads/ACP
python3 tests/test_manage.py
ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= python3 -m acp.tests.test_seeding
ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= python3 -m acp.tests.test_seeding_web
node --test extensions/facebook-seeding-assistant/tests/*.test.cjs
./manage.sh test
```

Then start ACP and validate one authorized/test Facebook target with campaign `auto_submit` **OFF** before enabling auto-submit.
