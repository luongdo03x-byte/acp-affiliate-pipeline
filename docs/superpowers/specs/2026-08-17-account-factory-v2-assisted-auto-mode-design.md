# ACP Account Factory V2 — Assisted Auto Mode Design

Date: 2026-08-17

## 1. Goal

Build Account Factory V2 as a PC-controlled, multi-AVD assisted workflow for onboarding a batch of 50 new Instagram → Threads profiles into ACP while minimizing repetitive operator work.

The Ubuntu PC is the source of truth and controller. Android Studio AVDs are workers. The Android phone is a remote dashboard and human-checkpoint controller.

The system automates orchestration, profile preparation, worker scheduling, state tracking, recovery, notifications, and official Threads OAuth into ACP. It does **not** automate or bypass CAPTCHA, OTP, selfie/device verification, platform security checks, or other protective controls. Account creation submission and required platform verification remain human checkpoints on the exact AVD session being used.

Success for V2 means the operator can start a 50-account batch, allow as many AVD workers as the machine can safely sustain, respond only to human-required checkpoints, and have completed Threads accounts connected to ACP with verified usernames and encrypted long-lived tokens.

## 2. Approved Product Decisions

- Deployment model: Android phone + Android Studio AVDs on Ubuntu.
- AVDs do most of the orchestration; human-required platform steps are handled manually in the same AVD session.
- Ubuntu PC is the primary controller. The phone is a dashboard/remote, not the source of truth.
- Worker count is adaptive, based on host CPU/RAM/swap/load and AVD health.
- Android Studio AVD is the standard emulator implementation.
- Remote access supports LAN/private VPN and public HTTPS fallback.
- Pairing uses a one-time QR flow; no permanent Factory Key is embedded in the QR.
- Multiple phones may be paired, but only one active ADMIN device is allowed. Other devices are VIEWER-only.
- Human-checkpoint reminders are configurable, default 10 minutes, and support Snooze.
- Controller architecture is Controller + independent Worker Agents rather than one monolithic worker loop.
- SQLite is sufficient for V2 on one Ubuntu host; persistence APIs are isolated behind repository boundaries.
- Profile generation is fully automatic for batch creation.
- Synthetic names are Vietnamese, approximately 70% female / 30% male.
- Affiliate niches are distributed across multiple categories.
- Avatar plans mix illustrations/graphics with objects/scenery/niche visuals rather than real-person impersonation.

## 3. Safety and Platform Boundary

V2 is an assisted orchestration system, not a protection-bypass system.

Allowed automation in V2:

- Generate synthetic profile metadata.
- Allocate and start AVD workers.
- Open the official Instagram/Threads app or web surface.
- Prepare profile values for the operator, including clipboard/pre-staged data where technically appropriate.
- Track which account and AVD are active.
- Detect generic workflow progress where reliable and non-invasive.
- Pause at human-required checkpoints.
- Resume only after the operator explicitly confirms completion and a post-check succeeds.
- Run official Threads OAuth and ACP onboarding.
- Verify OAuth username against the expected account.
- Encrypt and store Threads tokens only on the ACP backend.

Explicitly out of scope:

- CAPTCHA solving or bypass.
- OTP interception/bypass.
- Selfie, identity, or device-verification bypass.
- Anti-detection or fingerprint-spoofing systems.
- Proxy rotation intended to evade platform controls.
- Automated creation of disposable identities, fake documents, or impersonation of real people.
- Automatic submission through platform security checkpoints.
- Storing Instagram passwords, OTPs, CAPTCHA results, selfie data, Threads app secret, ACP master key, or plaintext Threads access tokens in Factory state.

If a platform checkpoint is encountered, the exact AVD session transitions to `WAITING_HUMAN`; other workers continue independently.

## 4. High-Level Architecture

