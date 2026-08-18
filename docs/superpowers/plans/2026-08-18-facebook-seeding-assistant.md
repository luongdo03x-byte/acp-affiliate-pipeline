# Facebook Seeding Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Facebook-first seeding assistant to ACP that processes an operator-supplied URL queue, prepares template-first contextual comments, auto-submits only through explicit low-risk gates, pauses for review otherwise, and records shift KPI/audit data.

**Architecture:** ACP remains the source of truth for campaign configuration, target ordering, content/risk decisions, pause state, and reporting. A thin Chrome Manifest V3 extension handles only the rendered Facebook page: extract minimal current-post context, fill the composer, best-effort submit when ACP authorizes it, verify the result, and hand control back to ACP. The extension never owns policy decisions and fails closed whenever ACP, the DOM, or Facebook trust/safety state is uncertain.

**Tech Stack:** Python 3, Flask/Jinja2, SQLite, existing ACP Gemini callback (`fn(prompt) -> str`), Chrome Manifest V3 JavaScript, Node built-in test runner for pure extension helpers.

**Spec:** `docs/superpowers/specs/2026-08-18-facebook-seeding-assistant-design.md`

## Global Constraints

- MVP platform is Facebook only.
- Target URLs are supplied by the operator/company; MVP performs no Facebook discovery/search.
- Execution mode is hybrid: low-risk/high-confidence targets may auto-submit only when the campaign explicitly enables it; all uncertainty pauses for review.
- Generation is template-first; campaign brief is fallback only.
- New campaigns default `auto_submit=0`; default confidence threshold is `0.90`; API/UI reject thresholds below `0.85`.
- No account creation/rotation, profile farming, fingerprint spoofing, anti-detection, CAPTCHA solving, checkpoint bypass, proxy rotation, or rate-limit evasion.
- No fabricated customer testimonial, fabricated first-person experience, fake review, or automation intended to impersonate an independent customer.
- Extension extracts only the current rendered target context and never stores Facebook cookies/passwords/session tokens in ACP.
- Any Facebook login verification/checkpoint/rate restriction, unknown submit result, unsupported DOM, or ACP outage fails closed and pauses automation.
- All database changes are additive/backward-compatible.
- Tests run with `ACP_ADAPTER=mock`, `ACP_SOURCE=mock`, and must never post to real Facebook.
- Production secrets/data named in `AGENTS.md` remain untouched and unlogged.

---

## File Map

### Create

- `core/seeding.py` — seeding domain service: campaign/template/target/shift operations, structured generation, deterministic risk gates, duplicate detection, result recording, KPI aggregation.
- `tests/test_seeding.py` — isolated SQLite/domain/API contract tests using a temporary database and fake LLM.
- `web/templates/seeding.html` — Jinja2 operator screen for campaigns, templates, target import, shift controls, activity/KPI, and global pause.
- `extensions/facebook-seeding-assistant/manifest.json` — Chrome MV3 manifest with Facebook + local ACP permissions only.
- `extensions/facebook-seeding-assistant/parser.js` — pure DOM/text normalization helpers, exposed to browser and CommonJS tests.
- `extensions/facebook-seeding-assistant/runner.js` — pure execution-decision and verification helpers, exposed to browser and CommonJS tests.
- `extensions/facebook-seeding-assistant/content.js` — Facebook page orchestration, injected review panel, fill/submit/verify/next-target loop.
- `extensions/facebook-seeding-assistant/background.js` — extension configuration storage and ACP fetch proxy.
- `extensions/facebook-seeding-assistant/tests/parser.test.cjs` — Node tests for parser/runner behavior using small fake DOM-shaped objects and pure helpers.
- `extensions/facebook-seeding-assistant/README.md` — local developer install/config/test instructions.

### Modify

- `core/db.py` — add five seeding tables/indexes to `SCHEMA`.
- `core/system_settings.py` — add explicit `SEEDING_GLOBAL_PAUSED` helpers while preserving current publish-worker behavior.
- `web/server.py` — initialize seeding LLM callback; dashboard routes; token-protected extension API; fail-closed validation.
- `web/templates/base.html` — add one `Seeding` sidebar link.
- `web/static/acp.css` — minimal styles for seeding page/status chips/KPI grid/forms.
- `.env.example` — document `ACP_SEEDING_EXTENSION_TOKEN` only; never add a real token.
- `manage.sh` — include `acp.tests.test_seeding` in release verification under mock mode.
- `tests/test_manage.py` — make fake releases include a stub `test_seeding.py` and verify `manage.sh test` executes it.
- `docs/ACP_RUNBOOK.md` — document enabling/configuring the local extension and safe test procedure without live posting.

