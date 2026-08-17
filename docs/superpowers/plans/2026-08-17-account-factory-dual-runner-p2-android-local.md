# Account Factory Dual-Runner P2 Android Local Device Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing Android Account Factory app into both a controller UI and a real `LOCAL_DEVICE` runner that can execute the same runner-neutral workflow as an Ubuntu AVD.

**Architecture:** The Android app registers itself as a local runner, polls controller commands, executes only allowed local actions, reports observations/results, and maintains heartbeat while active. App-to-app orchestration uses Android intents plus a deliberately observation-only accessibility service for foreground package events; authoritative stage transitions remain on the controller.

**Tech Stack:** Kotlin, Jetpack Compose, Android Lifecycle/ViewModel, coroutines, OkHttp, AccessibilityService, compileSdk/targetSdk 36, minSdk 26, Java/Kotlin 17.

## Global Constraints

- The physical phone runs at most one active creation job in P0.
- Controller remains authoritative; Android must never directly mark `IG_CREATED`, `THREADS_CREATED`, or `ACP_ACTIVE`.
- Local runner may open official Instagram/Threads apps, prepare non-secret text, observe foreground package/activity, report checkpoints, and heartbeat.
- Accessibility integration is observation-only for P0: no generic click automation, no password entry, no OTP/CAPTCHA handling, no security-check bypass.
- Do not store Threads access tokens, Instagram passwords, OTPs, CAPTCHA results, Threads app secret, or ACP master key on Android.
- `X-ACP-Factory-Key` remains the P0/P1 device authentication boundary.
- Existing AVD mode must keep working.

---

## File Structure

- Modify `android/account-factory/app/src/main/AndroidManifest.xml`.
- Create `android/account-factory/app/src/main/res/xml/factory_accessibility_service.xml`.
- Create `android/account-factory/app/src/main/java/com/acp/accountfactory/runner/RunnerModels.kt`.
- Create `android/account-factory/app/src/main/java/com/acp/accountfactory/runner/FactoryAccessibilityService.kt`.
- Create `android/account-factory/app/src/main/java/com/acp/accountfactory/runner/LocalDeviceActions.kt`.
- Create `android/account-factory/app/src/main/java/com/acp/accountfactory/runner/LocalDeviceRunner.kt`.
- Modify `network/FactoryV2Dtos.kt` and `network/FactoryV2Api.kt`.
- Modify `ui/FactoryViewModel.kt`.
- Create `ui/CreateAccountScreen.kt` and `ui/RunnersScreen.kt` or adapt the existing workers screen.
- Modify `MainActivity.kt` navigation and lifecycle ownership.
- Add focused JVM unit tests under `app/src/test/java/com/acp/accountfactory/runner` and `ui`.

### Task 1: Android runner transport DTOs and API methods

**Files:**
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/network/FactoryV2Dtos.kt`
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/network/FactoryV2Api.kt`
- Create: `android/account-factory/app/src/test/java/com/acp/accountfactory/network/RunnerApiMappingTest.kt`

**Interfaces:**
- Produces DTOs `FactoryRunnerDto`, `RegisterRunnerRequest`, `RunnerHeartbeatRequest`, `RunnerCommandDto`, `RunnerCommandResultRequest`.
- Produces API methods `registerLocalRunner`, `heartbeatRunner`, `nextRunnerCommand`, `submitRunnerCommandResult`, `runners`, and `createAccount` with execution target.

- [ ] **Step 1: Write failing mapping/request tests**

```kotlin
@Test
fun `local runner json maps without adb fields`() {
    val json = """{"id":"phone-1","runner_type":"LOCAL_DEVICE","device_id":"abc","device_name":"Pixel","state":"READY"}"""
    val dto = gson.fromJson(json, FactoryRunnerDto::class.java)
    assertEquals("LOCAL_DEVICE", dto.runnerType)
    assertEquals("abc", dto.deviceId)
    assertNull(dto.avdName)
}

@Test
fun `runner command preserves allowed payload`() {
    val json = """{"id":"c1","job_id":"j1","account_id":"a1","action":"OPEN_PACKAGE","payload":{"package":"com.instagram.android"}}"""
    val dto = gson.fromJson(json, RunnerCommandDto::class.java)
    assertEquals("OPEN_PACKAGE", dto.action)
    assertEquals("com.instagram.android", dto.payload["package"])
}
```

