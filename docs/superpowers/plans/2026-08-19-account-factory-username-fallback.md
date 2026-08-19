# Deterministic Instagram Username Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically recover from an unavailable Instagram signup username with at most five deterministic fallback candidates, continue only after Instagram positively validates a candidate, and persist the accepted username back to `factory_account.username`.

**Architecture:** Extend the existing fail-closed detector with explicit username-valid/unavailable states, keep fallback candidate generation as a pure deterministic identity-layer function, and add an additive `FlowResult.profile_updates` channel. The AVD worker sanitizes that channel; the Controller re-validates it and persists only the accepted username for the currently leased account before scheduling the next UI action.

**Tech Stack:** Python 3, `unittest`, Flask controller runtime, SQLite, Android ADB/uiautomator accessibility snapshots.

**Spec:** `docs/superpowers/specs/2026-08-19-account-factory-username-fallback-design.md`

## Global Constraints

- Branch is `feat/account-factory-android`; do not merge `main` as part of this work.
- Never click Instagram visual suggestion rows by coordinates.
- Test the requested username plus at most five deterministic fallback candidates per signup episode.
- Stop candidate attempts immediately on rate-limit, action-blocked, password, OTP, CAPTCHA, identity/security, recovery, or other protected states.
- The final official Instagram `Create account` / final signup submit remains HUMAN-only.
- Password, OTP, verification code, recovery code, and security data must never enter worker profile updates.
- Unknown UI must fail closed.
- No new database column is required; `factory_account.username` remains the durable source of truth.
- The existing `SOCIAL_ONLY`, `ACP_ACTIVE`, LOCAL_DEVICE, and one-AVD pilot semantics must not regress.

---

## File structure / responsibilities

- `core/factory_v2/ui_automation/selectors.py` — generic normalized selector matching; add an all-substrings text matcher without changing existing match precedence.
- `core/factory_v2/ui_automation/instagram/selectors.py` — username valid/unavailable markers for the known `Create a username` context.
- `core/factory_v2/ui_automation/instagram/screens.py` — explicit `IG_USERNAME_VALID` and `IG_USERNAME_UNAVAILABLE` signatures with correct priority.
- `core/factory_v2/identity.py` — pure deterministic fallback candidate generator.
- `core/factory_v2/ui_automation/flow_result.py` — additive sanitized profile-update result contract.
- `core/factory_v2/ui_automation/instagram/flow.py` — bounded username availability state machine and fallback attempts.
- `workers/account_factory_worker.py` — pass `WorkerCommand.account_id` into Instagram flow and sanitize outbound username updates.
- `core/factory_v2/service.py` — authoritative validation and persistence of worker-selected username for the currently leased account.
- `core/factory_v2/runtime.py` — validate response shape, persist username update before scheduling the next remote action, and fail closed on synchronization errors.
- Existing test modules are extended; no new production subsystem is introduced.

---

### Task 1: Detect explicit username validation states

**Files:**
- Modify: `core/factory_v2/ui_automation/selectors.py`
- Modify: `core/factory_v2/ui_automation/instagram/selectors.py`
- Modify: `core/factory_v2/ui_automation/instagram/screens.py`
- Test: `tests/test_factory_v2_ui_detector.py`

**Interfaces:**
- Produces: `Selector.text_contains_all: tuple[str, ...]`
- Produces: `USERNAME_VALID_MARKER`, `USERNAME_UNAVAILABLE_MARKER`
- Produces detector states: `IG_USERNAME_VALID`, `IG_USERNAME_UNAVAILABLE`
- Preserves: existing exact resource-id/content-desc/text/class matching precedence and all protected/error priorities.

- [ ] **Step 1: Write failing selector and detector tests**

Add tests equivalent to:

