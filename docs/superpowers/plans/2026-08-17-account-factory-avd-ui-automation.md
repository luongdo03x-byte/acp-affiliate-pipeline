# Account Factory AVD UI Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `REMOTE_AVD` into a fail-closed semi-automated runner that navigates known safe Instagram/Threads UI, fills only approved non-sensitive profile fields, pauses for protected human steps, auto-resumes only after a positively detected safe successor screen, and then continues through the existing ACP OAuth path.

**Architecture:** `FactoryControllerRuntime` remains authoritative for account/job/checkpoint state. A new ADB/UI automation package lives behind the isolated AVD worker process; it returns only sanitized observations and results. `LOCAL_DEVICE` keeps its existing command allowlist and phone workflow unchanged.

**Tech Stack:** Python 3, stdlib `xml.etree.ElementTree`, dataclasses, existing `AvdManager`/ADB transport, SQLite Factory V2 controller, `unittest`, official Instagram package `com.instagram.android`, official Threads package `com.instagram.barcelona`.

## Global Constraints

- Work only on `feat/account-factory-android`; do not merge `main`.
- Do not generate, enter, store, retrieve, or submit passwords.
- Do not retrieve, enter, solve, or bypass OTP/email/SMS verification codes or CAPTCHA.
- Do not automate selfie, identity, recovery, or security challenges.
- Do not publish Threads content as part of account creation.
- Unknown or ambiguous UI must fail closed; never exploratory-tap unknown screens.
- Controller/DB remains authoritative for business stages.
- AVD UI state is worker observation only; do not add AVD-only `AccountStage` values.
- Screenshot capture is diagnostic-only, disabled by default, and not part of the first decision loop.
- Raw hierarchy XML and sensitive values must not be persisted to DB or logs.
- A normal safe UI action gets at most three total attempts: initial attempt plus two retries.
- `LOCAL_DEVICE` behavior/lifecycle must remain unchanged.
- Reuse the existing ACP activation/OAuth implementation; do not create a second OAuth path.

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

    instagram/
        __init__.py
        screens.py
        selectors.py
        flow.py

    threads/
        __init__.py
        screens.py
        selectors.py
        flow.py

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

Modify only as required:

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

**Files:**
- Create: `core/factory_v2/ui_automation/__init__.py`
- Create: `core/factory_v2/ui_automation/adb.py`
- Create: `core/factory_v2/ui_automation/hierarchy.py`
- Create: `tests/test_factory_v2_ui_hierarchy.py`

**Interfaces:**
- Consumes: existing `AvdManager` command-runner semantics.
- Produces: `UiBounds`, `UiNode`, `UiSnapshot`, `UiHierarchyReader`, `AdbClient`.

- [ ] **Step 1: Write failing hierarchy parser tests**

```python
import unittest

from core.factory_v2.ui_automation.hierarchy import UiHierarchyReader


class UiHierarchyTests(unittest.TestCase):
    def test_parse_exposes_sanitized_metadata(self):
        xml = '''<hierarchy><node text="Username"
          resource-id="com.instagram.android:id/username"
          class="android.widget.EditText" clickable="true" enabled="true"
          bounds="[10,20][210,80]" /></hierarchy>'''
        snapshot = UiHierarchyReader().parse(
            xml,
            package="com.instagram.android",
            activity=".MainActivity",
        )
        node = snapshot.nodes[0]
        self.assertEqual("Username", node.text)
        self.assertEqual("com.instagram.android:id/username", node.resource_id)
        self.assertEqual((110, 50), node.bounds.center)
        self.assertTrue(node.clickable)

    def test_password_node_text_is_redacted(self):
        xml = '''<hierarchy><node text="secret-value" password="true"
          class="android.widget.EditText" bounds="[0,0][100,100]" /></hierarchy>'''
        snapshot = UiHierarchyReader().parse(xml, package="x", activity="y")
        self.assertEqual("", snapshot.nodes[0].text)
```

- [ ] **Step 2: Run RED test**

```bash
python3 -m unittest tests.test_factory_v2_ui_hierarchy -v
```

