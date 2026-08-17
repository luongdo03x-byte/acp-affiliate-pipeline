# Account Factory V2 P0 API and Phone Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose Factory V2 controller state through a safe REST API and convert the existing Android app from local source-of-truth into a remote dashboard/checkpoint controller.

**Architecture:** Flask routes read/write only through Factory V2 services. P0 keeps the existing `X-ACP-Factory-Key` authentication boundary; QR pairing and ADMIN/VIEWER device credentials remain P1. Android Room becomes optional cache only, while REST responses drive the dashboard, accounts, checkpoints, and worker views.

**Tech Stack:** Flask, existing ACP app launcher, Python `unittest`, Kotlin/Compose, OkHttp, Room cache, Gradle Android unit tests.

## Global Constraints

- API namespace is `/api/factory/v2/...`.
- Phone never calls ADB or emulator console directly.
- P0 authentication uses existing `X-ACP-Factory-Key`; do not embed that value in source or logs.
- REST is authoritative in P0; realtime SSE/WebSocket is P1.
- Phone actions `CONTINUE`, `RETRY`, `STOP`, `PAUSE`, `RESUME` must be controller commands, not blind local state changes.
- `CONTINUE` invokes a server/worker post-check; it does not directly mark a platform step successful.
- Android must not store Threads access tokens, Instagram passwords, OTPs, CAPTCHA results, Threads app secret, or ACP master key.
- Android build remains compileSdk/targetSdk 36, minSdk 26, Java/Kotlin 17.

---

## File Structure

- Create `web/factory_v2.py` — REST routes and response serializers.
- Modify `account_factory_server.py` — register V2 routes alongside OAuth routes.
- Create `tests/test_factory_v2_api.py` — Flask route authorization/state tests.
- Create `android/account-factory/app/src/main/java/com/acp/accountfactory/network/FactoryV2Api.kt` — V2 REST client.
- Create `android/account-factory/app/src/main/java/com/acp/accountfactory/network/FactoryV2Dtos.kt` — transport models.
- Create `android/account-factory/app/src/main/java/com/acp/accountfactory/ui/FactoryViewModel.kt` — remote state and actions.
- Create `android/account-factory/app/src/main/java/com/acp/accountfactory/ui/DashboardScreen.kt`.
- Create `android/account-factory/app/src/main/java/com/acp/accountfactory/ui/AccountsScreen.kt`.
- Create `android/account-factory/app/src/main/java/com/acp/accountfactory/ui/CheckpointsScreen.kt`.
- Create `android/account-factory/app/src/main/java/com/acp/accountfactory/ui/WorkersScreen.kt`.
- Modify `android/account-factory/app/src/main/java/com/acp/accountfactory/MainActivity.kt` — navigation/composition only.
- Modify `android/account-factory/app/src/main/java/com/acp/accountfactory/data/Entities.kt` and `FactoryRepository.kt` — cache-only semantics; no workflow authority.
- Create Android tests for DTO/action mapping and ViewModel state reduction.

### Task 1: Flask dashboard/read API

**Files:**
- Create: `web/factory_v2.py`
- Create: `tests/test_factory_v2_api.py`

**Interfaces:**
- Consumes `FactoryRepository` and controller services.
- Produces `register_factory_v2_routes(app)`.
- Endpoints: `GET /api/factory/v2/dashboard`, `/batches/<id>`, `/accounts`, `/accounts/<id>`, `/workers`, `/checkpoints`.

- [ ] **Step 1: Write failing authorization and dashboard tests**

```python
def test_dashboard_requires_factory_key(self):
    res = self.client.get("/api/factory/v2/dashboard")
    self.assertEqual(401, res.status_code)


def test_dashboard_returns_controller_counts(self):
    res = self.client.get(
        "/api/factory/v2/dashboard",
        headers={"X-ACP-Factory-Key": "test-key"},
    )
    self.assertEqual(200, res.status_code)
    body = res.get_json()
    self.assertIn("accounts", body)
    self.assertIn("workers", body)
    self.assertIn("host", body)
```

Use a temporary SQLite database and set `ACP_FACTORY_API_KEY=test-key` only inside test setup.

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m unittest tests.test_factory_v2_api -v`

Expected: FAIL because routes do not exist.

- [ ] **Step 3: Implement read routes**

Reuse the existing factory-key comparison behavior from `web/account_factory.py`, but centralize it in a small helper local to `web/factory_v2.py` for P0. Serialize only allowlisted fields. Dashboard response shape:

```json
{
  "ok": true,
  "batch": {"id":"...","status":"RUNNING","target_count":50},
  "accounts": {"total":50,"active":18,"running":6,"waiting_human":2,"error":1,"queued":23},
  "workers": {"total":7,"running":5,"waiting_human":2,"starting":0},
  "host": {"cpu_percent":58.0,"ram_available_mb":8400,"swap_used_mb":200,"capacity_state":"YELLOW"}
}
```

If no active batch/resource sample exists, return `null`/zero values rather than fabricating metrics.

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest tests.test_factory_v2_api -v`

