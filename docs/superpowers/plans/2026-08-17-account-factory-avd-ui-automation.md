# Account Factory AVD UI Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `REMOTE_AVD` into a fail-closed semi-automated runner that navigates known safe Instagram/Threads UI, fills only approved non-sensitive profile fields, pauses for protected human steps, auto-resumes only after a positively detected safe successor screen, and then continues to ACP OAuth.

**Architecture:** Keep `FactoryControllerRuntime` authoritative for business stage and job state. Add a focused ADB/UI automation package used only by the AVD worker process; the worker returns sanitized screen observations/results and never writes authoritative account stages directly. `LOCAL_DEVICE` remains unchanged and continues through the existing runner gateway path.

**Tech Stack:** Python 3, stdlib `xml.etree.ElementTree`, dataclasses/enums, existing `AvdManager`/ADB transport, SQLite-backed Factory V2 controller, `unittest` test suite, official Instagram (`com.instagram.android`) and Threads (`com.instagram.barcelona`) Android apps.

## Global Constraints

- Work only on `feat/account-factory-android`; do not merge `main`.
- Do not generate, enter, store, retrieve, or submit passwords.
- Do not retrieve, enter, solve, or bypass OTP/email/SMS verification codes or CAPTCHA.
- Do not automate selfie, identity, recovery, or security challenges.
- Do not publish Threads content as part of account creation.
- Unknown or ambiguous UI must fail closed; never exploratory-tap unknown screens.
- Controller/DB remains authoritative for `RUNNER_ASSIGNED`, `WAITING_HUMAN`, `IG_CREATED`, `THREADS_CREATED`, `ACP_CONNECTING`, and `ACP_ACTIVE`.
- AVD UI states are observations only and must not replace AccountStage values.
- Screenshot capture is diagnostic-only, disabled by default, and never part of the first decision loop.
- Raw hierarchy XML and sensitive field values must not be persisted to DB or logs.
- Normal safe UI actions may retry at most two times after the initial attempt.
- `LOCAL_DEVICE` behavior and lifecycle must remain unchanged.

---

## File Structure

Create focused modules:

```text
core/factory_v2/ui_automation/
    __init__.py          # public UI automation types
    adb.py               # ADB operations scoped to one serial
    hierarchy.py         # XML -> sanitized UiSnapshot/UiNode
    selectors.py         # generic selector matching primitives
    detector.py          # generic screen result/signature engine
    driver.py            # SafeUiDriver precondition/action/postcondition API

    instagram/
        __init__.py
        screens.py       # Instagram screen kinds + signatures
        selectors.py     # known Instagram selectors
        flow.py          # fail-closed Instagram state machine

    threads/
        __init__.py
        screens.py       # Threads screen kinds + signatures
        selectors.py     # known Threads selectors
        flow.py          # fail-closed Threads state machine
```

Add tests/fixtures:

```text
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

Modify existing integration points only where required:

```text
workers/account_factory_worker.py
core/factory_v2/runtime.py
core/factory_v2/service.py
core/factory_v2/runner_gateway.py
tests/test_factory_v2_runtime.py
tests/test_factory_v2_worker_process.py
docs/ACP_ACCOUNT_FACTORY_RUNBOOK.md
scripts/verify_account_factory_dual_runner.sh
```

---

### Task 1: Sanitized UI hierarchy model and scoped ADB client

**Files:**
- Create: `core/factory_v2/ui_automation/__init__.py`
- Create: `core/factory_v2/ui_automation/adb.py`
- Create: `core/factory_v2/ui_automation/hierarchy.py`
- Create: `tests/test_factory_v2_ui_hierarchy.py`

**Interfaces:**
- Consumes: existing `core.factory_v2.avd.CompletedCommand`-compatible command runner semantics.
- Produces: `UiBounds`, `UiNode`, `UiSnapshot`, `UiHierarchyReader.parse(xml_text, package, activity)`, and `AdbClient` methods used by Tasks 2–7.

- [ ] **Step 1: Write hierarchy parser tests first**

```python
from core.factory_v2.ui_automation.hierarchy import UiHierarchyReader

XML = '''<hierarchy><node text="Username" resource-id="com.instagram.android:id/username"
 class="android.widget.EditText" clickable="true" enabled="true"
 bounds="[10,20][210,80]" /></hierarchy>'''