Expected: import failure because the package does not exist yet.

- [ ] **Step 3: Implement immutable UI types**

```python
@dataclass(frozen=True)
class UiBounds:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def center(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)


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

`UiHierarchyReader.parse(xml_text, *, package, activity)` must parse in memory, blank `text` and `content-desc` for `password="true"`, skip malformed bounds, and never write XML to host storage.

- [ ] **Step 4: Write failing ADB scoping tests**

```python
class AdbClientTests(unittest.TestCase):
    def test_tap_is_scoped_to_serial(self):
        runner = FakeRunner()
        client = AdbClient("emulator-5554", adb_path="adb", runner=runner)
        client.tap(120, 480)
        self.assertEqual(
            ["adb", "-s", "emulator-5554"],
            runner.calls[-1][0][:3],
        )
```

- [ ] **Step 5: Implement `AdbClient`**

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

Every command must begin with `[adb_path, "-s", serial, ...]`. `set_text()` rejects control characters and values longer than 500 characters. Do not add password/OTP helper APIs.

- [ ] **Step 6: Run GREEN tests and commit**

```bash
python3 -m unittest tests.test_factory_v2_ui_hierarchy -v
git add core/factory_v2/ui_automation tests/test_factory_v2_ui_hierarchy.py
git commit -m "feat: add sanitized AVD UI hierarchy reader"
```

Expected: PASS.

---

### Task 2: Selector engine, sanitized fixtures, and safety-first detector

**Files:**
- Create: `core/factory_v2/ui_automation/selectors.py`
- Create: `core/factory_v2/ui_automation/detector.py`
- Create: platform `__init__.py`, `screens.py`, `selectors.py`
- Create: six fixture XML files listed above
- Create: `tests/test_factory_v2_ui_detector.py`

**Interfaces:**
- Consumes: `UiSnapshot` and `UiNode`.
- Produces: `Selector`, `ScreenSignature`, `DetectedScreen`, `ScreenDetector`, `build_instagram_detector()`, `build_threads_detector()`.

- [ ] **Step 1: Write failing selector precedence test**

```python
class SelectorTests(unittest.TestCase):
    def test_resource_id_match_has_priority(self):
        selector = Selector(
            semantic="continue",
            resource_ids=("com.instagram.android:id/next_button",),
            texts=("Next", "Continue", "Tiếp tục", "Tiếp"),
        )
        node = selector.find(self.snapshot)
        self.assertEqual("com.instagram.android:id/next_button", node.resource_id)
```

Use this exact type:

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

Precedence: resource-id -> content-desc -> exact text -> normalized known alias -> class/clickable semantic combination.

- [ ] **Step 2: Write failing detector priority tests**

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

`ScreenDetector.detect()` evaluates ascending priority and returns `UNKNOWN` if no signature is strong enough.

- [ ] **Step 4: Define protected/error/normal signatures**

Protected kinds:

```text
PASSWORD_REQUIRED
OTP_REQUIRED
CAPTCHA_REQUIRED
EMAIL_OR_PHONE_VERIFICATION
SELFIE_OR_IDENTITY_CHECK
SECURITY_CHALLENGE
ACCOUNT_RECOVERY
CONSENT_WITH_SECURITY_IMPACT
```

Error kinds:

```text
NETWORK_ERROR
RATE_LIMITED
ACTION_BLOCKED
ACCOUNT_DISABLED
```

Normal pilot kinds:

```text
IG_SIGNUP_ENTRY
IG_PROFILE_SETUP
IG_HOME
IG_POSTCHECK_OK
THREADS_ONBOARDING
THREADS_PROFILE_SETUP
THREADS_HOME
THREADS_POSTCHECK_OK
```

Protected signatures get higher priority than error, success, and normal signatures. Credible protected evidence must stop even below the normal `0.90` automation threshold.

- [ ] **Step 5: Add sanitized fixtures**

Fixtures must use fake data only (`sample_user`, `Sample User`) and contain no real code/password/token values.

- [ ] **Step 6: Run tests and commit**

```bash
python3 -m unittest tests.test_factory_v2_ui_detector -v
git add core/factory_v2/ui_automation tests/fixtures/android_ui tests/test_factory_v2_ui_detector.py
git commit -m "feat: add fail-closed AVD screen detector"
```

Expected: PASS.

---

### Task 3: Safe UI driver with explicit mutation guards

**Files:**
- Create: `core/factory_v2/ui_automation/driver.py`
- Create: `tests/test_factory_v2_ui_driver.py`

**Interfaces:**
- Consumes: `AdbClient`, `UiHierarchyReader`, `Selector`, `ScreenDetector`.
- Produces: `ActionResult`, `SafeUiDriver`.

- [ ] **Step 1: Write failing no-op/not-found tests**

```python
class SafeUiDriverTests(unittest.TestCase):
    def test_set_text_is_noop_when_value_already_matches(self):
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
    def tap(
        self,
        selector: Selector,
        *,
        expected_screens: tuple[str, ...] = (),
        timeout: float = 8.0,
    ) -> ActionResult: ...
    def set_text(self, selector: Selector, value: str) -> ActionResult: ...
    def wait_for(self, screens: tuple[str, ...], timeout: float) -> DetectedScreen: ...