```python
def test_text_contains_all_is_normalized_and_requires_every_term(self):
    snapshot = UiSnapshot("x", "y", (
        node(text="The username BAONGOCD   is not available."),
        node(text="Username is valid."),
    ))
    selector = Selector(
        semantic="username_unavailable",
        text_contains_all=("username", "is not available"),
    )
    self.assertEqual(
        "The username BAONGOCD   is not available.",
        selector.find(snapshot).text,
    )


def test_username_unavailable_requires_create_username_context(self):
    snapshot = UiSnapshot(
        "com.instagram.android",
        ".activity.MainTabActivity",
        (
            node(text="Create a username", content_desc="Create a username"),
            node(
                text="baongocd",
                content_desc="Username,dragon.3275826",
                class_name="android.widget.EditText",
                clickable=True,
            ),
            node(text="The username baongocd is not available."),
        ),
    )
    detected = build_instagram_detector().detect(snapshot)
    self.assertEqual("IG_USERNAME_UNAVAILABLE", detected.kind)
    self.assertTrue(detected.automation_allowed)


def test_username_valid_requires_positive_marker_and_next(self):
    snapshot = UiSnapshot(
        "com.instagram.android",
        ".activity.MainTabActivity",
        (
            node(text="Create a username", content_desc="Create a username"),
            node(
                text="baongocd483102",
                content_desc="Username,baongocd483102",
                class_name="android.widget.EditText",
                clickable=True,
            ),
            node(content_desc="Input Username is valid.", class_name="android.widget.ImageView"),
            node(content_desc="Next", class_name="android.widget.Button", clickable=True),
        ),
    )
    detected = build_instagram_detector().detect(snapshot)
    self.assertEqual("IG_USERNAME_VALID", detected.kind)
    self.assertTrue(detected.automation_allowed)


def test_generic_not_available_text_does_not_become_username_unavailable(self):
    snapshot = UiSnapshot(
        "com.instagram.android",
        ".activity.MainTabActivity",
        (node(text="This feature is not available."),),
    )
    self.assertNotEqual(
        "IG_USERNAME_UNAVAILABLE",
        build_instagram_detector().detect(snapshot).kind,
    )


def test_rate_limit_still_wins_over_username_context(self):
    snapshot = UiSnapshot(
        "com.instagram.android",
        ".activity.MainTabActivity",
        (
            node(text="Create a username", content_desc="Create a username"),
            node(text="baongocd", class_name="android.widget.EditText", clickable=True),
            node(text="The username baongocd is not available."),
            node(text="Try again later"),
        ),
    )
    self.assertEqual("RATE_LIMITED", build_instagram_detector().detect(snapshot).kind)
```

- [ ] **Step 2: Run RED**

Run:

```bash
python3 -m unittest tests.test_factory_v2_ui_detector -v
```

Expected: new tests fail because `text_contains_all`, `IG_USERNAME_VALID`, and `IG_USERNAME_UNAVAILABLE` do not exist yet; existing protected/error tests remain green.

- [ ] **Step 3: Implement the generic all-substrings matcher**

Extend `Selector` additively:

```python
@dataclass(frozen=True)
class Selector:
    semantic: str | None = None
    resource_ids: tuple[str, ...] = ()
    content_descs: tuple[str, ...] = ()
    texts: tuple[str, ...] = ()
    text_contains_all: tuple[str, ...] = ()
    class_names: tuple[str, ...] = ()
    require_clickable: bool = False
```

In `find`, after exact/normalized text aliases and before class-name fallback, use:

```python
contains = tuple(
    normalize_ui_text(value)
    for value in self.text_contains_all
    if normalize_ui_text(value)
)
if contains:
    for node in nodes:
        actual = normalize_ui_text(node.text)
        if all(expected in actual for expected in contains):
            return node
```

Do not change behavior when `text_contains_all=()`.

- [ ] **Step 4: Add Instagram validation markers**

In `instagram/selectors.py` add:

```python
USERNAME_VALID_MARKER = Selector(
    semantic="username_valid",
    content_descs=("Input Username is valid.",),
    texts=("Input Username is valid.",),
)

USERNAME_UNAVAILABLE_MARKER = Selector(
    semantic="username_unavailable",
    text_contains_all=("username", "is not available"),
)
```

Keep `USERNAME_ENTRY_INPUT` contextual and class-based; do not add coordinate selectors.

- [ ] **Step 5: Add explicit detector states above generic username entry**

Import the markers and insert these after global protected/error states but before `IG_USERNAME_ENTRY`:

```python
ScreenSignature(
    "IG_USERNAME_UNAVAILABLE",
    PACKAGE,
    (CREATE_USERNAME_TITLE, USERNAME_ENTRY_INPUT, USERNAME_UNAVAILABLE_MARKER),
    3,
    0.99,
    False,
    74,
),
ScreenSignature(
    "IG_USERNAME_VALID",
    PACKAGE,
    (CREATE_USERNAME_TITLE, USERNAME_ENTRY_INPUT, USERNAME_VALID_MARKER, CONTINUE),
    4,
    0.99,
    False,
    75,
),
```

Keep generic `IG_USERNAME_ENTRY` at priority 76 so explicit validation states win.

- [ ] **Step 6: Run GREEN and commit**

Run:

```bash
python3 -m unittest tests.test_factory_v2_ui_detector -v
```

Expected: all tests pass.

Commit:

```bash
git add core/factory_v2/ui_automation/selectors.py \
        core/factory_v2/ui_automation/instagram/selectors.py \
        core/factory_v2/ui_automation/instagram/screens.py \
        tests/test_factory_v2_ui_detector.py
git commit -m "feat: detect Instagram username availability"
```

---

### Task 2: Generate bounded deterministic fallback usernames

**Files:**
- Modify: `core/factory_v2/identity.py`
- Test: `tests/test_factory_v2_identity.py`

**Interfaces:**
- Produces: `username_fallback_candidates(requested_username: str, account_id: str, max_candidates: int = 5) -> tuple[str, ...]`
- Later tasks call this function only for Instagram username recovery.