def test_parse_exposes_sanitized_node_metadata():
    snapshot = UiHierarchyReader().parse(
        XML,
        package="com.instagram.android",
        activity=".MainActivity",
    )
    node = snapshot.nodes[0]
    assert node.text == "Username"
    assert node.resource_id == "com.instagram.android:id/username"
    assert node.bounds.center == (110, 50)
    assert node.clickable is True


def test_sensitive_input_value_is_redacted():
    xml = '''<hierarchy><node text="123456" password="true"
      class="android.widget.EditText" bounds="[0,0][100,100]" /></hierarchy>'''
    snapshot = UiHierarchyReader().parse(xml, package="x", activity="y")
    assert snapshot.nodes[0].text == ""
```

- [ ] **Step 2: Run the parser tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_factory_v2_ui_hierarchy -v
```

Expected: import/module failure because `ui_automation.hierarchy` does not exist yet.

- [ ] **Step 3: Implement immutable sanitized model and parser**

Use exact public shapes:

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

`UiHierarchyReader.parse()` must parse XML in memory, blank `text`/`content-desc` when `password="true"`, reject malformed bounds by skipping the node, and never write the XML to disk.

- [ ] **Step 4: Add scoped `AdbClient` tests and implementation**

Test exact command scoping:

```python
def test_adb_client_always_scopes_serial(fake_runner):
    client = AdbClient("emulator-5554", adb_path="adb", runner=fake_runner)
    client.tap(120, 480)
    assert fake_runner.calls[-1][0][:3] == ["adb", "-s", "emulator-5554"]
```

Implement:

```python
class AdbClient:
    def foreground(self) -> tuple[str | None, str | None]: ...
    def dump_hierarchy(self) -> str: ...
    def tap(self, x: int, y: int) -> None: ...
    def set_text(self, text: str) -> None: ...
    def back(self) -> None: ...
    def home(self) -> None: ...
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None: ...
    def open_package(self, package: str) -> None: ...
```

`set_text()` must reject control characters and values over 500 characters. No password/OTP-specific API is added.

- [ ] **Step 5: Run focused tests and commit**

```bash
python3 -m unittest tests.test_factory_v2_ui_hierarchy -v
git add core/factory_v2/ui_automation tests/test_factory_v2_ui_hierarchy.py
git commit -m "feat: add sanitized AVD UI hierarchy reader"
```

Expected: PASS.

---

### Task 2: Selector matching and fail-closed screen detector

**Files:**
- Create: `core/factory_v2/ui_automation/selectors.py`
- Create: `core/factory_v2/ui_automation/detector.py`
- Create: `core/factory_v2/ui_automation/instagram/__init__.py`
- Create: `core/factory_v2/ui_automation/instagram/screens.py`
- Create: `core/factory_v2/ui_automation/instagram/selectors.py`
- Create: `core/factory_v2/ui_automation/threads/__init__.py`
- Create: `core/factory_v2/ui_automation/threads/screens.py`
- Create: `core/factory_v2/ui_automation/threads/selectors.py`
- Create: `tests/test_factory_v2_ui_detector.py`
- Create: six sanitized fixture XML files listed in File Structure.

**Interfaces:**
- Consumes: `UiSnapshot`, `UiNode` from Task 1.
- Produces: `Selector`, `ScreenSignature`, `DetectedScreen`, `ScreenDetector.detect(snapshot)` and platform-specific detector builders.

- [ ] **Step 1: Write selector precedence tests**

```python
def test_resource_id_beats_text_alias(snapshot):
    selector = Selector(
        resource_ids=("com.instagram.android:id/next_button",),
        texts=("Next", "Continue", "Tiếp tục", "Tiếp"),
    )
    match = selector.find(snapshot)
    assert match.resource_id == "com.instagram.android:id/next_button"
```

Define exact selector shape:

```python
@dataclass(frozen=True)
class Selector:
    resource_ids: tuple[str, ...] = ()
    content_descs: tuple[str, ...] = ()
    texts: tuple[str, ...] = ()
    class_names: tuple[str, ...] = ()
    require_clickable: bool = False

    def find(self, snapshot: UiSnapshot) -> UiNode | None: ...
```