```

`tap()` only taps the center of a positively matched node. When `expected_screens` is supplied, success requires a positively detected expected screen.

- [ ] **Step 3: Add protected-field denylist test and implementation**

```python
class ProtectedFieldTests(unittest.TestCase):
    def test_password_selector_is_rejected(self):
        selector = Selector(semantic="password", texts=("Password",))
        with self.assertRaisesRegex(ValueError, "protected field automation is disabled"):
            self.driver.set_text(selector, "anything")
```

Deny exact semantics:

```text
password
otp
verification_code
recovery_code
```

- [ ] **Step 4: Make text replay idempotent**

For approved fields, focus the known element, clear existing content with key events/select-all behavior, input the desired value once, take a fresh snapshot, and return `completed` only if the value/postcondition can be verified. If already equal, return `noop` without ADB mutation.

- [ ] **Step 5: Run tests and commit**

```bash
python3 -m unittest tests.test_factory_v2_ui_driver -v
git add core/factory_v2/ui_automation/driver.py core/factory_v2/ui_automation/selectors.py tests/test_factory_v2_ui_driver.py
git commit -m "feat: add verified safe AVD UI driver"
```

Expected: PASS.

---

### Task 4: Shared flow result plus Instagram fail-closed state machine

**Files:**
- Create: `core/factory_v2/ui_automation/flow_result.py`
- Create: `core/factory_v2/ui_automation/instagram/flow.py`
- Modify: Instagram selectors/screens
- Create: `tests/test_factory_v2_instagram_flow.py`

**Interfaces:**
- Consumes: `SafeUiDriver`, profile `{username, display_name, bio}`.
- Produces: shared `FlowResult` and `InstagramFlow.run(profile)` / `observe_checkpoint()`.

- [ ] **Step 1: Define shared result contract**

```python
@dataclass(frozen=True)
class FlowResult:
    status: str  # running | waiting_human | completed | needs_confirmation | retry_pending | error
    screen: str
    reason: str | None = None
    last_safe_step: str | None = None
```

- [ ] **Step 2: Write protected-step test**

```python
class InstagramFlowTests(unittest.TestCase):
    def test_otp_stops_before_any_mutation(self):
        self.driver.detected = DetectedScreen(
            kind="OTP_REQUIRED",
            confidence=0.82,
            evidence=("verification-code-marker",),
            protected=True,
        )
        result = InstagramFlow(self.driver).run(self.profile)
        self.assertEqual("waiting_human", result.status)
        self.assertEqual("OTP_REQUIRED", result.screen)
        self.assertEqual([], self.driver.mutations)
```

- [ ] **Step 3: Write approved-profile-field test**

```python
    def test_profile_setup_only_sets_approved_fields(self):
        InstagramFlow(self.driver).run(self.profile)
        self.assertEqual(
            [
                ("username", "sample_user"),
                ("display_name", "Sample User"),
                ("bio", "Sample bio"),
            ],
            self.driver.set_values,
        )
