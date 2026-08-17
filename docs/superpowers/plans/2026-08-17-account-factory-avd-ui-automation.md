# Account Factory AVD UI Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `REMOTE_AVD` into a fail-closed semi-automated runner that handles known safe Instagram/Threads UI, fills only approved non-sensitive profile fields, pauses for protected human steps, auto-resumes only after a positively detected safe successor screen, and then continues through the existing ACP OAuth path.

**Architecture:** `FactoryControllerRuntime` remains authoritative for account/job/checkpoint state. A new UI automation package is used only by the isolated AVD worker process and returns sanitized observations/results. `LOCAL_DEVICE` keeps its current command allowlist and phone workflow unchanged.

**Tech Stack:** Python 3, stdlib `xml.etree.ElementTree`, dataclasses, existing `AvdManager`/ADB transport, SQLite Factory V2 controller, `unittest`, official Instagram `com.instagram.android`, official Threads `com.instagram.barcelona`.

## Global Constraints

- Work only on `feat/account-factory-android`; do not merge `main`.
- Never generate, enter, store, retrieve, or submit passwords.
- Never retrieve, enter, solve, or bypass OTP/email/SMS codes or CAPTCHA.
- Never automate selfie, identity, recovery, or security challenges.
- Never publish Threads content as part of this flow.
- Unknown/ambiguous UI must fail closed; no exploratory tapping.
- Controller/DB remains authoritative for business stages; do not add AVD-only `AccountStage` values.
- Screenshot capture is diagnostic-only, disabled by default, and not part of the first decision loop.
- Raw hierarchy XML and sensitive values must not be persisted to DB/logs.
- Normal safe UI actions get at most three total attempts: initial + two retries.
- `LOCAL_DEVICE` behavior/lifecycle must remain unchanged.
- Reuse existing ACP activation/OAuth code.

---

## File Structure

Create:

```text
core/factory_v2/ui_automation/
    __init__.py
    adb.py
    hierarchy.py
    selectors.py
    detector.py
    driver.py
    flow_result.py
    instagram/{__init__.py,screens.py,selectors.py,flow.py}
    threads/{__init__.py,screens.py,selectors.py,flow.py}

tests/fixtures/android_ui/
    instagram_signup.xml
    instagram_profile.xml
    instagram_otp.xml
    instagram_error.xml
    threads_onboarding.xml
    threads_profile.xml

tests/test_factory_v2_ui_hierarchy.py
tests/test_factory_v2_ui_detector.py
tests/test_factory_v2_ui_driver.py
tests/test_factory_v2_instagram_flow.py
tests/test_factory_v2_threads_flow.py
tests/test_factory_v2_avd_worker_agent.py
```

Modify only where integration requires it:

```text
workers/account_factory_worker.py
core/factory_v2/runtime.py
core/factory_v2/service.py
core/factory_v2/runner_gateway.py
tests/test_factory_v2_runtime.py
tests/test_factory_v2_worker_process.py
tests/test_factory_v2_checkpoint_retry.py
docs/ACP_ACCOUNT_FACTORY_RUNBOOK.md
scripts/verify_account_factory_dual_runner.sh
```

---

### Task 1: Sanitized hierarchy model and serial-scoped ADB client

**Files:** create `ui_automation/{__init__,adb,hierarchy}.py`, test `tests/test_factory_v2_ui_hierarchy.py`.

**Interfaces:** produces `UiBounds`, `UiNode`, `UiSnapshot`, `UiHierarchyReader`, `AdbClient`.

- [ ] **Step 1: Write failing hierarchy tests**