Precedence must be resource-id -> content-desc -> exact text -> normalized alias -> semantic class/clickable match.

- [ ] **Step 2: Write detector safety-priority tests**

```python
def test_otp_signature_wins_over_normal_continue_button(otp_snapshot):
    detected = build_instagram_detector().detect(otp_snapshot)
    assert detected.kind == "OTP_REQUIRED"
    assert detected.protected is True


def test_unknown_screen_never_returns_automation_allowed(unknown_snapshot):
    detected = build_instagram_detector().detect(unknown_snapshot)
    assert detected.kind == "UNKNOWN"
    assert detected.automation_allowed is False
```

Use exact result type:

```python
@dataclass(frozen=True)
class DetectedScreen:
    kind: str
    confidence: float
    evidence: tuple[str, ...]
    protected: bool = False

    @property
    def automation_allowed(self) -> bool:
        return (not self.protected) and self.kind != "UNKNOWN" and self.confidence >= 0.90
```

- [ ] **Step 3: Implement `ScreenSignature` and ordered detector**

```python
@dataclass(frozen=True)
class ScreenSignature:
    kind: str
    package: str
    required_any: tuple[Selector, ...]
    minimum_matches: int
    confidence: float
    protected: bool = False
    priority: int = 100
```

`ScreenDetector.detect()` evaluates lower `priority` first. Platform builders must place protected screens before error, success, normal, then unknown.

Instagram protected kinds must include exactly:

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

Error kinds must include `NETWORK_ERROR`, `RATE_LIMITED`, `ACTION_BLOCKED`, `ACCOUNT_DISABLED`.

- [ ] **Step 4: Add sanitized fixtures and normal-screen signatures**

Fixtures contain only fake values such as `sample_user`, never real credentials/codes. Add signatures sufficient for the pilot:

```text
Instagram: IG_SIGNUP_ENTRY, IG_PROFILE_SETUP, IG_HOME, IG_POSTCHECK_OK
Threads: THREADS_ONBOARDING, THREADS_PROFILE_SETUP, THREADS_HOME, THREADS_POSTCHECK_OK
```

- [ ] **Step 5: Run detector tests and commit**

```bash
python3 -m unittest tests.test_factory_v2_ui_detector -v
git add core/factory_v2/ui_automation tests/fixtures/android_ui tests/test_factory_v2_ui_detector.py
git commit -m "feat: add fail-closed AVD screen detector"
```

Expected: PASS including protected-screen priority and unknown-screen fail closed behavior.

---

### Task 3: Safe UI driver with precondition/postcondition verification

**Files:**
- Create: `core/factory_v2/ui_automation/driver.py`
- Create: `tests/test_factory_v2_ui_driver.py`

**Interfaces:**
- Consumes: `AdbClient`, `UiHierarchyReader`, `Selector`, `ScreenDetector`.
- Produces: `ActionResult` and `SafeUiDriver` used by Instagram/Threads flows.

- [ ] **Step 1: Write idempotent text-setting and tap verification tests**

```python
def test_set_text_is_noop_when_field_already_matches(driver):
    result = driver.set_text(USERNAME_INPUT, "sample_user")
    assert result.status == "noop"
    assert driver.adb.input_calls == []


def test_tap_requires_known_node(driver):
    result = driver.tap(MISSING_SELECTOR)
    assert result.status == "not_found"
    assert driver.adb.tap_calls == []
```

- [ ] **Step 2: Run tests and verify RED**

```bash
python3 -m unittest tests.test_factory_v2_ui_driver -v
```

Expected: module/class missing.

- [ ] **Step 3: Implement exact driver API**

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

`tap()` must locate a known node, tap its center, then positively verify one of `expected_screens` if supplied. Retry policy belongs to flow code, not inside unbounded driver loops.

`set_text()` must only operate on a node matched by an explicit selector and must clear the selected non-sensitive field before input to avoid `abcabc` replay behavior.

- [ ] **Step 4: Add a protected-field denylist guard**

Before any `set_text`, reject selectors whose semantic tag is one of:

```text
password
otp
verification_code
recovery_code
```

Represent this with `Selector.semantic: str | None` and raise `ValueError("protected field automation is disabled")`.

- [ ] **Step 5: Run focused tests and commit**