---

### Task 1: Add Seeding Schema and Global Pause State

**Files:**
- Modify: `core/db.py`
- Modify: `core/system_settings.py`
- Create: `tests/test_seeding.py`

**Interfaces:**
- Produces: SQLite tables `seeding_campaign`, `seeding_template`, `seeding_target`, `seeding_shift`, `seeding_activity`.
- Produces: `SEEDING_GLOBAL_PAUSED = "seeding_global_paused"`.
- Produces: `seeding_global_paused(conn) -> bool` and `set_seeding_global_paused(conn, paused: bool, actor: str = "operator") -> None`.
- Existing `get_system_setting`, `set_system_setting`, and `publish_worker_enabled` behavior must not change.

- [ ] **Step 1: Write the failing schema/settings tests**

Add the initial `tests/test_seeding.py` harness using a temporary DB:

```python
import os
import tempfile
import unittest

_tmp = tempfile.mkdtemp(prefix="acp-seeding-test-")
os.environ["ACP_DB"] = os.path.join(_tmp, "seeding.db")

from acp.core import db, system_settings  # noqa: E402

db.DB_PATH = os.environ["ACP_DB"]


class SeedingSchemaTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()

    def test_seeding_tables_exist(self):
        names = {row[0] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        self.assertTrue({
            "seeding_campaign", "seeding_template", "seeding_target",
            "seeding_shift", "seeding_activity",
        }.issubset(names))

    def test_global_pause_defaults_fail_safe_to_false_and_is_audited(self):
        self.assertFalse(system_settings.seeding_global_paused(self.conn))
        system_settings.set_seeding_global_paused(self.conn, True, actor="test")
        self.assertTrue(system_settings.seeding_global_paused(self.conn))
        row = self.conn.execute(
            "SELECT action, actor FROM audit_log WHERE entity='system_setting' "
            "AND entity_id=? ORDER BY id DESC LIMIT 1",
            (system_settings.SEEDING_GLOBAL_PAUSED,),
        ).fetchone()
        self.assertEqual(("set", "test"), (row["action"], row["actor"]))
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= python3 -m acp.tests.test_seeding
```

Expected: FAIL because the seeding tables and `seeding_global_paused` helpers do not exist.

- [ ] **Step 3: Add the five additive tables and indexes to `SCHEMA`**

Add schema equivalent to:

```sql
CREATE TABLE IF NOT EXISTS seeding_campaign (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    brand TEXT,
    brief TEXT NOT NULL,
    allowed_claims TEXT NOT NULL DEFAULT '[]',
    prohibited_topics TEXT NOT NULL DEFAULT '[]',
    disclosure_policy TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    auto_submit INTEGER NOT NULL DEFAULT 0,
    confidence_threshold REAL NOT NULL DEFAULT 0.90,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seeding_template (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES seeding_campaign(id),
    intent TEXT NOT NULL,
    source_text TEXT NOT NULL,
    allowed_claims TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seed_template_campaign
    ON seeding_template(campaign_id, intent, enabled);

CREATE TABLE IF NOT EXISTS seeding_target (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES seeding_campaign(id),
    url TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'READY',
    context_summary TEXT,
    intent TEXT,
    risk_level TEXT,
    risk_labels TEXT NOT NULL DEFAULT '[]',
    confidence REAL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(campaign_id, url)
);
CREATE INDEX IF NOT EXISTS idx_seed_target_queue
    ON seeding_target(campaign_id, status, position);

CREATE TABLE IF NOT EXISTS seeding_shift (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES seeding_campaign(id),
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    started_at TEXT NOT NULL,
    ended_at TEXT,
    target_count INTEGER NOT NULL DEFAULT 0,
    posted_count INTEGER NOT NULL DEFAULT 0,
    review_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    unknown_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_seed_shift_campaign
    ON seeding_shift(campaign_id, status, started_at);

CREATE TABLE IF NOT EXISTS seeding_activity (
    id TEXT PRIMARY KEY,
    target_id TEXT NOT NULL REFERENCES seeding_target(id),
    shift_id TEXT REFERENCES seeding_shift(id),
    action TEXT NOT NULL,
    intent TEXT,
    template_id TEXT REFERENCES seeding_template(id),
    generated_text TEXT,
    final_text TEXT,
    mode TEXT,
    result TEXT,
    proof_ref TEXT,
    error_detail TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seed_activity_shift
    ON seeding_activity(shift_id, created_at);
```