```text
Android Phone
┌──────────────────────────┐
│ Dashboard                │
│ Checkpoints              │
│ Notifications            │
│ ADMIN remote actions     │
└────────────┬─────────────┘
             │ LAN / VPN / HTTPS
             ▼
Ubuntu PC — Factory Controller
┌───────────────────────────────────────────────┐
│ Identity Profile Generator                    │
│ Account Queue                                 │
│ State Machine                                 │
│ Adaptive Resource Manager                     │
│ AVD Pool Manager                              │
│ Worker Supervisor                             │
│ Checkpoint Manager                            │
│ Notification Scheduler                        │
│ Pairing/Auth                                  │
│ Factory REST API + realtime events            │
│ SQLite                                        │
│                                               │
│ Worker-01 → AVD-01                            │
│ Worker-02 → AVD-02                            │
│ Worker-03 → AVD-03                            │
│ ...                                           │
└──────────────────────┬────────────────────────┘
                       │ official Threads OAuth
                       ▼
ACP Backend
┌───────────────────────────────────────────────┐
│ OAuth state/session                           │
│ Expected username verification                │
│ Long-lived Threads token exchange             │
│ Token encryption                              │
│ channel upsert                                │
└───────────────────────────────────────────────┘
```

The phone must never talk directly to ADB or emulator console ports. All state-changing phone operations go through the Factory API and then through Controller/Supervisor boundaries.

## 5. Identity Profile Generator

### 5.1 Batch defaults

For a batch of 50:

- Approximately 35 female-name profiles and 15 male-name profiles.
- Vietnamese-style synthetic names.
- Display names preserve Vietnamese diacritics.
- Usernames are normalized without diacritics and should look natural rather than sequential or factory-generated.
- No use of celebrity names or deliberate impersonation of a specific real person.

Example output:

```text
Lê Mai Anh       → maianh.le
Trần Ngọc Linh   → ngoclinh.tran
Phạm Khánh Vy    → khanhvy.pham
Nguyễn Thảo My   → thaomy.ng
Vũ Minh Quân     → minhquan.vu
Hoàng Đức Anh    → ducanh.hoang
```

Avoid patterns such as:

```text
acp001
user00023
account50
khanhvy01 / khanhvy02 / khanhvy03
```

### 5.2 Username candidate scoring

For each synthetic profile, generate several candidates and choose the best based on:

- Naturalness.
- Match to display name.
- Shortness/readability.
- No duplicate inside the batch.
- Low numeric usage.
- Low structural similarity to other batch usernames.

If the platform reports a username unavailable during the human signup flow, the account may request a new candidate from the generator. V2 must not implement aggressive platform-wide username availability probing.

### 5.3 Niche distribution

Default 50-account distribution:

```text
Beauty / Skincare       9
Fashion / Accessories   9
Tech / Gadgets          8
Home / Lifestyle        8
Fitness / Wellness      8
Food / Kitchen          8
TOTAL                   50
```

Each account receives:

- `primary_niche`
- optional `secondary_interest`
- `personality_style`
- `content_tone`
- bio
- avatar plan

Personality examples: `minimal`, `friendly`, `casual`, `enthusiastic`, `reviewer`.

Bio generation should be varied within a niche and should not simply change the name in a single template.

### 5.4 Avatar plan

Default target mix:

- ~60% illustration/graphic avatar.
- ~40% object/scenery/niche visual.

Examples:

- Beauty: illustration, skincare flat-lay, flowers/vanity setup.
- Fashion: fashion illustration, outfit flat-lay, accessories.
- Tech: desk setup, keyboard/gadget, minimal tech graphic.
- Home: decor, plants, kitchen/organization.
- Fitness: sports illustration, shoes/gym gear, running scene.
- Food: coffee, kitchen tools, food illustration.

The generator stores `avatar_type`, `avatar_theme`, `avatar_prompt`, and later optionally `avatar_file`. It must not create an avatar intended to impersonate a real person.

## 6. Account State Machine

Primary happy path:

```text
NEW
 ↓
PROFILE_READY
 ↓
AVD_ASSIGNED
 ↓
IG_READY_FOR_HUMAN
 ↓
IG_CREATED
 ↓
THREADS_READY_FOR_HUMAN
 ↓
THREADS_CREATED
 ↓
ACP_CONNECTING
 ↓
ACP_ACTIVE
```

Supporting states:

```text
WAITING_HUMAN
NEEDS_VERIFICATION
NEEDS_CONFIRMATION
USERNAME_UNAVAILABLE
COOLDOWN
RETRY_PENDING
ERROR
DISABLED
```

The controller stores both `stage` and `last_safe_stage`.

Example:

```text
stage           = THREADS_READY_FOR_HUMAN
last_safe_stage = IG_CREATED
```

If the AVD crashes at this point, recovery resumes from `IG_CREATED`; the controller must not infer that Threads was successfully created.

### 6.1 Human checkpoint behavior

When a manual platform step is required:

```text
AUTO WORK
  ↓
WAITING_HUMAN
  ↓
notification
  ↓
operator completes required platform step in the same AVD
  ↓
CONTINUE
  ↓
VERIFYING / post-check
  ├─ pass → next workflow state
  └─ fail → WAITING_HUMAN or ERROR
```

`CONTINUE` is therefore a request to verify/resume, not a blind state change.

## 7. Controller and Worker Agent Model

Each AVD has one independent Worker Agent. Workers do not choose accounts; the Controller scheduler owns the queue and issues leased jobs.

Worker identity fields:

```text
worker_id
avd_name
adb_serial
state
current_account_id
current_job_id
last_heartbeat_at
last_progress_at
processed_count
recovery_count
estimated_ram_mb
current_ram_mb
current_cpu_percent
```

Worker states:

```text
STOPPED
STARTING
READY
RUNNING
WAITING_HUMAN
RECOVERING
DRAINING
ERROR
```

### 7.1 Lease model

Every assignment is represented by a job lease:

```text
Account #17
  ↓
job + lease_token
  ↓
Worker-03
```

Only one active lease may exist for an account. A worker crash or controller restart must reconcile the lease before the account can be assigned elsewhere.

### 7.2 Heartbeat

Workers periodically report:

- ADB online status.
- emulator boot status.
- current foreground package where observable.
- current account/job.
- current workflow state.
- last progress timestamp.
- current checkpoint status.

A missing heartbeat transitions a running worker to `UNRESPONSIVE`/`RECOVERING`; it does not immediately release the account to a different worker.

### 7.3 Desired vs observed state

Controller sends desired action; worker reports observed state.

Example:

```text
desired_action = PREPARE_THREADS
observed_state = THREADS_FOREGROUND
```

The controller advances workflow only when the observed state and safe transition rules agree.

### 7.4 Idempotent commands

Every controller command has a `command_id`. Repeated delivery of the same command must execute at most once for actions such as:

- `START_OAUTH`
- `MARK_CHECKPOINT`
- `RELEASE_ACCOUNT`
- `STOP_WORKER`

## 8. Adaptive AVD Pool

The system maximizes safe concurrency instead of using a hard-coded worker count.

### 8.1 Resource states

Default policy:

```text
GREEN
CPU < 65%
RAM available > 6 GB
swap activity near zero
→ may scale up

YELLOW
CPU 65–85%
RAM available 3–6 GB
or swap begins increasing
→ hold worker count

RED
CPU > 85%
RAM available < 3 GB
or sustained swap pressure
→ drain/scale down
```

These values are configurable defaults, not permanent constants.

The scheduler evaluates a rolling window; one short sample must not trigger scaling. Typical stability window: 30–60 seconds.

### 8.2 Scale up

Start at most one additional AVD at a time for the normal path:

```text
4 workers
→ start worker 5
→ wait READY
→ observe resources ~45s
→ still GREEN?
   yes → consider worker 6
   no  → hold
```

Maximum simultaneously `STARTING` AVDs should default to 1 and be configurable up to 2.

### 8.3 Scale down

Prefer draining rather than killing an active session:

1. Stop unused READY workers first.
2. Mark a worker DRAINING so it receives no new account.
3. Allow current safe step to finish, then stop.
4. Emergency stop only when host stability is at risk.

### 8.4 WAITING_HUMAN accounting

`WAITING_HUMAN` AVDs still consume memory and must count toward capacity.

Default human-wait pressure rule:

```text
maxWaitingHuman = min(3, approximately 40% of active pool)
```

If the limit is reached, pause scale-up until the number of pending manual checkpoints falls.

### 8.5 Learned AVD resource estimate

Controller keeps a rolling estimate of memory cost per AVD from observed processes. It may use this estimate to calculate a theoretical capacity ceiling, but live CPU/RAM/swap policy remains authoritative.

### 8.6 Emergency mode

Suggested default trigger includes RAM available below approximately 1.5 GB or strong host memory pressure.

