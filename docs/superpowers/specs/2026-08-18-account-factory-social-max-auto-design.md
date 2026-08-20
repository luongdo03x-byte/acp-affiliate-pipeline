# Account Factory V2 — Social-Only MAX AUTO Design

Date: 2026-08-18
Branch: `feat/account-factory-android`
Status: Approved delta design
Base design: `docs/superpowers/specs/2026-08-17-account-factory-avd-ui-automation-design.md`

## 1. Goal

Add a `SOCIAL_ONLY` Account Factory mode optimized for one purpose: create an Instagram account and its Threads account with the maximum safe UI automation possible on one `REMOTE_AVD`.

For this mode, successful completion is:

```text
IG_CREATED -> THREADS_CREATED -> job COMPLETED
```

`SOCIAL_ONLY` must not start ACP OAuth. The existing ACP activation path remains available and unchanged for batches/accounts using the legacy/default `ACP_ACTIVE` completion mode.

The operator may provide the routine signup inputs up front:

- signup contact type (`phone` or `email`);
- phone and/or email value;
- birth date;
- optional prepared avatar file.

The worker may enter those non-secret signup values when the official app exposes a positively identified ordinary entry screen.

## 2. Non-negotiable safety boundaries

This delta does not relax the security boundaries of the base design.

The worker must not:

- generate, store, retrieve, enter, or submit passwords;
- retrieve, enter, or solve OTP/email/SMS verification codes;
- solve or bypass CAPTCHA;
- automate selfie, identity, account-recovery, or security challenges;
- automate security-impacting consent;
- evade platform security controls;
- blindly tap an unknown screen;
- use arbitrary host paths or arbitrary ADB/shell commands supplied by API input;
- publish Threads content;
- press the final official Instagram `Create account` / final signup submit action.

Password, OTP, CAPTCHA, identity/security checks, and the final Instagram account-creation submit remain human checkpoints. The AVD stays open while the operator handles those checkpoints.

## 3. Completion mode

Add an explicit batch-level completion mode:

```text
ACP_ACTIVE   # backward-compatible existing behavior
SOCIAL_ONLY  # new IG + Threads only behavior
```

Schema default is `ACP_ACTIVE` so existing persisted callers and tests keep their current behavior unless they opt into `SOCIAL_ONLY`.

For `SOCIAL_ONLY`:

```text
PROFILE_READY
  -> RUNNER_ASSIGNED
  -> Instagram automation / human checkpoints
  -> IG_CREATED
  -> Threads automation / human checkpoints
  -> THREADS_CREATED
  -> release factory job as COMPLETED
  -> worker READY
  -> account completed_at set
```

No `START_ACP`, `ACP_CONNECTING`, OAuth URL, or `ACP_ACTIVE` transition is performed.

`THREADS_CREATED` remains a durable safe stage globally. It is terminal only when the owning batch has `completion_mode=SOCIAL_ONLY`.

## 4. Signup input model

Extend `factory_account` with routine non-secret signup data:

```text
signup_contact_type TEXT  # NULL | phone | email
phone               TEXT
email               TEXT
birth_date           TEXT  # YYYY-MM-DD
```

Reuse existing `avatar_file` for a prepared avatar path.

The create-account API may accept:

```json
{
  "execution_target": "AUTO_AVD",
  "batch_name": "AVD UI Pilot 01",
  "completion_mode": "SOCIAL_ONLY",
  "signup_contact_type": "phone",
  "phone": "+84901234567",
  "email": null,
  "birth_date": "2000-05-20",
  "avatar_file": "var/factory_avatars/account-01.jpg"
}
```

Validation:

- `completion_mode` must be `ACP_ACTIVE` or `SOCIAL_ONLY`;
- `signup_contact_type`, when supplied, must be `phone` or `email`;
- the selected contact value must be present;
- phone/email values are treated as opaque signup data after bounded length/control-character validation; the system does not invent missing contacts;
- `birth_date` must be an ISO `YYYY-MM-DD` calendar date, must not be in the future, and the automation accepts only an adult date (18+ at validation time);
- `avatar_file`, when supplied, must be a relative path resolving inside the repository root and must never allow traversal outside it;
- password/OTP fields are not accepted by this API and are never included in worker payloads.

The controller sends only the selected contact value to the worker:

```json
{
  "profile": {
    "username": "...",
    "display_name": "...",
    "bio": "...",
    "signup_contact_type": "phone",
    "signup_contact": "+84901234567",
    "birth_date": "2000-05-20",
    "avatar_file": "var/factory_avatars/account-01.jpg"
  }
}
```

## 5. Instagram screen model

The current detector incorrectly treats generic contact-entry labels such as `Mobile number or email`, `Email address`, or `Phone number` as protected verification. Split entry from verification.

### 5.1 Safe ordinary screens

Add positively identified normal screens:

```text
IG_CONTACT_ENTRY
IG_BIRTHDAY
IG_PROFILE_SETUP
IG_AVATAR_SETUP
```

`IG_CONTACT_ENTRY` is ordinary data entry. The worker may enter the controller-supplied selected contact and press a known `Continue/Next` control after verifying the selector exists.

`IG_BIRTHDAY` is ordinary profile data entry. Automation is allowed only when the worker positively identifies supported date controls. Unsupported picker variants fail closed to `NEEDS_CONFIRMATION`; no coordinate guessing is allowed.

`IG_AVATAR_SETUP` may use a prepared avatar only when both the source file and known UI selectors are validated. If the official picker cannot be safely identified, the flow falls back to a human/confirmation checkpoint rather than guessing.

### 5.2 Protected screens

Keep these protected:

```text
PASSWORD_REQUIRED
OTP_REQUIRED
CAPTCHA_REQUIRED
EMAIL_OR_PHONE_VERIFICATION
SELFIE_OR_IDENTITY_CHECK
SECURITY_CHALLENGE
ACCOUNT_RECOVERY
CONSENT_WITH_SECURITY_IMPACT
IG_FINAL_SIGNUP_SUBMIT
```

`EMAIL_OR_PHONE_VERIFICATION` must use actual verification language such as `Confirm your email`, `Confirm your phone number`, `Verify your email`, or `Verify your phone number`. Generic contact-entry labels are removed from this signature.

`IG_FINAL_SIGNUP_SUBMIT` is explicitly protected. The initial signup entry selector must not reuse a broad `Sign up` selector that could match the final submit action. Initial automation should prefer/require `Create new account` / equivalent entry semantics.

## 6. Safe text fields

`SafeUiDriver.set_text` currently allows only `username`, `display_name`, and `bio`. Extend its non-secret allowlist to:

```text
username
display_name
bio
signup_contact
birth_date
```

Continue to reject:

```text
password
otp
verification_code
recovery_code
```

A field semantic that is neither explicitly approved nor explicitly protected remains rejected.

## 7. Birthday automation

The first implementation supports only positively identified editable birthday/date controls exposed by the current Instagram hierarchy.

Rules:

1. validate the controller-provided ISO date before it reaches the worker;
2. detect `IG_BIRTHDAY` using more than a generic `Next` button;
3. locate a known birthday/date input selector;
4. set the date idempotently;
5. press known `Continue/Next` only after the value/action precondition is satisfied;
6. verify a known successor screen;
7. unsupported wheel/calendar/picker variants return `NEEDS_CONFIRMATION` rather than using fixed coordinates.

This keeps the implementation safe while still automating versions of the official UI that expose an editable date field.

## 8. Avatar automation

Avatar is optional and best-effort.

Before entering Instagram avatar setup:

1. resolve `avatar_file` against the repository root;
2. reject missing files and paths outside the repository;
3. push the validated file to a fixed AVD-owned destination such as `/sdcard/Pictures/ACP/avatar.jpg` using an ADB primitive whose remote path is generated by code, never API input;
4. on `IG_AVATAR_SETUP`, tap only a known add/import-photo control;
5. select the known staged avatar only when the picker exposes a positively identified selector/state;
6. otherwise return `NEEDS_CONFIRMATION` without blind tapping.

Raw image bytes are not stored in SQLite.

## 9. Human checkpoint behavior

`WAITING_HUMAN` remains an active session.

When a protected screen appears:

```text
worker -> waiting_human
controller -> WAITING_HUMAN
job -> WAITING_HUMAN / OBSERVE_CHECKPOINT
worker -> WAITING_HUMAN
AVD -> remains running on the current screen
```

The controller continues observation-only polling. Auto-resume is allowed only after a known safe successor screen is positively detected.

The supervisor must not normal-resource-drain a worker with a current account/job or a `WAITING_HUMAN` session. Only explicit operator stop or an unrecoverable host/process failure may end the session.

Only one AVD should be active for the current resource-constrained host during this mode.

## 10. AVD resource profile

Use the already verified stable graphics configuration and lower the guest-memory footprint:

```text
-memory 1536
-gpu swiftshader
-feature -Vulkan
-no-snapshot
-noaudio
```

The first memory target is 1536 MB. If a real pilot proves Instagram/Threads are killed by Android memory pressure, increase deliberately to 1792 MB, then 2048 MB. Do not silently return to 2560 MB.

The system must not start a second AVD while the single worker is active or waiting for a human checkpoint.

## 11. Threads flow

Threads continues to use the existing fail-closed onboarding/profile flow:

- open the official Threads package;
- recognize known onboarding;
- import/continue from the created Instagram account where positively identified;
- fill approved display name/bio fields if exposed;
- stop on password/OTP/CAPTCHA/verification/security screens;
- never publish content;
- finish only on `THREADS_HOME` / `THREADS_POSTCHECK_OK`.

No new credential automation is introduced for Threads.