- [ ] **Step 4: Add explicit pause helpers without changing generic redaction semantics**

In `core/system_settings.py` add:

```python
SEEDING_GLOBAL_PAUSED = "seeding_global_paused"


def seeding_global_paused(conn) -> bool:
    return get_system_setting(conn, SEEDING_GLOBAL_PAUSED, "0") == "1"


def set_seeding_global_paused(conn, paused: bool, actor: str = "operator") -> None:
    set_system_setting(conn, SEEDING_GLOBAL_PAUSED, "1" if paused else "0", actor=actor)
```

Do not broaden the existing `audit_value` disclosure logic for unrelated settings.

- [ ] **Step 5: Run focused tests and confirm GREEN**

Run:

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= python3 -m acp.tests.test_seeding
```

Expected: PASS for the schema and pause tests.

- [ ] **Step 6: Commit Task 1**

```bash
git add core/db.py core/system_settings.py tests/test_seeding.py
git commit -m "feat: add Facebook seeding schema and pause state"
```

---

### Task 2: Implement the Seeding Domain Engine and Risk Gate

**Files:**
- Create: `core/seeding.py`
- Modify: `tests/test_seeding.py`

**Interfaces:**
- Produces: `set_llm(fn) -> None` where `fn(prompt: str) -> str` returns JSON text.
- Produces: `create_campaign(conn, *, name, brand, brief, allowed_claims, prohibited_topics, disclosure_policy, auto_submit=False, confidence_threshold=0.90) -> dict`.
- Produces: `add_template(conn, campaign_id, *, intent, source_text, allowed_claims=()) -> dict`.
- Produces: `import_targets(conn, campaign_id, urls: list[str]) -> dict` with `created`, `duplicates`, `invalid` counts.
- Produces: `start_shift(conn, campaign_id) -> dict`, `pause_shift(conn, shift_id) -> None`, `end_shift(conn, shift_id) -> dict`.
- Produces: `next_target(conn, shift_id) -> dict | None`.
- Produces: `prepare_target(conn, shift_id, target_id, context: dict) -> dict` containing `decision`, `drafts`, `confidence`, `risk_level`, `risk_labels`, `template_id`.
- Produces: `record_result(conn, shift_id, target_id, *, result, mode, final_text=None, proof_ref=None, error_detail=None) -> dict`.
- Produces: `shift_summary(conn, shift_id) -> dict`.

- [ ] **Step 1: Add failing lifecycle/import tests**

Append tests that prove threshold validation, URL normalization, stable queue order, duplicate rejection, and one active shift per campaign:

```python
from acp.core import seeding


def test_campaign_threshold_and_target_queue(self):
    with self.assertRaises(ValueError):
        seeding.create_campaign(
            self.conn, name="x", brand="b", brief="brief",
            allowed_claims=[], prohibited_topics=[], disclosure_policy="",
            confidence_threshold=0.84,
        )
    campaign = seeding.create_campaign(
        self.conn, name="Campaign A", brand="Brand", brief="Chỉ dùng claim đã duyệt",
        allowed_claims=["free_consultation"], prohibited_topics=["refund"],
        disclosure_policy="promotional", auto_submit=True, confidence_threshold=0.90,
    )
    res = seeding.import_targets(self.conn, campaign["id"], [
        "https://www.facebook.com/groups/demo/posts/1/",
        "https://www.facebook.com/groups/demo/posts/2/",
        "https://www.facebook.com/groups/demo/posts/1/",
        "javascript:alert(1)",
    ])
    self.assertEqual({"created": 2, "duplicates": 1, "invalid": 1}, res)
    shift = seeding.start_shift(self.conn, campaign["id"])
    first = seeding.next_target(self.conn, shift["id"])
    self.assertTrue(first["url"].endswith("/posts/1/"))
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run the module; expected failure is `ImportError`/missing `core.seeding`.