```

- [ ] **Step 4: Implement bounded known-screen navigation**

Allowed safe pilot mutations are only those attached to explicit selectors for `IG_SIGNUP_ENTRY` and `IG_PROFILE_SETUP`. Every transition follows detect -> precondition -> mutation -> positive postcondition. Use:

```python
for attempt in range(3):
    action = driver.tap(selector, expected_screens=expected)
    if action.status in {"completed", "noop"}:
        break
else:
    return FlowResult("needs_confirmation", current.kind, "UI_CHANGED", last_safe_step)
```

No password/OTP/CAPTCHA/security selector may be passed to `set_text()`.

- [ ] **Step 5: Implement observation-only auto-resume**

`observe_checkpoint()` performs zero mutation. It returns `completed` only when both conditions hold:

```text
previous protected screen is absent
AND a known valid successor is positively detected
```

Instagram valid successors for pilot: `IG_PROFILE_SETUP`, `IG_HOME`, `IG_POSTCHECK_OK`. Absence of the challenge alone never means success.

- [ ] **Step 6: Run tests and commit**

```bash
python3 -m unittest tests.test_factory_v2_instagram_flow -v
git add core/factory_v2/ui_automation/flow_result.py core/factory_v2/ui_automation/instagram tests/test_factory_v2_instagram_flow.py
git commit -m "feat: automate safe Instagram AVD flow"
```

Expected: PASS.

---

### Task 5: Threads fail-closed state machine

**Files:**
- Create: `core/factory_v2/ui_automation/threads/flow.py`
- Modify: Threads selectors/screens
- Create: `tests/test_factory_v2_threads_flow.py`

**Interfaces:**
- Consumes: shared `FlowResult`, `SafeUiDriver`, approved profile.
- Produces: `ThreadsFlow.run(profile)` / `observe_checkpoint()` using the same status contract as Instagram.

- [ ] **Step 1: Write no-publishing and protected-screen tests**

```python
class ThreadsFlowTests(unittest.TestCase):
    def test_normal_flow_never_contains_publish_action(self):
        ThreadsFlow(self.driver).run(self.profile)
        self.assertNotIn("publish", self.driver.actions)

    def test_security_challenge_stops_without_mutation(self):
        self.driver.detected = DetectedScreen(
            kind="SECURITY_CHALLENGE",
            confidence=0.80,
            evidence=("security-check-marker",),
            protected=True,
        )
        result = ThreadsFlow(self.driver).run(self.profile)
        self.assertEqual("waiting_human", result.status)
        self.assertEqual([], self.driver.mutations)
```

- [ ] **Step 2: Implement only known onboarding/profile transitions**

Safe pilot screens:

```text
THREADS_ONBOARDING
THREADS_PROFILE_SETUP
THREADS_HOME
THREADS_POSTCHECK_OK
```

There must be no selector or flow branch for composing/publishing content.

- [ ] **Step 3: Implement observation-only resume**

Valid successor detection is required after a human checkpoint; challenge disappearance alone does not resume.

- [ ] **Step 4: Run tests and commit**

```bash
python3 -m unittest tests.test_factory_v2_threads_flow -v
git add core/factory_v2/ui_automation/threads tests/test_factory_v2_threads_flow.py
git commit -m "feat: automate safe Threads AVD flow"
```

Expected: PASS.

---

### Task 6: Integrate safe flows into the isolated AVD worker

**Files:**
- Modify: `workers/account_factory_worker.py`
- Create: `tests/test_factory_v2_avd_worker_agent.py`
- Modify: `tests/test_factory_v2_worker_process.py`

**Interfaces:**
- Consumes: `WorkerCommand`, `CommandLedger`, platform flows.
- Produces AVD worker commands: `PREPARE_INSTAGRAM`, `AUTOMATE_INSTAGRAM`, `OBSERVE_CHECKPOINT`, `AUTOMATE_THREADS`; keeps existing `OPEN_URL`.

- [ ] **Step 1: Write worker result-sanitization test**

```python
class AvdWorkerAgentTests(unittest.TestCase):
    def test_waiting_human_result_contains_no_sensitive_payload(self):
        response = self.agent.execute(WorkerCommand(
            command_id="cmd-1",
            action="AUTOMATE_INSTAGRAM",
            account_id="acc-1",
            payload={"job_id": "job-1", "profile": self.profile},
        ))
        self.assertTrue(response["ok"])
        self.assertEqual("waiting_human", response["status"])
        result = response["result"]
        self.assertEqual("OTP_REQUIRED", result["screen"])
        for forbidden in ("password", "code", "raw_xml", "token"):
            self.assertNotIn(forbidden, result)