- [ ] **Step 2: Run and verify RED**

```bash
gradle -p android/account-factory testDebugUnitTest --tests '*RunnerApiMappingTest' --no-daemon --max-workers=2 --console=plain
```
Expected: FAIL because runner DTOs/API methods do not exist.

- [ ] **Step 3: Implement DTOs/client methods**

Add methods to `FactoryV2ApiClient` and `FactoryV2Api`:

```kotlin
suspend fun registerLocalRunner(connection: FactoryConnection, deviceId: String, deviceName: String): FactoryRunnerDto
suspend fun heartbeatRunner(connection: FactoryConnection, workerId: String, currentAccountId: String?, currentJobId: String?): FactoryRunnerDto
suspend fun nextRunnerCommand(connection: FactoryConnection, workerId: String): RunnerCommandDto?
suspend fun submitRunnerCommandResult(connection: FactoryConnection, workerId: String, commandId: String, status: String, result: Map<String, String?>)
suspend fun runners(connection: FactoryConnection): List<FactoryRunnerDto>
suspend fun createAccount(connection: FactoryConnection, executionTarget: String): FactoryAccountDto
```

Every request adds `X-ACP-Factory-Key`; error text remains synthesized/allowlisted as in the current client.

- [ ] **Step 4: Run mapping tests**

```bash
gradle -p android/account-factory testDebugUnitTest --tests '*RunnerApiMappingTest' --no-daemon --max-workers=2 --console=plain
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add android/account-factory/app/src/main/java/com/acp/accountfactory/network android/account-factory/app/src/test/java/com/acp/accountfactory/network/RunnerApiMappingTest.kt
git commit -m "feat: add android dual runner api transport"
```

### Task 2: Stable local device identity and registration

**Files:**
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/runner/RunnerModels.kt`
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/runner/LocalRunnerIdentity.kt`
- Create: `android/account-factory/app/src/test/java/com/acp/accountfactory/runner/LocalRunnerIdentityTest.kt`

**Interfaces:**
- Produces `LocalRunnerIdentity(deviceId: String, deviceName: String)`.
- Produces `LocalRunnerIdentityStore.getOrCreate(): LocalRunnerIdentity`.
- The generated id is app-scoped and persisted; it is not an advertising id or fingerprint.

- [ ] **Step 1: Write failing identity tests**

```kotlin
@Test
fun `identity is stable across reads`() {
    val prefs = FakePreferences()
    val store = LocalRunnerIdentityStore(prefs, deviceNameProvider = { "Pixel" }, idProvider = { "local-123" })
    assertEquals("local-123", store.getOrCreate().deviceId)
    assertEquals("local-123", store.getOrCreate().deviceId)
}

@Test
fun `identity does not include sensitive hardware identifiers`() {
    val identity = LocalRunnerIdentity("local-123", "Pixel")
    assertFalse(identity.deviceId.contains("imei", ignoreCase = true))
}
```

- [ ] **Step 2: Run and verify RED**

```bash
gradle -p android/account-factory testDebugUnitTest --tests '*LocalRunnerIdentityTest' --no-daemon --max-workers=2 --console=plain
```
Expected: FAIL for missing classes.

- [ ] **Step 3: Implement app-scoped identity**

Use `SharedPreferences` with `UUID.randomUUID().toString()` on first run. Device name may use `Build.MANUFACTURER + " " + Build.MODEL`; never use IMEI, serial, MAC, advertising id, or Android fingerprint for identity.

- [ ] **Step 4: Run tests and commit**