```python
import unittest
from core.factory_v2.ui_automation.hierarchy import UiHierarchyReader

class UiHierarchyTests(unittest.TestCase):
    def test_parse_exposes_sanitized_metadata(self):
        xml = '''<hierarchy><node text="Username"
          resource-id="com.instagram.android:id/username"
          class="android.widget.EditText" clickable="true" enabled="true"
          bounds="[10,20][210,80]" /></hierarchy>'''
        snap = UiHierarchyReader().parse(xml, package="com.instagram.android", activity=".MainActivity")
        self.assertEqual("Username", snap.nodes[0].text)
        self.assertEqual((110, 50), snap.nodes[0].bounds.center)

    def test_password_node_text_is_redacted(self):
        xml = '''<hierarchy><node text="secret" password="true"
          class="android.widget.EditText" bounds="[0,0][100,100]" /></hierarchy>'''
        snap = UiHierarchyReader().parse(xml, package="x", activity="y")
        self.assertEqual("", snap.nodes[0].text)
```

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest tests.test_factory_v2_ui_hierarchy -v
```

Expected: missing module/import.

- [ ] **Step 3: Implement exact immutable types**

```python
@dataclass(frozen=True)
class UiBounds:
    left: int; top: int; right: int; bottom: int
    @property
    def center(self) -> tuple[int, int]:
        return ((self.left + self.right)//2, (self.top + self.bottom)//2)

@dataclass(frozen=True)
class UiNode:
    text: str
    content_desc: str
    resource_id: str
    class_name: str
    clickable: bool
    enabled: bool
    bounds: UiBounds

@dataclass(frozen=True)
class UiSnapshot:
    package: str | None
    activity: str | None
    nodes: tuple[UiNode, ...]
```

`UiHierarchyReader.parse()` parses in memory, redacts password nodes, skips malformed bounds, and never persists XML.

- [ ] **Step 4: Add ADB scoping test and implementation**

```python
class AdbClientTests(unittest.TestCase):
    def test_tap_is_scoped_to_serial(self):
        runner = FakeRunner()
        AdbClient("emulator-5554", adb_path="adb", runner=runner).tap(120, 480)
        self.assertEqual(["adb", "-s", "emulator-5554"], runner.calls[-1][0][:3])
```

Implement:

```python
class AdbClient:
    def foreground(self) -> tuple[str | None, str | None]: ...
    def dump_hierarchy(self) -> str: ...
    def tap(self, x: int, y: int) -> None: ...
    def set_text(self, text: str) -> None: ...
    def keyevent(self, keycode: int) -> None: ...
    def back(self) -> None: ...
    def home(self) -> None: ...
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None: ...
    def open_package(self, package: str) -> None: ...
```

Every command is `[adb, "-s", serial, ...]`. `set_text()` rejects control characters and >500 chars. No password/OTP helper exists.

- [ ] **Step 5: Run GREEN and commit**

```bash
python3 -m unittest tests.test_factory_v2_ui_hierarchy -v
git add core/factory_v2/ui_automation tests/test_factory_v2_ui_hierarchy.py
git commit -m "feat: add sanitized AVD UI hierarchy reader"
```

---

### Task 2: Selector engine, fixtures, and safety-first detector

**Files:** create `selectors.py`, `detector.py`, platform `screens.py/selectors.py`, six sanitized fixtures, `tests/test_factory_v2_ui_detector.py`.

**Interfaces:** produces `Selector`, `ScreenSignature`, `DetectedScreen`, `ScreenDetector`, `build_instagram_detector()`, `build_threads_detector()`.

- [ ] **Step 1: Write selector precedence test**

```python
class SelectorTests(unittest.TestCase):
    def test_resource_id_has_priority(self):
        selector = Selector(
            semantic="continue",
            resource_ids=("com.instagram.android:id/next_button",),
            texts=("Next", "Continue", "Tiếp tục", "Tiếp"),
        )
        self.assertEqual("com.instagram.android:id/next_button", selector.find(self.snapshot).resource_id)
```

Use:

```python
@dataclass(frozen=True)
class Selector:
    semantic: str | None = None
    resource_ids: tuple[str, ...] = ()
    content_descs: tuple[str, ...] = ()
    texts: tuple[str, ...] = ()
    class_names: tuple[str, ...] = ()
    require_clickable: bool = False
    def find(self, snapshot: UiSnapshot) -> UiNode | None: ...
```

Precedence: resource-id -> content-desc -> exact text -> normalized known alias -> semantic class/clickable combination.

- [ ] **Step 2: Write detector priority tests**

```python
class DetectorTests(unittest.TestCase):
    def test_otp_wins_over_continue_button(self):
        detected = build_instagram_detector().detect(self.otp_snapshot)
        self.assertEqual("OTP_REQUIRED", detected.kind)
        self.assertTrue(detected.protected)
        self.assertFalse(detected.automation_allowed)

    def test_unknown_never_allows_automation(self):
        detected = build_instagram_detector().detect(self.unknown_snapshot)
        self.assertEqual("UNKNOWN", detected.kind)
        self.assertFalse(detected.automation_allowed)
```

- [ ] **Step 3: Implement detector types**

```python
@dataclass(frozen=True)
class DetectedScreen:
    kind: str
    confidence: float
    evidence: tuple[str, ...]
    protected: bool = False
    @property
    def automation_allowed(self) -> bool:
        return not self.protected and self.kind != "UNKNOWN" and self.confidence >= 0.90

@dataclass(frozen=True)
class ScreenSignature:
    kind: str
    package: str
    selectors: tuple[Selector, ...]
    minimum_matches: int
    confidence: float
    protected: bool = False
    priority: int = 100
```

- [ ] **Step 4: Define signatures in strict priority order**

Protected:

```text
PASSWORD_REQUIRED, OTP_REQUIRED, CAPTCHA_REQUIRED,
EMAIL_OR_PHONE_VERIFICATION, SELFIE_OR_IDENTITY_CHECK,
SECURITY_CHALLENGE, ACCOUNT_RECOVERY, CONSENT_WITH_SECURITY_IMPACT
```

Error:

```text
NETWORK_ERROR, APP_CRASH, RATE_LIMITED, ACTION_BLOCKED, ACCOUNT_DISABLED
```

Known normal/success:

```text
IG_SIGNUP_ENTRY, IG_PROFILE_SETUP, IG_HOME, IG_POSTCHECK_OK,
THREADS_ONBOARDING, THREADS_PROFILE_SETUP, THREADS_HOME, THREADS_POSTCHECK_OK
```

Protected is evaluated before error, success, normal, unknown. Credible protected evidence stops automation even below the normal 0.90 threshold.

- [ ] **Step 5: Add sanitized fixtures** using only fake values such as `sample_user`; no real code/password/token.

- [ ] **Step 6: Run and commit**

```bash
python3 -m unittest tests.test_factory_v2_ui_detector -v
git add core/factory_v2/ui_automation tests/fixtures/android_ui tests/test_factory_v2_ui_detector.py
git commit -m "feat: add fail-closed AVD screen detector"
```

---

### Task 3: Safe UI driver with protected-field guard

**Files:** create `driver.py`, `tests/test_factory_v2_ui_driver.py`.

**Interfaces:** consumes Task 1/2; produces `ActionResult`, `SafeUiDriver`.

- [ ] **Step 1: Write no-op/not-found tests**

```python
class SafeUiDriverTests(unittest.TestCase):
    def test_set_text_noops_if_value_matches(self):
        result = self.driver.set_text(USERNAME_INPUT, "sample_user")
        self.assertEqual("noop", result.status)
        self.assertEqual([], self.adb.input_calls)

    def test_missing_selector_never_taps(self):
        result = self.driver.tap(MISSING_SELECTOR)
        self.assertEqual("not_found", result.status)
        self.assertEqual([], self.adb.tap_calls)
```

- [ ] **Step 2: Implement exact API**

```python
@dataclass(frozen=True)
class ActionResult:
    status: str  # completed | noop | not_found | postcondition_failed
    before: str | None = None
    after: str | None = None

class SafeUiDriver:
    def snapshot(self) -> UiSnapshot: ...
    def detect_screen(self) -> DetectedScreen: ...
    def find(self, selector: Selector) -> UiNode | None: ...
    def tap(self, selector: Selector, *, expected_screens: tuple[str, ...] = (), timeout: float = 8.0) -> ActionResult: ...
    def set_text(self, selector: Selector, value: str) -> ActionResult: ...
    def wait_for(self, screens: tuple[str, ...], timeout: float) -> DetectedScreen: ...
```

`tap()` mutates only a positively matched node center; expected transition must be positively detected.

- [ ] **Step 3: Write/implement protected selector denial**

```python
class ProtectedFieldTests(unittest.TestCase):
    def test_password_semantic_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "protected field automation is disabled"):
            self.driver.set_text(Selector(semantic="password", texts=("Password",)), "x")