```

- [ ] **Step 2: Refactor constructor for dependency injection**

```python
class WorkerAgent:
    def __init__(
        self,
        worker_id: str,
        avd_name: str,
        serial: str,
        *,
        avd: AvdManager | None = None,
        instagram_flow=None,
        threads_flow=None,
    ):
        ...
```

Default flow construction uses one serial-scoped `AdbClient` backed by the existing `AvdManager` runner.

- [ ] **Step 3: Add exact dispatch behavior**

```text
PREPARE_INSTAGRAM -> open official Instagram, detect current screen, no blind navigation
AUTOMATE_INSTAGRAM -> run InstagramFlow with only username/display_name/bio
OBSERVE_CHECKPOINT -> call observation-only method for payload.flow instagram|threads
AUTOMATE_THREADS -> open official Threads, then run ThreadsFlow
OPEN_URL -> preserve existing HTTPS-only behavior
```

Worker response shape:

```python
{
    "ok": True,
    "status": flow_result.status,
    "result": {
        "screen": flow_result.screen,
        "reason": flow_result.reason,
        "last_safe_step": flow_result.last_safe_step,
    },
}
```

- [ ] **Step 4: Keep only sanitized recovery metadata**

Worker memory may track:

```python
self.flow: str | None
self.last_known_screen: str | None
self.last_safe_step: str | None
```

Heartbeat must not contain raw hierarchy/profile values.

- [ ] **Step 5: Verify duplicate command idempotency**

Execute the same `command_id` twice and assert `CommandLedger` returns the cached result without a second driver mutation.

- [ ] **Step 6: Run tests and commit**

```bash
python3 -m unittest \
  tests.test_factory_v2_avd_worker_agent \
  tests.test_factory_v2_worker_process -v
git add workers/account_factory_worker.py tests/test_factory_v2_avd_worker_agent.py tests/test_factory_v2_worker_process.py
git commit -m "feat: connect AVD worker to safe UI automation"
```

Expected: PASS, including existing safe-env assertions.

---

### Task 7: Controller routing, legal stage transitions, and automatic checkpoint observation

**Files:**
- Modify: `core/factory_v2/runtime.py`
- Modify: `core/factory_v2/service.py`
- Modify: `core/factory_v2/runner_gateway.py`
- Modify: `tests/test_factory_v2_runtime.py`
- Modify: `tests/test_factory_v2_checkpoint_retry.py`

**Interfaces:**
- Consumes: sanitized AVD `FlowResult` response.
- Produces: legal authoritative stages/checkpoints/jobs while preserving `LOCAL_DEVICE` behavior.

- [ ] **Step 1: Write remote-vs-local routing regression tests**

For `REMOTE_AVD`, first job flow must use AVD automation commands rather than the phone-only sequence. For `LOCAL_DEVICE`, keep the existing sequence:

```text
PREPARE_TEXT -> OPEN_PACKAGE -> REPORT_WAITING_HUMAN
```

- [ ] **Step 2: Keep gateway isolation explicit**

`RunnerGateway.send()` already forwards worker-process commands for `REMOTE_AVD` and restricts `LOCAL_DEVICE` with `_LOCAL_ACTIONS`. Add tests proving:

```text
REMOTE_AVD + AUTOMATE_INSTAGRAM -> forwarded
LOCAL_DEVICE + AUTOMATE_INSTAGRAM -> ValueError
```

Do not add AVD automation actions to `_LOCAL_ACTIONS`.

- [ ] **Step 3: Add approved error codes**

Extend `_ALLOWED_ERROR_CODES` in `service.py` with:

```text
UI_CHANGED
RATE_LIMITED
ACTION_BLOCKED
ACCOUNT_DISABLED
```

- [ ] **Step 4: Implement legal Instagram stage transitions**

Because current state machine does not allow `RUNNER_ASSIGNED -> IG_CREATED` directly, use existing legal intermediate states:

```text
RUNNER_ASSIGNED
  -> IG_READY_FOR_HUMAN
  -> WAITING_HUMAN        when a protected step appears