Emergency behavior:

- no new AVD starts.
- no new account assignments.
- stop READY workers.
- drain RUNNING workers when safe.
- preserve WAITING_HUMAN sessions when possible.

## 9. Persistence Model

The Ubuntu Controller database is the authoritative state store. Phone Room becomes cache-only in V2.

### 9.1 `factory_batch`

Fields:

```text
id
name
target_count
status
created_at
started_at
completed_at
paused_at
desired_max_workers
reminder_interval_minutes
created_by_device_id
```

Batch states: `DRAFT`, `READY`, `RUNNING`, `PAUSED`, `COMPLETED`, `CANCELLED`.

### 9.2 `factory_account`

Fields:

```text
id
batch_id
sequence
group_no
username
display_name
bio
primary_niche
secondary_interest
personality_style
content_tone
avatar_type
avatar_theme
avatar_prompt
avatar_file
stage
last_safe_stage
assigned_worker_id
current_job_id
oauth_session_id
threads_user_id
channel_id
channel_code
retry_count
last_error_code
last_error_message
created_at
updated_at
completed_at
```

### 9.3 `factory_worker`

Fields:

```text
id
avd_name
adb_serial
state
current_account_id
current_job_id
pid
started_at
last_heartbeat_at
last_progress_at
processed_count
recovery_count
estimated_ram_mb
current_ram_mb
current_cpu_percent
draining
last_error
```

### 9.4 `factory_job`

Fields:

```text
id
account_id
worker_id
lease_token
state
desired_action
observed_state
command_id
leased_at
lease_expires_at
heartbeat_at
attempt
started_at
finished_at
error_code
```

### 9.5 `factory_checkpoint`

Fields:

```text
id
batch_id
account_id
worker_id
type
status
message
created_at
last_reminded_at
next_reminder_at
reminder_count
snoozed_until
resolved_at
resolved_by_device_id
resolution
```

Checkpoint types may include:

- `IG_SIGNUP_CONFIRMATION`
- `OTP_REQUIRED`
- `CAPTCHA_REQUIRED`
- `SELFIE_VERIFICATION`
- `DEVICE_VERIFICATION`
- `THREADS_CONFIRMATION`
- `ACCOUNT_STATE_CONFIRMATION`

These are classification/notification states only. They do not imply automated solving.

### 9.6 `factory_paired_device`

Fields:

```text
id
name
role
credential_hash
created_at
last_seen_at
revoked_at
revoked_by
push_registration
```

Roles: `ADMIN`, `VIEWER`.

Constraint: at most one active ADMIN.

### 9.7 `factory_resource_sample`

Fields:

```text
id
timestamp
cpu_percent
ram_total_mb
ram_available_mb
swap_used_mb
swap_in_rate
load_1m
load_5m
avd_total
avd_running
avd_waiting_human
capacity_state
desired_workers
```

Samples are operational telemetry and may be purged after a configurable retention period such as 7–14 days.

## 10. Controller Restart and Recovery

On controller boot:

```text
load DB
→ find RUNNING/PAUSED batches
→ discover/reconnect existing AVDs
→ reconcile workers
→ reconcile job leases
→ restore unresolved checkpoints/reminders
→ validate safe stages
→ resume queue if batch is RUNNING
```

No account may be advanced merely because a pre-restart worker disappeared.

## 11. Factory API

Namespace:

```text
/api/factory/v2/...
```

Representative read APIs:

```text
GET /api/factory/v2/dashboard
GET /api/factory/v2/batches/{batch_id}
GET /api/factory/v2/accounts
GET /api/factory/v2/accounts/{account_id}
GET /api/factory/v2/workers
GET /api/factory/v2/checkpoints
```

Representative ADMIN actions:

```text
POST /api/factory/v2/batches/{id}/pause
POST /api/factory/v2/batches/{id}/resume
POST /api/factory/v2/checkpoints/{id}/continue
POST /api/factory/v2/checkpoints/{id}/retry
POST /api/factory/v2/checkpoints/{id}/snooze
POST /api/factory/v2/accounts/{id}/stop
POST /api/factory/v2/accounts/{id}/retry
POST /api/factory/v2/workers/{id}/drain
POST /api/factory/v2/workers/{id}/restart
```