```

Deny exact semantics: `password`, `otp`, `verification_code`, `recovery_code`.

- [ ] **Step 4: Make approved text replay idempotent**: if current field equals target return `noop`; otherwise focus known field, clear it, input once, refresh snapshot, verify postcondition.

- [ ] **Step 5: Run and commit**

```bash
python3 -m unittest tests.test_factory_v2_ui_driver -v
git add core/factory_v2/ui_automation/driver.py core/factory_v2/ui_automation/selectors.py tests/test_factory_v2_ui_driver.py
git commit -m "feat: add verified safe AVD UI driver"
```

---

### Task 4: Shared flow result and Instagram fail-closed state machine

**Files:** create `flow_result.py`, `instagram/flow.py`, test `tests/test_factory_v2_instagram_flow.py`.

**Interfaces:**

```python
@dataclass(frozen=True)
class FlowResult:
    status: str  # running | waiting_human | completed | needs_confirmation | retry_pending | error
    screen: str
    reason: str | None = None
    last_safe_step: str | None = None
```

- [ ] **Step 1: Write protected-step test**

```python
class InstagramFlowTests(unittest.TestCase):
    def test_otp_stops_before_mutation(self):
        self.driver.detected = DetectedScreen("OTP_REQUIRED", 0.82, ("verify-marker",), True)
        result = InstagramFlow(self.driver).run(self.profile)
        self.assertEqual("waiting_human", result.status)
        self.assertEqual([], self.driver.mutations)