```bash
python3 -m unittest tests.test_factory_v2_ui_driver -v
git add core/factory_v2/ui_automation/driver.py core/factory_v2/ui_automation/selectors.py tests/test_factory_v2_ui_driver.py
git commit -m "feat: add verified safe AVD UI driver"
```

Expected: PASS.

---

### Task 4: Instagram fail-closed automation flow

**Files:**
- Create: `core/factory_v2/ui_automation/instagram/flow.py`
- Modify: `core/factory_v2/ui_automation/instagram/selectors.py`
- Modify: `core/factory_v2/ui_automation/instagram/screens.py`
- Create: `tests/test_factory_v2_instagram_flow.py`

**Interfaces:**
- Consumes: `SafeUiDriver`, approved profile payload `{username, display_name, bio}`.
- Produces: `FlowResult(status, screen, reason, last_safe_step)` via `InstagramFlow.run(profile)` and `InstagramFlow.observe_checkpoint()`.

- [ ] **Step 1: Write protected-step interruption test**

```python
def test_instagram_stops_without_mutation_on_otp(fake_driver):
    fake_driver.detected = DetectedScreen(
        kind="OTP_REQUIRED", confidence=0.82,
        evidence=("verification code",), protected=True,
    )
    result = InstagramFlow(fake_driver).run({
        "username": "sample_user",
        "display_name": "Sample User",
        "bio": "Sample bio",
    })
    assert result.status == "waiting_human"
    assert result.screen == "OTP_REQUIRED"
    assert fake_driver.mutations == []
```

- [ ] **Step 2: Write known safe profile preparation test**

```python
def test_instagram_fills_only_approved_profile_fields(fake_driver):
    result = InstagramFlow(fake_driver).run(PROFILE)
    assert result.status in {"running", "waiting_human", "completed"}
    assert fake_driver.set_values == [
        ("username", "sample_user"),
        ("display_name", "Sample User"),
        ("bio", "Sample bio"),
    ]
```

- [ ] **Step 3: Implement exact result/state shape**

```python
@dataclass(frozen=True)
class FlowResult:
    status: str  # running | waiting_human | completed | needs_confirmation | retry_pending | error
    screen: str
    reason: str | None = None
    last_safe_step: str | None = None
```

`InstagramFlow.run()` may automate only `IG_SIGNUP_ENTRY` and `IG_PROFILE_SETUP` transitions defined in selectors/signatures. It must not submit password, OTP, CAPTCHA, identity/security, recovery, or unknown screens.

For a normal action:

```python
for attempt in range(3):  # initial + 2 retries
    result = driver.tap(selector, expected_screens=expected)
    if result.status == "completed":
        break
else:
    return FlowResult("needs_confirmation", current.kind, "UI_CHANGED", last_safe_step)
```

- [ ] **Step 4: Implement observation-only auto-resume rule**

`observe_checkpoint()` returns `completed` only when the protected screen has disappeared **and** one known valid successor (`IG_PROFILE_SETUP`, `IG_HOME`, `IG_POSTCHECK_OK`) is positively detected. A plain absence of OTP/challenge returns `waiting_human` or `needs_confirmation`, never success.

- [ ] **Step 5: Run tests and commit**

```bash
python3 -m unittest tests.test_factory_v2_instagram_flow -v
git add core/factory_v2/ui_automation/instagram tests/test_factory_v2_instagram_flow.py
git commit -m "feat: automate safe Instagram AVD flow"
```

Expected: PASS.

---

### Task 5: Threads fail-closed automation flow

**Files:**
- Create: `core/factory_v2/ui_automation/threads/flow.py`
- Modify: `core/factory_v2/ui_automation/threads/selectors.py`
- Modify: `core/factory_v2/ui_automation/threads/screens.py`
- Create: `tests/test_factory_v2_threads_flow.py`

**Interfaces:**
- Consumes: `SafeUiDriver`, approved profile payload.
- Produces: same `FlowResult` contract as Instagram; no publishing action.

- [ ] **Step 1: Write Threads onboarding and protected-screen tests**