- [ ] **Step 1: Write failing deterministic-generator tests**

Add:

```python
from core.factory_v2.identity import username_fallback_candidates


def test_username_fallback_candidates_are_stable_bounded_and_safe(self):
    first = username_fallback_candidates("baongocd", "acc-1")
    second = username_fallback_candidates("baongocd", "acc-1")
    self.assertEqual(first, second)
    self.assertEqual(5, len(first))
    self.assertEqual(5, len(set(first)))
    self.assertNotIn("baongocd", first)
    self.assertTrue(all(len(value) <= 30 for value in first))
    self.assertTrue(all(re.fullmatch(r"[a-z0-9._]+", value) for value in first))


def test_username_fallback_candidates_change_with_account_id(self):
    self.assertNotEqual(
        username_fallback_candidates("baongocd", "acc-1"),
        username_fallback_candidates("baongocd", "acc-2"),
    )


def test_username_fallback_candidates_never_exceed_five(self):
    self.assertEqual(
        5,
        len(username_fallback_candidates("baongocd", "acc-1", max_candidates=99)),
    )
```

Add `import re` to the test module.

- [ ] **Step 2: Run RED**

Run:

```bash
python3 -m unittest tests.test_factory_v2_identity -v
```

Expected: import/name failure for `username_fallback_candidates`.

- [ ] **Step 3: Implement the pure generator**

Add `import hashlib` and constants/helpers in `identity.py`:

```python
_INSTAGRAM_USERNAME_MAX = 30
_USERNAME_SAFE = re.compile(r"[^a-z0-9._]+")


def _normalize_fallback_base(value: str) -> str:
    cleaned = _USERNAME_SAFE.sub("", str(value or "").casefold()).strip("._")
    return cleaned or "profile"


def username_fallback_candidates(
    requested_username: str,
    account_id: str,
    max_candidates: int = 5,
) -> tuple[str, ...]:
    limit = min(max(0, int(max_candidates)), 5)
    if limit == 0:
        return ()
    stable_id = str(account_id or "").strip()
    if not stable_id:
        raise ValueError("account_id is required for username fallback")

    requested = _normalize_fallback_base(requested_username)
    suffix_width = 6
    prefix = requested[: _INSTAGRAM_USERNAME_MAX - suffix_width].rstrip("._") or "profile"
    result: list[str] = []
    for attempt in range(1, limit + 1):
        digest = hashlib.sha256(f"{stable_id}:{attempt}".encode("utf-8")).digest()
        suffix = f"{int.from_bytes(digest[:8], 'big') % 1_000_000:06d}"
        candidate = f"{prefix}{suffix}"[:_INSTAGRAM_USERNAME_MAX]
        if candidate == requested or candidate in result:
            continue
        result.append(candidate)
    return tuple(result)
```

The function must not use `random` or current time.

- [ ] **Step 4: Run GREEN and commit**

Run:

```bash
python3 -m unittest tests.test_factory_v2_identity -v
```

Expected: all identity tests pass.

Commit:

```bash
git add core/factory_v2/identity.py tests/test_factory_v2_identity.py
git commit -m "feat: generate deterministic username fallbacks"
```

---

### Task 3: Add sanitized profile updates to the worker result contract

**Files:**
- Modify: `core/factory_v2/ui_automation/flow_result.py`
- Modify: `workers/account_factory_worker.py`
- Test: `tests/test_factory_v2_avd_worker_agent.py`

**Interfaces:**
- Produces: `FlowResult.profile_updates: dict[str, str] | None`
- Changes Instagram flow call to `InstagramFlow.run(profile: dict, *, account_id: str | None = None)`; Task 4 implements the production signature.
- Worker response may include `result.profile_updates = {"username": "..."}` and no other update keys.

- [ ] **Step 1: Write failing FlowResult/worker sanitation tests**

Update `FakeFlow.run` to accept the future interface and record the account id:

```python
def run(self, profile, *, account_id=None):
    self.run_calls.append(dict(profile))
    self.account_ids = getattr(self, "account_ids", [])
    self.account_ids.append(account_id)
    return self.result
```

Add tests:

```python
def test_instagram_flow_receives_stable_account_id(self):
    agent, instagram, _ = self.make_agent(FlowResult("running", "IG_USERNAME_ENTRY"))
    agent.execute(WorkerCommand(
        "account-seed", "AUTOMATE_INSTAGRAM", "acc-123", {"profile": self.profile}
    ))
    self.assertEqual(["acc-123"], instagram.account_ids)


def test_worker_returns_only_sanitized_username_profile_update(self):
    agent, _, _ = self.make_agent(
        FlowResult(
            "running",
            "IG_USERNAME_VALID",
            last_safe_step="IG_USERNAME_ENTRY",
            profile_updates={"username": "baongocd483102"},
        )
    )
    response = agent.execute(WorkerCommand(
        "username-update", "AUTOMATE_INSTAGRAM", "acc-1", {"profile": self.profile}
    ))
    self.assertEqual(
        {"username": "baongocd483102"},
        response["result"]["profile_updates"],
    )


def test_worker_rejects_unknown_or_sensitive_profile_update_keys(self):
    agent, _, _ = self.make_agent(
        FlowResult(
            "running",
            "IG_USERNAME_VALID",
            profile_updates={"username": "safe_name", "password": "secret"},
        )
    )
    with self.assertRaisesRegex(ValueError, "profile_updates"):
        agent.execute(WorkerCommand(
            "bad-update", "AUTOMATE_INSTAGRAM", "acc-1", {"profile": self.profile}
        ))
```

- [ ] **Step 2: Run RED**

Run:

```bash
python3 -m unittest tests.test_factory_v2_avd_worker_agent -v
```

Expected: `FlowResult` does not accept `profile_updates`, and worker does not pass `account_id` to `run`.

- [ ] **Step 3: Extend FlowResult additively**

Change to:

```python
@dataclass(frozen=True)
class FlowResult:
    status: str
    screen: str
    reason: str | None = None
    last_safe_step: str | None = None
    profile_updates: dict[str, str] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "screen": self.screen,
            "reason": self.reason,
            "last_safe_step": self.last_safe_step,
            "profile_updates": self.profile_updates,
        }
```

Existing four-argument constructions remain valid.

- [ ] **Step 4: Sanitize worker profile updates**

In `account_factory_worker.py` add:

```python
_USERNAME_UPDATE_RE = re.compile(r"^[a-z0-9._]{1,30}$")


def _safe_profile_updates(value) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"username"}:
        raise ValueError("invalid profile_updates")
    username = _clean_profile_text(value.get("username"), "username", max_length=30)
    if username is None or _USERNAME_UPDATE_RE.fullmatch(username) is None:
        raise ValueError("invalid profile_updates")
    return {"username": username}
```

In `_flow_response` sanitize `getattr(result, "profile_updates", None)` and include the result only as:

```python
"profile_updates": profile_updates,
```

Do not add password/OTP/contact fields to this allowlist.

- [ ] **Step 5: Pass the command account id explicitly to Instagram flow**

Change only the Instagram call:

```python
self.instagram_flow.run(profile, account_id=command.account_id)
```

Do not put `account_id` inside the profile dictionary, and do not change Threads flow input.

- [ ] **Step 6: Run GREEN and commit**

Run:

```bash
python3 -m unittest tests.test_factory_v2_avd_worker_agent -v
```

Expected: all worker-agent tests pass.

Commit:

```bash
git add core/factory_v2/ui_automation/flow_result.py \
        workers/account_factory_worker.py \
        tests/test_factory_v2_avd_worker_agent.py
git commit -m "feat: return sanitized worker profile updates"
```

---

### Task 4: Implement the bounded Instagram username fallback state machine

**Files:**
- Modify: `core/factory_v2/ui_automation/instagram/flow.py`
- Test: `tests/test_factory_v2_instagram_flow.py`

**Interfaces:**
- Consumes: `username_fallback_candidates(...)` from Task 2.
- Consumes detector states `IG_USERNAME_VALID` / `IG_USERNAME_UNAVAILABLE` from Task 1.
- Produces: `InstagramFlow.run(profile, *, account_id=None)` and `FlowResult.profile_updates` only after successful Next/postcondition.

- [ ] **Step 1: Upgrade FakeDriver for validation polling**

Extend the existing test fake without changing production behavior:

```python
def __init__(..., wait_screens=None, node_texts=None):
    ...
    self.wait_screens = list(wait_screens or [])
    self.node_texts = dict(node_texts or {})


def find(self, selector):
    if selector.semantic not in self.available:
        return None
    return SimpleNamespace(text=self.node_texts.get(selector.semantic, ""))


def wait_for(self, expected_screens, timeout):
    if self.wait_screens:
        return self.wait_screens.pop(0)
    return self.last
```

When `set_text` succeeds for semantic `username`, update `self.node_texts["username"] = value` so a later `find` reflects the typed value.

- [ ] **Step 2: Write failing flow tests for requested-valid and fallback-valid cases**

Add:

```python
def test_requested_username_valid_taps_next_without_profile_update(self):
    driver = FakeDriver(
        [DetectedScreen("IG_USERNAME_ENTRY", 0.97, ("create_username",), False)],
        available=("username", "continue"),
        node_texts={"username": "dragon.3275826"},
        wait_screens=[DetectedScreen("IG_USERNAME_VALID", 0.99, ("valid",), False)],
    )
    result = InstagramFlow(driver).run(self.profile, account_id="acc-1")
    self.assertEqual("running", result.status)
    self.assertEqual([("username", "sample_user")], driver.set_values)
    self.assertIsNone(result.profile_updates)
    self.assertIn(("tap", "continue"), driver.mutations)


def test_unavailable_username_uses_first_valid_fallback_and_reports_update(self):
    driver = FakeDriver(
        [DetectedScreen("IG_USERNAME_UNAVAILABLE", 0.99, ("unavailable",), False)],
        available=("username", "continue"),
        node_texts={"username": "sample_user"},
        wait_screens=[DetectedScreen("IG_USERNAME_VALID", 0.99, ("valid",), False)],
    )
    expected = username_fallback_candidates("sample_user", "acc-1")[0]
    result = InstagramFlow(driver).run(self.profile, account_id="acc-1")
    self.assertEqual("running", result.status)
    self.assertEqual(expected, driver.set_values[-1][1])
    self.assertEqual({"username": expected}, result.profile_updates)
```

- [ ] **Step 3: Add failing bounded/error tests**

Add cases proving:

```python
def test_five_unavailable_fallbacks_stop_without_sixth_candidate(self):
    unavailable = DetectedScreen("IG_USERNAME_UNAVAILABLE", 0.99, ("unavailable",), False)
    driver = FakeDriver(
        [unavailable],
        available=("username", "continue"),
        node_texts={"username": "sample_user"},
        wait_screens=[unavailable] * 5,
    )
    result = InstagramFlow(driver).run(self.profile, account_id="acc-1")
    self.assertEqual("needs_confirmation", result.status)
    self.assertEqual("USERNAME_UNAVAILABLE", result.reason)
    self.assertEqual(5, len(driver.set_values))


def test_username_rate_limit_stops_before_next_candidate(self):
    driver = FakeDriver(
        [DetectedScreen("IG_USERNAME_UNAVAILABLE", 0.99, (), False)],
        available=("username", "continue"),
        node_texts={"username": "sample_user"},
        wait_screens=[DetectedScreen("RATE_LIMITED", 0.99, ("rate",), False)],
    )
    result = InstagramFlow(driver).run(self.profile, account_id="acc-1")
    self.assertEqual("retry_pending", result.status)
    self.assertEqual("RATE_LIMITED", result.reason)
    self.assertEqual(1, len(driver.set_values))


def test_username_unknown_validation_fails_closed(self):
    driver = FakeDriver(
        [DetectedScreen("IG_USERNAME_ENTRY", 0.97, (), False)],
        available=("username", "continue"),
        node_texts={"username": "dragon.3275826"},
        wait_screens=[DetectedScreen("UNKNOWN", 0.0, (), False)],
    )
    result = InstagramFlow(driver).run(self.profile, account_id="acc-1")
    self.assertEqual("needs_confirmation", result.status)
    self.assertEqual("UI_CHANGED", result.reason)
```

Also add a missing-account-id test for an unavailable username and confirm password/OTP/final-submit tests still make zero mutation.

- [ ] **Step 4: Run RED**

Run:

```bash
python3 -m unittest tests.test_factory_v2_instagram_flow -v
```

Expected: new tests fail because current `run` has no `account_id` keyword and current flow has no validation/fallback state machine.

- [ ] **Step 5: Implement username validation helpers**

Import:

```python
from core.factory_v2.identity import username_fallback_candidates
```

Add terminal validation states:

```python
_USERNAME_VALIDATION_STATES = _IG_PROTECTED + _IG_ERRORS + (
    "IG_USERNAME_VALID",
    "IG_USERNAME_UNAVAILABLE",
)
```

Add `IG_USERNAME_VALID` and `IG_USERNAME_UNAVAILABLE` to `_AFTER_SIGNUP`, `_AFTER_ADD_ACCOUNT`, and other username-related successor tuples so postconditions accept the explicit detector states.

Implement a helper that maps validation results without probing again:

```python
def _username_terminal_result(self, detected):
    if detected.protected:
        return FlowResult("waiting_human", detected.kind, "HUMAN_VERIFICATION_REQUIRED")
    if detected.kind in {"RATE_LIMITED", "ACTION_BLOCKED"}:
        return FlowResult("retry_pending", detected.kind, detected.kind)
    if detected.kind == "ACCOUNT_DISABLED":
        return FlowResult("error", detected.kind, "ACCOUNT_DISABLED")
    if detected.kind == "NETWORK_ERROR":
        return FlowResult("retry_pending", detected.kind, "NETWORK_ERROR")
    if detected.kind not in {"IG_USERNAME_VALID", "IG_USERNAME_UNAVAILABLE"}:
        return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
    return None
```

- [ ] **Step 6: Implement the bounded username routine**

Add a helper shaped as:

```python
def _handle_username(self, detected, profile: dict, account_id: str | None) -> FlowResult:
    requested = str(profile.get("username") or "").strip()
    if not requested:
        return FlowResult("needs_confirmation", detected.kind, "MISSING_USERNAME")
    if self.driver.find(USERNAME_ENTRY_INPUT) is None:
        return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")

    current = self.driver.find(USERNAME_ENTRY_INPUT)
    current_text = str(getattr(current, "text", "") or "")

    if detected.kind != "IG_USERNAME_UNAVAILABLE":
        if current_text != requested:
            action = self._attempt(lambda: self.driver.set_text(USERNAME_ENTRY_INPUT, requested))
            if action.status not in {"completed", "noop"}:
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
        validation = self.driver.wait_for(_USERNAME_VALIDATION_STATES, 8.0)
        terminal = self._username_terminal_result(validation)
        if terminal is not None:
            return terminal
        if validation.kind == "IG_USERNAME_VALID":
            return self._accept_username(requested, requested, detected.kind)

    stable_id = str(account_id or "").strip()
    if not stable_id:
        return FlowResult("needs_confirmation", detected.kind, "MISSING_ACCOUNT_ID")

    for candidate in username_fallback_candidates(requested, stable_id, max_candidates=5):
        action = self._attempt(lambda candidate=candidate: self.driver.set_text(USERNAME_ENTRY_INPUT, candidate))
        if action.status not in {"completed", "noop"}:
            return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
        validation = self.driver.wait_for(_USERNAME_VALIDATION_STATES, 8.0)
        terminal = self._username_terminal_result(validation)
        if terminal is not None:
            return terminal
        if validation.kind == "IG_USERNAME_UNAVAILABLE":
            continue
        if validation.kind == "IG_USERNAME_VALID":
            return self._accept_username(candidate, requested, detected.kind)

    return FlowResult("needs_confirmation", "IG_USERNAME_UNAVAILABLE", "USERNAME_UNAVAILABLE")
```

Implement `_accept_username(selected, requested, original_screen)` so it first requires `CONTINUE`, taps it with `_AFTER_USERNAME`, and returns `profile_updates={"username": selected}` only if `selected != requested` **and** the tap completes. If Next/postcondition fails, return `needs_confirmation` with no profile updates.

- [ ] **Step 7: Route all three username states through the routine**

Change:

```python
def run(self, profile: dict, *, account_id: str | None = None) -> FlowResult:
    return self._handle_detected(
        self._detect_bounded(),
        dict(profile or {}),
        account_id=account_id,
    )
```

Propagate `account_id` through recursive `_handle_detected` calls (`NETWORK_ERROR`, `APP_CRASH`). Route:

```python
if detected.kind in {"IG_USERNAME_ENTRY", "IG_USERNAME_VALID", "IG_USERNAME_UNAVAILABLE"}:
    return self._handle_username(detected, profile, account_id)
```

Remove the old one-shot `IG_USERNAME_ENTRY` branch so username behavior has a single owner.

- [ ] **Step 8: Run GREEN and commit**

Run:

```bash
python3 -m unittest \
  tests.test_factory_v2_instagram_flow \
  tests.test_factory_v2_ui_detector \
  tests.test_factory_v2_instagram_existing_session \
  -v
```

Expected: all focused Instagram tests pass.

Commit:

```bash
git add core/factory_v2/ui_automation/instagram/flow.py \
        tests/test_factory_v2_instagram_flow.py
git commit -m "feat: retry unavailable Instagram usernames"
```

---

### Task 5: Persist the accepted username through the authoritative Controller

**Files:**
- Modify: `core/factory_v2/service.py`
- Modify: `core/factory_v2/runtime.py`
- Test: `tests/test_factory_v2_runtime_remote.py`
- Test: `tests/test_factory_v2_service.py` if present; otherwise add service-specific tests to `tests/test_factory_v2_create_account.py` using a real in-memory repository fixture already used there.

**Interfaces:**
- Produces: `FactoryService.update_worker_selected_username(account_id: str, *, job_id: str, worker_id: str, username: str) -> dict`
- Runtime accepts only `result.profile_updates == {"username": <safe value>}` for Instagram running responses.
- Persistence must occur before `_set_remote_running(..., "AUTOMATE_INSTAGRAM")` acknowledges progress.

- [ ] **Step 1: Write failing service validation/persistence tests**

Test with a real factory account/worker/job binding that:

```python
updated = service.update_worker_selected_username(
    account_id,
    job_id=job_id,
    worker_id=worker_id,
    username="baongocd483102",
)
self.assertEqual("baongocd483102", updated["username"])
```

Add negative cases:

```python
with self.assertRaisesRegex(ValueError, "binding"):
    service.update_worker_selected_username(
        account_id,
        job_id="wrong-job",
        worker_id=worker_id,
        username="baongocd483102",
    )

with self.assertRaisesRegex(ValueError, "username"):
    service.update_worker_selected_username(
        account_id,
        job_id=job_id,
        worker_id=worker_id,
        username="INVALID USERNAME",
    )
```