```

- [ ] **Step 2: Write approved-field test**

```python
    def test_profile_setup_sets_only_approved_fields(self):
        InstagramFlow(self.driver).run(self.profile)
        self.assertEqual([
            ("username", "sample_user"),
            ("display_name", "Sample User"),
            ("bio", "Sample bio"),
        ], self.driver.set_values)
```

- [ ] **Step 3: Implement bounded known-screen flow** for `IG_SIGNUP_ENTRY` and `IG_PROFILE_SETUP`; each normal action gets max 3 attempts and returns `needs_confirmation/UI_CHANGED` on exhaustion.

- [ ] **Step 4: Implement unknown handling with zero mutation**

```text
UNKNOWN -> snapshot/detect up to 3 times -> still UNKNOWN -> needs_confirmation/UI_CHANGED
```

No tap occurs during the three unknown refreshes.

- [ ] **Step 5: Implement error policy**

```text
NETWORK_ERROR -> limited retry, max 3 observations/actions total
APP_CRASH -> reopen Instagram once, then re-detect
RATE_LIMITED/ACTION_BLOCKED -> retry_pending immediately, no rapid retry
ACCOUNT_DISABLED -> error immediately
```

- [ ] **Step 6: Implement observation-only checkpoint resume**: no mutations; `completed` only if protected screen disappeared **and** `IG_PROFILE_SETUP`, `IG_HOME`, or `IG_POSTCHECK_OK` is positively detected.

- [ ] **Step 7: Run and commit**

```bash
python3 -m unittest tests.test_factory_v2_instagram_flow -v
git add core/factory_v2/ui_automation/flow_result.py core/factory_v2/ui_automation/instagram tests/test_factory_v2_instagram_flow.py
git commit -m "feat: automate safe Instagram AVD flow"
```

---

### Task 5: Threads fail-closed state machine

**Files:** create `threads/flow.py`, modify Threads selectors/screens, test `tests/test_factory_v2_threads_flow.py`.

- [ ] **Step 1: Write no-publish/protected tests**

```python
class ThreadsFlowTests(unittest.TestCase):
    def test_normal_flow_has_no_publish_action(self):
        ThreadsFlow(self.driver).run(self.profile)
        self.assertNotIn("publish", self.driver.actions)

    def test_security_challenge_stops_without_mutation(self):
        self.driver.detected = DetectedScreen("SECURITY_CHALLENGE", 0.80, ("security-marker",), True)
        result = ThreadsFlow(self.driver).run(self.profile)
        self.assertEqual("waiting_human", result.status)
        self.assertEqual([], self.driver.mutations)
