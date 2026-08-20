# Facebook Seeding E2E Task Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the manual-task workflow from mapped Chrome/Facebook profiles through post-context analysis, distinct per-account comment plans, operator-reviewed execution, persisted final text, and a Google-Sheets-compatible B/C/D CSV report.

**Architecture:** Keep ACP as the source of truth. Each Chrome Profile identifies itself by its stable extension instance id; ACP resolves that id to exactly one mapped task account slot and only returns work for that slot. One mapped profile may submit the visible target context to prepare the whole task plan. Multi-account task execution remains operator-reviewed: the extension can navigate, show/fill the assigned text, and record the operator-confirmed result, but it does not unattended-submit coordinated comments across accounts.

**Tech Stack:** Flask/Jinja2, SQLite, existing ACP Gemini callback, Chrome Manifest V3, Node contract tests, Python unittest, stdlib CSV.

**Spec:** `docs/superpowers/specs/2026-08-18-facebook-seeding-assistant-design.md`

## Global Constraints

- Facebook-only execution for this phase.
- Target URL must be the operator-supplied task URL.
- Never store Facebook passwords, cookies, browser sessions, or fingerprints in ACP.
- Every account receives only its own slot work.
- Generated text must respect task forbidden words and cross-account near-duplicate checks.
- Multi-account tasks require operator review/confirmation for each submitted comment.
- Failed/uncertain submission is recorded as UNKNOWN and is not auto-retried.
- No checkpoint/CAPTCHA/rate-limit bypass or anti-detection logic.
- Tests use mock/local fixtures only and never submit to live Facebook.

---

### Task 1: Account-aware work dispatch

**Files:**
- Modify: `core/seeding_accounts.py`
- Create: `tests/test_seeding_account_dispatch.py`

**Interfaces:**
- Consumes: `seeding_account`, `seeding_task_account`, `seeding_comment_slot`.
- Produces: `next_account_work(conn, instance_id, campaign_id=None) -> dict | None` and `resolve_instance_account(conn, instance_id) -> dict`.

- [ ] Write failing tests proving FB01 only receives account_slot=1 rows, FB02 only slot=2, unmapped profiles receive no work, and completed rows are skipped.
- [ ] Run the focused tests and verify RED.
- [ ] Implement minimal deterministic dispatch ordered MAIN before REPLY and item_index ascending.
- [ ] Run focused tests and verify GREEN.

### Task 2: Prepare one distinct multi-account plan from the visible post

**Files:**
- Modify: `core/seeding_tasks.py`
- Modify: `web/seeding_account_routes.py`
- Create: `tests/test_seeding_task_prepare_api_contract.py`

**Interfaces:**
- Consumes: existing `generate_comment_plan(..., llm_fn=...)` and mapped task.
- Produces token-protected `POST /api/seeding/task/prepare` accepting `instance_id`, `campaign_id`, `target_id`, and visible `post_text`.

- [ ] Write failing tests/contracts for authorization, mapped-instance requirement, target/task ownership, and idempotent reuse of an already generated plan.
- [ ] Verify RED.
- [ ] Add a helper that returns existing generated slots when the full plan already exists; otherwise generate and atomically persist all slots with the configured ACP caption LLM.
- [ ] Add the token-protected API route.
- [ ] Verify GREEN.

### Task 3: Operator-reviewed per-account execution

**Files:**
- Modify: `web/seeding_account_routes.py`
- Modify: `extensions/facebook-seeding-assistant/background.js`
- Modify: `extensions/facebook-seeding-assistant/content.js`
- Create: `extensions/facebook-seeding-assistant/tests/account-work.test.cjs`

**Interfaces:**
- Produces `POST /api/seeding/account/next-work` and `POST /api/seeding/account/result`.
- Result states: `POSTED`, `SKIPPED`, `UNKNOWN`; persisted text uses the final operator-visible text.

- [ ] Write failing extension/static contracts proving each profile sends its `extensionInstanceId`, receives only assigned work, and multi-account work requires a visible confirmation action.
- [ ] Verify RED.
- [ ] Add API endpoints for next-work and result with exact account/slot ownership validation.
- [ ] Update extension panel to show `Task → FBxx → Main/Reply`, allow text edit, fill the currently selected composer, then require operator confirmation before recording result.
- [ ] For REPLY rows, do not guess a parent comment; instruct the operator to click Reply first, then fill the unique visible composer.
- [ ] Verify GREEN.

### Task 4: Persist final text and task completion state

**Files:**
- Modify: `core/seeding_accounts.py`
- Modify: `core/seeding_tasks.py`
- Create: `tests/test_seeding_account_results.py`

**Interfaces:**
- Produces `record_account_slot_result(...)` and `task_execution_summary(...)`.

- [ ] Write failing tests for final_text persistence, idempotent result recording, UNKNOWN non-retry behavior, and task summary counts.
- [ ] Verify RED.
- [ ] Implement result transitions and summary.
- [ ] Verify GREEN.

### Task 5: B/C/D report export compatible with Google Sheets

**Files:**
- Create: `core/seeding_reports.py`
- Modify: `web/seeding_account_routes.py`
- Modify: `web/templates/seeding.html`
- Create: `tests/test_seeding_report.py`

**Interfaces:**
- Produces `build_sheet_rows(conn, campaign_id) -> list[list[str]]` and `GET /seeding/campaign/<id>/report.csv`.

- [ ] Write failing tests for the exact report layout: column B task name then original post URL; column C main comments ordered by account slot; column D replies ordered by account slot and reply index; use `final_text` before `generated_text`.
- [ ] Verify RED.
- [ ] Implement stdlib CSV export with UTF-8 BOM for Vietnamese Excel/Google Sheets compatibility.
- [ ] Add a dashboard button `Tải báo cáo Sheet (.csv)` and execution summary.
- [ ] Verify GREEN.

### Task 6: Release-gate integration and documentation

**Files:**
- Modify: `tests/test_seeding_web.py`
- Modify: `extensions/facebook-seeding-assistant/README.md`
- Modify: `docs/FACEBOOK_SEEDING_RUNBOOK.md`

**Interfaces:** Existing `./manage.sh test` seeding gate.

- [ ] Ensure new Python account/prepare/result/report test modules are loaded by the existing Seeding release suite.
- [ ] Ensure Node extension tests cover account pairing and per-account work UI contracts.
- [ ] Document the Chrome Profile setup and manual execution flow.
- [ ] Run fresh targeted Python/Node/syntax verification.
- [ ] Run `./manage.sh test` when the full runtime is available; if unavailable, state that limitation explicitly.
- [ ] Review PR diff for secrets/live data and update Draft PR #5.
