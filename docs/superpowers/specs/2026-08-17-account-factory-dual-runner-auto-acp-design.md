# Account Factory Dual-Runner + Auto ACP Activation Design

**Date:** 2026-08-17  
**Status:** Approved design baseline  
**Branch:** `feat/account-factory-android`

## 1. Goal

Build **Account Factory** as a product separate from ACP publishing. It must be able to create and complete an Instagram + Threads account workflow on either of two execution targets:

1. **LOCAL_DEVICE** — the physical Android phone running the Account Factory app.
2. **REMOTE_AVD** — an Android Studio AVD running on the Ubuntu host.

Both execution targets use the same workflow, state machine, retry semantics, checkpoints, and final success definition.

An account is not considered complete at `THREADS_CREATED`. After Threads is verified, Account Factory must automatically begin the existing official Threads OAuth connection into ACP and finish at:

```text
ACP_ACTIVE
```

`ACP_ACTIVE` is the terminal success state for the creation workflow.

## 2. Product Boundary

Account Factory is **not** the ACP affiliate/publishing web app.

The Account Factory runtime must not depend on ACP publishing/dashboard features such as:

- `/` ACP dashboard
- `post`
- `product`
- `campaign`
- affiliate product ingestion
- publishing queues
- content generation
- post metrics

The only integration point with ACP after account creation is the existing secure account/OAuth/channel layer required to activate the newly created Threads identity inside ACP.

### Required separation

```text
ACCOUNT FACTORY                         ACP PUBLISHING
----------------                         --------------
Create account                            Products
Runner scheduling                         Posts
Instagram / Threads workflow              Campaigns
Checkpoints                               Affiliate links
Recovery                                  Publishing
Auto ACP activation -------- OAuth ---->  Channel/account storage
```

The current `account_factory_server.py -> acp.web.server.create_app()` coupling is transitional and must be removed from the final architecture. The Account Factory controller must have its own minimal application entrypoint and must not render the ACP publishing dashboard.

## 3. Selected Architecture

Use **one controller workflow with two runner implementations** rather than two independent workflow engines.

```text
                         ACCOUNT FACTORY
                               │
                    Factory Controller API
                               │
                    Authoritative State DB
                               │
                     Scheduler / Workflow
                               │
                ┌──────────────┴──────────────┐
                │                             │
                ▼                             ▼
        LOCAL_DEVICE runner            REMOTE_AVD runner
        physical Android               Ubuntu + AVD worker
                │                             │
                ▼                             ▼
        Instagram / Threads             Instagram / Threads
                │                             │
                └──────────────┬──────────────┘
                               ▼
                        THREADS_CREATED
                               ▼
                     AUTO ACP ACTIVATION
                               ▼
                        Official OAuth
                               ▼
                          ACP_ACTIVE
```

### Why this architecture

- Reuses the existing Factory V2 repository, scheduler, state machine, retry rules, AVD manager, supervisor, and OAuth bridge.
- Prevents LOCAL_DEVICE and REMOTE_AVD from drifting into different business rules.
- Lets a batch distribute jobs across both physical devices and AVDs.
- Keeps OAuth tokens and ACP secrets on the backend.
- Removes the accidental dependency on the ACP publishing web application.

## 4. Controller Authority

For P0/P1 implementation, the **Factory Controller remains the authoritative workflow/state owner** for both runner types.

The phone can execute a local job on itself, but it reports observations and checkpoint results to the Controller instead of directly changing authoritative account stages.

This means:

```text
Android UI / LocalDeviceRunner
        │
        │ observations + commands
        ▼
Factory Controller
        │
        ├── validates transition
        ├── persists state
        ├── assigns/release jobs
        └── starts ACP activation
```

Offline local-only workflow is not required for this design. It may be added later without changing the workflow model.

## 5. Runner Model

Introduce a runner type shared by jobs/workers:

```text
LOCAL_DEVICE
REMOTE_AVD
```

Every active job must have exactly one execution target.

Suggested logical interface:

```kotlin
interface AccountRunner {
    suspend fun prepareInstagram(job: FactoryJob)
    suspend fun observeInstagram(job: FactoryJob): RunnerObservation
    suspend fun prepareThreads(job: FactoryJob)
    suspend fun observeThreads(job: FactoryJob): RunnerObservation
    suspend fun stop(job: FactoryJob)
    suspend fun heartbeat(): RunnerHeartbeat
}
```