```

- [ ] **Step 2: Implement only** `THREADS_ONBOARDING -> THREADS_PROFILE_SETUP -> THREADS_HOME/THREADS_POSTCHECK_OK`; no compose/publish selector or branch.

- [ ] **Step 3: Mirror Instagram unknown/error policy**: three observation-only UNKNOWN refreshes; network limited retry; app crash reopen once; rate/block immediate `retry_pending`; disabled immediate `error`.

- [ ] **Step 4: Implement observation-only resume** requiring a positively known successor; challenge disappearance alone is not success.

- [ ] **Step 5: Run and commit**

```bash
python3 -m unittest tests.test_factory_v2_threads_flow -v
git add core/factory_v2/ui_automation/threads tests/test_factory_v2_threads_flow.py
git commit -m "feat: automate safe Threads AVD flow"
```

---

### Task 6: Integrate flows into the isolated AVD worker

**Files:** modify `workers/account_factory_worker.py`, create `tests/test_factory_v2_avd_worker_agent.py`, modify `tests/test_factory_v2_worker_process.py`.

**Interfaces:** worker actions `PREPARE_INSTAGRAM`, `AUTOMATE_INSTAGRAM`, `OBSERVE_CHECKPOINT`, `AUTOMATE_THREADS`; preserve existing `OPEN_URL`.

- [ ] **Step 1: Write sanitized-response test**

```python
class AvdWorkerAgentTests(unittest.TestCase):
    def test_waiting_human_result_contains_no_sensitive_keys(self):
        response = self.agent.execute(WorkerCommand(
            command_id="cmd-1", action="AUTOMATE_INSTAGRAM", account_id="acc-1",
            payload={"job_id": "job-1", "profile": self.profile},
        ))
        self.assertEqual("waiting_human", response["status"])
        result = response["result"]
        self.assertEqual("OTP_REQUIRED", result["screen"])
        for key in ("password", "code", "raw_xml", "token"):
            self.assertNotIn(key, result)
```

- [ ] **Step 2: Refactor constructor**

```python
class WorkerAgent:
    def __init__(self, worker_id, avd_name, serial, *, avd=None, instagram_flow=None, threads_flow=None): ...
```

Default flows share one serial-scoped `AdbClient` backed by the existing `AvdManager` runner.

- [ ] **Step 3: Dispatch exactly**

```text
PREPARE_INSTAGRAM -> open official Instagram + detect, no blind navigation
AUTOMATE_INSTAGRAM -> InstagramFlow.run(username/display_name/bio only)
OBSERVE_CHECKPOINT -> platform flow observation-only method
AUTOMATE_THREADS -> open official Threads + ThreadsFlow.run(...)
OPEN_URL -> existing HTTPS-only implementation
```

Return only `status` and sanitized `screen/reason/last_safe_step`.

- [ ] **Step 4: Track sanitized recovery metadata**: existing `current_account_id/current_job_id` plus `flow`, `last_known_screen`, `last_safe_step`; never raw XML/profile text in heartbeat.

- [ ] **Step 5: Add duplicate-command test**: same `command_id` twice returns `CommandLedger` cached result with no second mutation.

- [ ] **Step 6: Run and commit**

```bash
python3 -m unittest tests.test_factory_v2_avd_worker_agent tests.test_factory_v2_worker_process -v
git add workers/account_factory_worker.py tests/test_factory_v2_avd_worker_agent.py tests/test_factory_v2_worker_process.py
git commit -m "feat: connect AVD worker to safe UI automation"
```

---

### Task 7: Controller routing, legal stage transitions, and auto-resume

**Files:** modify `runtime.py`, `service.py`, `runner_gateway.py`, `test_factory_v2_runtime.py`, `test_factory_v2_checkpoint_retry.py`.

- [ ] **Step 1: Write routing regression**: `REMOTE_AVD` uses AVD commands; `LOCAL_DEVICE` still uses `PREPARE_TEXT -> OPEN_PACKAGE -> REPORT_WAITING_HUMAN`.

- [ ] **Step 2: Keep gateway isolation**: test `REMOTE_AVD + AUTOMATE_INSTAGRAM` forwards; `LOCAL_DEVICE + AUTOMATE_INSTAGRAM` raises `ValueError`; do not expand `_LOCAL_ACTIONS`.

- [ ] **Step 3: Add service error codes**

```text
UI_CHANGED, RATE_LIMITED, ACTION_BLOCKED, ACCOUNT_DISABLED
```

`NETWORK_TRANSIENT` remains the controller-facing code for `NETWORK_ERROR`; existing worker timeout/error handling remains unchanged.

- [ ] **Step 4: Implement exact remote desired-action mapping**

```text
job desired_action=PREPARE_INSTAGRAM:
  remote -> PREPARE_INSTAGRAM then AUTOMATE_INSTAGRAM
  local  -> existing phone checkpoint sequence