```python
def test_threads_known_onboarding_can_continue(fake_driver):
    result = ThreadsFlow(fake_driver).run(PROFILE)
    assert result.status != "error"
    assert "publish" not in fake_driver.actions


def test_threads_security_challenge_stops_immediately(fake_driver):
    fake_driver.detected = DetectedScreen(
        "SECURITY_CHALLENGE", 0.80, ("security check",), protected=True
    )
    result = ThreadsFlow(fake_driver).run(PROFILE)
    assert result.status == "waiting_human"
    assert fake_driver.mutations == []
```

- [ ] **Step 2: Implement Threads flow using only known selectors**

Allowed pilot transitions are limited to `THREADS_ONBOARDING` -> `THREADS_PROFILE_SETUP` -> `THREADS_POSTCHECK_OK/THREADS_HOME`. Do not add any selector/action for creating or publishing a post.

- [ ] **Step 3: Implement observation-only auto-resume**

Successor requirement mirrors Instagram: protected screen gone + known valid Threads successor present.

- [ ] **Step 4: Run tests and commit**

```bash
python3 -m unittest tests.test_factory_v2_threads_flow -v
git add core/factory_v2/ui_automation/threads tests/test_factory_v2_threads_flow.py
git commit -m "feat: automate safe Threads AVD flow"
```

Expected: PASS.

---

### Task 6: Integrate automation into the isolated AVD worker agent

**Files:**
- Modify: `workers/account_factory_worker.py`
- Create: `tests/test_factory_v2_avd_worker_agent.py`
- Modify: `tests/test_factory_v2_worker_process.py`

**Interfaces:**
- Consumes: `AdbClient`, platform detector/flows, existing `WorkerCommand`/`CommandLedger`.
- Produces worker actions `PREPARE_INSTAGRAM`, `AUTOMATE_INSTAGRAM`, `OBSERVE_CHECKPOINT`, `AUTOMATE_THREADS`, existing `OPEN_URL`.

- [ ] **Step 1: Write worker command tests**

```python
def test_automate_instagram_returns_sanitized_waiting_human(agent):
    response = agent.execute(WorkerCommand(
        command_id="cmd-1",
        action="AUTOMATE_INSTAGRAM",
        account_id="acc-1",
        payload={
            "job_id": "job-1",
            "profile": {
                "username": "sample_user",
                "display_name": "Sample User",
                "bio": "Sample bio",
            },
        },
    ))
    assert response["ok"] is True
    assert response["status"] == "waiting_human"
    assert response["result"]["screen"] == "OTP_REQUIRED"
    assert "otp" not in str(response).lower()
```

The final assertion should check values/keys, not reject the literal screen name; specifically assert there is no `code`, `password`, `raw_xml`, or credential value in the result payload.

- [ ] **Step 2: Refactor `WorkerAgent` constructor for dependency injection**

Use:

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

Default flows are built from `AdbClient(serial, adb_path=self.avd.adb, runner=self.avd.runner)`.

- [ ] **Step 3: Add exact action dispatch**

```text
PREPARE_INSTAGRAM -> launch official Instagram and detect entry state
AUTOMATE_INSTAGRAM -> run InstagramFlow with approved profile payload
OBSERVE_CHECKPOINT -> run observation-only method for payload.flow == instagram|threads
AUTOMATE_THREADS -> launch/run ThreadsFlow
OPEN_URL -> keep existing safe HTTPS behavior
```

Worker heartbeat observation fields track only:

```text
flow
last_known_screen
last_safe_step
```

Do not include raw XML or profile field values in heartbeat.

- [ ] **Step 4: Preserve at-most-once command behavior**

Add a test that executing the same `command_id` twice returns the ledger-cached result and does not duplicate driver mutation calls.

- [ ] **Step 5: Run worker tests and commit**

```bash
python3 -m unittest \
  tests.test_factory_v2_avd_worker_agent \
  tests.test_factory_v2_worker_process -v
git add workers/account_factory_worker.py tests/test_factory_v2_avd_worker_agent.py tests/test_factory_v2_worker_process.py
git commit -m "feat: connect AVD worker to safe UI automation"
```

Expected: PASS and existing safe worker environment test remains green.

---

### Task 7: Controller integration, checkpoints, auto-resume, and failure mapping

**Files:**
- Modify: `core/factory_v2/runtime.py`
- Modify: `core/factory_v2/service.py`
- Modify: `core/factory_v2/runner_gateway.py`
- Modify: `tests/test_factory_v2_runtime.py`

