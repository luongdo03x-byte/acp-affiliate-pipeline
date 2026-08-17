# Account Factory Dual-Runner P4 End-to-End Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify the completed Account Factory architecture end-to-end for both a physical Android phone and an Ubuntu AVD, including automatic ACP activation, recovery, CI coverage, and operator runbook updates.

**Architecture:** Treat backend tests, Android unit/build tests, local-device smoke tests, AVD smoke tests, and official OAuth acceptance as separate evidence gates. Automated tests may use fake providers/runners; real acceptance requires one physical phone and one real AVD to each reach `ACP_ACTIVE` without live Threads publishing.

**Tech Stack:** Python `unittest`, Gradle Android unit/build, GitHub Actions, Android SDK/ADB/AVD, official Threads OAuth for final manual acceptance.

## Global Constraints

- Do not merge `main` as part of this plan.
- Do not read or print secrets.
- No live Threads publishing.
- No CAPTCHA/OTP solving, security-check bypass, fingerprint spoofing, proxy/fingerprint evasion, or credential automation.
- Real acceptance starts with one account per runner type, not a large batch.
- `ACP_ACTIVE` is the only successful terminal creation state.
- Physical phone and AVD evidence must be recorded separately.

---

## File Structure

- Modify `.github/workflows/account-factory-ci.yml` — include all new backend + Android suites.
- Create `tests/test_factory_v2_dual_runner_e2e.py` — fake-runner/controller integration test.
- Modify `docs/ACP_ACCOUNT_FACTORY_RUNBOOK.md` — dedicated service + dual-runner setup/acceptance.
- Optionally create `scripts/check_account_factory_env.py` if no equivalent safe preflight exists.
- Modify tests only as required to make acceptance deterministic; no production behavior should be added solely for test convenience.

### Task 1: Fake dual-runner E2E integration test

**Files:**
- Create: `tests/test_factory_v2_dual_runner_e2e.py`

**Interfaces:**
- Uses real Factory V2 repository/service/scheduler/runtime with fake runner gateway and fake OAuth provider.
- Executes the same logical workflow once as `LOCAL_DEVICE` and once as `REMOTE_AVD`.

- [ ] **Step 1: Write failing parameterized-style unittest cases**

```python
class DualRunnerE2ETests(unittest.TestCase):
    def test_local_device_reaches_acp_active(self):
        self._run_success_flow("LOCAL_DEVICE")

    def test_remote_avd_reaches_acp_active(self):
        self._run_success_flow("REMOTE_AVD")

    def _run_success_flow(self, runner_type):
        worker = self.seed_worker(runner_type=runner_type, state="READY")
        account = self.seed_account(execution_target=worker["id"])

        self.drive_until_checkpoint("IG_POSTCHECK")
        self.fake_gateway.set_foreground("com.instagram.android")
        self.continue_latest_checkpoint(account["id"])

        self.drive_until_checkpoint("THREADS_POSTCHECK")
        self.fake_gateway.set_foreground("com.instagram.barcelona")
        self.continue_latest_checkpoint(account["id"])

        self.fake_oauth.complete_success(account["username"])
        self.drive_until_stage(account["id"], "ACP_ACTIVE")

        saved = self.repo.get_account(account["id"])
        self.assertEqual("ACP_ACTIVE", saved["stage"])
        self.assertIsNotNone(saved["channel_code"])
```

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_factory_v2_dual_runner_e2e -v
```
Expected: FAIL until P1–P3 are implemented.

- [ ] **Step 3: Complete deterministic fake fixtures only**

Use fake runner responses for foreground package observations and fake OAuth provider success. Never bypass the production state machine; tests must call the same service/API/runtime methods used by real runners.

- [ ] **Step 4: Add OAuth retry E2E case**

```python
def test_oauth_failure_retries_without_recreating_platform_accounts(self):
    account = self.drive_to_threads_created("LOCAL_DEVICE")
    ig_checkpoint_count = self.count_checkpoint_type(account["id"], "IG_POSTCHECK")
    threads_checkpoint_count = self.count_checkpoint_type(account["id"], "THREADS_POSTCHECK")

    self.fake_oauth.complete_error()
    self.drive_until_stage(account["id"], "RETRY_PENDING")
    self.retry_acp_activation(account["id"])
    self.fake_oauth.complete_success(account["username"])
    self.drive_until_stage(account["id"], "ACP_ACTIVE")

    self.assertEqual(ig_checkpoint_count, self.count_checkpoint_type(account["id"], "IG_POSTCHECK"))
    self.assertEqual(threads_checkpoint_count, self.count_checkpoint_type(account["id"], "THREADS_POSTCHECK"))
