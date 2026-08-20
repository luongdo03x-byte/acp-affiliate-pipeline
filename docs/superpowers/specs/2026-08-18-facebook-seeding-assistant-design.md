# Facebook Seeding Assistant — Design Spec

**Date:** 2026-08-18  
**Branch:** `feat/facebook-seeding-assistant`  
**Status:** Design approved in chat; implementation not started

## 1. Context

ACP currently provides a Flask/Jinja2/SQLite operator application with existing dashboard, system settings, audit logging, Meta connections, and test infrastructure. This feature adds a Facebook-first seeding workflow for campaigns where the operator/company supplies a list of target Facebook URLs and approved campaign content.

The objective is to minimize repetitive work during a seeding shift while keeping the automation observable and stoppable. The browser extension operates only on target URLs already entered into ACP. It reads the rendered target context, asks ACP to classify and prepare a comment, and then either submits automatically or pauses for human review according to campaign-specific risk gates.

This design intentionally does **not** depend on ACP's generic post-content guard behavior. Seeding has its own policy/risk gate because the inputs, context, and publishing surface differ from ACP's normal channel publishing pipeline.

## 2. Confirmed product decisions

The following decisions were explicitly selected during design:

1. **Platform scope:** Facebook only for the MVP.
2. **Target source:** operator/company supplies target URLs; no automated target discovery in MVP.
3. **Execution mode:** hybrid.
   - High-confidence, low-risk targets may auto-submit when the campaign explicitly enables it.
   - Risky, ambiguous, negative, sensitive, or low-confidence targets stop for review.
4. **Comment source:** hybrid template-first generation.
   - Prefer company-provided templates/claims.
   - Adapt the template to the current context.
   - Generate from the campaign brief only when no suitable template exists.
5. **Architecture:** ACP backend plus a thin Chrome Manifest V3 extension.

## 3. Goals

### Primary goals

- Import and manage an ordered queue of Facebook target URLs.
- Store campaign brief, approved templates, and approved claims.
- Start/end a seeding shift and track progress/KPIs.
- Read only the rendered context necessary for the current target.
- Classify target intent and risk.
- Select an approved template when possible.
- Generate a context-appropriate comment without inventing personal experience or unsupported claims.
- Auto-submit only when all configured gates pass.
- Pause for review when a gate fails.
- Verify whether the comment appeared before marking the target complete.
- Record activity, execution mode, result, and proof/reference when available.
- Provide a global kill switch that stops future automatic submits immediately.

### Success criteria for the MVP

The MVP is successful when an operator can:

1. create a campaign;
2. add templates and approved claims;
3. import target URLs;
4. start a shift;
5. run the extension through the queue;
6. see low-risk targets processed with minimal interaction;
7. see risky targets paused with a clear reason and editable drafts;
8. stop automation instantly;
9. finish with an accurate activity/KPI report;
10. run all tests without posting to real Facebook.

## 4. Non-goals

The MVP will not implement:

- automatic discovery/search of Facebook posts or groups;
- bulk account creation, account rotation, or profile farming;
- fingerprint spoofing, anti-detection, CAPTCHA solving, checkpoint bypass, or rate-limit evasion;
- proxy/VPN rotation for account evasion;
- fake reviews, fabricated customer testimonials, or fabricated first-person experience;
- automated engagement intended to impersonate independent customers;
- crawling Facebook outside the current user-visible target page;
- storage of Facebook passwords, cookies, session tokens, or browser profiles in ACP;
- TikTok, Threads, Instagram, or other platforms in the MVP;
- production posting during automated test runs.

If Facebook displays login verification, checkpoint, rate restriction, or another trust/safety intervention, the system pauses instead of trying to bypass it.

## 5. Architecture

```text
ACP Dashboard
  ├── Campaigns
  ├── Templates / approved claims
  ├── Target queue
  ├── Shift / KPI
  ├── Activity log
  └── Global pause / kill switch
          │
          ▼
ACP Seeding API
  ├── extension status
  ├── next target
  ├── analyze context
  ├── prepare comment
  ├── record result
  └── pause state
          ▲
          │ dedicated local token
          ▼
Chrome MV3 Extension
  ├── navigate to supplied target
  ├── extract visible target context
  ├── request decision + draft
  ├── fill comment composer
  ├── auto-submit when allowed
  ├── pause/review when required
  ├── verify result
  └── request next target
```