```bash
gradle -p android/account-factory testDebugUnitTest --tests '*LocalRunnerIdentityTest' --no-daemon --max-workers=2 --console=plain
git add android/account-factory/app/src/main/java/com/acp/accountfactory/runner android/account-factory/app/src/test/java/com/acp/accountfactory/runner/LocalRunnerIdentityTest.kt
git commit -m "feat: add stable local factory runner identity"
```

### Task 3: Observation-only accessibility service

**Files:**
- Modify: `android/account-factory/app/src/main/AndroidManifest.xml`
- Create: `android/account-factory/app/src/main/res/xml/factory_accessibility_service.xml`
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/runner/FactoryAccessibilityService.kt`
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/runner/ForegroundObservationStore.kt`
- Create: `android/account-factory/app/src/test/java/com/acp/accountfactory/runner/ForegroundObservationStoreTest.kt`

**Interfaces:**
- Produces `ForegroundObservation(packageName: String?, className: String?, observedAtEpochMs: Long)`.
- `ForegroundObservationStore.latest()` exposes only current observed package/class/time.
- Accessibility service records `TYPE_WINDOW_STATE_CHANGED` / `TYPE_WINDOWS_CHANGED`; it does not call `performAction()`.

- [ ] **Step 1: Write failing observation store tests**

```kotlin
@Test
fun `latest foreground observation replaces older observation`() {
    val store = ForegroundObservationStore()
    store.update("com.instagram.android", "LoginActivity", 100L)
    store.update("com.instagram.barcelona", "MainActivity", 200L)
    assertEquals("com.instagram.barcelona", store.latest().packageName)
    assertEquals(200L, store.latest().observedAtEpochMs)
}
```

- [ ] **Step 2: Run and verify RED**

```bash
gradle -p android/account-factory testDebugUnitTest --tests '*ForegroundObservationStoreTest' --no-daemon --max-workers=2 --console=plain
```
Expected: FAIL because observation classes do not exist.

- [ ] **Step 3: Implement service declaration and service**

Manifest service:

```xml
<service
    android:name=".runner.FactoryAccessibilityService"
    android:permission="android.permission.BIND_ACCESSIBILITY_SERVICE"
    android:exported="false">
    <intent-filter>
        <action android:name="android.accessibilityservice.AccessibilityService" />
    </intent-filter>
    <meta-data
        android:name="android.accessibilityservice"
        android:resource="@xml/factory_accessibility_service" />
</service>
```

Service XML requests window state/window change event types and package names `com.instagram.android,com.instagram.barcelona`. Do not request `canRetrieveWindowContent` unless strictly needed for package/class observation; P0 should use event package/class only. The service implementation must contain no `performAction`, gesture dispatch, text scraping, password inspection, or node traversal.

- [ ] **Step 4: Add settings helper**

Add a pure helper `AccessibilityReadiness.isEnabled(context): Boolean` and an app event that opens `Settings.ACTION_ACCESSIBILITY_SETTINGS` when local mode is selected but service is disabled.

- [ ] **Step 5: Run tests/build and commit**

```bash
gradle -p android/account-factory testDebugUnitTest assembleDebug --no-daemon --max-workers=2 --console=plain
git add android/account-factory/app/src/main/AndroidManifest.xml android/account-factory/app/src/main/res/xml/factory_accessibility_service.xml android/account-factory/app/src/main/java/com/acp/accountfactory/runner android/account-factory/app/src/test/java/com/acp/accountfactory/runner/ForegroundObservationStoreTest.kt
git commit -m "feat: observe official app foreground state"
```

### Task 4: Allowed local device actions