```

- [ ] **Step 5: Run and commit**

```bash
python3 -m unittest tests.test_factory_v2_dual_runner_e2e -v
git add tests/test_factory_v2_dual_runner_e2e.py
git commit -m "test: cover dual runner account activation e2e"
```

### Task 2: Recovery acceptance tests

**Files:**
- Modify: `tests/test_factory_v2_dual_runner_e2e.py`
- Modify: existing recovery tests only where shared helpers are needed.

**Interfaces:**
- Runner loss during `WAITING_HUMAN` -> `NEEDS_CONFIRMATION`.
- Runner loss before human checkpoint -> recover/retry from `last_safe_stage`.
- OAuth failure preserves `THREADS_CREATED` safe state.

- [ ] **Step 1: Add physical-runner-loss ambiguity test**

```python
def test_local_runner_loss_during_human_checkpoint_needs_confirmation(self):
    account, worker = self.drive_to_waiting_human("LOCAL_DEVICE", checkpoint="IG_POSTCHECK")
    self.expire_worker_heartbeat(worker["id"])
    self.scheduler.reconcile_expired_leases(self.now_after_expiry())
    saved = self.repo.get_account(account["id"])
    self.assertEqual("NEEDS_CONFIRMATION", saved["stage"])
    self.assertEqual("PROFILE_READY", saved["last_safe_stage"])
```

- [ ] **Step 2: Add AVD loss/resume test**

Resume from `last_safe_stage=IG_CREATED` must continue at Threads preparation, not Instagram preparation.

- [ ] **Step 3: Run focused recovery suites**

```bash
python3 -m unittest tests.test_factory_v2_dual_runner_e2e tests.test_factory_v2_scheduler_recovery tests.test_factory_v2_restart_recovery tests.test_factory_v2_runtime_resume -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_factory_v2_dual_runner_e2e.py tests/test_factory_v2_scheduler_recovery.py tests/test_factory_v2_restart_recovery.py tests/test_factory_v2_runtime_resume.py
git commit -m "test: verify dual runner recovery semantics"
```

### Task 3: CI gate includes every Factory V2 suite

**Files:**
- Modify: `.github/workflows/account-factory-ci.yml`

**Interfaces:**
- Backend CI runs every `tests/test_factory_v2_*.py` suite plus `tests.test_account_factory`.
- Android CI runs unit tests and `assembleDebug`.

- [ ] **Step 1: List current Factory V2 test modules**

Run locally:

```bash
find tests -maxdepth 1 -name 'test_factory_v2_*.py' -printf '%f\n' | sort
```

- [ ] **Step 2: Update backend CI command/matrix**

Ensure all new modules are included, especially:

```text
test_factory_v2_factory_app
test_factory_v2_runner_schema
test_factory_v2_runner_service
test_factory_v2_dual_scheduler
test_factory_v2_runner_api
test_factory_v2_runner_gateway
test_factory_v2_runner_commands_api
test_factory_v2_activation
test_factory_v2_dual_runner_e2e
```

- [ ] **Step 3: Keep Android CI exact gate**

```bash
gradle -p android/account-factory testDebugUnitTest assembleDebug --no-daemon --max-workers=2 --console=plain
```

- [ ] **Step 4: Verify workflow references every test file**

Use a local script/check that compares `find tests -name 'test_factory_v2_*.py'` against workflow text and fails on missing suite.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/account-factory-ci.yml
git commit -m "ci: gate dual runner account factory"
```