**Interfaces:**
- Consumes: sanitized AVD worker result `{status, result:{screen, reason, last_safe_step?}}`.
- Produces authoritative AccountStage/job/checkpoint transitions while leaving `LOCAL_DEVICE` commands unchanged.

- [ ] **Step 1: Extend runtime tests for REMOTE_AVD automatic path**

Add a fake worker process returning staged responses and assert the first remote AVD job uses:

```text
PREPARE_INSTAGRAM
AUTOMATE_INSTAGRAM
```

instead of the existing phone-style `PREPARE_TEXT -> OPEN_PACKAGE -> REPORT_WAITING_HUMAN` sequence.

Keep a separate regression test asserting a `LOCAL_DEVICE` job still uses the existing phone command sequence.

- [ ] **Step 2: Add error codes required by the approved spec**

Extend `_ALLOWED_ERROR_CODES` in `service.py` with:

```text
UI_CHANGED
RATE_LIMITED
ACTION_BLOCKED
ACCOUNT_DISABLED
```

Map worker results:

```text
waiting_human -> WAITING_HUMAN + OPEN checkpoint
needs_confirmation/UI_CHANGED -> NEEDS_CONFIRMATION
retry_pending/RATE_LIMITED/ACTION_BLOCKED -> RETRY_PENDING
error/ACCOUNT_DISABLED -> ERROR
completed Instagram -> IG_CREATED
completed Threads -> THREADS_CREATED then START_ACP
```

Use existing legal state-machine transitions only; do not add AVD-only AccountStage values.

- [ ] **Step 3: Add remote-A VD command helpers without changing local gateway allowlist**

`RunnerGateway.send()` already forwards arbitrary actions for `REMOTE_AVD` and restricts `LOCAL_DEVICE` to `_LOCAL_ACTIONS`. Add a regression test proving `AUTOMATE_INSTAGRAM` is accepted for `REMOTE_AVD` but rejected for `LOCAL_DEVICE`; no broadening of `_LOCAL_ACTIONS`.

- [ ] **Step 4: Implement automatic human checkpoint observation**

When a remote AVD account is in `WAITING_HUMAN`, runtime issues:

```python
self._command(job, "OBSERVE_CHECKPOINT", {"flow": "instagram"})
```

or `threads` according to checkpoint type. If result is still `waiting_human`, only heartbeat/lease is refreshed. If positively `completed`, resolve the checkpoint and advance. Manual `VERIFY_CHECKPOINT` remains supported as fallback.

- [ ] **Step 5: Ensure Threads completion starts ACP OAuth on the same job**

Reuse existing `_start_activation()` and `OPEN_URL` behavior; do not duplicate OAuth logic. Add assertion that `THREADS_CREATED` causes desired action `START_ACP`/activation and not a new account lease.

- [ ] **Step 6: Run controller integration tests and commit**

```bash
python3 -m unittest \
  tests.test_factory_v2_runtime \
  tests.test_factory_v2_checkpoint_retry \
  tests.test_factory_v2_dual_scheduler -v
git add core/factory_v2/runtime.py core/factory_v2/service.py core/factory_v2/runner_gateway.py tests/test_factory_v2_runtime.py
git commit -m "feat: drive remote AVD automation from controller"
```

Expected: PASS, with `LOCAL_DEVICE` regression green.

---

### Task 8: Restart reconciliation, bounded retries, and idempotency regression

**Files:**
- Modify: `workers/account_factory_worker.py`
- Modify: `core/factory_v2/runtime.py`
- Modify: `tests/test_factory_v2_avd_worker_agent.py`
- Modify: `tests/test_factory_v2_runtime.py`

**Interfaces:**
- Consumes: current UI snapshot + authoritative account/job stage.
- Produces safe recovery decisions without blindly replaying mutating actions.

- [ ] **Step 1: Write restart reconciliation test**

Scenario:

```text
DB stage = RUNNER_ASSIGNED
current AVD UI = IG_PROFILE_SETUP with username already present
worker process restarted
```

Expected: worker detects actual screen, treats already-applied field value as no-op, and resumes from the current screen rather than replaying signup entry navigation.

- [ ] **Step 2: Write lost-ACK duplicate command test**