**Files:**
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/runner/LocalDeviceActions.kt`
- Create: `android/account-factory/app/src/test/java/com/acp/accountfactory/runner/LocalDeviceActionsTest.kt`

**Interfaces:**
- Produces `LocalDeviceActions.execute(command: RunnerCommandDto): RunnerCommandResult`.
- Supported actions: `OPEN_PACKAGE`, `PREPARE_TEXT`, `OBSERVE_FOREGROUND`, `REPORT_WAITING_HUMAN`.
- Unsupported action returns `FAILED` with allowlisted `UNSUPPORTED_ACTION`.

- [ ] **Step 1: Write failing action dispatch tests**

```kotlin
@Test
fun `open package only accepts official package allowlist`() {
    val actions = LocalDeviceActions(fakePlatform, fakeClipboard, fakeObservationStore)
    val bad = command("OPEN_PACKAGE", mapOf("package" to "com.example.other"))
    val result = actions.execute(bad)
    assertEquals("FAILED", result.status)
    assertEquals("PACKAGE_NOT_ALLOWED", result.result["error_code"])
}

@Test
fun `observe foreground reports package without workflow stage`() {
    fakeObservationStore.update("com.instagram.android", "MainActivity", 123L)
    val result = actions.execute(command("OBSERVE_FOREGROUND"))
    assertEquals("com.instagram.android", result.result["package"])
    assertFalse(result.result.containsKey("stage"))
}
```

- [ ] **Step 2: Run and verify RED**

```bash
gradle -p android/account-factory testDebugUnitTest --tests '*LocalDeviceActionsTest' --no-daemon --max-workers=2 --console=plain
```
Expected: FAIL for missing action executor.

- [ ] **Step 3: Implement actions**

`OPEN_PACKAGE` allowlist exactly:

```text
com.instagram.android
com.instagram.barcelona
```

Use `PackageManager.getLaunchIntentForPackage()` + `FLAG_ACTIVITY_NEW_TASK`. `PREPARE_TEXT` may place only controller-provided non-secret profile text in clipboard. Reject payload keys named `password`, `otp`, `captcha`, `token`, `secret`. `REPORT_WAITING_HUMAN` returns `{waiting_human: "true"}` without interacting with another app.

- [ ] **Step 4: Run tests and commit**

```bash
gradle -p android/account-factory testDebugUnitTest --tests '*LocalDeviceActionsTest' --no-daemon --max-workers=2 --console=plain
git add android/account-factory/app/src/main/java/com/acp/accountfactory/runner/LocalDeviceActions.kt android/account-factory/app/src/test/java/com/acp/accountfactory/runner/LocalDeviceActionsTest.kt
git commit -m "feat: execute safe local factory actions"
```

### Task 5: LocalDeviceRunner loop and heartbeat

**Files:**
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/runner/LocalDeviceRunner.kt`
- Create: `android/account-factory/app/src/test/java/com/acp/accountfactory/runner/LocalDeviceRunnerTest.kt`

**Interfaces:**
- Produces `LocalDeviceRunner.start()` and `stop()`.
- Registers once, heartbeats every 10 seconds while active, polls next command every 1 second while a job exists and every 5 seconds while READY.
- Submits each command result exactly once by command id.

- [ ] **Step 1: Write failing runner-loop tests with virtual time**

```kotlin
@Test
fun `runner registers then heartbeats with returned worker id`() = runTest {
    val api = FakeFactoryApi()
    val runner = LocalDeviceRunner(api, connectionProvider, identityStore, actions, testDispatcher)
    runner.start()
    advanceUntilIdle()
    assertEquals(1, api.registerCalls)
    assertEquals("phone-1", api.lastHeartbeatWorkerId)
}

@Test
fun `runner executes delivered command and submits one result`() = runTest {
    val api = FakeFactoryApi(commands = mutableListOf(openInstagramCommand()))
    val runner = LocalDeviceRunner(api, connectionProvider, identityStore, actions, testDispatcher)
    runner.runSingleIterationForTest()
    assertEquals(1, api.submittedResults.size)
    assertEquals("c1", api.submittedResults.single().commandId)
}
```

- [ ] **Step 2: Run and verify RED**

```bash
gradle -p android/account-factory testDebugUnitTest --tests '*LocalDeviceRunnerTest' --no-daemon --max-workers=2 --console=plain
```
Expected: FAIL for missing runner.

- [ ] **Step 3: Implement lifecycle-safe coroutine runner**