IG_CREATED:
  job desired_action=PREPARE_THREADS
  next remote tick -> AUTOMATE_THREADS

THREADS_CREATED:
  job desired_action=START_ACP
  reuse existing _start_activation()
```

- [ ] **Step 5: Preserve legal Instagram stages**

```text
RUNNER_ASSIGNED -> IG_READY_FOR_HUMAN -> WAITING_HUMAN   (protected)
RUNNER_ASSIGNED -> IG_READY_FOR_HUMAN -> IG_CREATED      (automatic completion)
WAITING_HUMAN -> IG_CREATED                              (auto/manual resume)
```

- [ ] **Step 6: Preserve legal Threads stages**

```text
IG_CREATED -> THREADS_READY_FOR_HUMAN -> WAITING_HUMAN   (protected)
IG_CREATED -> THREADS_READY_FOR_HUMAN -> THREADS_CREATED  (automatic completion)
WAITING_HUMAN -> THREADS_CREATED                          (auto/manual resume)
```

- [ ] **Step 7: Map worker results**

```text
waiting_human -> OPEN checkpoint + WAITING_HUMAN
needs_confirmation/UI_CHANGED -> NEEDS_CONFIRMATION
retry_pending/RATE_LIMITED|ACTION_BLOCKED -> RETRY_PENDING
error/ACCOUNT_DISABLED -> ERROR
NETWORK_ERROR after bounded retries -> RETRY_PENDING with NETWORK_TRANSIENT
completed Instagram -> IG_CREATED, desired_action=PREPARE_THREADS
completed Threads -> THREADS_CREATED, desired_action=START_ACP
```

- [ ] **Step 8: Add automatic observation loop**: remote `WAITING_HUMAN` sends `OBSERVE_CHECKPOINT {flow: instagram|threads}` each controller tick. Still waiting only refreshes lease. Positive completed resolves checkpoint and advances. Manual `VERIFY_CHECKPOINT` stays fallback.

- [ ] **Step 9: Reuse OAuth**: after Threads completion call existing `_start_activation()`/`OPEN_URL`, never duplicate OAuth code.

- [ ] **Step 10: Run and commit**

```bash
python3 -m unittest tests.test_factory_v2_runtime tests.test_factory_v2_checkpoint_retry tests.test_factory_v2_dual_scheduler -v
git add core/factory_v2/runtime.py core/factory_v2/service.py core/factory_v2/runner_gateway.py tests/test_factory_v2_runtime.py tests/test_factory_v2_checkpoint_retry.py
git commit -m "feat: drive remote AVD automation from controller"
```

---

### Task 8: Restart reconciliation, lost-ACK safety, bounded retries

**Files:** modify worker, flows, `test_factory_v2_avd_worker_agent.py`, `test_factory_v2_ui_driver.py`, `test_factory_v2_runtime.py`.

- [ ] **Step 1: Write restart test**: DB/job is in Instagram flow, worker restarts, UI is already `IG_PROFILE_SETUP`, username already matches. Expected: detect actual screen, field update `noop`, signup-entry tap not replayed.

- [ ] **Step 2: Write lost-ACK test**: if UI is already on expected successor, retry returns completion/no-op without second mutation.

- [ ] **Step 3: Write bounded-retry test**: driver always returns `postcondition_failed`; assert exactly three total attempts and final `FlowResult("needs_confirmation", ..., "UI_CHANGED", ...)`.

- [ ] **Step 4: Write error retry tests**: `RATE_LIMITED/ACTION_BLOCKED` perform zero rapid mutation retries; `APP_CRASH` reopens app once only; `NETWORK_ERROR` is bounded; `ACCOUNT_DISABLED` is terminal for current account.

- [ ] **Step 5: Implement reconciliation-first behavior**: every `AUTOMATE_*` invocation detects current UI before mutation; a missing prior ACK never proves the previous mutation failed.

- [ ] **Step 6: Run and commit**

```bash
python3 -m unittest tests.test_factory_v2_avd_worker_agent tests.test_factory_v2_ui_driver tests.test_factory_v2_runtime -v
git add workers/account_factory_worker.py core/factory_v2/ui_automation tests/test_factory_v2_avd_worker_agent.py tests/test_factory_v2_ui_driver.py tests/test_factory_v2_runtime.py
git commit -m "fix: make AVD automation restart-safe and idempotent"
```

---

### Task 9: Full verification, runbook, and real `acp-worker-01` pilot

**Files:** modify `docs/ACP_ACCOUNT_FACTORY_RUNBOOK.md`, `scripts/verify_account_factory_dual_runner.sh`.

- [ ] **Step 1: Keep/add Python automation tests to verification script** without removing existing Android gates or printing secrets.

- [ ] **Step 2: Run full Python suite**

```bash
python3 -m unittest discover -s tests -p 'test*.py' -v
```

Expected: all PASS.

- [ ] **Step 3: Run Android tests/build with JDK 17**

```bash
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
~/.local/gradle/gradle-8.13/bin/gradle \
  -p android/account-factory \
  testDebugUnitTest assembleDebug \
  --no-daemon --max-workers=2 --console=plain