Create another account in the same batch with the candidate username and assert SQLite uniqueness failure is not swallowed.

- [ ] **Step 2: Run service RED**

Run the exact service/create-account module containing the new tests, for example:

```bash
python3 -m unittest tests.test_factory_v2_create_account -v
```

Expected: missing `update_worker_selected_username`.

- [ ] **Step 3: Implement authoritative service method**

Add `import re` and:

```python
_WORKER_USERNAME_RE = re.compile(r"^[a-z0-9._]{1,30}$")
```

Implement:

```python
def update_worker_selected_username(
    self,
    account_id: str,
    *,
    job_id: str,
    worker_id: str,
    username: str,
) -> dict:
    account = self.repo.get_account(account_id)
    if account is None:
        raise KeyError(account_id)
    if account.get("current_job_id") != job_id or account.get("assigned_worker_id") != worker_id:
        raise ValueError("worker profile update binding mismatch")
    value = str(username or "").strip()
    if _WORKER_USERNAME_RE.fullmatch(value) is None:
        raise ValueError("invalid worker-selected username")
    self.repo.conn.execute(
        "UPDATE factory_account SET username=?, updated_at=? WHERE id=?",
        (value, now(), account_id),
    )
    return self.repo.get_account(account_id)
```

Do not catch `sqlite3.IntegrityError` here; the Controller must fail closed on a local uniqueness conflict.

- [ ] **Step 4: Add failing runtime ordering/synchronization tests**

Update the remote runtime fakes so account rows have:

```python
"assigned_worker_id": "worker-1",
"current_job_id": "job-1",
```

and `FakeService` records username updates:

```python
def update_worker_selected_username(self, account_id, *, job_id, worker_id, username):
    self.username_updates.append((account_id, job_id, worker_id, username))
    self.repo.account["username"] = username
    return self.repo.account
```

Add:

```python
def test_running_instagram_result_persists_username_before_next_action(self):
    acc = account()
    repo = FakeRepo(acc, completion_mode="SOCIAL_ONLY")
    service = FakeService(repo)
    runtime = TestRuntime(
        repo,
        service,
        FakeGateway([{
            "ok": True,
            "status": "running",
            "result": {
                "screen": "IG_USERNAME_VALID",
                "profile_updates": {"username": "baongocd483102"},
            },
        }]),
    )
    runtime._drive_job(job("AUTOMATE_INSTAGRAM"))
    self.assertEqual("baongocd483102", acc["username"])
    self.assertEqual("AUTOMATE_INSTAGRAM", runtime.running_actions[-1])
```

Add tests that no update leaves username unchanged, Threads profile updates are rejected/fail closed, and malformed/unknown update keys do not advance the job.

- [ ] **Step 5: Run runtime RED**

Run:

```bash
python3 -m unittest tests.test_factory_v2_runtime_remote -v
```

Expected: profile updates are currently ignored.

- [ ] **Step 6: Validate and persist response updates before scheduling next action**

In `FactoryControllerRuntime`, add a narrow helper:

```python
def _apply_profile_updates(self, job, account, *, flow: str, detail: dict) -> bool:
    updates = detail.get("profile_updates")
    if updates in {None, {}}:
        return True
    if flow != "instagram" or not isinstance(updates, dict) or set(updates) != {"username"}:
        return False
    self.service.update_worker_selected_username(
        account["id"],
        job_id=job["id"],
        worker_id=job["worker_id"],
        username=updates["username"],
    )
    return True
```

Because dictionaries are unhashable, implement the empty check as `if updates is None or updates == {}:` rather than literally using a set expression.

In `_handle_remote_result`, for `status == "running"`, call this helper **before** `_set_remote_running`. Catch `ValueError`, `KeyError`, and `sqlite3.IntegrityError`. On failure, call `_ensure_remote_checkpoint(... confirmation=True)` with a clear synchronization message and do not schedule another UI action.

Extend `_ensure_remote_checkpoint` additively with optional parameters:

```python
error_code: str = "UI_CHANGED",
error_message: str | None = None,
```

Use `error_message or f"Unrecognized {flow} UI: {screen}"` in the `NEEDS_CONFIRMATION` transition. Existing callers keep their current behavior; username-sync failure passes a message such as `"Instagram username synchronization failed; verify the account username before continuing."`.

- [ ] **Step 7: Run GREEN and commit**

Run:

```bash
python3 -m unittest \
  tests.test_factory_v2_runtime_remote \
  tests.test_factory_v2_create_account \
  -v
```

Expected: all new persistence/binding tests and existing SOCIAL_ONLY/ACP_ACTIVE tests pass.

Commit:

```bash
git add core/factory_v2/service.py \
        core/factory_v2/runtime.py \
        tests/test_factory_v2_runtime_remote.py \
        tests/test_factory_v2_create_account.py
git commit -m "feat: persist worker-selected Instagram username"
```