The Python AVD worker exposes equivalent protocol actions. The workflow consumes runner-neutral observations instead of hard-coding ADB behavior.

### Runner responsibilities

A runner may:

- open the official Instagram app
- open the official Threads app
- prepare non-secret text owned by Account Factory
- report the foreground package/activity
- report that the operator reached a checkpoint
- report health/heartbeat
- report post-check observations

A runner must not be the source of truth for workflow stage.

## 6. LOCAL_DEVICE Mode

The Account Factory Android app gains a real execution component named conceptually `LocalDeviceRunner`.

It runs on the same physical Android device on which the user starts the job.

### P0 behavior

```text
Create Account
    ↓
Target = This phone
    ↓
Controller leases account to LOCAL_DEVICE worker
    ↓
App opens Instagram
    ↓
Human checkpoint when signup/security interaction is required
    ↓
App performs allowed post-check observation
    ↓
Controller marks IG_CREATED
    ↓
App opens Threads
    ↓
Human checkpoint
    ↓
App performs allowed post-check observation
    ↓
Controller marks THREADS_CREATED
    ↓
Auto ACP activation
```

The Android app must register a stable local worker/device id with the controller and send heartbeat while a job is active.

Only one active creation job per physical phone is allowed in P0.

## 7. REMOTE_AVD Mode

Keep the existing Ubuntu architecture where the Controller owns scheduling and each Android Studio AVD is driven by one standalone worker process.

```text
Controller
   ↓
Scheduler
   ↓
AVD worker
   ↓
Instagram / Threads
```

Existing AVD capabilities remain useful:

- AVD discovery/start/stop
- STARTING -> READY boot lifecycle
- one active job lease per worker
- heartbeat/recovery
- adaptive resource policy
- drain/restart
- crash recovery from `last_safe_stage`

The AVD worker and LocalDeviceRunner must emit the same logical observations so the workflow is runner-neutral.

## 8. Unified Workflow State Machine

The successful path is:

```text
PROFILE_READY
    ↓
AVD_ASSIGNED / RUNNER_ASSIGNED
    ↓
IG_READY_FOR_HUMAN
    ↓
WAITING_HUMAN
    ↓
IG_VERIFYING
    ↓
IG_CREATED
    ↓
THREADS_READY_FOR_HUMAN
    ↓
WAITING_HUMAN
    ↓
THREADS_VERIFYING
    ↓
THREADS_CREATED
    ↓  automatic, no separate Connect ACP button
ACP_CONNECTING
    ↓
ACP_ACTIVE
```

The existing stage name `AVD_ASSIGNED` is too execution-specific. The implementation plan should migrate it to a neutral `RUNNER_ASSIGNED` or equivalent without breaking persisted rows/recovery.

### Terminal success

Only:

```text
ACP_ACTIVE
```

counts as completed.

`THREADS_CREATED` is an intermediate safe stage.

## 9. Automatic ACP Activation

When the Controller commits `THREADS_CREATED`, it immediately schedules ACP activation for that same account.

No normal-flow `Connect ACP` button is required.

```text
THREADS_CREATED
    ↓
start_account_oauth(account_id)
    ↓
ACP_CONNECTING
    ↓
return official authorization URL
    ↓
open URL on the execution-side Android UI
    ↓
operator approves official OAuth if required
    ↓
callback validates identity
    ↓
ACP_ACTIVE
```

### Security ownership

Keep the existing backend ownership model:

- authoritative expected username comes from `factory_account`
- OAuth callback verifies returned identity
- username mismatch -> `ACCOUNT_MISMATCH`
- Threads access token stays on ACP backend
- token is encrypted before persistence
- phone/AVD never receives access token, app secret, ACP master key, password, OTP, or CAPTCHA result

### OAuth retry

If OAuth is cancelled, denied, or expires:

```text
THREADS_CREATED
    ↓
ACP_CONNECTING
    ↓ failure
RETRY_PENDING + OAUTH_FAILED
```

Retry must resume at ACP activation only. It must **not** return to Instagram or Threads creation.

## 10. Human Checkpoints and Safety Boundary

The workflow may automate orchestration around official apps, but interactive security verification remains an explicit human checkpoint.

Examples include:

- OTP
- CAPTCHA
- selfie/identity challenge
- account recovery/security confirmation
- other official platform security prompts

Account Factory must not implement CAPTCHA solving, OTP interception, fingerprint spoofing, proxy/fingerprint evasion, anti-detection bypass, or security-check circumvention.