Phone requests never directly execute ADB commands. Controller validates permissions/state and delegates safe actions to Supervisor/Workers.

REST is authoritative. Realtime UI updates may use SSE or WebSocket events; disconnection falls back to REST refresh and does not affect the batch.

## 12. Phone V2

Main screens:

```text
Dashboard
Checkpoints
Accounts
Workers
Devices
Settings
```

Dashboard should surface human-required work first, followed by batch progress and resource capacity.

Example summary:

```text
ACTIVE          18 / 50
RUNNING          6
WAITING HUMAN    2
ERROR            1
QUEUED           23

Workers          7 / AUTO
CPU              58%
RAM              21 / 32 GB
Capacity         YELLOW
```

Checkpoint actions for ADMIN:

```text
CONTINUE
RETRY
SNOOZE
STOP ACCOUNT
```

VIEWER devices may view state and receive notifications but cannot perform state-changing actions.

## 13. QR Pairing and Device Authentication

Pairing flow:

```text
PC → create short-lived pairing session
→ show QR
→ phone scans
→ POST pairing completion
→ controller verifies token unused + unexpired
→ register device
→ consume pairing token
```

Defaults:

- TTL approximately 3 minutes.
- single use.
- QR does not contain the permanent Factory credential.

After pairing, the phone holds its own device credential in Android-protected application storage, with Android Keystore used for protection where applicable. Controller stores only a credential verifier/hash.

The device identity works across LAN/private VPN/public HTTPS endpoints for the same Controller.

## 14. Network and Remote Access

Connection priority:

```text
LAN/private endpoint
→ private VPN endpoint
→ public HTTPS fallback
```

Remote-control API must not be exposed over plaintext HTTP. Public fallback requires TLS and authentication.

Never expose ADB server, emulator console, or Worker internal RPC ports directly to the Internet.

If the phone or remote path is offline, Ubuntu workers continue. Accounts that reach human checkpoints wait; other workers may continue.

## 15. Notification Behavior

On `WAITING_HUMAN`, create a checkpoint notification immediately.

Default reminder interval: 10 minutes, configurable in Settings.

Snooze presets may include 10 minutes, 30 minutes, and 1 hour.

Once the checkpoint is resolved or leaves `WAITING_HUMAN`, its reminder schedule stops automatically.

## 16. ACP OAuth Integration

Reuse the P0 server-side security boundary:

```text
expected username
→ official Threads OAuth
→ short-lived token
→ long-lived token
→ /me profile fetch
→ actual username verification
→ encrypt token
→ upsert channel
→ ACP_ACTIVE
```

Hard rule: `actual_username != expected_username` results in `ACCOUNT_MISMATCH`; no channel is activated/updated for the mismatched onboarding session.

Android phone and Worker Agent receive only safe account/session status such as `threads_user_id` and `channel_code`; they do not receive the access token.

## 17. Error Handling and Retry

Errors are classified by layer:

```text
STEP ERROR
ACCOUNT ERROR
WORKER ERROR
CONTROLLER/HOST ERROR
```

Retries are bounded. Suggested initial policy:

- ADB reconnect: up to 3 attempts.
- Worker restart: up to 3 attempts.
- AVD boot: up to 3 attempts.
- transient network: bounded exponential backoff.
- OAuth: create a new OAuth session when the existing session is terminal/expired.

After the bound is exceeded, transition to `ERROR` or `RETRY_PENDING`; never create an infinite restart loop.

Recovery examples:

- Worker process dies → restart Worker Agent, preserve AVD/session if possible.
- ADB disconnects → reconnect; if unrecoverable, restart AVD.
- AVD crashes → restart and resume from `last_safe_stage`.
- Crash during human checkpoint → `NEEDS_CONFIRMATION`, not automatic success.
- Phone loses network → Factory continues.
- OAuth mismatch → account error, no ACP channel update.

## 18. Testing Strategy

Testing sequence:

```text
UNIT
→ INTEGRATION
→ FAKE WORKER
→ 1 REAL AVD
→ 2–3 REAL AVDs
→ RESOURCE SCALE TEST
→ 1 ACCOUNT END-TO-END
→ SMALL BATCH
→ FULL BATCH
```

### 18.1 Unit tests

Identity generator:

- 50 unique account IDs/usernames.
- approximate 70/30 name-gender distribution.
- expected niche distribution.
- normalized usernames.
- no simple sequential naming pattern.
- no duplicate username inside the batch.

State machine:

- legal happy-path transitions pass.
- illegal shortcuts such as `NEW → ACP_ACTIVE` fail.
- human checkpoints cannot be skipped by a raw Continue command without successful post-check.

Scheduler:

- GREEN may scale up.
- YELLOW holds.
- RED drains.
- WAITING_HUMAN AVD memory counts against capacity.
- waiting-human limit prevents unbounded scale-up.

Lease:

- one active account lease at a time.
- expired/missing-worker leases require reconciliation before reassignment.

Pairing/auth:

- expired QR rejected.
- reused QR rejected.
- VIEWER cannot call ADMIN actions.
- revoked devices rejected.
- only one active ADMIN.

### 18.2 Integration/failure tests

Use a FakeWorker protocol to simulate:

- RUNNING.
- WAITING_HUMAN.
- crash/recovery.
- successful ACP_ACTIVE.

Deliberately test:

- worker kill.
- emulator kill.
- ADB disconnect.
- controller restart.
- phone disconnect.
- OAuth username mismatch.
- low disk condition.
- low RAM/RED capacity state.

## 19. Delivery Scope

### P0 — Core Factory

Must deliver the first end-to-end usable flow:

- Controller SQLite schema/repositories.
- Identity Generator with Vietnamese synthetic profiles.
- 50-account batch creation.
- State machine + safe-stage semantics.
- Worker Supervisor.
- Android Studio AVD discovery/start/stop.
- ADB health detection.
- Account queue + job lease.
- basic Adaptive Resource Manager using CPU/RAM/swap.
- WAITING_HUMAN checkpoints.
- Factory REST API.
- Phone dashboard/accounts/checkpoints.
- Phone Continue/Retry controls.
- official Threads OAuth → username verification → ACP_ACTIVE.

P0 acceptance flow:

```text
START BATCH
→ profiles generated
→ Controller assigns account to AVD
→ worker prepares official flow
→ human checkpoint when required
→ Threads setup confirmation
→ ACP OAuth
→ ACP_ACTIVE
→ worker released
→ next account assigned
```

### P1 — Production usability

- QR pairing.
- ADMIN/VIEWER permissions.
- private VPN + public HTTPS fallback.
- notifications/reminders/Snooze.
- SSE/WebSocket realtime events.
- learned RAM-per-AVD estimates.
- improved worker recovery/drain flows.
- avatar generation pipeline.
- improved profile quality scoring.
- Devices and advanced Worker controls.

### P2 — Scale and intelligence

- multi-PC worker hosts.
- historical performance analytics.
- scheduler learning from account/hour, human wait time, AVD reliability, memory trend.
- dynamic niche allocation.
- richer profile-quality scoring.
- advanced operational dashboard.

Distributed multi-PC architecture is explicitly deferred until the single-PC controller is stable.

## 20. Acceptance Criteria

The V2 design is considered implemented when P0 can demonstrate all of the following on one Ubuntu PC:

1. Start a 50-profile batch with automatic Vietnamese synthetic profile generation.
2. Start multiple AVD workers subject to adaptive host-capacity policy.
3. Assign at most one worker lease per account.
4. Pause one account at a human checkpoint without stopping unrelated workers.
5. Resume an account only after operator action plus successful post-check.
6. Recover a worker/AVD failure without losing batch state or incorrectly advancing account state.
7. Restart the Controller and reconcile workers/jobs/checkpoints from SQLite.
8. Complete official Threads OAuth, reject username mismatch, and keep token handling server-side.
9. Reach `ACP_ACTIVE` for a verified account and release that worker to the next queued account.
10. Keep ADB/emulator control surfaces private and keep sensitive credentials/tokens out of Factory state.

## 21. Non-Goals for This Spec

- Fully unattended creation of platform accounts.
- Automation of security challenges or verification controls.
- Anti-detection systems.
- Proxy-evasion systems.
- Fake-document or identity-verification generation.
- Multi-PC orchestration in P0.
- High-scale analytics warehouse.
- Replacing ACP's existing encrypted Threads token storage model.
