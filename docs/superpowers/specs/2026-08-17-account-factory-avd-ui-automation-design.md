# Account Factory V2 — AVD UI Automation Design

Date: 2026-08-17
Branch: `feat/account-factory-android`
Status: Approved design

## 1. Goal

Turn `REMOTE_AVD` into the primary semi-automated Account Factory runner while keeping `LOCAL_DEVICE` unchanged as a separate phone runner/fallback.

The AVD worker should automate routine, low-risk UI actions in the official Instagram and Threads apps, then stop and wait for the operator whenever a human-sensitive or security-sensitive step appears.

Target pilot flow:

```text
Create account
  -> assign REMOTE_AVD
  -> launch Instagram
  -> navigate known safe screens
  -> fill non-sensitive profile fields
  -> stop at password / OTP / CAPTCHA / identity/security verification
  -> operator completes required human step
  -> worker observes successful transition and auto-resumes
  -> confirm Instagram completion
  -> launch Threads
  -> automate known safe Threads screens
  -> stop at human-sensitive steps if present
  -> confirm Threads completion
  -> start official ACP OAuth
  -> ACP_ACTIVE
```

The controller remains authoritative for business stages. AVD-local UI observations are worker sub-state only.

## 2. Non-goals and safety boundaries

The automation must not:

- generate, enter, store, retrieve, or submit passwords;
- retrieve, enter, or solve OTP/email/SMS verification codes;
- solve or bypass CAPTCHA;
- automate selfie, identity, recovery, or security challenges;
- attempt to evade platform security controls;
- blindly tap unknown screens;
- publish Threads content as part of account creation;
- make `REMOTE_AVD` logic control or alter `LOCAL_DEVICE` lifecycle.

When any protected or ambiguous screen is detected, the worker must fail closed and transition to a human checkpoint or confirmation state.

## 3. Existing architecture constraints

The current controller/runtime remains the system of record.

- `WorkerSupervisor` owns only `REMOTE_AVD` lifecycle: start, stop, boot detection, capacity, recovery.
- `LOCAL_DEVICE` continues to register and heartbeat from the Android app.
- `FactoryControllerRuntime` remains authoritative for account stages and job assignment.
- Existing stages such as `RUNNER_ASSIGNED`, `WAITING_HUMAN`, `IG_CREATED`, `THREADS_CREATED`, `ACP_CONNECTING`, and `ACP_ACTIVE` remain authoritative.
- AVD-specific states such as `IG_NAVIGATING`, `OTP_REQUIRED`, or `UNKNOWN` are observations/sub-state and do not replace controller stages.

## 4. Component architecture

Add a dedicated UI automation layer for AVD workers rather than placing UI logic in `supervisor.py`.

```text
FactoryControllerRuntime
        |
        v
   RunnerGateway
        |
        v
   AvdWorkerAgent
        |
        +-- AdbClient
        +-- UiHierarchyReader
        +-- SafeUiDriver
        +-- ScreenDetector
        +-- InstagramFlow
        +-- ThreadsFlow
```

Package layout:

```text
core/factory_v2/ui_automation/
    adb.py
    hierarchy.py
    selectors.py
    detector.py
    driver.py

    instagram/
        screens.py
        selectors.py
        flow.py

    threads/
        screens.py
        selectors.py
        flow.py
```

Each unit must remain independently testable.

### 4.1 `AdbClient`

Responsibilities:

- execute scoped `adb -s <serial> ...` commands;
- launch/force-stop packages;
- query current package/activity;
- run UI hierarchy dump;
- tap a previously identified node center;
- set text only for approved non-sensitive fields;
- Back/Home/scroll actions;
- optional screenshot capture for diagnostics.

It must never contain Instagram/Threads flow policy.

### 4.2 `UiHierarchyReader`

Reads `uiautomator dump`, parses nodes, and exposes sanitized metadata:

- `text`;
- `content-desc`;
- `resource-id`;
- class;
- clickable/enabled;
- bounds.

Raw XML should be parsed in memory and discarded. Sensitive-looking field values must not be persisted to DB or logs.

### 4.3 `SafeUiDriver`

The flow layer uses a small interface instead of direct ADB calls:

```python
class SafeUiDriver:
    def snapshot(self) -> UiSnapshot: ...
    def detect_screen(self) -> DetectedScreen: ...
    def find(self, selector) -> UiNode | None: ...
    def tap(self, selector) -> ActionResult: ...
    def set_text(self, selector, value) -> ActionResult: ...
    def wait_for(self, screens, timeout) -> DetectedScreen: ...
```

Every action follows:

```text
precondition -> action -> postcondition
```

No action is treated as successful merely because ADB returned exit code 0.

## 5. Selector strategy

Avoid fixed screen coordinates as the primary mechanism.

Selector precedence:

1. resource-id;
2. content-desc;
3. exact text;
4. normalized text / known aliases;
5. semantic combination of node attributes;
6. node bounds only after a known node has already been identified.

