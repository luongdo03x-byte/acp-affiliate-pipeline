# Threads Tester Batch Automation Design

## Goal

Run a Factory V2 batch of N accounts (initially 5) up to `THREADS_CREATED`, pause once so the
operator can add every pending username as a Threads Tester in the Meta App Dashboard in a single
sitting, then automatically accept the invitations on each account's device and drive the existing
account-bound OAuth flow through to `ACP_ACTIVE` without further human input.

## Background

Meta exposes no Graph API for the Threads Tester role. While an app is unpublished, only accounts
holding that role can complete the Threads authorization window, and the invitation must be accepted
from the invited account's own Threads session. Meta's Threads "Get started" documentation confirms the role is
dashboard-only; the acceptance surface exists in both the Threads website and the Threads mobile app,
under Settings > Account > Website permissions > Invites.

The controller already models both milestones. `core/factory_v2/threads_onboarding.py` persists
`tester_invited_at` and `tester_accepted_at` and derives `NEEDS_TESTER_INVITE`,
`NEEDS_TESTER_ACCEPT`, `READY_FOR_OAUTH`. `web/threads_onboarding.py` exposes an operator wizard over
those milestones. `FactoryControllerRuntime._start_activation()` already opens the authorization URL
on the account's own device and parks the job in `WAITING_HUMAN` for the final tap.

Two gaps remain, both observed in production on 2026-09-09:

1. Nothing enforces the milestones. `core/factory_v2/oauth_bridge.start_account_oauth()` accepts any
   account in `THREADS_CREATED` or an OAuth `RETRY_PENDING`, so a direct call to
   `POST /api/factory/v2/accounts/<id>/oauth/start` starts a session for an account that was never
   invited. Account `phuongthao.pham26` was driven this way with both milestone columns empty; the
   authorization window returned Meta's generic `error_code 1` and the account fell back to
   `RETRY_PENDING`.
2. Every account still costs two human actions: accepting the invitation and tapping the
   authorization button.

## Approved behavior

### Tester gate before activation

An account reaching `THREADS_CREATED` no longer flows straight into activation. The runtime starts
activation only when `tester_accepted_at` is set.

When the runtime encounters an account at `THREADS_CREATED` without `tester_accepted_at`, it opens a
per-account checkpoint of type `TESTER_INVITE` with status `WAITING_EXTERNAL` and releases the job,
preserving the account stage.

The checkpoint is per-account rather than per-batch because `factory_checkpoint.account_id` is
`NOT NULL`. Aggregation into a single operator action happens in the dashboard, not in the schema, so
this design needs no migration.

The checkpoint opens for the first eligible account rather than waiting for the batch to reach
`target_count`. A batch that partially fails still produces a usable invite list.

A batch of five accounts requires no new configuration beyond `factory_batch.target_count = 5`.

### Dashboard invite list

`web/threads_onboarding.py` gains a batch-scoped section listing every account with an open
`TESTER_INVITE` checkpoint, grouped by `batch_id`:

- the pending usernames, in a form that can be copied as one block;
- a link to the Meta App Roles page, from the existing `META_APP_TESTERS_URL` environment variable;
- a single button, "Đã thêm trên Meta", scoped to one batch.

That button resolves every open `TESTER_INVITE` checkpoint for the batch with resolution
`TESTER_INVITED` and calls `mark_tester_invited()` for each listed account. It records only that the
operator says the invitations were sent; it does not claim acceptance.

### On-device invitation acceptance

A new worker action, `ACCEPT_THREADS_TESTER`, runs a new flow in
`core/factory_v2/ui_automation/threads/`. The flow drives the already-authenticated Threads app:
Settings, Account, Website permissions, Invites, then the accept confirmation.

New screen signatures and selectors:

- `THREADS_SETTINGS`
- `THREADS_WEBSITE_PERMISSIONS`
- `THREADS_TESTER_INVITE_LIST`
- `THREADS_TESTER_INVITE_CONFIRM`