Simulate the UI already being on a valid successor when the controller retries. Expected: postcondition detection returns completion/no-op without a second mutating tap.

- [ ] **Step 3: Implement recovery observation state**

Keep only in-memory worker metadata:

```python
self.flow: str | None
self.last_known_screen: str | None
self.last_safe_step: str | None
```

On each `AUTOMATE_*` invocation, detect current UI before taking any mutation. Never infer failure solely from a missing prior ACK.

- [ ] **Step 4: Verify bounded retry policy**

Add tests that a safe tap can occur at most 3 attempts total and then returns `needs_confirmation` with `UI_CHANGED`. `RATE_LIMITED`/`ACTION_BLOCKED` must not rapid-retry.

- [ ] **Step 5: Run recovery tests and commit**

```bash
python3 -m unittest \
  tests.test_factory_v2_avd_worker_agent \
  tests.test_factory_v2_runtime \
  tests.test_factory_v2_ui_driver -v
git add workers/account_factory_worker.py core/factory_v2/runtime.py tests/test_factory_v2_avd_worker_agent.py tests/test_factory_v2_runtime.py
git commit -m "fix: make AVD automation restart-safe and idempotent"
```

Expected: PASS.

---

### Task 9: Full verification, runbook, and real `acp-worker-01` pilot

**Files:**
- Modify: `docs/ACP_ACCOUNT_FACTORY_RUNBOOK.md`
- Modify: `scripts/verify_account_factory_dual_runner.sh`

**Interfaces:**
- Consumes: complete implementation from Tasks 1–8.
- Produces: repeatable regression command and documented pilot procedure.

- [ ] **Step 1: Add Python tests to verification script**

Ensure the script runs the repository's existing Python suite plus the new focused tests. Do not remove Android verification already present.

- [ ] **Step 2: Run the full Python suite**

```bash
python3 -m unittest discover -s tests -p 'test*.py' -v
```

Expected: all tests PASS with no new credential/raw-XML output.

- [ ] **Step 3: Run Android unit tests and APK build with JDK 17**

```bash
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
~/.local/gradle/gradle-8.13/bin/gradle \
  -p android/account-factory \
  testDebugUnitTest assembleDebug \
  --no-daemon --max-workers=2 --console=plain
```

Expected: `BUILD SUCCESSFUL`.

- [ ] **Step 4: Run the repository verification script**

```bash
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
bash scripts/verify_account_factory_dual_runner.sh
```

Expected: Python and Android gates PASS. Do not claim green until this fresh output is observed.

- [ ] **Step 5: Update runbook with AVD pilot commands**

Document:

```bash
export ANDROID_HOME="$HOME/Android/Sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"
adb devices
```

Then confirm `acp-worker-01` exists, official Instagram/Threads packages are installed, Controller is started with the existing Factory key, and create a single account targeting `AUTO_AVD`/the selected remote worker via the existing UI/API.

- [ ] **Step 6: Real pilot acceptance checklist**

Observe, without automating protected steps:

```text
REMOTE_AVD assigned
Instagram auto-launches
known safe navigation/profile preparation runs
protected step -> WAITING_HUMAN with zero further mutation
operator completes protected step manually
auto-resume requires known safe successor
IG_CREATED
Threads auto-launches
known safe Threads flow runs
THREADS_CREATED
ACP OAuth opens
ACP_ACTIVE after official OAuth completes
```

Additionally verify an unknown screen produces `NEEDS_CONFIRMATION`, not blind tapping.

- [ ] **Step 7: Commit verification/runbook changes**

```bash
git add docs/ACP_ACCOUNT_FACTORY_RUNBOOK.md scripts/verify_account_factory_dual_runner.sh
git commit -m "docs: add AVD automation pilot verification"
```

---

## Final Review Gate

Before declaring this phase complete, inspect:

```bash
git status --short
git log --oneline --decorate -12
```

Then verify these invariants from tests and the real pilot:

```text
no password automation
no OTP automation
no CAPTCHA automation
no identity/security bypass
unknown UI never mutates
protected UI stops immediately
human auto-resume requires a positively known successor
controller remains business-stage authority
LOCAL_DEVICE behavior is unchanged
REMOTE_AVD retries are bounded/idempotent
ACP OAuth reuses existing activation path
no merge to main
```