### Responsibility boundary

**ACP owns:** campaign data, templates, claims, target ordering, content generation, risk decision, confidence threshold, audit/KPI state, kill switch, and final activity state.

**Extension owns:** browser navigation, minimal DOM extraction, comment composer interaction, submission interaction, DOM verification, and the review side panel.

The extension does not make policy decisions independently. It executes the decision returned by ACP and must re-check the global pause state immediately before a submit action.

## 6. Core modules

### 6.1 `core/seeding.py`

A focused domain module that contains:

- campaign CRUD helpers;
- template retrieval/selection;
- target queue transitions;
- shift state calculations;
- intent classification orchestration;
- draft preparation;
- deterministic risk gate;
- auto-submit eligibility decision;
- activity recording;
- KPI aggregation.

It must not contain browser DOM selectors or Facebook-specific DOM manipulation.

### 6.2 LLM integration

Reuse the project's existing LLM/Gemini abstraction where practical, but expose a seeding-specific structured request/response contract.

Expected normalized response:

```json
{
  "intent": "recommendation_request",
  "draft": "...",
  "confidence": 0.94,
  "risk_labels": [],
  "template_id": "...",
  "claims_used": ["free_consultation"]
}
```

The server validates this response. Model output alone never authorizes auto-submit.

### 6.3 Chrome extension

Location:

```text
extensions/facebook-seeding-assistant/
```

Manifest V3 components:

- `manifest.json`
- background/service worker
- content script for Facebook pages
- side-panel or injected operator panel
- shared parser/helpers
- fixture-based JavaScript tests

The extension communicates only with the configured local ACP URL and does not upload Facebook context to any service other than ACP's configured generation provider via the ACP backend.

## 7. Target lifecycle

Normalized target statuses:

```text
READY
  ↓
OPENING
  ↓
ANALYZING
  ↓
PREPARED
  ├── AUTO_READY
  └── REVIEW_REQUIRED
        ↓
SUBMITTING
        ↓
VERIFYING
  ├── POSTED
  ├── UNKNOWN
  ├── SKIPPED
  └── UNAVAILABLE
```

Rules:

- A target must never be submitted twice automatically.
- `UNKNOWN` is terminal for automatic execution until a human explicitly retries it.
- `UNAVAILABLE` is used when the supplied target cannot be loaded or no usable target content exists.
- A manually reviewed target records `mode=reviewed` even if submission after review is performed by the extension.

## 8. Per-target flow

```text
1. ACP returns next READY target.
2. Extension opens exact supplied URL.
3. Extension waits for the relevant rendered post area.
4. Extension extracts minimum necessary visible context.
5. Extension sends target ID + context to ACP.
6. ACP normalizes context.
7. ACP classifies intent.
8. ACP selects the best enabled company template, if any.
9. ACP prepares a context-specific draft.
10. ACP validates claims and checks duplication.
11. ACP computes risk labels and confidence.
12. ACP evaluates the deterministic auto-submit gate.
13a. If eligible: extension fills composer, re-checks pause state, submits, verifies, records result, advances.
13b. If not eligible: extension shows reason + draft choices; human may edit, submit, or skip.
14. ACP records activity and updates shift KPI.
```

## 9. Context extraction

The extension may extract only the minimum content needed to understand the current target, such as:

- target post text;
- visible target/post identifier or permalink when available;
- group/page name if visibly rendered and useful for context;
- current URL;
- limited nearby visible text necessary to disambiguate the target.

The MVP does not crawl timelines, enumerate group membership, harvest profile data, or scan unrelated posts.

The parser should return a normalized object rather than raw HTML:

```json
{
  "url": "https://www.facebook.com/...",
  "post_text": "...",
  "surface_name": "...",
  "post_ref": "..."
}
```

Raw DOM snapshots are not persisted in the production database.

## 10. Comment generation strategy

### 10.1 Template-first path

1. classify intent;
2. retrieve enabled templates matching the intent;
3. choose the best template;
4. adapt wording to the target context;
5. preserve the semantic meaning of approved claims;
6. validate the draft before eligibility evaluation.

### 10.2 Brief fallback path

Used only if no suitable template exists. The generator receives:

- campaign brief;
- allowed claims;
- prohibited claims/topics;
- target context;
- disclosure/promotion policy;
- explicit instruction not to invent personal use or first-person experience.

