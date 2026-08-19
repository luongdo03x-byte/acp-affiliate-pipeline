# Account Factory V2 — Deterministic Instagram Username Fallback Design

Date: 2026-08-19
Branch: `feat/account-factory-android`
Status: Pending user review
Base design: `docs/superpowers/specs/2026-08-18-account-factory-social-max-auto-design.md`

## 1. Goal

Extend the existing fail-closed Instagram signup flow so an unavailable generated username does not force a human checkpoint immediately.

The desired behavior is:

```text
IG_USERNAME_ENTRY
  -> enter requested username
  -> Instagram validates it
  -> if available: Next
  -> if unavailable: try a bounded deterministic fallback candidate
  -> when a candidate is accepted: Next
  -> persist the accepted username back to factory_account.username
  -> continue normal signup
```

The database must end with the username that the automation actually selected in Instagram. It is not acceptable for the UI to continue with one username while `factory_account.username` retains another.

This delta does not change the protected/human boundaries from the base design. Password, OTP, CAPTCHA, identity/security challenges, and the final official Instagram account-creation submit remain human-only.

## 2. Evidence and current failure

The Android 15 Instagram hierarchy for the username step exposes a normal valid state like:

```text
Create a username
android.widget.EditText text="dragon.3275826"
content-desc="Username,dragon.3275826"
content-desc="Input Username is valid."
android.widget.Button content-desc="Next"
```

After automation enters an unavailable requested username, the hierarchy exposes:

```text
Create a username
android.widget.EditText text="baongocd"
content-desc="Username,dragon.3275826"   # accessibility description may be stale
The username baongocd is not available.
```

The visual suggestion rows shown by Instagram are not exposed as usable accessibility nodes in the captured hierarchy. Therefore this design must not click those visual suggestions by coordinates.

The current flow verifies text-entry using `UiNode.text`, which is correct for this screen; it must not treat the stale `content-desc` as the authoritative current username value.

## 3. Non-negotiable safety rules

The fallback flow must:

- use only positively identified Instagram username UI;
- never click visual suggestion rows by fixed coordinates;
- never loop indefinitely;
- never continue testing candidates after rate-limit/action-blocked/security signals;
- never automate passwords, OTPs, CAPTCHA, recovery, identity checks, or final signup submit;
- never use username probing to evade platform rate limits or security controls;
- fail closed if the expected validation state does not appear;
- keep the AVD open when a human checkpoint is required.

Username availability is treated as ordinary profile/signup validation, not a protected credential or security challenge.

## 4. Screen model

Keep `IG_USERNAME_ENTRY` as the known ordinary username-entry screen and add two explicit validation states.

### 4.1 `IG_USERNAME_VALID`

Positive signature:

```text
package = com.instagram.android
Create a username marker
+ username EditText
+ Input Username is valid. marker
+ clickable Continue/Next
```

The `Input Username is valid.` marker is important after changing the field because it prevents a race where the old `Next` button remains briefly visible before Instagram finishes server-side validation.

### 4.2 `IG_USERNAME_UNAVAILABLE`

Positive signature:

```text
package = com.instagram.android
Create a username marker
+ username EditText
+ dynamic text containing "username" and "is not available"
```

The unavailable text contains the attempted username, so exact-text matching is insufficient. Add an optional normalized substring matcher to the generic `Selector` model, with empty defaults so all existing selectors retain their current semantics.

The substring matcher is used narrowly for the username-unavailable marker. The full screen signature still requires the create-username context and username input, so a generic `not available` message elsewhere cannot trigger this state by itself.

Priority order must ensure:

```text
protected/security screens
  > errors/rate limit
  > IG_USERNAME_UNAVAILABLE / IG_USERNAME_VALID
  > generic IG_USERNAME_ENTRY
  > UNKNOWN
```

## 5. Deterministic fallback candidates

Add a pure candidate generator in the identity/profile layer, not in the ADB driver.

Input:

```text
requested username
stable account identifier
maximum candidate count
```

Output: an ordered tuple of fallback usernames.

The generator must be:

- deterministic across process restarts;
- bounded;
- free of random/runtime-clock dependence;
- limited to the same conservative username character set already used by generated profiles;
- length-bounded so suffixing cannot produce oversized values;
- stable for a given account, so retrying a job does not create an unbounded stream of new names.

Initial implementation:

1. retain a normalized/truncated prefix derived from the requested username;
2. derive a stable numeric suffix from a cryptographic hash of `account_id + attempt_index`;
3. generate at most five fallback candidates;
4. de-duplicate candidates and skip the original requested username.

