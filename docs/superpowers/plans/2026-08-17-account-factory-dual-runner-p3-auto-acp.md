# Account Factory Dual-Runner P3 Auto ACP Activation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically continue every successfully created Threads account from `THREADS_CREATED` through official Threads OAuth to terminal `ACP_ACTIVE`, with safe retry from the Threads-complete stage.

**Architecture:** Keep the existing OAuth session/token/channel implementation and V2 OAuth bridge, but move activation initiation into the authoritative controller workflow. The controller creates the OAuth session, queues an `OPEN_URL` command to the assigned runner, and reconciles callback/status into `ACP_ACTIVE`; OAuth failure preserves `THREADS_CREATED` as the last safe stage.

**Tech Stack:** Python 3, Flask 3, existing `core.account_factory`, `core/factory_v2/oauth_bridge.py`, Factory V2 runtime/runner gateway, Kotlin/Compose Android client.

## Global Constraints

- Official Threads OAuth remains mandatory.
- Expected username always comes from authoritative `factory_account`.
- Username mismatch must never create/update the wrong channel.
- Threads token/app secret/master key remain backend-only.
- `THREADS_CREATED` automatically transitions to `ACP_CONNECTING`; no normal-flow Connect ACP button.
- OAuth cancellation/denial/expiry maps to retryable `RETRY_PENDING + OAUTH_FAILED` with `last_safe_stage=THREADS_CREATED`.
- OAuth retry must not replay Instagram or Threads creation.
- Terminal success is exactly `ACP_ACTIVE`.
- Do not publish a Threads post as part of activation acceptance.

---

## File Structure

- Create `core/factory_v2/activation.py` — controller-owned OAuth activation orchestration.
- Modify `core/factory_v2/oauth_bridge.py` only where needed for idempotent controller use.
- Modify `core/factory_v2/runtime.py` — auto activation after Threads completion and retry.
- Modify `core/factory_v2/runner_gateway.py` — `OPEN_URL` action for both runners.
- Modify `core/factory_v2/avd.py` / worker protocol if necessary for safe URL opening.
- Modify `web/account_factory.py` — keep callback sync, share provider/redirect construction through reusable functions.
- Modify `web/factory_v2.py` — status/retry API semantics; legacy manual OAuth start may remain backward-compatible but is not normal flow.
- Modify Android `LocalDeviceActions.kt`, DTO/ViewModel/UI.
- Add `tests/test_factory_v2_activation.py`, update OAuth/runtime tests, add Android activation presentation tests.

### Task 1: Extract reusable OAuth provider/redirect construction

**Files:**
- Modify: `web/account_factory.py`
- Create: `core/factory_v2/oauth_config.py`
- Modify: `tests/test_factory_v2_oauth_bridge.py`

**Interfaces:**
- Produces `build_factory_redirect_uri(base_url: str) -> str`.
- Produces `build_threads_oauth_provider(app=None)` or a dependency-injected provider factory callable used by both routes and activation service.

- [ ] **Step 1: Write failing redirect/provider tests**

```python
from core.factory_v2.oauth_config import build_factory_redirect_uri


def test_factory_redirect_uri_is_exact_callback():
    self.assertEqual(
        "https://factory.example.com/oauth/account-factory/threads/callback",
        build_factory_redirect_uri("https://factory.example.com/"),
    )
```

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_factory_v2_oauth_bridge -v
```
Expected: FAIL because `oauth_config` does not exist.

- [ ] **Step 3: Extract helpers without changing callback behavior**

Move only URL/provider construction; leave token exchange, mismatch enforcement, session persistence, and callback responses in existing modules. `ACP_PUBLIC_BASE_URL` remains the preferred production base URL; route request host fallback remains for local tests.

- [ ] **Step 4: Run OAuth regression tests**

```bash
python3 -m unittest tests.test_account_factory tests.test_factory_v2_oauth_bridge tests.test_factory_v2_oauth_expiry -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/factory_v2/oauth_config.py web/account_factory.py tests/test_factory_v2_oauth_bridge.py
git commit -m "refactor: share factory oauth configuration"
```

### Task 2: Idempotent controller activation service

**Files:**
- Create: `core/factory_v2/activation.py`
- Modify: `core/factory_v2/oauth_bridge.py`
- Create: `tests/test_factory_v2_activation.py`

**Interfaces:**
- Produces `FactoryActivationService.start(account_id: str) -> dict`.
- Produces `FactoryActivationService.reconcile(account_id: str) -> dict`.
- Repeated `start()` while an unexpired `WAITING_AUTH` session exists returns that same session/URL and does not create a duplicate.

- [ ] **Step 1: Write failing activation tests**

```python
def test_threads_created_starts_oauth_and_marks_connecting(self):
    account = self.seed_account(stage="THREADS_CREATED", last_safe_stage="THREADS_CREATED")
    result = self.activation.start(account["id"])
    saved = self.repo.get_account(account["id"])
    self.assertEqual("ACP_CONNECTING", saved["stage"])
    self.assertEqual(result["session_id"], saved["oauth_session_id"])
    self.assertTrue(result["authorization_url"].startswith("https://"))


