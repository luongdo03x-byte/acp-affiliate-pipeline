# Account Factory Zero-Config Android Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Android Account Factory app auto-discover its LAN controller, auto-enroll for a per-device token, persist that token securely, and auto-start its LOCAL_DEVICE runner without manual URL/Factory Key entry.

**Architecture:** Add a small credential/auth layer to the Flask Factory V2 controller and a zero-config bootstrap layer to Android. Preserve the existing Factory Key path as fallback; device tokens are independently revocable and stored hashed server-side. Android discovery is limited to private IPv4 LAN candidates and persisted credentials are preferred after first success.

**Tech Stack:** Python 3, Flask, SQLite; Kotlin, Android SDK 26+, OkHttp, coroutines, Android Keystore, JUnit 4.

## Global Constraints

- Do not hardcode `ACP_FACTORY_API_KEY` into the APK.
- Auto-enroll is server-gated by `ACP_FACTORY_LAN_AUTO_ENROLL=true`.
- Raw device tokens are never persisted in SQLite or returned by read APIs.
- Existing `X-ACP-Factory-Key` behavior remains backward-compatible.
- Accessibility permission remains a one-time manual Android action.
- Discovery scans private IPv4 LAN only and stops on the first valid Account Factory controller.

---

### Task 1: Controller credential schema and token authentication

**Files:**
- Modify: `core/factory_v2/schema.py`
- Create: `core/factory_v2/device_credentials.py`
- Test: `tests/test_factory_v2_auto_enroll.py`

**Interfaces:**
- `issue_device_token(conn, device_id: str, device_name: str) -> str`
- `authenticate_device_token(conn, token: str) -> dict | None`
- `revoke_device_token(conn, device_id: str) -> bool`

- [ ] Write failing tests for token hashing, rotation, authentication, and revocation.
- [ ] Run focused test and confirm failure because credential table/functions do not exist.
- [ ] Add schema + minimal credential functions.
- [ ] Run focused test and confirm green.

### Task 2: Discovery, enrollment, and dual authentication routes

**Files:**
- Modify: `web/factory_v2.py`
- Test: `tests/test_factory_v2_auto_enroll.py`

**Interfaces:**
- `GET /api/factory/discovery`
- `POST /api/factory/enroll`
- Existing Factory V2 routes accept `X-ACP-Device-Token` in addition to `X-ACP-Factory-Key`.

- [ ] Add failing route tests: public discovery, default-disabled enrollment, private-IP enrollment, device-token auth, invalid token 401, legacy key still valid.
- [ ] Run tests and confirm expected failures.
- [ ] Implement `_require_factory_auth`, private-address validation, discovery and enrollment routes.
- [ ] Run focused and existing Factory V2 API tests.

### Task 3: Android connection model and LAN discovery helpers

**Files:**
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/network/FactoryV2Api.kt`
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/network/ControllerDiscovery.kt`
- Test: `android/account-factory/app/src/test/java/com/acp/accountfactory/network/ControllerDiscoveryTest.kt`

**Interfaces:**
- `FactoryConnection(baseUrl, factoryKey = "", deviceToken = "")`
- `FactoryV2Api.discover(candidateBaseUrl: String): DiscoveryDto?`
- `FactoryV2Api.enroll(baseUrl, deviceId, deviceName): EnrollmentDto`
- `ControllerDiscovery.private24Candidates(ipv4: String, port: Int): List<String>`

- [ ] Write pure JVM tests for candidate generation, public-IP rejection, discovery JSON validation, and auth-header preference.
- [ ] Run Android unit test and confirm failures.
- [ ] Implement minimal DTO/parser/helper/API changes.
- [ ] Run Android unit test green.

### Task 4: Secure Android credential persistence and bootstrap

**Files:**
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/settings/FactorySettingsStore.kt`
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/settings/SecureDeviceTokenStore.kt`
- Create: `android/account-factory/app/src/main/java/com/acp/accountfactory/network/ZeroConfigBootstrap.kt`
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/MainActivity.kt`
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/runner/LocalRunnerService.kt`

**Interfaces:**
- `FactorySettingsStore.deviceToken`
- `FactorySettingsStore.saveEnrollment(baseUrl, deviceToken)`
- `ZeroConfigBootstrap.ensureConfigured(): BootstrapResult`

- [ ] Add unit-testable bootstrap decision tests with fakes.
- [ ] Implement native Android Keystore AES/GCM token storage.
- [ ] Implement bootstrap order: persisted credential -> legacy manual config -> private LAN discovery/enroll.
- [ ] Start `LocalRunnerService` automatically after successful bootstrap.
- [ ] Keep manual settings dialog as fallback/troubleshooting only.

### Task 5: Deployment config, docs, and verification

**Files:**
- Modify: `.env.example`
- Modify: `android/account-factory/README.md`
- Modify: `README.md` or Account Factory runbook section if present.

- [ ] Add `ACP_FACTORY_LAN_AUTO_ENROLL=false` and document setting `ACP_HOST=0.0.0.0`, `ACP_PORT=5001` for phone LAN use.
- [ ] Run Python focused tests and Factory V2 regression tests.
- [ ] Run Android JVM unit tests and `assembleDebug` when Android Gradle environment is available.
- [ ] Inspect branch diff for secrets and unrelated files.