All four sit below the existing protected-screen band in the detector's priority order, so a password,
OTP, CAPTCHA, contact-verification, identity-check, security-challenge or security-consent screen
still wins and still hands control back to a human.

Outcomes:

- Accept confirmed: call `mark_tester_accepted()` and continue to activation in the same tick.
- Invite list reached but empty: leave the account at `NEEDS_TESTER_ACCEPT` and open a fresh
  `TESTER_INVITE` checkpoint carrying a message that the Meta-side invitation is missing. Do not retry
  blindly.
- Any unrecognized screen: stop, report, leave the account untouched.

### On-device authorization consent

`_start_activation()` keeps its current `OPEN_URL` step. Instead of parking in `WAITING_HUMAN`
immediately, it issues a new action, `CONFIRM_THREADS_OAUTH`, which detects the Threads authorization
window and taps the approval control.

A new signature `THREADS_OAUTH_CONSENT` matches that window. It is deliberately distinct from
`CONSENT_WITH_SECURITY_IMPACT` and ranks below it, so a two-factor or security-settings screen is
never mistaken for an authorization prompt.

If the consent screen is not detected within three polls, the job falls back to exactly today's
behavior: `WAITING_HUMAN`, `desired_action='WAIT_ACP'`, and the existing `ACP_OAUTH` checkpoint. The
flow never taps a control it has not positively identified.

### API guard

`start_account_oauth()` raises `ValueError` when the account has no `tester_accepted_at`.
`POST /api/factory/v2/accounts/<id>/oauth/start` maps that to HTTP 409 with a message naming the
missing milestone. This closes the path that produced the 2026-09-09 failure.

`FactoryActivationService.start()` inherits the guard through `start_account_oauth()`.

## Data model

No schema migration. The design uses:

- `factory_account.tester_invited_at`, `factory_account.tester_accepted_at` (existing)
- `factory_checkpoint` rows with `type='TESTER_INVITE'` (new value, existing table)
- `factory_batch.target_count` (existing)

## Testing

All tests run offline with `ACP_ADAPTER=mock`, a fake driver, and a fake OAuth provider. No test
touches a real device, the Meta API, or the production database.

1. Gate: an account at `THREADS_CREATED` without `tester_accepted_at` opens a `TESTER_INVITE`
   checkpoint and does not start an OAuth session.
2. Guard: `start_account_oauth()` raises, and the endpoint returns 409, when `tester_accepted_at` is
   unset; the existing accepted-path tests still pass.
3. Dashboard: the batch button resolves every open `TESTER_INVITE` checkpoint in its batch and sets
   `tester_invited_at` for each account, and touches no account outside that batch.
4. Accept flow: confirmed acceptance sets `tester_accepted_at`; an empty invite list leaves the
   milestone unset and opens a fresh checkpoint; a protected screen aborts the flow.
5. Consent flow: a detected consent screen taps once and proceeds; an undetected screen falls back to
   `WAITING_HUMAN` with the `ACP_OAUTH` checkpoint intact.
6. Priority: protected-screen signatures outrank both new screen families.

## Risks

Meta UI changes break the selectors. The failure mode is a stopped flow and an operator checkpoint,
not a misdirected tap, because every new flow acts only on positively identified screens.

Bulk account creation combined with automated permission granting conflicts with Meta's platform
terms. App suspension and account bans are real outcomes. The operator was told this on 2026-09-09
and 2026-09-10 and chose to proceed; it is recorded here so the trade-off stays visible to anyone
reading this design later.

Publishing the app through App Review removes the tester requirement entirely and makes this whole
subsystem unnecessary. Current Meta review cycles run about twenty days per submission, and a review
of this use case is unlikely to pass in its present form. This design is the pragmatic path while the
app stays unpublished, not the endpoint.

## Out of scope

- Automating the Meta App Dashboard invitation itself. No API exists, and browser automation against
  the operator's own admin account is not worth its fragility.
- App Review preparation.
- Any change to Instagram flows, the scheduler, or the runner gateway.