The product goal is to automate the surrounding workflow and resume reliably after the operator completes the required official interaction.

## 11. Scheduling Across Both Runner Types

A batch may use both runner types simultaneously.

Example:

```text
Batch: 10 accounts

LOCAL phone-01       -> account 1
AVD acp-worker-01    -> account 2
AVD acp-worker-02    -> account 3
AVD acp-worker-03    -> account 4
...
```

Scheduler rules:

- one active job per runner
- one active job per account
- runner must be READY and not draining
- user may explicitly choose `THIS_PHONE`, `AUTO_AVD`, or a specific AVD
- future `AUTO` mode may choose any healthy runner
- `WAITING_HUMAN` still consumes that runner
- retry uses `last_safe_stage` and must not replay completed platform stages

## 12. Android UX

The Android app becomes both:

1. Factory control UI.
2. Local physical-device runner.

### Primary screens

```text
Dashboard
Create Account
Accounts
Runners
Checkpoints
```

### Create Account

Required target selector:

```text
Run on
● This phone
○ Auto-select AVD
○ acp-worker-01
○ acp-worker-02
```

For P0, `This phone` creates one job. Batch execution can use AVDs and may later include multiple registered physical devices.

### Account status

Show runner type and execution target alongside stage:

```text
@username
THREADS_READY_FOR_HUMAN
Runner: LOCAL_DEVICE / Pixel 8
```

or:

```text
@username
IG_CREATED
Runner: REMOTE_AVD / acp-worker-02
```

### ACP activation UX

After `THREADS_CREATED`, the app does not show a separate normal-flow activation button.

It should display:

```text
Activating ACP...
```

If browser authorization is required, open the server-returned official OAuth URL automatically and poll the account until `ACP_ACTIVE` or an OAuth error state.

A manual `Retry ACP activation` action is shown only after a retryable OAuth failure.

## 13. Dedicated Factory Controller Service

Replace the current launcher dependency on `acp.web.server.create_app()` with a minimal Account Factory application.

Conceptually:

```text
account_factory_server.py
    ├── Flask app / factory-only health route
    ├── /api/factory/v2/*
    ├── /oauth/account-factory/*
    └── FactoryControllerRuntime
```

It must **not** register the ACP publishing dashboard routes.

Therefore starting Account Factory on port `5001` must not require the `post` table and visiting `/` must not execute `attribution.funnel()`.

A factory-only root may return a small JSON/service status response or redirect to a factory-specific health endpoint.

## 14. Database Boundary

The existing SQLite database may remain physically shared in the repository during migration, but Account Factory code must depend only on:

- Factory V2 tables
- OAuth session/account connection tables required for activation
- channel metadata required to finish `ACP_ACTIVE`

It must not require publishing-domain tables just to boot or serve Factory API requests.

Longer term, Account Factory can move to a separate database without changing runner/workflow interfaces.

## 15. Data Model Changes

Add or normalize these concepts:

```text
factory_worker.runner_type
    LOCAL_DEVICE | REMOTE_AVD

factory_worker.device_name
factory_worker.device_id

factory_job.runner_type
factory_job.worker_id
```

`adb_serial` is valid only for `REMOTE_AVD` and must not be exposed as a required field for local physical workers.

Account/job serializers should expose safe runner metadata only.

## 16. API Changes

Keep the Factory V2 API but make it runner-neutral.

Required concepts:

```text
GET  /api/factory/v2/runners
POST /api/factory/v2/runners/local/register
POST /api/factory/v2/runners/<id>/heartbeat
POST /api/factory/v2/accounts or /batches with execution_target
POST /api/factory/v2/jobs/<id>/observations
```

Exact endpoint shapes may be adjusted in the implementation plan to match current service/repository patterns.

Existing checkpoint, account, OAuth status, drain/restart, and dashboard endpoints can be retained where still applicable.

`X-ACP-Factory-Key` remains acceptable for P0. Device pairing/rotatable per-device credentials are a later improvement.

## 17. Recovery Semantics

### Runner disappears before a human checkpoint

Use `last_safe_stage` and move to retry/recovery. Do not blindly advance.

### Runner disappears during `WAITING_HUMAN`

Move account to `NEEDS_CONFIRMATION` when completion cannot be proven. Preserve last safe stage.

### Instagram post-check fails

Do not mark `IG_CREATED`. Keep/reopen checkpoint and require operator confirmation/retry.

### Threads post-check fails

Do not mark `THREADS_CREATED`.

### OAuth fails