- [ ] **Step 3: Implement lifecycle helpers and strict Facebook URL validation**

Use `urllib.parse.urlsplit` and accept only `https://facebook.com/...`, `https://www.facebook.com/...`, or `https://m.facebook.com/...`; reject credentials, fragments containing control characters, non-HTTPS schemes, and non-Facebook hosts. Preserve exact normalized URL in the queue so extension navigation can compare it against the supplied target.

Core status transitions must reject impossible automatic re-submission: `POSTED`, `UNKNOWN`, `SKIPPED`, and `UNAVAILABLE` are not returned by `next_target`.

- [ ] **Step 4: Add failing generation/risk tests with a fake structured LLM**

Add a deterministic fake:

```python
def fake_llm(prompt):
    return '{"intent":"recommendation_request","draft":"Bạn có thể tham khảo Brand; hiện có tư vấn miễn phí.","confidence":0.96,"risk_labels":[],"claims_used":["free_consultation"]}'
```

Test these cases:

```python
seeding.set_llm(fake_llm)
# LOW + 0.96 + allowed claim + campaign auto ON => AUTO_READY
# complaint text => REVIEW_REQUIRED even if model returns 0.99
# first-person claim "mình đã làm ở đây" => REVIEW_REQUIRED
# unknown claim => REVIEW_REQUIRED
# duplicate draft similar to recent POSTED comment => REVIEW_REQUIRED
# global pause => REVIEW_REQUIRED / auto_allowed False
```

- [ ] **Step 5: Implement structured generation and deterministic risk mapping**

`prepare_target` must:

```python
{
    "decision": "AUTO_READY" | "REVIEW_REQUIRED" | "SKIP_RECOMMENDED",
    "drafts": ["..."],
    "confidence": 0.96,
    "risk_level": "LOW" | "MEDIUM" | "HIGH",
    "risk_labels": ["complaint"],
    "template_id": "..." | None,
    "claims_used": ["free_consultation"],
}
```

Deterministic mandatory-review labels include:

```python
MANDATORY_REVIEW = {
    "negative_brand_context", "complaint", "refund_dispute", "legal_threat",
    "medical_complication", "fraud_allegation", "ambiguous_context",
    "unsupported_claim", "personal_experience_required", "first_person_testimonial",
    "sensitive_personal_data", "model_uncertainty", "target_mismatch", "dom_uncertainty",
}
```

Local rule checks must supplement model labels. At minimum, detect common Vietnamese/English complaint/refund/scam/adverse-event terms and first-person experience phrases before auto eligibility. These checks only make the gate stricter; they do not rewrite content.

Use `difflib.SequenceMatcher` on normalized text with a default similarity cutoff of `0.88` against recent successful comments in the same campaign. Similarity triggers review/regeneration; it is not used to evade platform detection.

If no LLM is configured, template text may be used as a draft only when an intent-matching enabled template exists; brief-only fallback without an LLM returns `REVIEW_REQUIRED` with no auto-submit.

- [ ] **Step 6: Implement result recording idempotently**

`record_result` accepts only:

```text
POSTED | UNKNOWN | SKIPPED | UNAVAILABLE
```

Rules:

- `POSTED` increments `posted_count` exactly once and sets `completed_at`.
- `UNKNOWN` increments `unknown_count` exactly once and becomes terminal for auto execution.
- `SKIPPED`/`UNAVAILABLE` increment `skipped_count` exactly once.
- Replaying the same terminal result must not double-increment KPI.
- Transitioning from a terminal state without explicit manual reset raises `ValueError`.

- [ ] **Step 7: Run domain tests and confirm GREEN**