def test_start_is_idempotent_for_waiting_session(self):
    account = self.seed_account(stage="THREADS_CREATED", last_safe_stage="THREADS_CREATED")
    first = self.activation.start(account["id"])
    second = self.activation.start(account["id"])
    self.assertEqual(first["session_id"], second["session_id"])
```

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_factory_v2_activation -v
```
Expected: FAIL because activation service does not exist.

- [ ] **Step 3: Implement service using existing bridge**

`start()` accepts only:
- `THREADS_CREATED`; or
- `RETRY_PENDING` with `last_error_code='OAUTH_FAILED'` and `last_safe_stage='THREADS_CREATED'`.

It calls `start_account_oauth()` with authoritative account identity and configured redirect/provider. For retry, resolve/expire old terminal session before creating the new one. Return only `session_id`, `authorization_url`, `status`, `expires_at`.

`reconcile()` delegates to `sync_account_from_oauth_session()` and returns authoritative account fields.

- [ ] **Step 4: Add failure mapping tests**

```python
def test_oauth_failure_preserves_threads_safe_stage(self):
    account = self.seed_account(stage="ACP_CONNECTING", last_safe_stage="THREADS_CREATED")
    self.mark_oauth_error(account)
    self.activation.reconcile(account["id"])
    saved = self.repo.get_account(account["id"])
    self.assertEqual("RETRY_PENDING", saved["stage"])
    self.assertEqual("THREADS_CREATED", saved["last_safe_stage"])
    self.assertEqual("OAUTH_FAILED", saved["last_error_code"])
```

- [ ] **Step 5: Run tests and commit**

```bash
python3 -m unittest tests.test_factory_v2_activation tests.test_factory_v2_oauth_bridge tests.test_factory_v2_oauth_expiry -v
git add core/factory_v2/activation.py core/factory_v2/oauth_bridge.py tests/test_factory_v2_activation.py
git commit -m "feat: add controller-owned ACP activation"
```

### Task 3: Safe `OPEN_URL` runner action

**Files:**
- Modify: `core/factory_v2/runner_gateway.py`
- Modify: `core/factory_v2/avd.py`
- Modify: `core/factory_v2/worker_protocol.py`
- Modify: `workers/account_factory_worker.py`
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/runner/LocalDeviceActions.kt`
- Modify: related Python/Kotlin tests.

**Interfaces:**
- Runner action `OPEN_URL` accepts only an HTTPS authorization URL created by the controller activation service.
- AVD implementation uses existing `AvdManager.open_url(serial, url)`.
- Local Android implementation uses `Intent.ACTION_VIEW`.

- [ ] **Step 1: Write failing URL validation tests**

Python:

```python
def test_open_url_rejects_non_https(self):
    with self.assertRaises(ValueError):
        validate_factory_authorization_url("http://example.com/oauth")
```

Kotlin:

```kotlin
@Test
fun `open url rejects non https scheme`() {
    val result = actions.execute(command("OPEN_URL", mapOf("url" to "http://example.com")))
    assertEquals("FAILED", result.status)
    assertEquals("URL_NOT_ALLOWED", result.result["error_code"])
}
```

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_factory_v2_runner_gateway tests.test_factory_v2_avd -v
gradle -p android/account-factory testDebugUnitTest --tests '*LocalDeviceActionsTest' --no-daemon --max-workers=2 --console=plain
```
Expected: FAIL for missing `OPEN_URL` support/validation.

- [ ] **Step 3: Implement validation/execution**

Controller only queues the exact URL returned by `FactoryActivationService.start()`. Validate scheme is `https`; reject embedded username/password and control characters. Do not accept arbitrary phone-supplied redirect/auth URLs.

- [ ] **Step 4: Run transport tests**

