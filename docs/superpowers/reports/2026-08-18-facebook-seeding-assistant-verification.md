# Facebook Seeding Assistant — Verification Report

**Branch:** `feat/facebook-seeding-assistant`

## Baseline verification evidence

Earlier isolated harness verification covered the Seeding foundation:

```text
9 domain tests: PASS
2 schema/settings tests: PASS
13 extension Node contracts: PASS
manager integration harness: 5 tests PASS
```

The baseline covered risk gates, queue/idempotency, pause-before-submit, single-submit/UNKNOWN behavior, extension permissions, KPI counters and manager release-test ordering.

## Manual task + profile-scoped execution

The branch now additionally contains:

- manual task intake (`task name + instruction + Facebook URL`);
- parsed LIKE/main/reply/max-account/forbidden-word rules;
- stable Chrome Profile `extensionInstanceId` pairing and heartbeat;
- task account-slot mapping (`FB01 → slot 1`, `FB02 → slot 2`, ...);
- `core/seeding_execution.py` as the canonical profile-scoped work dispatcher;
- generation only for accounts actually mapped to the task;
- cross-account forbidden-word and exact/near-duplicate validation;
- operator-reviewed MAIN/REPLY composer filling;
- final edited-text persistence and proof reference;
- B/C/D report generation, optional Google Sheet push and UNKNOWN recovery.

The multi-profile execution path does not click Facebook Like or Submit. Operator confirmation remains required.

## Structured Seeding LLM regression — 2026-08-20

Review found that `/api/seeding/account/prepare` used `factory.get_caption_llm()`, a free-form text callback, although `seeding_tasks.parse_comment_plan_response()` requires an `accounts[]` JSON document.

A dedicated TDD regression was added:

```text
RED:
- get_seeding_llm() missing
- rewrite_json() missing
- /prepare still used get_caption_llm()
Result: 2 failures + 2 errors

GREEN:
4/4 structured LLM contract tests PASS
Python compile checks PASS
```

The fix:

- adds `factory.get_seeding_llm()`;
- reuses `ACP_CAPTION_LLM=gemini` as the operator switch;
- routes Seeding generation to `llm_gemini.rewrite_json()`;
- enables Gemini `response_mime_type="application/json"`;
- makes `/api/seeding/account/prepare` use the structured callback;
- loads `test_seeding_llm_contract` through `tests/test_seeding_web.py` so it participates in the normal Seeding release gate.

## Security/scope

No Facebook password, cookie, browser session, account token, browser fingerprint, CAPTCHA/checkpoint bypass, proxy rotation, or anti-detection mechanism is added. No live Facebook Like/comment was executed during implementation verification.

## Remaining environment gate

The model container cannot resolve `github.com`, so a fresh full branch checkout and the complete `./manage.sh test` could not be executed after the latest profile-execution/structured-LLM changes. The focused structured-LLM RED→GREEN harness was executed successfully, but the full Ubuntu release gate must still run before merge/live use.

On the operator Ubuntu machine:

```bash
cd ~/Downloads/ACP/acp
git fetch origin
git switch feat/facebook-seeding-assistant
git pull --ff-only

cd ~/Downloads/ACP
./manage.sh test
```

Then restart ACP, reload the unpacked extension in every mapped Chrome Profile, and validate with an authorized/test task before normal use.