Run:

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= python3 -m acp.tests.test_seeding
```

Expected: all domain tests pass without network calls.

- [ ] **Step 8: Commit Task 2**

```bash
git add core/seeding.py tests/test_seeding.py
git commit -m "feat: add seeding queue generation and risk engine"
```

---

### Task 3: Add Token-Protected Extension API and ACP Dashboard

**Files:**
- Modify: `web/server.py`
- Create: `web/templates/seeding.html`
- Modify: `web/templates/base.html`
- Modify: `web/static/acp.css`
- Modify: `.env.example`
- Modify: `tests/test_seeding.py`

**Interfaces:**
- Dashboard: `GET /seeding` and authenticated/CSRF-protected POST routes for campaign/template/target/shift/global-pause operations.
- Extension API: `GET /api/seeding/status`, `POST /api/seeding/next-target`, `POST /api/seeding/analyze`, `POST /api/seeding/result`, `POST /api/seeding/review-result`.
- Extension auth header: `X-ACP-Seeding-Token` compared with `ACP_SEEDING_EXTENSION_TOKEN` using `hmac.compare_digest`.

- [ ] **Step 1: Add failing web contract tests**

Create a Flask test client with `ACP_ADMIN_PASSWORD=""` and `ACP_SEEDING_EXTENSION_TOKEN="test-seeding-token"` before `create_app()`.

Test:

```python
self.assertEqual(401, client.get("/api/seeding/status").status_code)
self.assertEqual(401, client.get(
    "/api/seeding/status", headers={"X-ACP-Seeding-Token": "wrong"}
).status_code)
self.assertEqual(200, client.get(
    "/api/seeding/status", headers={"X-ACP-Seeding-Token": "test-seeding-token"}
).status_code)
self.assertEqual(200, client.get("/seeding").status_code)
```

Also assert that `GET /seeding` contains `Seeding`, `Global pause`, and the auto-submit threshold UI.

- [ ] **Step 2: Run tests and confirm RED**

Expected: missing routes/template/API.

- [ ] **Step 3: Initialize seeding LLM callback in `create_app()`**

Add:

```python
from ..core import seeding

seeding.set_llm(factory.get_caption_llm())
```

This reuses the existing Gemini callback but keeps seeding prompting/parsing in `core/seeding.py`.

- [ ] **Step 4: Add dedicated extension authentication**

Add `/api/seeding/` to `PUBLIC_PREFIXES` only because these requests do not carry the dashboard session cookie. Every seeding API route must immediately call a helper equivalent to:

```python
def _require_seeding_token():
    expected = os.environ.get("ACP_SEEDING_EXTENSION_TOKEN", "")
    given = request.headers.get("X-ACP-Seeding-Token", "")
    if not expected or not given or not hmac.compare_digest(expected, given):
        abort(401)
```

A missing server token disables extension API access; it never falls back to unauthenticated mode.

- [ ] **Step 5: Add dashboard routes using existing auth/CSRF behavior**

Implement route handlers that call `core.seeding` rather than issuing business-state SQL directly. Dashboard form handlers redirect back with a human-readable `err` query parameter on validation failures.

For global pause, call `set_seeding_global_paused(conn, paused, actor="operator")`.

- [ ] **Step 6: Add extension API routes with strict state/ID validation**

Expected response shapes:

```json
GET /api/seeding/status
{"ok":true,"paused":false,"active_shift_id":"..."}

POST /api/seeding/next-target
{"ok":true,"target":{"id":"...","url":"https://www.facebook.com/..."}}

POST /api/seeding/analyze
{"ok":true,"target_id":"...","decision":"AUTO_READY","drafts":["..."],"confidence":0.96,"risk_level":"LOW","risk_labels":[]}

POST /api/seeding/result
{"ok":true,"target_status":"POSTED","summary":{"posted_count":1}}
```

The server must re-read global pause/campaign/shift/target state during `/analyze`; the extension cannot send `auto_allowed=true` and override server policy.

- [ ] **Step 7: Build the Jinja dashboard and sidebar link**

`seeding.html` includes:

- campaign create/edit form;
- approved claims/prohibited topics textareas as one item per line;
- `auto_submit` checkbox and threshold field with `min="0.85" max="1" step="0.01"`;
- template form with `intent`, `source_text`, and allowed claims;
- target bulk-import textarea;
- active shift controls;
- counters for READY/POSTED/REVIEW_REQUIRED/UNKNOWN/SKIPPED;
- recent activity table;
- prominent global pause control.

Add sidebar:

```html
<a href="/seeding" class="nav-item {{ 'nav-item--active' if page=='seeding' }}">
  <span class="nav-icon">✦</span><span class="nav-label">Seeding</span>