```bash
python3 -m unittest tests.test_factory_v2_runner_gateway tests.test_factory_v2_avd tests.test_factory_v2_worker_process -v
gradle -p android/account-factory testDebugUnitTest --tests '*LocalDeviceActionsTest' --no-daemon --max-workers=2 --console=plain
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/factory_v2/runner_gateway.py core/factory_v2/avd.py core/factory_v2/worker_protocol.py workers/account_factory_worker.py android/account-factory/app/src/main/java/com/acp/accountfactory/runner/LocalDeviceActions.kt
git commit -m "feat: open official oauth on assigned runner"
```

### Task 4: Runtime auto-activation after Threads completion

**Files:**
- Modify: `core/factory_v2/runtime.py`
- Modify: runtime test files.

**Interfaces:**
- After atomic Threads post-check succeeds and account reaches `THREADS_CREATED`, runtime starts activation and queues `OPEN_URL` to the same assigned runner before releasing that runner.
- Job remains associated through the OAuth human wait, or transitions to an explicit activation wait state while still occupying the runner.

- [ ] **Step 1: Write failing runtime success test**

```python
def test_threads_postcheck_automatically_starts_acp_activation(self):
    job, account = self.seed_threads_verifying_job()
    self.fake_runner.observe_package = "com.instagram.barcelona"
    self.runtime.tick()
    saved = self.repo.get_account(account["id"])
    self.assertEqual("ACP_CONNECTING", saved["stage"])
    self.assertEqual("THREADS_CREATED", saved["last_safe_stage"])
    self.assertEqual("OPEN_URL", self.fake_gateway.commands[-1].action)
```

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_factory_v2_runtime tests.test_factory_v2_runtime_atomicity -v
```
Expected: FAIL because current runtime stops at `THREADS_CREATED`/job release.

- [ ] **Step 3: Implement atomic handoff**

Within the Threads success transaction:
1. persist `THREADS_CREATED` as last safe stage;
2. start/reuse OAuth activation;
3. transition account to `ACP_CONNECTING`;
4. keep job/runner in `WAITING_HUMAN` with checkpoint type `ACP_OAUTH`;
5. after transaction commits, queue `OPEN_URL`.

If URL queueing fails after OAuth session creation, preserve `ACP_CONNECTING` and allow runtime retry of the same OAuth session/URL; do not create a duplicate session.

- [ ] **Step 4: Add restart/idempotency regression**

A controller restart while `ACP_CONNECTING` with a valid WAITING_AUTH session must re-open/re-deliver the same activation URL only when needed and must not move account back to Threads creation.

- [ ] **Step 5: Run runtime suites and commit**

```bash
python3 -m unittest tests.test_factory_v2_runtime tests.test_factory_v2_runtime_atomicity tests.test_factory_v2_runtime_resume tests.test_factory_v2_restart_recovery tests.test_factory_v2_activation -v
git add core/factory_v2/runtime.py tests/test_factory_v2_runtime.py tests/test_factory_v2_runtime_atomicity.py tests/test_factory_v2_runtime_resume.py tests/test_factory_v2_restart_recovery.py
git commit -m "feat: auto activate ACP after Threads creation"
```

### Task 5: Reconcile OAuth completion and release runner

**Files:**
- Modify: `core/factory_v2/runtime.py`
- Modify: `core/factory_v2/scheduler.py`
- Modify: `core/factory_v2/service.py`
- Modify: activation/runtime tests.

**Interfaces:**
- While account is `ACP_CONNECTING`, runtime periodically calls `activation.reconcile(account_id)`.
- `ACP_ACTIVE` resolves `ACP_OAUTH` checkpoint and releases job/runner READY.
- Retryable OAuth failure resolves/reopens checkpoint as appropriate, releases the platform runner, and leaves account `RETRY_PENDING/OAUTH_FAILED`.

- [ ] **Step 1: Write failing completion tests**

```python
def test_oauth_active_releases_runner_and_completes_account(self):
    job, account = self.seed_acp_connecting_job()
    self.fake_activation.next_stage = "ACP_ACTIVE"
    self.runtime.tick()
    self.assertEqual("ACP_ACTIVE", self.repo.get_account(account["id"])["stage"])
    self.assertEqual("COMPLETED", self.repo.get_job(job["id"])["state"])
    self.assertEqual("READY", self.repo.get_worker(job["worker_id"])["state"])