Expected: read tests PASS.

- [ ] **Step 5: Commit**

```bash
git add web/factory_v2.py tests/test_factory_v2_api.py
git commit -m "feat: expose factory v2 read api"
```

### Task 2: Safe ADMIN action API for P0

**Files:**
- Modify: `web/factory_v2.py`
- Modify: `tests/test_factory_v2_api.py`

**Interfaces:**
- Endpoints: `POST /batches/<id>/pause`, `/resume`, `/checkpoints/<id>/continue`, `/checkpoints/<id>/retry`, `/checkpoints/<id>/snooze`, `/accounts/<id>/stop`, `/accounts/<id>/retry`, `/workers/<id>/drain`, `/workers/<id>/restart`.

- [ ] **Step 1: Write failing Continue semantics test**

```python
def test_continue_does_not_blindly_mark_checkpoint_success(self):
    checkpoint_id = self.seed_waiting_checkpoint()
    res = self.client.post(
        f"/api/factory/v2/checkpoints/{checkpoint_id}/continue",
        headers=self.auth,
    )
    self.assertEqual(202, res.status_code)
    cp = self.repo.get_checkpoint(checkpoint_id)
    self.assertEqual("VERIFYING", cp["status"])
    account = self.repo.get_account(cp["account_id"])
    self.assertNotEqual("IG_CREATED", account["stage"])
```

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m unittest tests.test_factory_v2_api -v`

Expected: FAIL for missing action routes.

- [ ] **Step 3: Implement command delegation**

Routes validate current state and delegate to service/supervisor methods. Return `202 Accepted` for asynchronous worker actions with a command id, e.g.:

```json
{"ok":true,"command_id":"...","status":"VERIFYING"}
```

Snooze accepts only positive bounded minutes; P0 allow `10`, `30`, `60` to match approved presets. Reject invalid transitions with 409, missing resource with 404, auth failure with 401.

- [ ] **Step 4: Add no-sensitive-fields test**

Walk every dashboard/account/checkpoint response and assert keys such as `token`, `password`, `otp`, `secret`, `ACP_MASTER_KEY` are absent.

- [ ] **Step 5: Run tests and commit**

```bash
python3 -m unittest tests.test_factory_v2_api -v
git add web/factory_v2.py tests/test_factory_v2_api.py
git commit -m "feat: add safe factory v2 control api"
```

### Task 3: Register V2 routes in the companion launcher

**Files:**
- Modify: `account_factory_server.py`
- Modify: `tests/test_factory_v2_api.py`

**Interfaces:**
- `build_app()` registers both existing OAuth routes and new V2 routes.

- [ ] **Step 1: Add failing route-registration test**

Import `build_app`, inspect `app.url_map`, and assert both `/oauth/account-factory/start` and `/api/factory/v2/dashboard` exist.

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m unittest tests.test_factory_v2_api.FactoryV2LauncherTests -v`

Expected: FAIL because V2 registration is absent.

- [ ] **Step 3: Register V2 routes**

Add:

```python
from acp.web.factory_v2 import register_factory_v2_routes
...
register_factory_v2_routes(app)
```

Keep the companion launcher model for P0; do not claim `./manage.sh start` serves V2 until a separate runtime-integration task changes it.

- [ ] **Step 4: Run test and commit**

```bash
python3 -m unittest tests.test_factory_v2_api.FactoryV2LauncherTests -v
git add account_factory_server.py tests/test_factory_v2_api.py
git commit -m "feat: register factory v2 api routes"
```

### Task 4: Android V2 transport client

**Files:**
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/network/FactoryV2Dtos.kt`
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/network/FactoryV2Api.kt`
- Create: `android/account-factory/app/src/test/java/com/acp/accountfactory/network/FactoryV2ApiMappingTest.kt`

**Interfaces:**
- Produces DTOs `DashboardDto`, `FactoryAccountDto`, `FactoryWorkerDto`, `FactoryCheckpointDto`, `CommandAcceptedDto`.
- Produces methods `dashboard`, `accounts`, `workers`, `checkpoints`, `continueCheckpoint`, `retryCheckpoint`, `snoozeCheckpoint`, `pauseBatch`, `resumeBatch`.

- [ ] **Step 1: Write failing JSON mapping test**

Use literal controller JSON and assert `WAITING_HUMAN`, counts, `capacity_state`, account id/username, and checkpoint worker id map exactly.

- [ ] **Step 2: Run and verify RED**