If a safe draft cannot be produced, the response is `REVIEW_REQUIRED` or `SKIP_RECOMMENDED`; it is never auto-submitted.

### 10.3 Duplicate protection

ACP compares the proposed draft against recent comments in the same campaign. The implementation may use normalized text similarity. A draft considered too close to a recent comment is regenerated or requires review.

Duplicate protection is a quality feature, not an evasion mechanism; it must not introduce artificial timing, fingerprint, or anti-detection behavior.

## 11. Auto-submit gate

Auto-submit is permitted only when **all** conditions below are true:

```text
system kill switch is OFF
AND active shift exists
AND campaign is ACTIVE
AND campaign.auto_submit = true
AND target.status is PREPARED
AND target URL is the supplied queued URL
AND risk_level = LOW
AND confidence >= campaign.confidence_threshold
AND approved template was used OR generated fallback passed claim validation
AND every factual/brand claim is present in campaign allowed claims
AND draft contains no disallowed first-person experience/testimonial claim
AND draft is not too similar to recent campaign comments
AND no sensitive/complaint/escalation label is present
```

### Defaults

- New campaign `auto_submit`: **OFF**.
- Default confidence threshold: **0.90**.
- Operators may raise the threshold per campaign.
- Lowering the threshold below `0.85` is rejected by the MVP API/UI.

The explicit campaign toggle is the operator's authorization boundary. Test fixtures always force auto-submit off or use a fake DOM harness; automated tests never publish externally.

## 12. Risk model

Risk is deterministic after model classification. The model may suggest labels, but ACP owns the final mapping.

### Mandatory review / no auto-submit labels

Examples include:

- negative brand context;
- complaint or refund dispute;
- legal threat or accusation;
- medical complication/adverse event;
- fraud/scam allegation;
- ambiguous target meaning;
- unsupported pricing or efficacy claim;
- request requiring personal customer experience;
- first-person testimonial language not explicitly supplied as truthful operator content;
- sensitive personal data;
- model uncertainty;
- target mismatch;
- composer/DOM uncertainty.

A mandatory-review label always overrides confidence.

## 13. Review panel

When review is required, the extension shows:

- target status;
- reason(s) automation stopped;
- context summary;
- 2–3 draft options when available;
- editable final comment field;
- `Post reviewed comment`;
- `Skip`;
- `Pause shift`.

Human review never silently enables automatic mode for future targets.

## 14. Kill switch and pause behavior

Two levels:

1. **Global seeding pause** in `system_setting` — blocks all extension auto-submit actions.
2. **Shift pause** — stops processing the current shift without modifying campaign configuration.

The extension must check pause state:

- when it starts;
- before requesting the next target;
- immediately before submit.

If ACP becomes unreachable before submit, fail closed: do not submit automatically.

## 15. Data model

Additive SQLite schema changes only.

### `seeding_campaign`

```text
id                    TEXT PRIMARY KEY
name                  TEXT NOT NULL
brand                 TEXT
brief                 TEXT NOT NULL
allowed_claims        TEXT NOT NULL DEFAULT '[]'      -- JSON
prohibited_topics     TEXT NOT NULL DEFAULT '[]'      -- JSON
disclosure_policy     TEXT
status                TEXT NOT NULL DEFAULT 'ACTIVE'
auto_submit           INTEGER NOT NULL DEFAULT 0
confidence_threshold  REAL NOT NULL DEFAULT 0.90
created_at            TEXT NOT NULL
updated_at            TEXT NOT NULL
```

### `seeding_template`

```text
id              TEXT PRIMARY KEY
campaign_id     TEXT NOT NULL REFERENCES seeding_campaign(id)
intent          TEXT NOT NULL
source_text     TEXT NOT NULL
allowed_claims  TEXT NOT NULL DEFAULT '[]'            -- JSON
enabled         INTEGER NOT NULL DEFAULT 1
created_at      TEXT NOT NULL
updated_at      TEXT NOT NULL
```

### `seeding_target`

```text
id                TEXT PRIMARY KEY
campaign_id       TEXT NOT NULL REFERENCES seeding_campaign(id)
url               TEXT NOT NULL
position          INTEGER NOT NULL DEFAULT 0
status            TEXT NOT NULL DEFAULT 'READY'
context_summary   TEXT
intent            TEXT
risk_level        TEXT
risk_labels       TEXT NOT NULL DEFAULT '[]'          -- JSON
confidence        REAL
last_error        TEXT
created_at        TEXT NOT NULL
updated_at        TEXT NOT NULL
completed_at      TEXT
UNIQUE(campaign_id, url)
```