def test_oauth_failure_releases_runner_but_retries_only_activation(self):
    job, account = self.seed_acp_connecting_job(last_safe_stage="THREADS_CREATED")
    self.fake_activation.next_stage = "RETRY_PENDING"
    self.runtime.tick()
    saved = self.repo.get_account(account["id"])
    self.assertEqual("OAUTH_FAILED", saved["last_error_code"])
    self.assertEqual("THREADS_CREATED", saved["last_safe_stage"])
```

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_factory_v2_runtime tests.test_factory_v2_scheduler_recovery -v
```
Expected: FAIL for missing ACP_CONNECTING reconciliation.

- [ ] **Step 3: Implement reconciliation/release**

`ACP_ACTIVE` is terminal success; set `completed_at`, resolve checkpoint `ACP_ACTIVE`, release job as `COMPLETED`, worker READY.

`RETRY_PENDING/OAUTH_FAILED` releases the runner because OAuth can be retried later without occupying Instagram/Threads runtime. Scheduler must explicitly exclude OAUTH_FAILED from platform runner leasing.

- [ ] **Step 4: Run recovery/OAuth suites**

```bash
python3 -m unittest tests.test_factory_v2_activation tests.test_factory_v2_oauth_bridge tests.test_factory_v2_oauth_expiry tests.test_factory_v2_runtime tests.test_factory_v2_scheduler_recovery -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/factory_v2/runtime.py core/factory_v2/scheduler.py core/factory_v2/service.py tests/test_factory_v2_runtime.py tests/test_factory_v2_scheduler_recovery.py
git commit -m "feat: complete factory accounts at ACP active"
```

### Task 6: Android auto-activation UX and retry-only action

**Files:**
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/ui/FactoryViewModel.kt`
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/ui/AccountsScreen.kt`
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/ui/CheckpointsScreen.kt`
- Create/modify: Android ViewModel/presentation tests.

**Interfaces:**
- Normal `THREADS_CREATED` flow does not show `Connect ACP`.
- When local runner receives controller `OPEN_URL`, it opens OAuth automatically.
- UI displays `Activating ACP...` for `ACP_CONNECTING`.
- `Retry ACP activation` appears only for `RETRY_PENDING` + `OAUTH_FAILED`.

- [ ] **Step 1: Write failing presentation tests**

```kotlin
@Test
fun `threads created does not show manual connect button`() {
    val actions = accountActions(account(stage = "THREADS_CREATED"))
    assertFalse(actions.contains(AccountAction.ConnectAcp))
}

@Test
fun `oauth failed shows retry activation only`() {
    val actions = accountActions(account(stage = "RETRY_PENDING", lastErrorCode = "OAUTH_FAILED"))
    assertEquals(listOf(AccountAction.RetryAcpActivation), actions)
}
```

- [ ] **Step 2: Run and verify RED**

```bash
gradle -p android/account-factory testDebugUnitTest --tests '*FactoryPresentationTest' --tests '*FactoryViewModelTest' --no-daemon --max-workers=2 --console=plain
```
Expected: FAIL because current UI exposes manual OAuth start from account list.

- [ ] **Step 3: Implement UI semantics**

Remove normal-flow Connect ACP action. Keep `startOAuth()` only as a compatibility/internal retry path if API still exposes it, and expose a clearly named `retryAcpActivation(accountId)` action for OAUTH_FAILED. Refresh/poll account state during `ACP_CONNECTING` without storing token/session secrets.

- [ ] **Step 4: Run Android full gate**

```bash
gradle -p android/account-factory testDebugUnitTest assembleDebug --no-daemon --max-workers=2 --console=plain
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add android/account-factory/app/src/main/java/com/acp/accountfactory/ui android/account-factory/app/src/test/java/com/acp/accountfactory/ui
git commit -m "feat: make ACP activation automatic in app"
```

## Completion Gate

Backend:

```bash
python3 -m unittest tests.test_account_factory tests.test_factory_v2_activation tests.test_factory_v2_oauth_bridge tests.test_factory_v2_oauth_expiry tests.test_factory_v2_runtime tests.test_factory_v2_runtime_atomicity tests.test_factory_v2_runtime_resume tests.test_factory_v2_restart_recovery tests.test_factory_v2_scheduler_recovery -v
```

Android:

```bash
gradle -p android/account-factory testDebugUnitTest assembleDebug --no-daemon --max-workers=2 --console=plain
```

Required behavior:

```text
THREADS_CREATED -> ACP_CONNECTING -> ACP_ACTIVE
```

and on OAuth failure:

```text
THREADS_CREATED -> ACP_CONNECTING -> RETRY_PENDING/OAUTH_FAILED
```

with retry resuming activation only.