### Task 4: Safe environment preflight

**Files:**
- Create: `scripts/check_account_factory_env.py`
- Create: `tests/test_account_factory_env_check.py`

**Interfaces:**
- Checks existence/presence without printing secret values.
- Reports controller, Android SDK, ADB/emulator availability, required OAuth env presence, and DB path.

- [ ] **Step 1: Write failing redaction test**

```python
def test_preflight_never_prints_secret_value(self):
    env = {
        "ACP_FACTORY_API_KEY": "super-secret-key",
        "THREADS_APP_SECRET": "super-secret-app-secret",
    }
    output = run_preflight_for_test(env)
    self.assertNotIn("super-secret-key", output)
    self.assertNotIn("super-secret-app-secret", output)
    self.assertIn("ACP_FACTORY_API_KEY=SET", output)
```

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_account_factory_env_check -v
```
Expected: FAIL because preflight script does not exist.

- [ ] **Step 3: Implement read-only checks**

Output examples:

```text
ACP_FACTORY_API_KEY=SET
ACP_PUBLIC_BASE_URL=SET
THREADS_APP_ID=SET
THREADS_APP_SECRET=SET
ANDROID_HOME=SET
adb=OK
emulator=OK
factory_db=/absolute/path/to/db
```

Never print env values except non-secret paths/booleans.

- [ ] **Step 4: Run test and commit**

```bash
python3 -m unittest tests.test_account_factory_env_check -v
git add scripts/check_account_factory_env.py tests/test_account_factory_env_check.py
git commit -m "chore: add safe factory environment preflight"
```

### Task 5: Rewrite operator runbook for dedicated dual-runner service

**Files:**
- Modify: `docs/ACP_ACCOUNT_FACTORY_RUNBOOK.md`

**Interfaces:**
- Documents exact commands for dedicated server, physical phone setup, AVD setup, one-account acceptance, OAuth, and recovery.

- [ ] **Step 1: Replace obsolete ACP publishing-web startup guidance**

Runbook startup must use the Account Factory server only:

```bash
cd ~/Downloads/ACP/worktrees/account-factory-android
source .venv/bin/activate
python3 scripts/check_account_factory_env.py
export ACP_FACTORY_CONTROLLER=1
export ACP_FACTORY_TICK_SECONDS=2
export ACP_HOST=0.0.0.0
export ACP_PORT=5001
python3 account_factory_server.py
```

State explicitly that `GET /` is a factory service status response and no publishing dashboard/table initialization is required.

- [ ] **Step 2: Document physical phone acceptance**

Exact checklist:

```text
1. Install app-debug.apk.
2. Configure controller URL/key once.
3. Enable Account Factory accessibility observation service.
4. Runners -> physical phone READY.
5. Create Account -> This phone.
6. Complete official Instagram/Threads human checkpoints when requested.
7. Official OAuth opens automatically.
8. Final state ACP_ACTIVE.
```

- [ ] **Step 3: Document AVD acceptance**

Include:

```bash
adb devices
emulator -list-avds
```

and expected controller lifecycle `STARTING -> READY -> RUNNING/WAITING_HUMAN -> READY`.

- [ ] **Step 4: Document recovery/OAuth retry semantics**

State clearly:

```text
OAuth failure -> RETRY_PENDING/OAUTH_FAILED -> Retry ACP activation
```

and it must not regenerate Instagram/Threads.

- [ ] **Step 5: Commit**

```bash
git add docs/ACP_ACCOUNT_FACTORY_RUNBOOK.md
git commit -m "docs: update dual runner account factory runbook"
```

### Task 6: Automated verification gate

**Files:**
- No production file changes unless a real failing regression is found.

- [ ] **Step 1: Run complete backend suite**

```bash
python3 -m unittest discover -s tests -p 'test_factory_v2_*.py' -v
python3 -m unittest tests.test_account_factory tests.test_account_factory_env_check -v
```
Expected: all tests PASS.

- [ ] **Step 2: Run Android full gate**

```bash
gradle -p android/account-factory testDebugUnitTest assembleDebug --no-daemon --max-workers=2 --console=plain
```
Expected: `BUILD SUCCESSFUL` and APK exists at:

```text
android/account-factory/app/build/outputs/apk/debug/app-debug.apk
```

- [ ] **Step 3: Verify dedicated server boot against factory-only DB**

Using a disposable DB path:

```bash
TMP_DB="$(mktemp -u)/factory.db"
ACP_DB="$TMP_DB" ACP_FACTORY_API_KEY=test-key ACP_FACTORY_CONTROLLER=0 ACP_PORT=5001 python3 account_factory_server.py
```

From another terminal:

```bash
curl -fsS http://127.0.0.1:5001/
curl -fsS http://127.0.0.1:5001/healthz
```

Expected: HTTP 200 factory JSON; no `no such table: post`.

- [ ] **Step 4: Verify GitHub Actions on exact HEAD**

Record exact commit SHA and confirm both backend and Android jobs are successful for that SHA. Do not infer CI success from older commits.

### Task 7: Real physical phone acceptance gate

**Files:**
- No code changes unless a reproducible defect is found; fixes must return to TDD first.

- [ ] **Step 1: Install exact built APK**

```bash
adb install -r android/account-factory/app/build/outputs/apk/debug/app-debug.apk
```

- [ ] **Step 2: Register phone and create one account**

Select `This phone`; verify controller shows `runner_type=LOCAL_DEVICE` and one active lease.

- [ ] **Step 3: Complete human checkpoints manually**

Do not automate credentials/password/OTP/CAPTCHA/security challenges. Confirm failed post-check never advances account stage.

- [ ] **Step 4: Complete official OAuth**

Verify URL opens automatically after Threads verification and final controller account becomes `ACP_ACTIVE` with safe channel metadata only.

- [ ] **Step 5: Record acceptance result**

Record only non-secret evidence: account id, runner id/type, stage transitions, final `ACP_ACTIVE`, timestamps, and any allowlisted error codes.

### Task 8: Real Ubuntu AVD acceptance gate

**Files:**
- No code changes unless a reproducible defect is found; fixes must return to TDD first.

- [ ] **Step 1: Confirm one real AVD**

```bash
emulator -list-avds
adb devices
```

Use one `acp-worker-*` AVD only for first acceptance.

- [ ] **Step 2: Create one AVD-targeted account**

Select `Auto-select AVD` or exact READY AVD. Verify `REMOTE_AVD` lease and heartbeat.

- [ ] **Step 3: Complete human checkpoints manually**

Confirm AVD worker opens official apps, reports checkpoints, and controller verifies foreground package before stage advancement.

- [ ] **Step 4: Complete official OAuth**

OAuth URL opens on assigned AVD; final account becomes `ACP_ACTIVE`.

- [ ] **Step 5: Test one ambiguity recovery case**

During a disposable human checkpoint, stop/restart the runner/AVD and verify account becomes `NEEDS_CONFIRMATION` rather than falsely advancing.

## Final Acceptance Criteria

All of the following must be true before calling Dual Runner production-accepted:

```text
Backend full Factory V2 suite          PASS
Legacy Account Factory OAuth suite     PASS
Android unit tests                     PASS
Android assembleDebug                  PASS
Factory-only server boot               PASS
GitHub Actions exact HEAD              PASS
Physical phone -> ACP_ACTIVE           PASS
Ubuntu AVD -> ACP_ACTIVE               PASS
OAuth retry without IG/Threads replay  PASS
WAITING_HUMAN runner-loss ambiguity    PASS
Live Threads publish                   NOT RUN
```

Do not mark the two real-device gates PASS without observed evidence from the actual phone/AVD environment.