### `seeding_activity`

```text
id                TEXT PRIMARY KEY
target_id         TEXT NOT NULL REFERENCES seeding_target(id)
shift_id          TEXT REFERENCES seeding_shift(id)
action            TEXT NOT NULL
intent            TEXT
template_id       TEXT REFERENCES seeding_template(id)
generated_text    TEXT
final_text        TEXT
mode              TEXT                              -- auto | reviewed
result            TEXT
proof_ref         TEXT
error_detail      TEXT
created_at        TEXT NOT NULL
```

### `seeding_shift`

```text
id                TEXT PRIMARY KEY
campaign_id       TEXT NOT NULL REFERENCES seeding_campaign(id)
status            TEXT NOT NULL DEFAULT 'ACTIVE'
started_at        TEXT NOT NULL
ended_at          TEXT
target_count      INTEGER NOT NULL DEFAULT 0
posted_count      INTEGER NOT NULL DEFAULT 0
review_count      INTEGER NOT NULL DEFAULT 0
skipped_count     INTEGER NOT NULL DEFAULT 0
unknown_count     INTEGER NOT NULL DEFAULT 0
```

Use existing `audit_log` for operator configuration events such as enabling/disabling auto-submit, changing threshold, starting/pausing a shift, and global pause changes.

## 16. API design

All extension endpoints use a dedicated token, not the dashboard session cookie.

Environment/config:

```text
ACP_SEEDING_EXTENSION_TOKEN=<secret>
```

Extension request header:

```text
X-ACP-Seeding-Token: <token>
```

The token must never be logged.

### Dashboard routes

Suggested routes:

```text
GET  /seeding
GET  /seeding/campaign/<id>
POST /seeding/campaign
POST /seeding/campaign/<id>/config
POST /seeding/campaign/<id>/templates
POST /seeding/campaign/<id>/targets/import
POST /seeding/campaign/<id>/shift/start
POST /seeding/shift/<id>/pause
POST /seeding/global-pause
```

### Extension API

Suggested JSON endpoints:

```text
GET  /api/seeding/status
POST /api/seeding/next-target
POST /api/seeding/analyze
POST /api/seeding/result
POST /api/seeding/review-result
```

The final implementation may consolidate endpoints if existing Flask patterns make that cleaner, but the responsibilities must remain explicit.

## 17. Dashboard UI

Add one sidebar entry: **Seeding**.

MVP screen responsibilities:

- campaign list/status;
- create/edit campaign brief;
- approved claims and prohibited topics;
- template management;
- auto-submit toggle;
- confidence threshold;
- target URL bulk import via textarea and optional CSV;
- queue counts by status;
- start/pause/end shift;
- current shift counters;
- recent activities/errors;
- global pause control.

No separate SPA framework is introduced; follow the project's existing Flask/Jinja2 conventions.

## 18. Proof and reporting

After a successful submission, the extension attempts to identify the rendered comment and returns the best available reference:

1. comment permalink/reference when available;
2. otherwise a stable rendered identifier when available;
3. otherwise a verification summary indicating text was observed.

Do not store a full-page screenshot by default. Screenshot support is deferred unless the job's real reporting process proves it necessary.

A shift report derives from `seeding_activity` and `seeding_shift`, not from browser local state.

## 19. Failure handling

### Target unavailable

- mark `UNAVAILABLE`;
- record error;
- advance to next target.

### Post rendered but parser cannot identify a safe context

- mark `REVIEW_REQUIRED`;
- do not guess a DOM target;
- wait for operator action.

### Comment composer not found

- do not click arbitrary inputs;
- set target to review/error state;
- record parser diagnostic category.

### LLM unavailable or malformed response

- use deterministic template fallback only when a matching approved template can be used without inventing context-specific facts;
- otherwise require review.

### Submit action initiated but verification fails

- mark `UNKNOWN`;
- do not immediately retry automatically;
- require operator inspection to avoid duplicate comments.

### Facebook checkpoint/login/restriction detected

- pause the shift/global execution path;
- surface a clear operator message;
- do not attempt bypass or automated recovery.

### ACP unreachable