Example shape only:

```text
requested: baongocd
fallbacks: baongocd483102, baongocd071944, ...
```

Exact suffix values are implementation details and must be covered by deterministic unit tests rather than relied on by UI code.

The flow may test the requested username plus at most five fallback candidates in one signup episode. If all bounded candidates are unavailable, stop with `USERNAME_UNAVAILABLE`; do not generate more candidates in a tight loop.

## 6. Username validation algorithm

For `IG_USERNAME_ENTRY`:

```text
set requested username
wait for one of:
  IG_USERNAME_VALID
  IG_USERNAME_UNAVAILABLE
  protected/security state
  rate-limit/action-blocked/network/error state
  timeout/unknown
```

If `IG_USERNAME_VALID`:

```text
tap known Next
verify known successor
continue signup
```

If `IG_USERNAME_UNAVAILABLE`:

```text
for candidate in deterministic_fallbacks(max=5):
    set candidate
    wait for terminal validation state

    if IG_USERNAME_VALID:
        tap known Next
        verify known successor
        return selected username to Controller

    if IG_USERNAME_UNAVAILABLE:
        continue to next bounded candidate

    if protected/security/rate-limit/error:
        stop immediately using existing policy

    if timeout/unknown:
        fail closed

all candidates unavailable:
    needs_confirmation / USERNAME_UNAVAILABLE
```

A new candidate must not be sent until Instagram has produced a terminal validation result for the previous candidate.

## 7. Driver behavior

Do not add a username-specific mutation primitive to ADB.

Reuse:

```text
SafeUiDriver.set_text(...)
SafeUiDriver.wait_for(...)
SafeUiDriver.tap(...)
```

`set_text` continues to verify the actual input using `UiNode.text`.

`wait_for` is used after each username change to observe server-side availability validation. The expected set includes only known username validation states plus existing protected/error states.

No screenshot/OCR or coordinate-based fallback is introduced.

## 8. Flow result contract

`FlowResult` currently returns only status/screen/reason/last-safe-step. Extend it additively with an optional sanitized profile-update map:

```python
profile_updates: dict[str, str] | None = None
```

For this delta the only allowed key is:

```text
username
```

Example successful fallback response:

```json
{
  "status": "running",
  "screen": "IG_USERNAME_VALID",
  "reason": null,
  "last_safe_step": "IG_USERNAME_ENTRY",
  "profile_updates": {
    "username": "baongocd483102"
  }
}
```

The worker must not use this channel for password, OTP, contact data, arbitrary DB fields, or security information.

`profile_updates` is emitted only after:

1. Instagram positively reports the candidate valid;
2. the known Next action succeeds and reaches a known successor screen.

If the candidate was merely typed but Next did not complete, no profile update is emitted.

## 9. Worker protocol sanitation

`WorkerAgent._flow_response` must sanitize `profile_updates` before putting it on the JSON-lines response.

Rules:

- only `username` is accepted;
- value must be a non-empty bounded plain string;
- no control characters;
- values outside the approved username format are rejected/fail closed;
- all unknown keys are discarded or rejected rather than forwarded.

This is defense in depth. The Controller remains authoritative and validates again before database mutation.

The profile input supplied to the worker may include the stable account identifier only as a non-secret candidate seed if required by the pure candidate generator. It must not expose credentials or security secrets.

## 10. Controller persistence

The Controller is authoritative for `factory_account.username`.

When an Instagram `running` result contains an approved username update:

```text
receive worker response
  -> validate profile_updates schema
  -> validate username value
  -> verify update applies to the currently leased account/job
  -> UPDATE factory_account.username, updated_at
  -> only then advance/refresh the remote RUNNING job
```

The update must happen in the same Controller-side transaction as the corresponding job progress update when practical, so Controller state cannot acknowledge the new username while leaving job state stale, or vice versa.

The existing schema already enforces `UNIQUE(batch_id, username)`. A local DB uniqueness conflict must fail closed rather than silently overwrite another account. Because fallback suffixes are derived from the stable account identifier, collisions should be extremely unlikely, but the database constraint remains authoritative.

If Controller validation or persistence fails after the UI has advanced, open `NEEDS_CONFIRMATION` with a clear synchronization error; do not continue with knowingly inconsistent account metadata.

## 11. Error semantics

Use the existing `USERNAME_UNAVAILABLE` factory error code when all bounded candidates are exhausted.