Run: `gradle -p android/account-factory testDebugUnitTest --tests '*FactoryV2ApiMappingTest'`

Expected: FAIL because DTO/client classes do not exist.

- [ ] **Step 3: Implement client**

Use existing OkHttp conventions. Every P0 request adds `X-ACP-Factory-Key`. Error text shown to UI must be synthesized from status code/allowlisted server `error`; do not display arbitrary HTML/provider bodies. Keep existing OAuth `AcpApi` separate until the OAuth bridge plan replaces call sites.

- [ ] **Step 4: Run test and commit**

```bash
gradle -p android/account-factory testDebugUnitTest --tests '*FactoryV2ApiMappingTest'
git add android/account-factory/app/src/main/java/com/acp/accountfactory/network android/account-factory/app/src/test/java/com/acp/accountfactory/network
git commit -m "feat: add factory v2 android api client"
```

### Task 5: Convert Android state to remote-authoritative ViewModel

**Files:**
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/ui/FactoryViewModel.kt`
- Create: `android/account-factory/app/src/test/java/com/acp/accountfactory/ui/FactoryViewModelTest.kt`
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/data/FactoryRepository.kt`
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/data/Entities.kt`

**Interfaces:**
- Produces `FactoryUiState(dashboard, accounts, checkpoints, workers, loading, error)`.
- ViewModel methods: `refresh()`, `continueCheckpoint(id)`, `retryCheckpoint(id)`, `snoozeCheckpoint(id, minutes)`, `pauseBatch(id)`, `resumeBatch(id)`.

- [ ] **Step 1: Write failing reducer test**

Use a fake `FactoryV2Api` interface and assert `refresh()` replaces local account stage with server-returned stage. Add a test proving a Continue action does not locally set `IG_CREATED`; it refreshes after the server accepts the command.

- [ ] **Step 2: Run and verify RED**

Run: `gradle -p android/account-factory testDebugUnitTest --tests '*FactoryViewModelTest'`

Expected: FAIL.

- [ ] **Step 3: Implement API interface + ViewModel**

Extract an interface around the V2 client so unit tests do not use network. Room entities may retain cached display fields, but remove methods that authoritatively perform workflow transitions (`transition`, `setConnecting`, `setActive`) from UI call sites. Mark cache writes as snapshots only.

- [ ] **Step 4: Run tests and commit**

```bash
gradle -p android/account-factory testDebugUnitTest --tests '*FactoryViewModelTest'
git add android/account-factory/app/src/main/java/com/acp/accountfactory/ui/FactoryViewModel.kt android/account-factory/app/src/test/java/com/acp/accountfactory/ui/FactoryViewModelTest.kt android/account-factory/app/src/main/java/com/acp/accountfactory/data
git commit -m "refactor: make factory controller authoritative on android"
```

### Task 6: Split phone dashboard/checkpoint screens

**Files:**
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/ui/DashboardScreen.kt`
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/ui/AccountsScreen.kt`
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/ui/CheckpointsScreen.kt`
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/ui/WorkersScreen.kt`
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/MainActivity.kt`

**Interfaces:**
- Dashboard shows active/total, running, waiting-human, error, queued, worker total, CPU/RAM/capacity.
- Checkpoint screen exposes `CONTINUE`, `RETRY`, `SNOOZE`, `STOP ACCOUNT` only through ViewModel callbacks.

- [ ] **Step 1: Add Compose/ViewModel presentation tests where feasible**

At minimum test pure formatting/mapping helpers: waiting duration, capacity label, action enabled state. Keep emulator/instrumentation UI tests out of P0 unless existing CI can run them reliably.

- [ ] **Step 2: Extract screens from the current large `MainActivity.kt`**

`MainActivity` should create settings/API/ViewModel and navigation only. Do not retain local batch creation button as source-of-truth; replace it with server batch status/actions. Surface human-required checkpoints at the top of Dashboard.

- [ ] **Step 3: Build Android app**

```bash
gradle -p android/account-factory testDebugUnitTest assembleDebug --no-daemon --max-workers=2 --console=plain
```

Expected: `BUILD SUCCESSFUL` and `android/account-factory/app/build/outputs/apk/debug/app-debug.apk` exists.

- [ ] **Step 4: Commit**

```bash
git add android/account-factory/app/src/main/java/com/acp/accountfactory/MainActivity.kt android/account-factory/app/src/main/java/com/acp/accountfactory/ui
git commit -m "feat: add factory v2 phone dashboard"
```

## Completion Gate

This plan is complete when the phone can refresh controller state, show pending human checkpoints and worker health, and send Continue/Retry/Snooze/Pause/Resume commands without locally advancing account workflow. P0 may continue using the Factory Key; QR pairing, notifications, realtime events, Devices screen, and public HTTPS connection fallback remain P1.
