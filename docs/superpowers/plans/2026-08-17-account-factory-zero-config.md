# Account Factory Zero-Config Android Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Android Account Factory app auto-discover its LAN controller, auto-enroll for a per-device token, persist that token securely, validate/recover that enrollment automatically, and auto-start its LOCAL_DEVICE runner without manual URL/Factory Key entry.

**Architecture:** Add a small credential/auth layer beside the Flask Factory V2 controller and a zero-config bootstrap layer to Android. Preserve the existing Factory Key path as fallback; device tokens are independently revocable and stored hashed server-side. To minimize risk on the mature Android networking layer, the enrolled credential is passed through its existing credential/header slot and the Controller auth bridge resolves device-token-vs-legacy-key semantics server-side.

**Tech Stack:** Python 3, Flask, SQLite; Kotlin, Android SDK 26+, OkHttp, coroutines, Android Keystore, JUnit 4.

## Global Constraints

- Do not hardcode `ACP_FACTORY_API_KEY` into the APK.
- Auto-enroll is server-gated by `ACP_FACTORY_LAN_AUTO_ENROLL=true`.
- Raw device tokens are never persisted in SQLite or returned by read APIs.
- Existing real `X-ACP-Factory-Key` behavior remains backward-compatible.
- Accessibility permission remains a one-time manual Android action.
- Discovery scans private IPv4 LAN only and stops on the first valid Account Factory controller.

---

### Task 1: Controller credential storage and authentication

**Files:**
- Create: `core/factory_v2/device_credentials.py`
- Test: `tests/test_factory_v2_auto_enroll.py`

**Interfaces:**
- `issue_device_token(conn, device_id: str, device_name: str) -> str`
- `authenticate_device_token(conn, token: str) -> dict | None`
- `revoke_device_token(conn, device_id: str) -> bool`

- [x] Define tests for token hashing, rotation, authentication, revocation and identity validation.
- [x] Implement lazy/idempotent credential table creation and minimal credential functions.
- [x] Verify the exact credential module with SQLite in the available sandbox: 2 focused tests pass.

### Task 2: Discovery, enrollment, and dual authentication routes

**Files:**
- Create: `web/factory_enrollment.py`
- Modify: `account_factory_server.py`
- Test: `tests/test_factory_v2_auto_enroll.py`

**Interfaces:**
- `GET /api/factory/discovery`
- `POST /api/factory/enroll`
- Existing Factory V2 routes accept `X-ACP-Device-Token` and preserve real `X-ACP-Factory-Key` semantics.
- Compatibility bridge also recognizes an enrolled device credential in the existing Android `X-ACP-Factory-Key` slot.

- [x] Define route tests for public discovery, default-disabled enrollment, private-IP enrollment, invalid token, rotation, Android compatibility slot and legacy key.
- [x] Implement private-address validation, enrollment and auth bridge as a separate module instead of rewriting the large Factory V2 route file.
- [x] Syntax-compile the enrollment module in the available sandbox.
- [ ] Run the real Flask focused/regression suite on a checkout with project dependencies installed.

### Task 3: Android LAN discovery helpers

**Files:**
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/network/ControllerDiscovery.kt`
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/network/ControllerBootstrapApi.kt`
- Test: `android/account-factory/app/src/test/java/com/acp/accountfactory/network/ControllerDiscoveryTest.kt`

**Interfaces:**
- `ControllerDiscovery.private24Candidates(ipv4: String, port: Int): List<String>`
- `ControllerDiscovery.parseDiscovery(body: String): DiscoveryDto?`
- `ControllerDiscovery.parseEnrollment(body: String): EnrollmentDto?`
- `ControllerBootstrapApi.validateCredential(...)`
- `ControllerBootstrapApi.enroll(...)`

- [x] Add pure helper tests for private `/24` candidate generation and discovery/enrollment parsing.
- [x] Implement bounded private-LAN candidates and strict Account Factory v2 response validation.
- [x] Compile and execute the exact `ControllerDiscovery.kt` logic with `kotlinc`: verification harness passes.

### Task 4: Secure persistence, validation/recovery and auto-start

**Files:**
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/settings/SecureDeviceTokenStore.kt`
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/settings/FactorySettingsStore.kt`
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/network/ZeroConfigBootstrap.kt`
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/runner/LocalRunnerService.kt`
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/MainActivity.kt`
- Modify: `android/account-factory/app/src/main/AndroidManifest.xml`

**Interfaces:**
- `FactorySettingsStore.deviceToken`
- `FactorySettingsStore.saveEnrollment(baseUrl, deviceToken)`
- `ZeroConfigBootstrap.ensureConfigured(): BootstrapResult`

- [x] Implement Android Keystore AES/GCM credential storage.
- [x] Validate a persisted credential before runner startup.
- [x] If an enrolled credential or remembered LAN URL is stale, rediscover and re-enroll automatically.
- [x] Start the foreground bootstrap service whenever the app opens.
- [x] Keep manual settings only behind the explicit Settings action; refresh/create retries zero-config instead of forcing a dialog.
- [x] Add `ACCESS_NETWORK_STATE` for private Wi-Fi discovery.
- [x] Compile the bootstrap logic with Android/network stubs + real coroutines using `kotlinc`.
- [ ] Run Android SDK/Gradle unit tests and `assembleDebug` on a machine with Android SDK 36 + Gradle 8.13.

### Task 5: Deployment config and docs

**Files:**
- Modify: `.env.example`
- Modify: `android/account-factory/README.md`
- Modify: `docs/ACP_ACCOUNT_FACTORY_RUNBOOK.md`
- Create/update: zero-config design/plan docs.

- [x] Document `ACP_HOST=0.0.0.0`, `ACP_PORT=5001`, `ACP_FACTORY_LAN_AUTO_ENROLL` and server-side fallback key boundaries.
- [x] Document first-launch zero-config flow and one-time Accessibility requirement.
- [x] Inspect the feature diff from pre-task branch head; changes are scoped to zero-config Controller/Android/tests/docs/config.
- [ ] Run full repository/Android release verification before merging or distributing a replacement APK.