Behavior:

```text
all bounded candidates unavailable
  -> needs_confirmation
  -> error_code = USERNAME_UNAVAILABLE
  -> AVD remains open on username UI
```

Existing policies remain:

```text
RATE_LIMITED / ACTION_BLOCKED -> stop candidate attempts immediately
NETWORK_ERROR                 -> bounded existing network policy
PASSWORD/OTP/security         -> WAITING_HUMAN
UNKNOWN                       -> fail closed / NEEDS_CONFIRMATION
```

Do not reinterpret a rate-limit as ordinary username unavailability.

## 12. Restart and idempotency

The candidate sequence is deterministic for the account, so restarting the Controller/worker cannot create an unbounded new namespace of usernames.

If a fallback username has already been persisted to `factory_account.username`, the next worker payload uses that persisted username as the requested username. This makes the accepted fallback the new durable source of truth.

If a worker dies after typing a candidate but before a successful Next/postcondition, no profile update was acknowledged. On restart the flow re-detects the current UI and safely retries from the durable Controller state.

If the UI is already on a later known signup screen after process recovery, existing recovery behavior resumes from the observed screen; it must not blindly navigate backward merely to re-enter a username.

## 13. Files/components expected to change

Implementation is expected to touch these bounded areas:

```text
core/factory_v2/identity.py
core/factory_v2/ui_automation/selectors.py
core/factory_v2/ui_automation/instagram/selectors.py
core/factory_v2/ui_automation/instagram/screens.py
core/factory_v2/ui_automation/instagram/flow.py
core/factory_v2/ui_automation/flow_result.py
workers/account_factory_worker.py
core/factory_v2/runtime.py
```

Tests will be extended in the corresponding existing test modules. No new database column is required.

If implementation reveals that a larger persistent candidate-attempt subsystem or new database schema is required, stop and revise this design instead of expanding scope silently.

## 14. TDD strategy

Implementation must follow RED -> GREEN for each layer.

### 14.1 Selector/detector

Tests:

- dynamic `The username <value> is not available.` detects `IG_USERNAME_UNAVAILABLE`;
- valid marker + Next detects `IG_USERNAME_VALID`;
- generic text containing `not available` outside the create-username context does not match;
- protected/rate-limit states still win priority.

### 14.2 Candidate generator

Tests:

- same username/account id produces identical candidate order across calls;
- different account ids produce different suffix sequences;
- at most five fallback candidates;
- output is de-duplicated, length-bounded, and uses approved characters;
- original requested username is not returned as a fallback.

### 14.3 Instagram flow

Tests:

- requested username valid -> Next, no profile update if username did not change;
- requested username unavailable -> first fallback valid -> Next + username profile update;
- multiple unavailable candidates -> next candidate is tried only after unavailable state;
- five fallback failures -> `USERNAME_UNAVAILABLE` without a sixth attempt;
- rate-limit/action-blocked stops immediately;
- unknown validation state fails closed;
- password/OTP/final-submit behavior does not regress.

### 14.4 Worker protocol

Tests:

- allowed username update is returned;
- unknown profile-update keys are not forwarded;
- password/OTP/security values can never be returned through profile updates;
- invalid username update fails closed.

### 14.5 Runtime persistence

Tests:

- accepted fallback updates `factory_account.username` before next job action;
- result without profile updates leaves username unchanged;
- profile update for the wrong account/job is rejected;
- DB uniqueness conflict does not silently continue;
- `SOCIAL_ONLY` and legacy `ACP_ACTIVE` stage behavior remain unchanged.

## 15. Acceptance criteria

A real AVD pilot starting from the currently observed unavailable-username screen can perform:

```text
baongocd is unavailable
  -> detect IG_USERNAME_UNAVAILABLE
  -> enter deterministic fallback candidate
  -> wait for Instagram validation
  -> if unavailable, try next bounded candidate
  -> when valid, press Next
  -> persist exact accepted username in factory_account.username
  -> continue to the next signup step
```

Acceptance additionally requires:

- no coordinate click on Instagram's inaccessible suggestion rows;
- maximum five fallback attempts per signup episode;
- no continued attempts after rate-limit/security signals;
- database username matches the selected Instagram username;
- restart remains deterministic/idempotent;
- password/OTP/CAPTCHA/security/final-submit boundaries remain unchanged;
- existing Instagram normal-flow tests, `SOCIAL_ONLY`, and `ACP_ACTIVE` behavior do not regress;
- do not merge `main` as part of this work.