- fail closed before auto-submit;
- extension may keep local display state, but cannot publish automatically without a fresh authorization decision.

## 20. Security and privacy

- Do not persist Facebook cookies, passwords, or browser session tokens in ACP.
- Do not log extension authorization tokens.
- Restrict extension API to the configured token and expected local origin/use case.
- Sanitize text shown in Jinja templates.
- Treat target text as untrusted input.
- Do not execute content extracted from Facebook as HTML/JavaScript.
- Keep stored context minimal.
- Do not include production secrets in fixtures.

## 21. Testing strategy

### Python unit tests

Add focused tests for:

- campaign configuration validation;
- threshold floor;
- target import and deduplication;
- target state transitions;
- template selection;
- approved-claim validation;
- prohibited first-person/testimonial detection;
- risk override of high confidence;
- duplicate-text gate;
- global pause gate;
- fail-closed behavior;
- `UNKNOWN` no-auto-retry behavior;
- shift KPI aggregation.

### Flask/contract tests

Verify:

- dashboard routes render;
- extension endpoints reject missing/invalid token;
- API request/response shapes;
- auto-submit remains disabled for new campaigns;
- system pause blocks an otherwise eligible decision.

### Extension tests

Use static HTML fixtures that mimic only the DOM shapes needed by the parser/composer adapter. Test:

- target context extraction;
- composer discovery;
- fill behavior;
- review-mode behavior;
- pause-before-submit behavior;
- successful rendered verification;
- verification failure -> `UNKNOWN`;
- parser failure -> no arbitrary click.

No test logs into Facebook or publishes a real comment.

### Release verification

Follow repository policy:

```text
ACP_ADAPTER=mock
./manage.sh test
```

Any extension-specific Node/JavaScript test command added by implementation must also be documented and run before completion.

## 22. Rollout plan

### Stage 1 — dry-run

Extension reads supplied targets and returns context/drafts/decision only. It does not fill or submit.

### Stage 2 — fill-only

Extension fills the comment composer but never submits automatically.

### Stage 3 — reviewed submit

Operator confirms every submission. Verification/proof/KPI pipeline is validated.

### Stage 4 — hybrid auto-submit

Operator explicitly enables `campaign.auto_submit`. Only low-risk targets above threshold auto-submit; all others pause.

This rollout does not require separate production code paths: the same decision engine is used with stricter execution mode settings.

## 23. Expected files

Likely implementation changes:

```text
core/db.py
core/seeding.py
web/server.py
web/templates/base.html
web/templates/seeding.html
.env.example
extensions/facebook-seeding-assistant/manifest.json
extensions/facebook-seeding-assistant/background.js
extensions/facebook-seeding-assistant/content.js
extensions/facebook-seeding-assistant/panel.*
extensions/facebook-seeding-assistant/lib/*
extensions/facebook-seeding-assistant/tests/*
tests/test_seeding.py
```

Exact JavaScript file split may change during implementation, but ACP domain logic and DOM automation must remain separated.

## 24. Acceptance criteria

The feature is complete only when all of the following are demonstrated in tests or controlled dry-run/fixture execution:

1. Target URLs can be imported and deduplicated.
2. A shift can be started and paused.
3. Extension retrieves only queued targets.
4. Current Facebook target context is normalized successfully from fixture DOM.
5. Template-first generation uses only approved campaign claims.
6. A high-confidence target with a mandatory-risk label cannot auto-submit.
7. A low-risk target above threshold becomes auto-eligible only when campaign auto-submit is explicitly enabled and global pause is off.
8. Extension checks pause state immediately before submit.
9. A successful fixture submit is recorded once and advances the queue.
10. An unverifiable submit becomes `UNKNOWN` and is not automatically retried.
11. A checkpoint/restriction-like fixture pauses execution rather than bypassing it.
12. Review mode supports edit/post/skip paths.
13. Activity records distinguish `auto` from `reviewed` execution.
14. Shift KPI/report data is derived from persisted activity.
15. No test publishes to real Facebook.
16. Repository test suite is run in mock mode before completion.

## 25. Deferred follow-ups

Only after the Facebook MVP is validated:

- operator-approved target discovery;
- richer reply/lead tracking;
- optional reporting screenshots if actually required;
- TikTok/Threads adapters using the same normalized seeding domain;
- per-client reusable campaign presets;
- performance/earning analytics.

These are deliberately out of the first implementation plan.
