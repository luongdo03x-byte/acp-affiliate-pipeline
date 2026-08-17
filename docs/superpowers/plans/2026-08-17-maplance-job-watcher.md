# Maplance Job Watcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect visible Maplance marketplace jobs from the operator browser, ingest them safely into ACP, filter them, notify on eligible new jobs, and provide an explicit open-job action without automated claiming or review posting.

**Architecture:** Add a generic SQLite-backed external-job core, a strict Maplance payload adapter, token-protected Flask routes, and a Chrome MV3 helper that observes rendered job cards. Keep all remote side effects outside ACP: the extension only reads visible DOM and sends normalized job metadata to the local ACP ingest endpoint.

**Tech Stack:** Python 3, SQLite, Flask/Jinja2, vanilla JavaScript, Chrome Manifest V3, Node built-in test runner.

## Global Constraints

- No automatic Maplance slot claiming.
- No Google review creation/posting automation.
- No browser cookie/session extraction or private API reverse engineering.
- No location spoofing, account rotation, CAPTCHA bypass, or anti-detection behavior.
- Do not log or persist `ACP_MAPLANCE_INGEST_TOKEN`.
- Tests and release verification run with `ACP_ADAPTER=mock` and `ACP_SOURCE=mock`.
- Existing production data must remain intact; schema changes are additive only.

---

### Task 1: External job core and schema

**Files:**
- Create: `core/external_jobs.py`
- Modify: `core/db.py`
- Test: `tests/test_external_jobs.py`

**Interfaces:**
- Produces: `WatcherRule`, `upsert_jobs(conn, source, jobs)`, `load_watcher_rule(conn, source)`, `save_watcher_rule(conn, source, rule)`, `list_jobs(conn, source, rule, limit=100)`.
- Job dict fields: `external_job_id`, `title`, `job_url`, `place_url`, `reward`, `available_slots`, `min_local_guide`, `location`, `raw_summary`.

- [ ] **Step 1: Write failing tests** for additive schema assumptions, rule normalization, idempotent upsert, first-seen semantics, eligibility, and config round-trip.
- [ ] **Step 2: Run** `python3 tests/test_external_jobs.py` and confirm failure because `core.external_jobs`/schema do not exist.
- [ ] **Step 3: Implement** `core/external_jobs.py` and append `external_job` DDL/indexes to `SCHEMA` in `core/db.py`.
- [ ] **Step 4: Run** `python3 tests/test_external_jobs.py` and confirm PASS.
- [ ] **Step 5: Commit** `feat: add external job watcher core`.

### Task 2: Maplance payload adapter

**Files:**
- Create: `adapters/maplance.py`
- Test: `tests/test_maplance_adapter.py`

**Interfaces:**
- Produces: `normalize_job(payload: dict) -> dict` and `normalize_batch(payloads: list[dict]) -> tuple[list[dict], list[str]]`.
- Accepts only HTTPS `maplance.online` / subdomain job URLs.

- [ ] **Step 1: Write failing tests** for URL allowlist, stable ID derivation, integer bounds, text caps, per-row rejection, and batch partial success.
- [ ] **Step 2: Run** `python3 tests/test_maplance_adapter.py` and confirm expected missing-module failure.
- [ ] **Step 3: Implement** strict normalization without network calls or cookies.
- [ ] **Step 4: Run** adapter tests and confirm PASS.
- [ ] **Step 5: Commit** `feat: add Maplance job payload adapter`.

### Task 3: Flask ingest/workspace routes

**Files:**
- Modify: `web/server.py`
- Modify: `web/templates/base.html`
- Create: `web/templates/maplance_jobs.html`
- Test: `tests/test_maplance_web_contract.py`

**Interfaces:**
- `GET /viec/maplance`
- `POST /viec/maplance/config`
- `POST /api/maplance/jobs`
- Header: `X-ACP-Maplance-Token`
- Environment: `ACP_MAPLANCE_INGEST_TOKEN`

- [ ] **Step 1: Write failing contract tests** that inspect route/template source and pure token-check helper behavior without requiring a live server.
- [ ] **Step 2: Run** the contract test and confirm failure because routes/template are absent.
- [ ] **Step 3: Implement** routes, constant-time token verification, config form, sidebar entry and recent-jobs workspace. No remote claim action.
- [ ] **Step 4: Run** contract tests plus `python3 -m py_compile web/server.py core/external_jobs.py adapters/maplance.py`.
- [ ] **Step 5: Commit** `feat: add Maplance watcher workspace`.

### Task 4: Chrome MV3 rendered-DOM collector

**Files:**
- Create: `extensions/maplance-job-watcher/manifest.json`
- Create: `extensions/maplance-job-watcher/parser.js`
- Create: `extensions/maplance-job-watcher/content.js`
- Create: `extensions/maplance-job-watcher/background.js`
- Create: `extensions/maplance-job-watcher/options.html`
- Create: `extensions/maplance-job-watcher/options.js`
- Test: `tests/test_maplance_extension.js`

**Interfaces:**
- Parser produces `{external_job_id?, title, job_url, place_url?, reward, available_slots, min_local_guide?, location?, raw_summary}`.
- Background POSTs `{jobs:[...]}` to `<acpBase>/api/maplance/jobs` with `X-ACP-Maplance-Token`.

- [ ] **Step 1: Write failing Node tests** for Vietnamese reward/slot/Local Guide extraction, Maplance URL requirement and de-duplication.
- [ ] **Step 2: Run** `node --test tests/test_maplance_extension.js` and confirm missing parser failure.
- [ ] **Step 3: Implement** parser, MutationObserver collector, service worker sender/notification, and options storage.
- [ ] **Step 4: Run** Node tests and JSON-parse `manifest.json`.
- [ ] **Step 5: Commit** `feat: add Maplance browser job collector`.

### Task 5: Operator docs and verification

**Files:**
- Create: `docs/MAPLANCE_JOB_WATCHER_RUNBOOK.md`
- Modify: `README.md`

**Interfaces:**
- Documents setting `ACP_MAPLANCE_INGEST_TOKEN`, loading unpacked extension, configuring ACP base URL/token, opening Maplance marketplace, and reviewing `/viec/maplance`.

- [ ] **Step 1: Document** setup, safety boundary, troubleshooting, and disable procedure.
- [ ] **Step 2: Run focused tests:** `python3 tests/test_external_jobs.py`, `python3 tests/test_maplance_adapter.py`, `python3 tests/test_maplance_web_contract.py`, `node --test tests/test_maplance_extension.js`.
- [ ] **Step 3: Run syntax/static checks** and inspect for `claim`, auto-click, cookie/session extraction, token logging, and full URLs in logs.
- [ ] **Step 4: Attempt release verification** with `ACP_ADAPTER=mock ACP_SOURCE=mock ./manage.sh test`; if the sandbox lacks Flask/Werkzeug, record that exact blocker without claiming success.
- [ ] **Step 5: Review branch diff/status through GitHub and open a draft PR; do not merge automatically.**