Example alias set:

```text
Continue / Next / Tiếp tục / Tiếp
```

Resource IDs remain preferred when available.

The automation must not scan an unknown screen and tap arbitrary buttons based only on approximate position or generic button text.

## 6. Screen detection

A screen is identified from a signature, not one text token.

Example `IG_PROFILE_SETUP` signature:

```text
package == com.instagram.android
AND at least two expected profile markers exist
```

Expected markers may include username input, profile-related input, and a known next/continue control.

`DetectedScreen` should include:

```python
DetectedScreen(
    kind="IG_PROFILE_SETUP",
    confidence=0.96,
    evidence=(...),
)
```

### 6.1 Detection priority

Detector ordering is safety-first:

1. security / CAPTCHA / OTP / verification;
2. error / blocked / rate limited;
3. success;
4. known normal screen;
5. unknown.

Human-sensitive signatures should require less evidence to stop automation than normal screens require to continue automation.

### 6.2 Confidence rules

Normal-screen continuation:

- `>= 0.90`: automation allowed;
- `0.70–0.89`: observation/retry only;
- `< 0.70`: unknown.

Security-sensitive screens are fail-closed: sufficiently credible evidence causes immediate stop even if confidence is below the normal automation threshold.

## 7. Worker flow state machines

The controller stage remains authoritative; worker flow state is transient/observational.

### 7.1 Instagram worker flow

```text
LAUNCH
  -> ENTRY_DETECTION
  -> KNOWN_SAFE_NAVIGATION
  -> PROFILE_PREPARATION
  -> HUMAN_REQUIRED if protected step appears
  -> POSTCHECK
  -> COMPLETE
```

The worker may:

- launch Instagram;
- navigate known ordinary UI;
- enter Controller-generated username/display name/bio/profile metadata;
- select a pre-prepared avatar when the selector and state are known;
- scroll;
- verify screen transitions.

The worker must stop on:

- `PASSWORD_REQUIRED`;
- `OTP_REQUIRED`;
- `CAPTCHA_REQUIRED`;
- `EMAIL_OR_PHONE_VERIFICATION`;
- `SELFIE_OR_IDENTITY_CHECK`;
- `SECURITY_CHALLENGE`;
- `ACCOUNT_RECOVERY`;
- `CONSENT_WITH_SECURITY_IMPACT`;
- `UNKNOWN_CRITICAL_SCREEN`.

### 7.2 Threads worker flow

The same principles apply to Threads:

```text
LAUNCH
  -> KNOWN_SAFE_NAVIGATION
  -> PROFILE_PREPARATION
  -> HUMAN_REQUIRED if necessary
  -> POSTCHECK
  -> COMPLETE
```

The worker does not publish content. Completion means the Threads profile/account state is suitable for the controller to move toward ACP OAuth.

## 8. Human checkpoint and auto-resume

When a protected step appears:

```text
worker detects challenge
  -> no further mutating UI actions
  -> report WAITING_HUMAN
  -> enter observation-only polling
```

Auto-resume is allowed only when both are true:

1. the protected screen is no longer present; and
2. a known valid successor screen is positively detected.

The absence of an OTP/challenge screen alone is not success.

The UI may retain a manual Continue action as fallback, but successful screen detection should allow the AVD worker to resume automatically without requiring routine dashboard interaction.

## 9. Controller and command integration

Extend AVD worker command handling without coupling controller business logic to Instagram/Threads UI details.

Commands added/used for this flow:

```text
PREPARE_INSTAGRAM
AUTOMATE_INSTAGRAM
OBSERVE_CHECKPOINT
AUTOMATE_THREADS
START_ACP
OPEN_URL
```

Worker results contain sanitized observations only, for example:

```json
{
  "status": "waiting_human",
  "result": {
    "screen": "OTP_REQUIRED",
    "reason": "HUMAN_VERIFICATION_REQUIRED"
  }
}
```

or:

```json
{
  "status": "completed",
  "result": {
    "package": "com.instagram.android",
    "screen": "IG_POSTCHECK_OK",
    "observation": "PROFILE_READY"
  }
}
```

The controller, not the worker, decides whether to transition an account to `WAITING_HUMAN`, `IG_CREATED`, `THREADS_CREATED`, `ACP_CONNECTING`, or `ACP_ACTIVE`.

## 10. Unknown UI handling

Unknown screens must never trigger exploratory tapping.

Policy:

```text
UNKNOWN
  -> refresh snapshot up to 3 times
  -> still UNKNOWN
  -> report UI_CHANGED / NEEDS_CONFIRMATION
```

Useful sanitized diagnostics may include:

- package;
- activity;
- non-sensitive marker names;
- selector/screen signature version;
- stable screen hash derived from sanitized node structure.

Screenshot capture is diagnostic-only, disabled by default, and never part of the first implementation's decision loop.