## 12. Runtime behavior for SOCIAL_ONLY

When Instagram completes:

```text
IG_CREATED -> PREPARE_THREADS
```

When Threads completes and batch completion mode is `SOCIAL_ONLY`:

```text
THREADS_CREATED
  -> resolve active Threads checkpoint
  -> set account.completed_at
  -> release job COMPLETED
  -> clear account assignment/current_job
  -> worker READY
```

When completion mode is `ACP_ACTIVE`, preserve the existing behavior:

```text
THREADS_CREATED -> START_ACP -> ACP_CONNECTING -> ACP_ACTIVE
```

Scheduler recovery must not enqueue `START_ACP` for a `SOCIAL_ONLY` account whose durable safe stage is already `THREADS_CREATED`.

## 13. Dashboard/API semantics

For a latest batch using `SOCIAL_ONLY`, dashboard completion/active count is based on `THREADS_CREATED` (or later legacy-compatible stages if present), not exclusively `ACP_ACTIVE`.

For `ACP_ACTIVE`, existing dashboard semantics remain unchanged.

Account API responses expose the new non-secret signup fields and completion mode where useful. Passwords, OTPs, and any security credentials are never exposed because they are never stored.

## 14. Error and unknown UI policy

Keep the base policy unchanged:

```text
NETWORK_ERROR -> bounded retry
APP_CRASH -> reopen once, re-detect
RATE_LIMITED/ACTION_BLOCKED -> RETRY_PENDING / confirmation
ACCOUNT_DISABLED -> terminal ERROR
UNKNOWN -> observe up to 3 times -> UI_CHANGED / NEEDS_CONFIRMATION
```

No new screen permits exploratory tapping.

## 15. Test strategy

Implement with strict TDD.

### 15.1 Schema/service/API

Test:

- additive migration for `completion_mode`, contact fields, and `birth_date`;
- existing DB rows receive backward-compatible `ACP_ACTIVE` mode;
- `SOCIAL_ONLY` create-account request persists validated input;
- missing selected contact is rejected;
- invalid/future/under-18 birth date is rejected;
- password/OTP fields remain rejected by API;
- avatar path traversal is rejected.

### 15.2 Driver/detector

Test:

- `signup_contact` and supported `birth_date` semantics are allowed;
- password/OTP remain rejected;
- generic contact entry detects `IG_CONTACT_ENTRY`, not protected verification;
- actual confirm/verify language remains protected;
- final Instagram submit is protected;
- initial signup entry cannot use final submit selector.

### 15.3 Instagram flow

Test:

- supplied contact is entered and Continue is pressed on `IG_CONTACT_ENTRY`;
- no contact input causes confirmation rather than guessing;
- supported birthday input is filled idempotently;
- password and OTP stop before mutation;
- final submit stops before mutation;
- avatar setup only acts on known selectors;
- unknown picker/screen fails closed;
- known post-check completes.

### 15.4 Worker/runtime

Test:

- safe profile payload forwards contact/birth/avatar but strips unsupported fields;
- password cannot enter worker payload via allowlist;
- `SOCIAL_ONLY` Threads completion releases the job and never emits `START_ACP`/`OPEN_URL`;
- `ACP_ACTIVE` mode still follows existing activation behavior;
- `WAITING_HUMAN` keeps the active worker/job leased;
- restart from durable `THREADS_CREATED` does not start OAuth for `SOCIAL_ONLY`.

### 15.5 AVD launcher

Test exact stable/resource startup arguments:

```text
-memory 1536
-gpu swiftshader
-feature -Vulkan
-no-snapshot
-noaudio
```

## 16. Acceptance criteria

A real `acp-worker-01` pilot in `SOCIAL_ONLY` mode can perform:

```text
create account request with phone/email + birth date + optional avatar
  -> one AVD starts/attaches
  -> Instagram opens automatically
  -> known signup navigation runs automatically
  -> supplied contact is entered automatically
  -> birthday is entered automatically when supported by known UI
  -> password/OTP/security/final-submit screens stop for the operator
  -> AVD remains open during the checkpoint
  -> worker auto-resumes after a known successor appears
  -> prepared avatar is handled automatically when safe selectors are available
  -> Instagram completion is confirmed
  -> Threads opens automatically
  -> known Threads onboarding/profile flow runs
  -> Threads completion is confirmed
  -> account reaches THREADS_CREATED
  -> factory job completes
  -> worker returns READY
  -> no ACP OAuth starts
```

Additional acceptance conditions:

- no password automation;
- no OTP/CAPTCHA/security bypass;
- no final Instagram account-submit automation;
- no arbitrary shell/path execution;
- no blind coordinates on unknown UI;
- one AVD only for the pilot host;
- AVD uses 1536 MB guest RAM initially;
- existing `ACP_ACTIVE` mode and `LOCAL_DEVICE` behavior do not regress;
- do not merge `main` as part of this work.