```

When an AVD flow positively completes without needing a human checkpoint:

```text
RUNNER_ASSIGNED
  -> IG_READY_FOR_HUMAN
  -> IG_CREATED
```

When resuming from human checkpoint:

```text
WAITING_HUMAN -> IG_CREATED
```

No new AccountStage is introduced.

- [ ] **Step 5: Implement legal Threads transitions**

Similarly:

```text
IG_CREATED
  -> THREADS_READY_FOR_HUMAN
  -> WAITING_HUMAN        when protected step appears
```

or automatic completion:

```text
IG_CREATED
  -> THREADS_READY_FOR_HUMAN
  -> THREADS_CREATED
```

Human-resume path remains:

```text
WAITING_HUMAN -> THREADS_CREATED
```

- [ ] **Step 6: Map worker statuses exactly**

```text
waiting_human -> open/update checkpoint and WAITING_HUMAN
needs_confirmation + UI_CHANGED -> NEEDS_CONFIRMATION
retry_pending + RATE_LIMITED/ACTION_BLOCKED -> RETRY_PENDING
error + ACCOUNT_DISABLED -> ERROR
completed Instagram -> IG_CREATED, job desired_action=PREPARE_THREADS/AUTOMATE_THREADS
completed Threads -> THREADS_CREATED, then reuse START_ACP
```

- [ ] **Step 7: Add observation-only automatic resume**

For remote AVD jobs in `WAITING_HUMAN`, runtime sends:

```python
self._command(job, "OBSERVE_CHECKPOINT", {"flow": "instagram"})
```

or `threads` according to checkpoint type. `waiting_human` only refreshes lease/heartbeat. A positive `completed` resolves the checkpoint and legally advances the account. Manual `VERIFY_CHECKPOINT` remains as fallback.

- [ ] **Step 8: Preserve existing OAuth path**

After `THREADS_CREATED`, call existing `_start_activation()` and `OPEN_URL`; do not duplicate OAuth implementation. Test that the same job proceeds to `START_ACP`/activation.

- [ ] **Step 9: Run controller tests and commit**

```bash
python3 -m unittest \
  tests.test_factory_v2_runtime \
  tests.test_factory_v2_checkpoint_retry \
  tests.test_factory_v2_dual_scheduler -v
git add core/factory_v2/runtime.py core/factory_v2/service.py core/factory_v2/runner_gateway.py tests/test_factory_v2_runtime.py tests/test_factory_v2_checkpoint_retry.py
git commit -m "feat: drive remote AVD automation from controller"
```

Expected: PASS and phone regression unchanged.

---

### Task 8: Restart reconciliation, lost-ACK safety, and bounded retries

**Files:**
- Modify: `workers/account_factory_worker.py`
- Modify: platform flows as needed
- Modify: `tests/test_factory_v2_avd_worker_agent.py`
- Modify: `tests/test_factory_v2_ui_driver.py`
- Modify: `tests/test_factory_v2_runtime.py`

**Interfaces:**
- Consumes: current UI snapshot plus authoritative DB stage.
- Produces: no-op/recovery decisions instead of blind replay.

- [ ] **Step 1: Write restart reconciliation test**

Scenario:

```text
DB/account job says Instagram automation is active
worker process restarted
current AVD UI is IG_PROFILE_SETUP
username already equals sample_user
```

Expected: detector reconciles current UI, username set becomes `noop`, and signup-entry navigation is not replayed.

- [ ] **Step 2: Write lost-ACK test**

If a command response was lost but UI is already on the expected successor, retry must return completion/no-op without a second mutation.

- [ ] **Step 3: Enforce bounded retry**

Add a fake driver that always returns `postcondition_failed`. Assert exactly three total mutation attempts and final:

```python
FlowResult(
    status="needs_confirmation",
    screen=current.kind,
    reason="UI_CHANGED",
    last_safe_step=last_safe_step,
)
```

- [ ] **Step 4: Disable rapid retries for rate/block errors**

`RATE_LIMITED` and `ACTION_BLOCKED` return `retry_pending` immediately; the flow must not tap/reopen in a tight loop.

- [ ] **Step 5: Run recovery tests and commit**

```bash
python3 -m unittest \
  tests.test_factory_v2_avd_worker_agent \
  tests.test_factory_v2_ui_driver \
  tests.test_factory_v2_runtime -v
