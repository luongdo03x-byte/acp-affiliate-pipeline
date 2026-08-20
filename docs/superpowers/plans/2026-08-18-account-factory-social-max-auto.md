# Account Factory V2 — Social-Only MAX AUTO Implementation Plan

Date: 2026-08-18
Branch: `feat/account-factory-android`
Design: `docs/superpowers/specs/2026-08-18-account-factory-social-max-auto-design.md`

## Goal

Implement a backward-compatible `SOCIAL_ONLY` factory mode that ends after Instagram + Threads creation, accepts routine non-secret signup inputs, automates known safe Instagram signup screens, keeps human-sensitive checkpoints fail-closed, and runs a single lower-memory AVD profile.

## Constraints

- Do not merge `main`.
- Strict TDD: regression test first, observe RED, then minimal production change, observe GREEN.
- Do not automate or store passwords, OTPs, CAPTCHA, selfie/identity/security challenges, account recovery, security-impacting consent, or final Instagram signup submit.
- No arbitrary shell or arbitrary host/AVD paths.
- Unknown UI never causes blind taps.
- Preserve existing `ACP_ACTIVE` and `LOCAL_DEVICE` behavior.

## Task 1 — Add social completion mode and signup input schema/API

### Test first

Modify/add tests in:

- `tests/test_factory_v2_schema.py`
- `tests/test_factory_v2_api.py`
- `tests/test_factory_v2_service.py`

Required RED assertions:

1. `factory_batch.completion_mode` exists and defaults to `ACP_ACTIVE` for old callers.
2. `factory_account` contains `signup_contact_type`, `phone`, `email`, and `birth_date`.
3. POST `/api/factory/v2/accounts` accepts `completion_mode=SOCIAL_ONLY`, selected phone/email, ISO adult `birth_date`, and relative `avatar_file`.
4. The selected contact must be present.
5. Invalid/future/under-18 dates fail validation.
6. Absolute/traversal avatar paths fail validation.
7. Password/OTP request fields remain rejected.

Run focused RED:

```bash
python3 -m unittest \
  tests.test_factory_v2_schema \
  tests.test_factory_v2_service \
  tests.test_factory_v2_api -v
```

### Minimal production change

Touch:

- `core/factory_v2/schema.py`
- `core/factory_v2/models.py`
- `core/factory_v2/service.py`
- `web/factory_v2.py`

Implementation:

- add `CompletionMode` enum (`ACP_ACTIVE`, `SOCIAL_ONLY`);
- add additive schema migrations;
- extend single-account creation with validated optional signup profile input;
- keep generated identity fields but override only explicitly supplied routine signup fields;
- expose only non-secret fields in account API;
- reject all unknown fields, therefore password/OTP cannot be accepted.

Run focused GREEN with the same command.

Commit production change separately from the RED test commit.

## Task 2 — Pass only safe signup fields to the AVD worker

### Test first

Modify:

- `tests/test_factory_v2_avd_worker_agent.py`
- `tests/test_factory_v2_runtime_remote.py`

Required RED assertions:

1. Runtime profile payload includes selected contact as `signup_contact`, `signup_contact_type`, `birth_date`, and optional `avatar_file`.
2. Worker `_safe_profile` allows those fields after strict validation.
3. Worker never forwards `password`, `otp`, verification code, recovery code, or arbitrary unknown keys.
4. Invalid contact type/date/path is rejected before UI mutation.

Focused RED/GREEN:

```bash
python3 -m unittest \
  tests.test_factory_v2_avd_worker_agent \
  tests.test_factory_v2_runtime_remote -v
```

Production:

- `core/factory_v2/runtime.py`
- `workers/account_factory_worker.py`

Keep payload non-secret and bounded.

## Task 3 — Split Instagram contact entry from protected verification

### Test first

Modify:

- `tests/test_factory_v2_ui_detector.py`
- `tests/test_factory_v2_ui_driver.py`
- `tests/test_factory_v2_instagram_flow.py`

Required RED assertions:

1. `Mobile number or email` + known input + Continue detects `IG_CONTACT_ENTRY` and is not protected.
2. `Confirm/Verify your email/phone` remains protected `EMAIL_OR_PHONE_VERIFICATION`.
3. Final official signup submit detects protected `IG_FINAL_SIGNUP_SUBMIT`.
4. Initial entry selector cannot match a broad final `Sign up` action.
5. `signup_contact` is an approved `SafeUiDriver.set_text` semantic.
6. `password` and OTP semantics still raise before mutation.
7. Instagram flow fills selected contact and continues only with known successor screens.
8. Missing contact value or selector returns confirmation, never guesses.

Focused RED/GREEN:

```bash
python3 -m unittest \
  tests.test_factory_v2_ui_detector \
  tests.test_factory_v2_ui_driver \
  tests.test_factory_v2_instagram_flow -v
```

Production:

- `core/factory_v2/ui_automation/driver.py`
- `core/factory_v2/ui_automation/instagram/selectors.py`
- `core/factory_v2/ui_automation/instagram/screens.py`
- `core/factory_v2/ui_automation/instagram/flow.py`

## Task 4 — Add supported birthday automation and best-effort avatar staging

### Test first

Modify/add tests in:

- `tests/test_factory_v2_ui_driver.py`
- `tests/test_factory_v2_instagram_flow.py`
- `tests/test_factory_v2_avd_worker_agent.py`

Required RED assertions:

1. `birth_date` can be set only through a positively identified supported birthday selector.
2. Unsupported birthday picker returns `NEEDS_CONFIRMATION`; no coordinate fallback.
3. Known avatar setup uses only known add/import-photo selectors.
4. Avatar source must resolve inside repo and exist.
5. ADB push target is fixed/generated by worker code, not API input.
6. Unknown photo picker returns `NEEDS_CONFIRMATION` without arbitrary tap.

Production may touch:

- `core/factory_v2/ui_automation/adb.py`
- `core/factory_v2/ui_automation/driver.py`
- `core/factory_v2/ui_automation/instagram/selectors.py`
- `core/factory_v2/ui_automation/instagram/screens.py`
- `core/factory_v2/ui_automation/instagram/flow.py`
- `workers/account_factory_worker.py`

Do not add fixed screen coordinates as a fallback.

Focused test command:

```bash
python3 -m unittest \
  tests.test_factory_v2_ui_driver \
  tests.test_factory_v2_instagram_flow \
  tests.test_factory_v2_avd_worker_agent -v
```

## Task 5 — Make THREADS_CREATED terminal for SOCIAL_ONLY

### Test first

Modify:

- `tests/test_factory_v2_runtime_remote.py`
- `tests/test_factory_v2_runtime_activation.py`
- `tests/test_factory_v2_scheduler.py`
- `tests/test_factory_v2_api.py`

Required RED assertions:

1. In `SOCIAL_ONLY`, Threads completion transitions to `THREADS_CREATED`, sets `completed_at`, releases job `COMPLETED`, and returns worker to `READY`.
2. No `START_ACP`, `OPEN_URL`, OAuth session, or `ACP_CONNECTING` action occurs.
3. Existing `ACP_ACTIVE` mode still starts activation exactly as before.
4. Scheduler does not enqueue `START_ACP` for `SOCIAL_ONLY` with durable `THREADS_CREATED`.
5. Dashboard counts `THREADS_CREATED` as completed/active for a `SOCIAL_ONLY` batch but preserves existing ACP semantics otherwise.

Production:

- `core/factory_v2/runtime.py`
- `core/factory_v2/scheduler.py`
- `core/factory_v2/service.py`
- `web/factory_v2.py`

Focused RED/GREEN:

```bash
python3 -m unittest \
  tests.test_factory_v2_runtime_remote \
  tests.test_factory_v2_runtime_activation \
  tests.test_factory_v2_scheduler \
  tests.test_factory_v2_api -v
```

## Task 6 — Lower AVD memory footprint and lock pilot to one worker

### Test first

Modify:

- `tests/test_factory_v2_avd.py`
- `tests/test_factory_v2_resource_policy.py` and/or `tests/test_factory_v2_supervisor.py` only if a missing one-worker invariant requires production change.

Required launcher args:

```text
-memory 1536
-gpu swiftshader
-feature -Vulkan
-no-snapshot
-noaudio
```

The existing verified graphics flags must not regress.

The pilot must never start a second AVD while one AVD is active or `WAITING_HUMAN`. Prefer enforcing this through batch desired capacity / current supervisor policy rather than a new global hard-coded limit unless tests prove the current policy can exceed one.

Focused test:

```bash
python3 -m unittest \
  tests.test_factory_v2_avd \
  tests.test_factory_v2_resource_policy \
  tests.test_factory_v2_supervisor -v
```

## Task 7 — Full regression and real pilot

Run Python gate:

```bash
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
bash scripts/verify_account_factory_dual_runner.sh
```

If the script includes Android verification, keep JDK17. Do not claim a full green gate without fresh output.

Then run one real `SOCIAL_ONLY` AVD pilot with:

- `execution_target=AUTO_AVD`;
- one selected phone/email input;
- adult birth date;
- optional prepared avatar;
- no password/OTP in API or DB.

Observe:

```text
PROFILE_READY
-> RUNNER_ASSIGNED
-> IG routine automation
-> WAITING_HUMAN at protected step
-> operator completes protected step
-> auto-resume
-> IG_CREATED
-> Threads routine automation
-> THREADS_CREATED
-> job COMPLETED
-> worker READY
```

Verify no ACP OAuth browser/action starts.

## Commit discipline

For each task:

1. commit test that demonstrates missing behavior;
2. run and capture RED;
3. commit minimum production implementation;
4. run focused GREEN;
5. run broader related regressions;
6. only then move to next task.

Never merge `main` during this implementation.