Own a `SupervisorJob`; `start()` is idempotent; `stop()` cancels polling/heartbeat. Network errors back off but do not fabricate success. Command result submission retries only the same command id; never executes a second time after a successful submission.

- [ ] **Step 4: Run tests and commit**

```bash
gradle -p android/account-factory testDebugUnitTest --tests '*LocalDeviceRunnerTest' --no-daemon --max-workers=2 --console=plain
git add android/account-factory/app/src/main/java/com/acp/accountfactory/runner/LocalDeviceRunner.kt android/account-factory/app/src/test/java/com/acp/accountfactory/runner/LocalDeviceRunnerTest.kt
git commit -m "feat: run physical device factory worker"
```

### Task 6: Create Account target selector and runner UI

**Files:**
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/ui/CreateAccountScreen.kt`
- Modify/Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/ui/RunnersScreen.kt`
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/ui/FactoryViewModel.kt`
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/MainActivity.kt`
- Create: `android/account-factory/app/src/test/java/com/acp/accountfactory/ui/CreateAccountPresentationTest.kt`

**Interfaces:**
- Target options: `THIS_PHONE`, `AUTO_AVD`, exact AVD worker id.
- `FactoryViewModel.createAccount(target: ExecutionTarget)` calls controller and refreshes.
- Selecting `THIS_PHONE` requires configured controller + enabled accessibility service + registered local runner.

- [ ] **Step 1: Write failing presentation tests**

```kotlin
@Test
fun `target options include this phone and ready avds only`() {
    val options = buildExecutionTargets(localRunner, listOf(readyAvd, drainingAvd))
    assertEquals(listOf("THIS_PHONE", "AUTO_AVD", readyAvd.id), options.map { it.value })
}

@Test
fun `this phone target maps to registered local worker`() {
    val request = createAccountRequest(ExecutionTarget.ThisPhone("phone-1"))
    assertEquals("phone-1", request.executionTarget)
}
```

- [ ] **Step 2: Run and verify RED**

```bash
gradle -p android/account-factory testDebugUnitTest --tests '*CreateAccountPresentationTest' --no-daemon --max-workers=2 --console=plain
```
Expected: FAIL for missing UI/presentation model.

- [ ] **Step 3: Implement UI/viewmodel wiring**

Add `CREATE_ACCOUNT` and `RUNNERS` navigation destinations. Create screen displays:

```text
Run on
● This phone
○ Auto-select AVD
○ <READY AVD names>
```

No batch count >1 for `THIS_PHONE` in this phase. If local accessibility service is off, emit a one-shot event to open Accessibility Settings instead of starting the job.

- [ ] **Step 4: Own LocalDeviceRunner at Activity/application lifecycle**

Instantiate runner once per Activity using a lifecycle-aware owner. Start only when controller settings are configured; stop on Activity destruction. Do not create it inside a recomposing Composable.

- [ ] **Step 5: Run Android full gate and commit**

```bash
gradle -p android/account-factory testDebugUnitTest assembleDebug --no-daemon --max-workers=2 --console=plain
git add android/account-factory/app/src/main/java/com/acp/accountfactory/ui android/account-factory/app/src/main/java/com/acp/accountfactory/MainActivity.kt android/account-factory/app/src/test/java/com/acp/accountfactory/ui/CreateAccountPresentationTest.kt
git commit -m "feat: create accounts on phone or avd"
```

## Completion Gate

Required Android command:

```bash
gradle -p android/account-factory testDebugUnitTest assembleDebug --no-daemon --max-workers=2 --console=plain
```

Manual smoke gate before moving on:

1. Install debug APK on one physical Android phone.
2. Configure Factory Controller URL/key once.
3. Enable Account Factory observation accessibility service manually.
4. Confirm Runners shows the physical phone as `LOCAL_DEVICE / READY`.
5. Create one `THIS_PHONE` job and confirm Instagram opens.
6. Confirm controller, not Android local state, owns the job/account stage.
7. Do not complete real OAuth in this plan; that is P3.