# Facebook Seeding E2E Task Execution Implementation Plan

> **Status:** implemented on `feat/facebook-seeding-assistant`; full Ubuntu release gate still required before merge/live use.

**Goal:** Complete the manual-task workflow from mapped Chrome/Facebook profiles through post-context analysis, distinct per-account comment plans, operator-reviewed execution, persisted final text, and Google-Sheets-compatible B/C/D reporting.

**Architecture:** ACP is the source of truth. Each Chrome Profile identifies itself by a stable extension instance id; ACP resolves that id to exactly one mapped task account slot and only returns work for that slot. One mapped profile may submit the visible target context to prepare the whole task plan. Multi-account execution remains operator-reviewed: the extension can navigate and fill the assigned text, while the operator performs Like/Post on Facebook.

## Implemented flow

```text
Create task
→ map FB01 / FB02 / FB03
→ each Chrome Profile registers + heartbeats
→ next-work resolves extensionInstanceId → exact account_slot
→ LIKE confirmation if required
→ visible post context → /api/seeding/account/prepare
→ structured Gemini JSON plan for mapped slots only
→ cross-account forbidden-word + near-duplicate validation
→ FB01 receives slot 1 MAIN/REPLY only
→ FB02 receives slot 2 MAIN/REPLY only
→ FB03 receives slot 3 MAIN/REPLY only
→ extension fills selected composer
→ operator manually posts
→ ACP verifies/persists final text
→ B/C/D report / optional Google Sheet
```

## Completed tasks

- [x] Account-aware work dispatch in `core/seeding_execution.py`.
- [x] Stable Chrome Profile pairing and account-slot mapping.
- [x] Generate one distinct plan for accounts actually mapped to the task.
- [x] Profile-scoped next-work / prepare / like-result / work-result APIs.
- [x] Operator-reviewed MAIN and REPLY execution; no unattended submit.
- [x] Persist final edited text and proof reference.
- [x] UNKNOWN handling/recovery without automatic duplicate retry.
- [x] B/C/D report generation and optional Sheet push.
- [x] Structured Gemini JSON callback for Seeding (`get_seeding_llm()` → `rewrite_json()`).
- [x] Seeding regression modules loaded by `tests/test_seeding_web.py` release gate.
- [x] Extension/runbook documentation updated.

## Current constraints

- Facebook-only execution.
- Target must be the operator-supplied Facebook URL.
- No Facebook password/cookie/browser-session storage in ACP.
- No account creation/rotation, proxy rotation, fingerprint spoofing, CAPTCHA/checkpoint bypass or anti-detection logic.
- Multi-account comments remain operator-reviewed and manually submitted.
- Tests/verification do not publish to live Facebook.

## Verification gate

Focused structured-LLM regression was verified RED → GREEN with 4/4 passing after the fix. The model container cannot resolve `github.com`, so a fresh complete checkout and `./manage.sh test` cannot be run here after the latest changes.

Run on the normal Ubuntu ACP environment before merge/live use:

```bash
cd ~/Downloads/ACP/acp
git fetch origin
git switch feat/facebook-seeding-assistant
git pull --ff-only

cd ~/Downloads/ACP
./manage.sh test
```
