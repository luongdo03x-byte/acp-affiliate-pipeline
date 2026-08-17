# Maplance Job Watcher Design

## Goal

Add a safe external-job watcher to ACP that automatically detects visible Maplance marketplace jobs in the operator's browser, normalizes and stores them in SQLite, applies operator filters, and surfaces eligible new jobs in ACP with an explicit **Open job** action.

This feature does **not** automatically claim a Maplance slot, submit a Google review, rotate Google accounts, spoof location, or attempt to bypass platform/Google controls.

## Context

ACP is Flask/Jinja2/SQLite with shared server-rendered UI and release-safe migrations. Shopee Phase 4 is the stacked base for this branch. Existing repo rules require mock-mode verification and forbid unapproved live publishing or secret leakage.

Maplance does not expose a documented public API for this workflow. Therefore ACP must not depend on guessed/private endpoints. The collector observes only job cards already rendered in the operator's authenticated browser session.

## Architecture

### 1. Generic external job core

Create `core/external_jobs.py` with focused responsibilities:

- normalize watcher rules (`min_reward`, `min_slots`, `locations`);
- idempotently upsert jobs by `(source, external_job_id)`;
- mark whether a row was first seen during the current ingest;
- calculate eligibility from stored job fields and watcher rules;
- persist one JSON watcher configuration under `system_setting.key = 'maplance_watcher'`;
- list recent jobs without coupling core logic to Maplance DOM details.

Add table `external_job` to `core/db.py`:

- `id TEXT PRIMARY KEY`
- `source TEXT NOT NULL`
- `external_job_id TEXT NOT NULL`
- `title TEXT NOT NULL`
- `job_url TEXT NOT NULL`
- `place_url TEXT`
- `reward INTEGER NOT NULL DEFAULT 0`
- `available_slots INTEGER NOT NULL DEFAULT 0`
- `min_local_guide INTEGER`
- `location TEXT`
- `status TEXT NOT NULL DEFAULT 'OPEN'`
- `raw_summary TEXT`
- `first_seen_at TEXT NOT NULL`
- `last_seen_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`
- unique constraint `(source, external_job_id)`

No production data is deleted or rebuilt for this feature.

### 2. Maplance adapter boundary

Create `adapters/maplance.py`.

The adapter accepts only already-rendered collector payloads. It validates:

- source URLs must be HTTPS URLs on `maplance.online` or a subdomain;
- reward/slot/Local Guide fields are bounded non-negative integers;
- text fields are trimmed and capped;
- an external ID is derived deterministically from the job URL when the page does not expose one.

The adapter must not authenticate to Maplance, scrape private API endpoints, reuse browser cookies, or implement a claim action.

### 3. Authenticated ACP workspace and tokenized ingest endpoint

Add:

- `GET /viec/maplance` — recent jobs, current watcher rules, eligible/new badges;
- `POST /viec/maplance/config` — save filters through existing CSRF/auth guard;
- `POST /api/maplance/jobs` — extension ingest endpoint protected by `ACP_MAPLANCE_INGEST_TOKEN` and disabled when the token is missing.

The ingest endpoint returns only aggregate counters (`received`, `stored`, `new`, `eligible_new`). It never returns ACP secrets or creates/claims remote jobs.

Add `/api/maplance/` to the public-prefix auth bypass only because browser extensions cannot carry ACP's dashboard session/CSRF reliably; the route itself enforces the dedicated ingest token using constant-time comparison.

### 4. Chrome/Chromium MV3 helper

Create `extensions/maplance-job-watcher/` with:

- `manifest.json`
- `parser.js`
- `content.js`
- `background.js`
- `options.html`
- `options.js`

Behavior:

1. On `https://maplance.online/*`, a `MutationObserver` scans visible card-like containers.
2. `parser.js` extracts a conservative normalized candidate from visible text/anchors.
3. Duplicate candidates are collapsed by job URL before transmission.
4. `background.js` sends batches to the configured ACP local URL with `X-ACP-Maplance-Token`.
5. When ACP reports `eligible_new > 0`, the extension creates a browser notification.

The helper never clicks Maplance buttons and contains no automation for claim/review/proof submission.

## Watcher rules

Defaults:

- `min_reward = 0`
- `min_slots = 1`
- `locations = []` (all locations)

Eligibility:

- reward must be `>= min_reward`;
- available slots must be `>= min_slots`;
- if locations is non-empty, normalized job location must contain one configured location string case-insensitively.

Unknown location is not eligible when a location filter is configured.

## UI

Add a sidebar entry **Việc Maplance** and a server-rendered workspace using existing ACP CSS classes.

The page shows:

- watcher status (token configured / disabled);
- filter form;
- counters for total visible, eligible, newly detected;
- recent-job table with reward, slots, location, Local Guide requirement and detection time;
- `Mở job` external link for explicit operator action.

No `Nhận job`, `Auto claim`, or equivalent control is added.

## Error handling

- Invalid collector rows are rejected individually and do not block valid rows in the same batch.
- Missing/invalid ingest token returns 401/503 without leaking token details.
- Malformed JSON returns 400.
- Database upserts are idempotent.
- Collector network failures stay local to the extension and are retried only on the next DOM change/periodic scan; there is no high-frequency polling loop.

## Testing

Python focused tests cover:

- URL allowlist validation;
- stable ID derivation;
- filter behavior;
- idempotent upsert and first-seen semantics;
- config round-trip.

Node focused tests cover:

- reward/slots/Local Guide extraction from Vietnamese visible text;
- candidate rejection when no Maplance job URL exists;
- deterministic de-duplication.

Static checks cover:

- MV3 manifest validity;
- absence of remote click/claim code;
- Python compilation of changed files.

Full Flask and release suites remain required on the ACP Ubuntu environment if Flask/Werkzeug are unavailable in the assistant sandbox.