---

### Task 6: Regression verification and real AVD continuation

**Files:**
- No production changes unless a new independently reproduced bug is found.
- Test only if a real-pilot failure first gets a RED regression test.

**Interfaces:**
- Verifies the complete path from current `IG_USERNAME_UNAVAILABLE` UI to a persisted accepted username and the next signup screen.

- [ ] **Step 1: Run the focused Python regression suite**

Run:

```bash
python3 -m unittest \
  tests.test_factory_v2_identity \
  tests.test_factory_v2_ui_detector \
  tests.test_factory_v2_instagram_flow \
  tests.test_factory_v2_instagram_existing_session \
  tests.test_factory_v2_avd_worker_agent \
  tests.test_factory_v2_runtime_remote \
  tests.test_factory_v2_worker_process \
  tests.test_factory_v2_runner_gateway \
  tests.test_factory_v2_scheduler \
  -v
```

Expected: `OK` with zero failures/errors.

- [ ] **Step 2: Run repository Account Factory verification**

Run the existing verification script:

```bash
scripts/verify_account_factory_dual_runner.sh
```

Expected: Python verification succeeds and Android Gradle unit/build steps succeed. Do not claim full verification if the script cannot complete because of an external environment problem; report the exact blocker.

- [ ] **Step 3: Pull fresh code into the Ubuntu worktree and keep the existing AVD alive**

Use:

```bash
cd ~/Downloads/ACP/worktrees/account-factory-android
git fetch origin
git merge --ff-only origin/feat/account-factory-android
```

Do not merge `main`. Do not kill `emulator-5554` while it is still on the reproduced username-unavailable screen.

- [ ] **Step 4: Reset only the stale confirmation checkpoint and restart Controller/worker**

Stop `account_factory_server.py` and `workers/account_factory_worker.py`, not the emulator. Release the currently stale `WAITING_HUMAN/NEEDS_CONFIRMATION` job using the same guarded `Scheduler.release_job(..., "FAILED")` + `service.retry_account(...)` repair pattern already used in the pilot, only when the current account belongs to the latest `SOCIAL_ONLY` batch and matches the expected stale checkpoint signature.

Restart with:

```bash
export ACP_FACTORY_CONTROLLER=1
export ACP_FACTORY_TICK_SECONDS=2
export ACP_HOST=127.0.0.1
export ACP_PORT=5001

ACP_ADAPTER=mock ACP_SOURCE=mock \
python3 -u account_factory_server.py \
  > /tmp/acp-pilot-controller.log 2>&1 &
```

- [ ] **Step 5: Verify the live acceptance path without concurrent manual uiautomator calls**

Observe the AVD. Expected visible progression:

```text
The username <requested> is not available
→ automation enters a deterministic fallback
→ Instagram reports the candidate valid
→ automation taps Next
→ next known signup/protected screen appears
```

Do not run manual `uiautomator dump` while the worker is actively driving the screen.

- [ ] **Step 6: Verify the database username equals the accepted Instagram username**

After the UI advances, query:

```sql
SELECT id, username, stage, last_safe_stage, last_error_code
FROM factory_account
WHERE id='<current SOCIAL_ONLY account id>';
```

Expected: `username` equals the candidate accepted in Instagram, not the original unavailable username.

- [ ] **Step 7: Verify safety stops**

If the next screen is password, OTP, CAPTCHA, identity/security, email/phone verification, or final official Create Account, expected runtime state is a human checkpoint (`WAITING_HUMAN` / `OBSERVE_CHECKPOINT`) with the AVD left open. No protected field or final-submit mutation is permitted.

- [ ] **Step 8: Final verification commit only if verification changes were needed**

If no code/test change was required during the pilot, do not create an empty commit. If a new bug was reproduced, return to systematic-debugging + RED/GREEN TDD before any fix, then commit that independently.

---

## Plan self-review

- **Spec coverage:** explicit valid/unavailable detection, bounded deterministic candidates, restart-stable account-id seed, no coordinate suggestion clicks, `FlowResult.profile_updates`, worker sanitation, Controller double-validation, current lease binding, DB persistence, uniqueness failure, error stop semantics, and real AVD acceptance are all assigned to Tasks 1–6.
- **Placeholder scan:** no `TBD`, `TODO`, “implement later”, or unspecified “add tests” steps remain.
- **Type consistency:** `username_fallback_candidates(...) -> tuple[str, ...]`, `FlowResult.profile_updates: dict[str, str] | None`, `InstagramFlow.run(..., account_id=...)`, worker response `result.profile_updates`, and `FactoryService.update_worker_selected_username(...)` use the same names across producer/consumer tasks.
- **Scope check:** no new schema/persistent candidate-attempt subsystem is planned. If implementation proves one is necessary, stop and revise the approved design instead of expanding silently.