</a>
```

- [ ] **Step 8: Document only the token name in `.env.example`**

Add:

```text
# Token riêng cho Chrome extension Seeding -> ACP local API.
# Tự đặt giá trị ngẫu nhiên trong shared/.env.local; không commit token thật.
ACP_SEEDING_EXTENSION_TOKEN=
```

- [ ] **Step 9: Run web/domain tests and confirm GREEN**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= python3 -m acp.tests.test_seeding
```

Expected: all tests pass; no network request or real Facebook post occurs.

- [ ] **Step 10: Commit Task 3**

```bash
git add web/server.py web/templates/seeding.html web/templates/base.html web/static/acp.css .env.example tests/test_seeding.py
git commit -m "feat: add seeding dashboard and extension API"
```

---

### Task 4: Build the Facebook Chrome Extension with Fail-Closed Hybrid Execution

**Files:**
- Create: `extensions/facebook-seeding-assistant/manifest.json`
- Create: `extensions/facebook-seeding-assistant/parser.js`
- Create: `extensions/facebook-seeding-assistant/runner.js`
- Create: `extensions/facebook-seeding-assistant/background.js`
- Create: `extensions/facebook-seeding-assistant/content.js`
- Create: `extensions/facebook-seeding-assistant/tests/parser.test.cjs`
- Create: `extensions/facebook-seeding-assistant/README.md`

**Interfaces:**
- `parser.js`: `normalizeText(text)`, `extractPostContext(root, url)`, `findCommentComposer(root)`, `findSubmitControl(composer)`.
- `runner.js`: `shouldAttemptAutoSubmit(decision, status)`, `verifyObservedComment(root, expectedText)`.
- `background.js`: message types `ACP_GET_CONFIG`, `ACP_SET_CONFIG`, `ACP_API`.
- `content.js`: on supplied target pages, perform `next/analyze/fill/submit/verify/result`; inject review panel for non-auto cases.

- [ ] **Step 1: Write failing Node tests for pure parser/runner helpers**

Use Node's built-in `node:test` and `assert` so no npm dependency is needed:

```javascript
const test = require('node:test');
const assert = require('node:assert/strict');
const parser = require('../parser.js');
const runner = require('../runner.js');

test('normalizeText collapses whitespace', () => {
  assert.equal(parser.normalizeText('  xin  chào\n bạn  '), 'xin chào bạn');
});

test('auto submit requires server AUTO_READY and unpaused status', () => {
  assert.equal(runner.shouldAttemptAutoSubmit({decision:'AUTO_READY'}, {paused:false}), true);
  assert.equal(runner.shouldAttemptAutoSubmit({decision:'REVIEW_REQUIRED'}, {paused:false}), false);
  assert.equal(runner.shouldAttemptAutoSubmit({decision:'AUTO_READY'}, {paused:true}), false);
});

test('verification compares normalized visible text', () => {
  const fakeRoot = { textContent: 'Khác\nBạn có thể tham khảo Brand; hiện có tư vấn miễn phí.' };
  assert.equal(runner.verifyObservedComment(fakeRoot, 'Bạn có thể tham khảo Brand; hiện có tư vấn miễn phí.'), true);
});
```

- [ ] **Step 2: Run Node tests and confirm RED**

```bash
node --test extensions/facebook-seeding-assistant/tests/parser.test.cjs
```

Expected: FAIL because helper files do not exist.

- [ ] **Step 3: Implement pure parser and runner helpers**

Parser must prefer the current `div[role="article"]` and visible text, return normalized data only, and never serialize/store raw page HTML.

`findCommentComposer` may consider visible `[contenteditable="true"][role="textbox"]` candidates associated with the active article. If association is ambiguous, return `null` so the workflow pauses instead of acting on an arbitrary composer.

`findSubmitControl` may search for an enabled `button[type="submit"]` in the composer/form or an enabled nearby button with a comment/post semantic label. It must return `null` if ambiguous.

- [ ] **Step 4: Implement a minimal-permission MV3 manifest**

Use only:

```json
{
  "manifest_version": 3,
  "name": "ACP Facebook Seeding Assistant",
  "version": "0.1.0",
  "permissions": ["storage"],
  "host_permissions": [
    "https://www.facebook.com/*",
    "https://m.facebook.com/*",
    "http://127.0.0.1/*",
    "http://localhost/*"
  ],
  "background": {"service_worker": "background.js"},
  "content_scripts": [{
    "matches": ["https://www.facebook.com/*", "https://m.facebook.com/*"],
    "js": ["parser.js", "runner.js", "content.js"],
    "run_at": "document_idle"
  }]
}
```