## 11. Retry and idempotency

Normal UI action retry limit: at most two retries after the initial attempt unless a more specific action policy says otherwise.

After retry exhaustion:

```text
NEEDS_CONFIRMATION
```

No unbounded tap loops.

Actions should be idempotent where possible. Example: setting a field to `abc` when it already equals `abc` should be a no-op rather than appending a second copy.

Before replaying a command after crash/reconnect, the worker must inspect current UI state and decide whether the requested step is already complete.

## 12. Error handling

Recognized error observations include:

- `NETWORK_ERROR`;
- `APP_CRASH`;
- `RATE_LIMITED`;
- `ACTION_BLOCKED`;
- `ACCOUNT_DISABLED`;
- `UI_CHANGED`.

Policy:

```text
NETWORK_ERROR -> limited retry
APP_CRASH -> reopen once, then re-detect
RATE_LIMITED / ACTION_BLOCKED -> no rapid retry; RETRY_PENDING or NEEDS_CONFIRMATION
ACCOUNT_DISABLED -> terminal ERROR for the current account
UI_CHANGED -> NEEDS_CONFIRMATION
```

## 13. Restart and recovery

AVD worker recovery state tracks:

```text
account_id
job_id
flow
last_known_screen
last_safe_step
```

After controller/worker restart:

1. reconnect to the existing AVD if it is still online;
2. inspect the current foreground package and UI hierarchy;
3. reconcile the real UI with the authoritative account/job stage;
4. resume from the detected safe point;
5. never replay a mutating UI action merely because a previous ACK was lost.

The UI is authoritative for worker sub-state; the DB/controller is authoritative for business stage.

## 14. Logging and sensitive data handling

Allowed logs:

```text
SCREEN=OTP_REQUIRED
ACTION=WAIT_HUMAN
```

Disallowed logs include actual password, OTP, recovery code, verification code, App Secret, ACP master key, or other credentials.

Raw hierarchy dumps are not persisted by default. Test fixtures must be sanitized.

## 15. Test strategy

### 15.1 Unit tests

Cover:

- XML hierarchy parsing;
- selector matching and precedence;
- detector signatures;
- detector safety priority;
- confidence thresholds;
- challenge recognition;
- action pre/postcondition validation.

### 15.2 Flow tests

Use static/sanitized UI fixtures to cover:

- Instagram known navigation;
- Instagram profile preparation;
- password/OTP interruption;
- unknown screen;
- Threads normal navigation;
- Threads protected-step interruption;
- post-check success/failure.

Fixtures:

```text
tests/fixtures/android_ui/
    instagram_signup.xml
    instagram_profile.xml
    instagram_otp.xml
    instagram_error.xml
    threads_onboarding.xml
    threads_profile.xml
```

### 15.3 Worker integration tests

Cover:

- controller command -> worker execution -> sanitized result;
- `WAITING_HUMAN` creation;
- auto-resume only after known successor screen;
- duplicate command/retry idempotency;
- restart recovery;
- `LOCAL_DEVICE` behavior remains unaffected.

### 15.4 Real AVD pilot

Run against `acp-worker-01` with the official Instagram and Threads apps installed. Human-sensitive steps are completed manually by the operator.

## 16. AVD pilot acceptance criteria

The phase is complete when one real pilot can perform:

```text
Create account
  -> Controller assigns REMOTE_AVD
  -> acp-worker-01 starts/recovers
  -> Instagram launches automatically
  -> known safe screens are navigated automatically
  -> approved profile fields are prepared automatically
  -> worker stops on any protected step
  -> operator completes the protected step
  -> worker positively detects valid successor state and auto-resumes
  -> Instagram completion is confirmed
  -> Threads launches automatically
  -> known safe Threads flow proceeds
  -> human checkpoint is used if required
  -> Threads completion is confirmed
  -> official ACP OAuth starts
  -> account reaches ACP_ACTIVE
```

Additional acceptance conditions:

- no password automation;
- no OTP automation;
- no CAPTCHA automation;
- no security-challenge bypass;
- unknown UI never causes blind tapping;
- controller restart does not lose the account's recoverable stage;
- retries do not create duplicate actions;
- existing Android phone flow is not regressed;
- `LOCAL_DEVICE` and `REMOTE_AVD` remain operationally isolated.

## 17. Implementation order

Implementation proceeds in small verified slices:

1. ADB/UI hierarchy abstractions and sanitized model;
2. selectors + detector with fixtures;
3. safe driver action verification;
4. Instagram flow with fail-closed protected-screen handling;
5. Threads flow;
6. worker/controller command integration;
7. observation-only human checkpoint auto-resume;
8. restart/retry recovery;
9. full regression suite;
10. real `acp-worker-01` pilot.

Do not merge `main` as part of this work. All changes stay on `feat/account-factory-android` until separately reviewed and approved.