```

Expected: `BUILD SUCCESSFUL`; do not claim green without fresh output.

- [ ] **Step 4: Run repository verification**

```bash
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 bash scripts/verify_account_factory_dual_runner.sh
```

Expected: Python + Android gates PASS.

- [ ] **Step 5: Document/run AVD pilot setup**

```bash
export ANDROID_HOME="$HOME/Android/Sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"
adb devices
```

Verify `acp-worker-01`, official Instagram/Threads packages, and controller configuration. Create one account targeting `AUTO_AVD` or a selected `REMOTE_AVD` worker.

- [ ] **Step 6: Pilot acceptance**

```text
REMOTE_AVD assigned
Instagram auto-launches
known safe navigation/profile preparation runs
protected screen -> WAITING_HUMAN, zero further mutation
operator handles protected step manually
known safe successor -> automatic resume
IG_CREATED
Threads auto-launches
known safe Threads flow runs
THREADS_CREATED
existing ACP OAuth opens
ACP_ACTIVE after official OAuth completes
unknown UI -> NEEDS_CONFIRMATION, never blind tap
```

- [ ] **Step 7: Commit runbook/verification**

```bash
git add docs/ACP_ACCOUNT_FACTORY_RUNBOOK.md scripts/verify_account_factory_dual_runner.sh
git commit -m "docs: add AVD automation pilot verification"
```

---

## Final Review Gate

```bash
git status --short
git log --oneline --decorate -12
```

Complete only when tests/pilot prove:

```text
no password automation
no OTP automation
no CAPTCHA automation
no identity/security bypass
unknown UI never mutates
protected UI stops immediately
auto-resume requires positively known successor
controller remains business-stage authority
LOCAL_DEVICE remains unchanged
REMOTE_AVD retries are bounded and replay-safe
APP_CRASH/NETWORK/RATE_LIMIT policies match spec
ACP OAuth uses existing activation path
no merge to main
```