Preserve `THREADS_CREATED` as the safe platform state and retry only ACP activation.

### Controller restart

Reconcile leases and checkpoints. Never infer success from a missing worker/phone.

## 18. Migration From Current Branch

The current branch contains useful work but is partially coupled to the wrong product boundary.

### Keep

- `core/factory_v2` state/repository/service foundation
- scheduler and recovery logic
- AVD manager
- worker process/protocol
- supervisor and resource policy
- OAuth bridge
- Factory V2 API concepts
- Android Compose screens/ViewModel/DTO foundation
- tests covering leases, recovery, checkpoint semantics, OAuth identity verification

### Refactor

- make worker/job model runner-neutral
- extract a reusable workflow engine from AVD-specific runtime assumptions
- add LOCAL_DEVICE runner registration/protocol
- add Android `LocalDeviceRunner`
- add create-account target selection
- automatically start OAuth after `THREADS_CREATED`

### Remove from Account Factory boot path

- `acp.web.server.create_app()`
- ACP publishing dashboard route registration
- publishing-domain schema requirement

Do not delete ACP publishing functionality itself. Only remove Account Factory's dependency on it.

## 19. Testing Strategy

### Backend unit/integration

Must cover:

- local runner registration/heartbeat
- AVD runner still works
- scheduler can assign to either runner type
- scheduler never leases same account twice
- runner-neutral transitions
- dead local device recovery
- dead AVD recovery
- human checkpoint ambiguity -> `NEEDS_CONFIRMATION`
- `THREADS_CREATED` automatically starts ACP activation
- OAuth failure retries from Threads-safe stage
- OAuth success -> `ACP_ACTIVE`
- Account Factory server boots without publishing tables
- `/` or health request does not touch `post`

### Android unit tests

Must cover:

- create-account target mapping
- local runner state/heartbeat reduction
- local checkpoint action mapping
- automatic OAuth URL event after Threads completion
- OAuth retry UI only after failure
- no local blind transition to `IG_CREATED`, `THREADS_CREATED`, or `ACP_ACTIVE`

### Android build

Required gate:

```bash
gradle -p android/account-factory testDebugUnitTest assembleDebug --no-daemon --max-workers=2 --console=plain
```

### Real acceptance gates

Run at least:

1. One physical Android device job through `ACP_ACTIVE`.
2. One real Ubuntu AVD job through `ACP_ACTIVE`.
3. One retryable OAuth failure/retry without recreating IG/Threads.
4. One runner-loss case during a human checkpoint showing `NEEDS_CONFIRMATION`.

No live Threads publishing is required for Account Factory acceptance.

## 20. Success Criteria

The design is complete when all of the following are true:

- Account Factory boots as a dedicated factory service and does not require the ACP publishing dashboard/schema.
- The Android app can choose `This phone` or an Ubuntu AVD as execution target.
- A physical phone can execute the same logical workflow as an AVD.
- AVD scheduling/recovery continues to work.
- Both runner types use one authoritative Controller state machine.
- Human checkpoints are explicit and recoverable.
- `THREADS_CREATED` automatically continues into official ACP OAuth activation.
- OAuth identity mismatch cannot activate the wrong ACP channel.
- OAuth retry does not replay Instagram/Threads creation.
- The final successful account stage is `ACP_ACTIVE`.
- Provider tokens/secrets remain backend-only.

## 21. Non-Goals

This design does not include:

- live Threads post publishing
- CAPTCHA/OTP solving
- anti-detection or fingerprint spoofing
- proxy/fingerprint evasion
- security challenge bypass
- fully offline physical-phone workflow
- automatic creation of large live batches before single-device acceptance passes

## 22. Final Product Flow

```text
                    CREATE ACCOUNT
                           │
                    choose target
                           │
             ┌─────────────┴─────────────┐
             │                           │
             ▼                           ▼
       THIS PHONE                   UBUNTU AVD
     LOCAL_DEVICE                 REMOTE_AVD
             │                           │
             └─────────────┬─────────────┘
                           ▼
                       Instagram
                           ↓
                  Human checkpoint
                           ↓
                      Verify IG
                           ↓
                      IG_CREATED
                           ↓
                       Threads
                           ↓
                  Human checkpoint
                           ↓
                   Verify Threads
                           ↓
                   THREADS_CREATED
                           ↓ automatic
                   ACP_CONNECTING
                           ↓
                   Official OAuth
                           ↓
                 Identity verification
                           ↓
                      ACP_ACTIVE ✅
```