git add workers/account_factory_worker.py core/factory_v2/ui_automation tests/test_factory_v2_avd_worker_agent.py tests/test_factory_v2_ui_driver.py tests/test_factory_v2_runtime.py
git commit -m "fix: make AVD automation restart-safe and idempotent"
```

Expected: PASS.

---

### Task 9: Full verification, runbook, and real `acp-worker-01` pilot

**Files:**
- Modify: `docs/ACP_ACCOUNT_FACTORY_RUNBOOK.md`
- Modify: `scripts/verify_account_factory_dual_runner.sh`

**Interfaces:**
- Consumes: implementation from Tasks 1–8.
- Produces: repeatable regression gate and pilot procedure.

- [ ] **Step 1: Update verification script to include new Python tests**

Keep all existing Python and Android gates; add no secret-printing commands.

- [ ] **Step 2: Run full Python suite**

```bash
python3 -m unittest discover -s tests -p 'test*.py' -v
```

Expected: all tests PASS.

- [ ] **Step 3: Run Android unit tests and APK build with JDK 17**

```bash
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
~/.local/gradle/gradle-8.13/bin/gradle \
  -p android/account-factory \
  testDebugUnitTest assembleDebug \
  --no-daemon --max-workers=2 --console=plain
```

Expected: `BUILD SUCCESSFUL`. Do not claim this gate is green until fresh output is observed.

- [ ] **Step 4: Run repository verification script**

```bash
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
bash scripts/verify_account_factory_dual_runner.sh
```

Expected: Python and Android gates PASS.

- [ ] **Step 5: Document real AVD pilot setup**

Runbook must include:

```bash
export ANDROID_HOME="$HOME/Android/Sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"
adb devices
```

Verify `acp-worker-01`, official Instagram/Threads packages, controller configuration, then create one account targeted to `AUTO_AVD` or the selected `REMOTE_AVD` worker through the existing product surface.

- [ ] **Step 6: Execute pilot acceptance checklist**

Observe:

```text
REMOTE_AVD assigned
Instagram auto-launches
known safe navigation/profile preparation runs
protected screen -> WAITING_HUMAN and zero further mutation
operator handles protected step manually
worker auto-resumes only after a known safe successor
IG_CREATED
Threads auto-launches
known safe Threads flow runs
THREADS_CREATED
existing ACP OAuth opens
ACP_ACTIVE after official OAuth completes
```

Force or simulate one unknown fixture/UI state and confirm `NEEDS_CONFIRMATION`, not blind tapping.

- [ ] **Step 7: Commit verification/runbook changes**

```bash
git add docs/ACP_ACCOUNT_FACTORY_RUNBOOK.md scripts/verify_account_factory_dual_runner.sh
git commit -m "docs: add AVD automation pilot verification"
```

---

## Final Review Gate

Run:

```bash
git status --short
git log --oneline --decorate -12
```

The phase is complete only if tests/pilot demonstrate all of these:

```text
no password automation
no OTP automation
no CAPTCHA automation
no identity/security bypass
unknown UI never mutates
protected UI stops immediately
human auto-resume requires a positively known successor
controller remains business-stage authority
LOCAL_DEVICE behavior remains unchanged
REMOTE_AVD retries are bounded and replay-safe
ACP OAuth uses the existing activation path
no merge to main
```