Do not request `debugger`, proxy, cookies, webRequest interception, or broad `<all_urls>` permissions.

- [ ] **Step 5: Implement background configuration and ACP fetch proxy**

Store only:

```javascript
{ acpBaseUrl: 'http://127.0.0.1:5000', seedingToken: '...' }
```

Never read or store Facebook cookies/session data. `ACP_API` must attach `X-ACP-Seeding-Token` and return structured `{ok,status,data}` to the content script.

- [ ] **Step 6: Implement content-script orchestration**

Execution order:

```text
load target -> get ACP status -> compare current URL/queued target -> extract context
-> analyze -> render small status panel -> fill composer
-> if AUTO_READY: re-check status -> find unambiguous submit control -> click once
-> verify expected text is visible -> record POSTED or UNKNOWN -> advance
```

Rules:

- Never auto-submit when `findCommentComposer()` or `findSubmitControl()` is ambiguous.
- Never auto-submit if the current normalized URL does not correspond to the supplied target.
- Immediately before `.click()`, call `/api/seeding/status` again.
- If submit cannot be verified within a bounded retry window, report `UNKNOWN`; do not click submit a second time.
- If Facebook renders checkpoint/login/rate-restriction indicators, pause and show the operator panel; do not attempt bypass.
- No random timing, fingerprint manipulation, or anti-detection behavior.

- [ ] **Step 7: Implement the review panel**

Panel states:

```text
AUTO READY / SUBMITTING / VERIFYING
REVIEW REQUIRED
PAUSED
ERROR / UNKNOWN
```

For review-required targets show `risk_labels`, 2–3 drafts, editable final text, and buttons `Post reviewed comment`, `Skip`, `Pause shift`. Reviewed post uses the same single-submit + verification path and records `mode=reviewed`.

- [ ] **Step 8: Run Node tests and static manifest checks**

```bash
node --test extensions/facebook-seeding-assistant/tests/parser.test.cjs
python3 - <<'PY'
import json
from pathlib import Path
p = Path('extensions/facebook-seeding-assistant/manifest.json')
data = json.loads(p.read_text())
assert data['manifest_version'] == 3
assert 'debugger' not in data.get('permissions', [])
assert '<all_urls>' not in data.get('host_permissions', [])
print('MANIFEST_OK')
PY
```

Expected: tests PASS and `MANIFEST_OK`.

- [ ] **Step 9: Document local extension loading and safe dry-run**

README must tell the operator to:

1. start ACP locally;
2. set `ACP_SEEDING_EXTENSION_TOKEN` in `shared/.env.local`;
3. load the unpacked extension from `chrome://extensions`;
4. configure local ACP URL/token;
5. create a campaign with auto-submit OFF first;
6. use a non-production/test target to verify extraction/review UI;
7. enable auto-submit only after validating selectors and campaign rules.

- [ ] **Step 10: Commit Task 4**

```bash
git add extensions/facebook-seeding-assistant
git commit -m "feat: add Facebook seeding Chrome extension"
```

---

### Task 5: Add Shift Reporting, Manage.sh Coverage, and Operator Runbook

**Files:**
- Modify: `tests/test_seeding.py`
- Modify: `manage.sh`
- Modify: `tests/test_manage.py`
- Modify: `docs/ACP_RUNBOOK.md`

**Interfaces:**
- `manage.sh test` must execute `python -m acp.tests.test_seeding` under mock mode between existing Python suites and `run.py doctor`.
- Runbook must document only local configuration and controlled validation; it must not advise bypassing Facebook restrictions.

- [ ] **Step 1: Add failing KPI/report assertions**

Test an isolated shift with results `POSTED`, `REVIEW_REQUIRED -> POSTED reviewed`, `SKIPPED`, and `UNKNOWN`. Assert `shift_summary` returns exact counts and `auto_posted_count` vs `reviewed_posted_count` derived from activities.

Example expected object subset:

```python
self.assertEqual(2, summary["posted_count"])
self.assertEqual(1, summary["auto_posted_count"])
self.assertEqual(1, summary["reviewed_posted_count"])
self.assertEqual(1, summary["skipped_count"])
self.assertEqual(1, summary["unknown_count"])
```

- [ ] **Step 2: Run seeding tests and make report aggregation GREEN**

Implement missing aggregation in `core/seeding.py` only if the report tests expose a gap.

- [ ] **Step 3: Add a failing manage.sh integration expectation**

In `create_fake_release()` create:

```python
(app / "tests" / "test_seeding.py").write_text(
    "print('SEEDING_TEST_OK')\n", encoding="utf-8"
)
```

Add:

```python
def test_test_command_runs_seeding_suite(self):
    result = self.run_manage("test")
    self.assertIn("SEEDING_TEST_OK", result.stdout)
    self.assertIn("TEST_OK", result.stdout)
```

Run:

```bash
python3 tests/test_manage.py
```

Expected: FAIL because `manage.sh` does not invoke the new suite.

- [ ] **Step 4: Add `test_seeding` to `run_release_tests()`**

The mock-only block becomes:

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= "$release/.venv/bin/python" -m acp.tests.test_pipeline
ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= "$release/.venv/bin/python" -m acp.tests.test_pilot
ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= "$release/.venv/bin/python" -m acp.tests.test_seeding
```

- [ ] **Step 5: Run manage tests and confirm GREEN**

```bash
python3 tests/test_manage.py
```

Expected: PASS.

- [ ] **Step 6: Update `docs/ACP_RUNBOOK.md`**

Add a `Facebook Seeding Assistant` section covering:

```text
./manage.sh start
-> /seeding create campaign
-> keep auto-submit OFF for first selector check
-> import supplied Facebook target URLs
-> start shift
-> load/configure Chrome extension
-> validate review-mode target
-> only then enable campaign auto-submit when authorized
-> STOP NOW/global pause on checkpoint, wrong target, DOM uncertainty, or unexpected behavior
```

Explicitly state that test commands run in mock mode and do not post to Facebook.

- [ ] **Step 7: Commit Task 5**

```bash
git add core/seeding.py tests/test_seeding.py manage.sh tests/test_manage.py docs/ACP_RUNBOOK.md
git commit -m "test: cover seeding release workflow and reporting"
```

---

### Task 6: Full Verification and Review Readiness

**Files:**
- No intended production changes; only fix regressions found by verification.

**Interfaces:**
- All previous task interfaces must remain stable.

- [ ] **Step 1: Run the smallest focused Python suite**

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= python3 -m acp.tests.test_seeding
```

Expected: PASS, zero network/live Facebook calls.

- [ ] **Step 2: Run extension tests**

```bash
node --test extensions/facebook-seeding-assistant/tests/parser.test.cjs
```

Expected: PASS.

- [ ] **Step 3: Run manager integration tests because `manage.sh` changed**

```bash
python3 tests/test_manage.py
```

Expected: PASS.

- [ ] **Step 4: Run release-level ACP verification**

From the normal ACP deployment layout:

```bash
./manage.sh test
```

Expected: `test_pipeline`, `test_pilot`, `test_seeding`, and `run.py doctor` all pass under mock mode, ending with `TEST_OK`.

If the current execution environment lacks the deployment layout or dependencies, record that exact limitation and do not claim this step passed.

- [ ] **Step 5: Inspect diff and branch state**

```bash
git diff main...HEAD --stat
git diff main...HEAD -- core/seeding.py web/server.py extensions/facebook-seeding-assistant
git status --short
```

Confirm:

- no `.env.local`, database, token, cookie, browser profile, screenshot, or generated live data is present;
- no `debugger` permission, CAPTCHA solver, proxy rotation, random anti-detection timing, or account-rotation code exists;
- auto-submit is OFF by default and re-checks pause immediately before submit;
- unknown verification never retries submit automatically.

- [ ] **Step 6: Commit any verification-only fixes separately**

If verification required fixes, commit only those fixes with a message describing the concrete regression. If nothing changed, do not create an empty commit.

- [ ] **Step 7: Prepare branch for code review**

Summarize exact commands and PASS/FAIL output, list known limitations (especially live Facebook DOM validation), and keep the branch ready for a draft PR. Do not enable a live ACP adapter and do not execute a real Facebook post without explicit operator approval in the current task.
