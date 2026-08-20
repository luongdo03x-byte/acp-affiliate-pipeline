# Account Credential + Browser Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist an encrypted reusable password per factory account, isolate OAuth browser state when one AVD processes multiple accounts, and automatically fill recognized Threads/Meta login forms without persisting plaintext secrets or automating OTP/CAPTCHA/security/legal consent.

**Architecture:** Account credentials live in a dedicated controller-only table encrypted with `core.crypto` and keyed by `account_id`. OAuth browser isolation is enforced in the AVD worker by binding Chrome data to the current account and clearing Chrome when the account changes. Login credentials cross only the in-memory stdio channel through a dedicated remote-AVD transient method; browser login automation uses a narrow secret-aware driver instead of weakening `SafeUiDriver`'s protected-field policy.

**Tech Stack:** Python 3.12, SQLite, AES-GCM via `cryptography`, Android ADB, unittest, existing Account Factory V2 controller/worker IPC.

**Spec:** `docs/superpowers/specs/2026-08-20-account-credential-browser-session-design.md`

## Global Constraints

- Read the default account password from `ACP_DEFAULT_ACCOUNT_PASSWORD`; never commit a concrete password value to Git.
- Store only AES-GCM ciphertext in SQLite using `ACP_MASTER_KEY` through `core.crypto`.
- Never place plaintext passwords in `factory_account`, API responses, logs, heartbeat payloads, `factory_runner_command.payload_json`, OAuth callback URLs, or persisted recovery state.
- Password automation is allowed only for a recognized login form on a REMOTE_AVD worker and only through the transient command path.
- OTP, CAPTCHA, identity/security challenge, suspicious-login approval, Threads legal terms, and OAuth permission consent remain human-only.
- Existing accounts without a credential row must continue to load and operate for already-active channels.
- Existing `/me.username == expected_username` OAuth callback verification remains authoritative and unchanged.
- Follow RED -> verify failure -> minimal GREEN -> verify focused + regression tests -> commit for every task.

---

### Task 1: Encrypted account credential storage

**Files:**
- Modify: `core/factory_v2/schema.py`
- Create: `core/factory_v2/account_credentials.py`
- Create: `tests/test_factory_v2_account_credentials.py`

**Interfaces:**
- Consumes: `core.crypto.encrypt(plaintext: str) -> bytes`, `core.crypto.decrypt(blob: bytes) -> str`, `core.db.now() -> str`.
- Produces:
  - `store_account_password(conn, account_id: str, password: str) -> None`
  - `get_account_password(conn, account_id: str) -> str | None`
  - `has_account_password(conn, account_id: str) -> bool`

- [ ] **Step 1: Write the failing credential-storage tests**

```python
import os
import sqlite3
import unittest
from unittest.mock import patch

from core.factory_v2.account_credentials import (
    get_account_password,
    has_account_password,
    store_account_password,
)
from core.factory_v2.schema import ensure_schema


class AccountCredentialTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        with patch.dict(os.environ, {"ACP_MASTER_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}):
            ensure_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def _insert_account(self):
        self.conn.execute(
            "INSERT INTO factory_batch(id,name,target_count,status,created_at,completion_mode) VALUES('b1','b',1,'READY','2026-08-20T00:00:00+00:00','ACP_ACTIVE')"
        )
        self.conn.execute(
            """INSERT INTO factory_account(
                id,batch_id,sequence,group_no,username,display_name,stage,last_safe_stage,created_at,updated_at
            ) VALUES('a1','b1',1,1,'user1','User 1','PROFILE_READY','PROFILE_READY','2026-08-20T00:00:00+00:00','2026-08-20T00:00:00+00:00')"""
        )

    def test_password_round_trips_but_plaintext_is_not_stored(self):
        self._insert_account()
        with patch.dict(os.environ, {"ACP_MASTER_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}):
            store_account_password(self.conn, "a1", "example-secret")
            self.assertEqual("example-secret", get_account_password(self.conn, "a1"))
        row = self.conn.execute(
            "SELECT password_encrypted FROM factory_account_credential WHERE account_id='a1'"
        ).fetchone()
        self.assertIsInstance(row["password_encrypted"], bytes)
        self.assertNotIn(b"example-secret", row["password_encrypted"])

    def test_missing_credential_returns_none(self):
        self._insert_account()
        self.assertIsNone(get_account_password(self.conn, "a1"))
        self.assertFalse(has_account_password(self.conn, "a1"))

    def test_empty_password_is_rejected(self):
        self._insert_account()
        with self.assertRaisesRegex(ValueError, "password"):
            store_account_password(self.conn, "a1", "")
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python -m unittest tests.test_factory_v2_account_credentials -v
```

Expected: import/schema failures because `account_credentials.py` and `factory_account_credential` do not exist yet.

- [ ] **Step 3: Add the schema table and minimal credential module**

Add to `SCHEMA` in `core/factory_v2/schema.py`:

```sql
CREATE TABLE IF NOT EXISTS factory_account_credential (
    account_id TEXT PRIMARY KEY REFERENCES factory_account(id) ON DELETE CASCADE,
    password_encrypted BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Create `core/factory_v2/account_credentials.py`:

```python
from core.crypto import decrypt, encrypt
from core.db import now


def store_account_password(conn, account_id: str, password: str) -> None:
    account_id = str(account_id or "").strip()
    password = str(password or "")
    if not account_id:
        raise ValueError("account_id is required")
    if not password:
        raise ValueError("password is required")
    timestamp = now()
    encrypted = encrypt(password)
    conn.execute(
        """INSERT INTO factory_account_credential(account_id,password_encrypted,created_at,updated_at)
           VALUES(?,?,?,?)
           ON CONFLICT(account_id) DO UPDATE SET
             password_encrypted=excluded.password_encrypted,
             updated_at=excluded.updated_at""",
        (account_id, encrypted, timestamp, timestamp),
    )


def get_account_password(conn, account_id: str) -> str | None:
    row = conn.execute(
        "SELECT password_encrypted FROM factory_account_credential WHERE account_id=?",
        (str(account_id or "").strip(),),
    ).fetchone()
    if row is None:
        return None
    return decrypt(row["password_encrypted"])


def has_account_password(conn, account_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM factory_account_credential WHERE account_id=?",
        (str(account_id or "").strip(),),
    ).fetchone()
    return row is not None
```

- [ ] **Step 4: Run focused tests and schema regressions**

```bash
python -m unittest \
  tests.test_factory_v2_account_credentials \
  tests.test_factory_v2_schema -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/factory_v2/schema.py core/factory_v2/account_credentials.py tests/test_factory_v2_account_credentials.py
git commit -m "feat: store encrypted factory account credentials"
```

---

### Task 2: Attach the default encrypted password during account creation

**Files:**
- Modify: `core/factory_v2/service.py`
- Create: `tests/test_factory_v2_default_account_password.py`

**Interfaces:**
- Consumes: `store_account_password(conn, account_id, password)` from Task 1.
- Produces: new accounts created by `FactoryService.create_batch()` / `create_single_account()` have a credential row when `ACP_DEFAULT_ACCOUNT_PASSWORD` is configured; account dictionaries remain password-free.

- [ ] **Step 1: Write failing creation tests**

```python
import os
import sqlite3
import unittest
from unittest.mock import patch

from core.factory_v2.account_credentials import get_account_password
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService


class DefaultAccountPasswordTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_create_batch_encrypts_default_password_without_exposing_it(self):
        with patch.dict(os.environ, {
            "ACP_ENV": "development",
            "ACP_DEFAULT_ACCOUNT_PASSWORD": "example-secret",
        }, clear=False):
            service = FactoryService(self.repo)
            batch = service.create_batch("credential batch", count=1, seed=1)
        account = self.repo.list_accounts(batch["id"])[0]
        self.assertNotIn("password", account)
        self.assertNotIn("password_encrypted", account)
        self.assertEqual("example-secret", get_account_password(self.conn, account["id"]))

    def test_production_creation_fails_before_commit_when_default_password_missing(self):
        with patch.dict(os.environ, {"ACP_ENV": "production"}, clear=False):
            os.environ.pop("ACP_DEFAULT_ACCOUNT_PASSWORD", None)
            service = FactoryService(self.repo)
            with self.assertRaisesRegex(RuntimeError, "ACP_DEFAULT_ACCOUNT_PASSWORD"):
                service.create_batch("credential batch", count=1, seed=1)
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM factory_batch").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM factory_account").fetchone()[0])
```

- [ ] **Step 2: Run and verify RED**

```bash
python -m unittest tests.test_factory_v2_default_account_password -v
```

Expected: created account has no credential and production missing-password case does not fail early.

- [ ] **Step 3: Add one private config helper and store credentials in the same transaction**

In `service.py` add:

```python
import os
from .account_credentials import store_account_password

_DEFAULT_PASSWORD_ENV = "ACP_DEFAULT_ACCOUNT_PASSWORD"


def _configured_default_account_password() -> str | None:
    value = os.environ.get(_DEFAULT_PASSWORD_ENV)
    if value is None or not value:
        if os.environ.get("ACP_ENV") == "production":
            raise RuntimeError(f"{_DEFAULT_PASSWORD_ENV} is required in production")
        return None
    return value
```

At the start of `create_batch()`, resolve the default once before any DB writes. Inside its existing transaction, after `insert_accounts(account_rows)`, call `store_account_password(...)` for each inserted account when a default exists. Do not add password fields to `account_rows`.

- [ ] **Step 4: Run focused service regressions**

```bash
python -m unittest \
  tests.test_factory_v2_default_account_password \
  tests.test_factory_v2_service \
  tests.test_factory_v2_create_account -v
```

Expected: PASS and returned account dictionaries contain no password material.

- [ ] **Step 5: Commit**

```bash
git add core/factory_v2/service.py tests/test_factory_v2_default_account_password.py
git commit -m "feat: assign encrypted default password to new factory accounts"
```

---

### Task 3: Finish account-bound OAuth browser isolation

**Files:**
- Modify: `core/factory_v2/avd.py`
- Modify: `workers/account_factory_worker.py`
- Test: `tests/test_factory_v2_oauth_browser_session.py` (already RED on branch)
- Test: `tests/test_factory_v2_avd.py`

**Interfaces:**
- Produces:
  - `AvdManager.reset_browser_session(serial: str, browser_package: str) -> None`
  - `AvdManager.open_url(serial: str, url: str, *, browser_package: str | None = None) -> None`
  - Worker in-memory field `oauth_browser_account_id: str | None`

- [ ] **Step 1: Run the existing RED browser-isolation regression**

```bash
python -m unittest tests.test_factory_v2_oauth_browser_session -v
```

Expected: FAIL because `reset_browser_session()` and account-bound `OPEN_URL` behavior are not implemented.

- [ ] **Step 2: Implement strict browser package validation and reset**

In `avd.py` add:

```python
_BROWSER_PACKAGE_RE = re.compile(r"^[A-Za-z0-9_.]+$")


def _validate_browser_package(value: str) -> str:
    package = str(value or "").strip()
    if not _BROWSER_PACKAGE_RE.fullmatch(package):
        raise ValueError("invalid browser package")
    return package
```

Add:

```python
def reset_browser_session(self, serial: str, browser_package: str) -> None:
    package = _validate_browser_package(browser_package)
    self._checked(
        [self.adb, "-s", serial, "shell", "pm", "clear", package],
        timeout=20,
    )
```

Extend `open_url()` so `browser_package` appends `-p <package>` to the `am start` command only after validation.

- [ ] **Step 3: Bind the worker browser session to account ID**

In `WorkerAgent.__init__` add:

```python
self.oauth_browser_account_id = None
self.oauth_browser_package = "com.android.chrome"
```

Replace the `OPEN_URL` branch with:

```python
if action == "OPEN_URL":
    account_id = str(command.account_id or "").strip()
    if not account_id:
        raise ValueError("account binding is required for OAuth URL")
    if self.oauth_browser_account_id != account_id:
        self.avd.reset_browser_session(self.serial, self.oauth_browser_package)
        self.oauth_browser_account_id = account_id
    self.avd.open_url(
        self.serial,
        str(command.payload["url"]),
        browser_package=self.oauth_browser_package,
    )
    self.last_progress_at = _now()
    return {"ok": True}
```

The binding must only be assigned after `reset_browser_session()` succeeds.

- [ ] **Step 4: Run focused browser tests**

```bash
python -m unittest \
  tests.test_factory_v2_oauth_browser_session \
  tests.test_factory_v2_avd \
  tests.test_factory_v2_avd_worker_agent -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/factory_v2/avd.py workers/account_factory_worker.py tests/test_factory_v2_oauth_browser_session.py tests/test_factory_v2_avd.py
git commit -m "feat: isolate OAuth browser sessions by factory account"
```

---

### Task 4: Add a non-persisted transient secret handoff

**Files:**
- Modify: `core/factory_v2/worker_process.py`
- Modify: `core/factory_v2/runner_gateway.py`
- Modify: `workers/account_factory_worker.py`
- Create: `tests/test_factory_v2_transient_login_secret.py`

**Interfaces:**
- Produces:
  - `RunnerGateway.send_transient_login_secret(job: dict, *, username: str, password: str) -> dict`
  - worker action `TRANSIENT_BROWSER_LOGIN` accepted only over REMOTE_AVD stdio.

- [ ] **Step 1: Write failing tests proving no SQLite persistence**

```python
import sqlite3
import unittest
from types import SimpleNamespace

from core.factory_v2.repository import FactoryRepository
from core.factory_v2.runner_gateway import RunnerGateway
from core.factory_v2.schema import ensure_schema


class FakeProcesses:
    def __init__(self):
        self.commands = []
    def request(self, worker_id, command):
        self.commands.append((worker_id, command))
        return {"ok": True, "status": "waiting_human", "result": {"screen": "OAUTH_CONSENT"}}


class TransientLoginSecretTests(unittest.TestCase):
    def test_remote_secret_bypasses_persisted_runner_commands(self):
        conn = sqlite3.connect(":memory:", isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(conn)
        repo = FactoryRepository(conn)
        processes = FakeProcesses()
        gateway = RunnerGateway(repo, processes)
        job = {"id": "j1", "account_id": "a1", "worker_id": "w1", "runner_type": "REMOTE_AVD"}
        response = gateway.send_transient_login_secret(
            job, username="user1", password="example-secret"
        )
        self.assertEqual("waiting_human", response["status"])
        self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM factory_runner_command").fetchone()[0])
        command = processes.commands[0][1]
        self.assertEqual("TRANSIENT_BROWSER_LOGIN", command.action)
        self.assertEqual("user1", command.payload["username"])
        self.assertEqual("example-secret", command.payload["password"])
        self.assertNotIn("password", repr(response))
        conn.close()
```

Also add a local-runner test asserting the method raises `ValueError("transient login secret is REMOTE_AVD only")`.

- [ ] **Step 2: Run and verify RED**

```bash
python -m unittest tests.test_factory_v2_transient_login_secret -v
```

Expected: FAIL because `send_transient_login_secret()` does not exist.

- [ ] **Step 3: Add the dedicated remote-only gateway method**

In `runner_gateway.py`:

```python
def send_transient_login_secret(self, job: dict, *, username: str, password: str) -> dict:
    if self._runner_type(job) != RunnerType.REMOTE_AVD.value:
        raise ValueError("transient login secret is REMOTE_AVD only")
    username = str(username or "").strip()
    password = str(password or "")
    if not username or not password:
        raise ValueError("login credential is incomplete")
    return self.worker_processes.request(
        job["worker_id"],
        WorkerCommand(
            command_id=ulid(),
            action="TRANSIENT_BROWSER_LOGIN",
            account_id=job["account_id"],
            payload={"job_id": job["id"], "username": username, "password": password},
        ),
    )
```

Do not route this method through `send()` and do not call `create_runner_command()`.

Add `TRANSIENT_BROWSER_LOGIN` to `_UI_ACTIONS` in `worker_process.py` so it receives the existing 60-second UI timeout.

- [ ] **Step 4: Add a temporary worker stub that sanitizes output**

Until Task 5 supplies the browser flow, add a `TRANSIENT_BROWSER_LOGIN` branch in `WorkerAgent.execute()` that validates account binding and delegates to `self.browser_login_flow.run(username, password)` when injected; no returned structure may include the input strings.

- [ ] **Step 5: Run transient + gateway regressions**

```bash
python -m unittest \
  tests.test_factory_v2_transient_login_secret \
  tests.test_factory_v2_runner_gateway \
  tests.test_factory_v2_worker_process \
  tests.test_factory_v2_avd_worker_agent -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add core/factory_v2/worker_process.py core/factory_v2/runner_gateway.py workers/account_factory_worker.py tests/test_factory_v2_transient_login_secret.py
git commit -m "feat: add transient remote login secret handoff"
```

---

### Task 5: Build a narrow browser login detector and secret-aware driver

**Files:**
- Create: `core/factory_v2/ui_automation/browser/__init__.py`
- Create: `core/factory_v2/ui_automation/browser/screens.py`
- Create: `core/factory_v2/ui_automation/browser/secret_driver.py`
- Create: `core/factory_v2/ui_automation/browser/flow.py`
- Create: `tests/fixtures/browser_login_form.xml`
- Create: `tests/fixtures/browser_oauth_consent.xml`
- Create: `tests/fixtures/browser_security_challenge.xml`
- Create: `tests/test_factory_v2_browser_login_flow.py`

**Interfaces:**
- Produces `BrowserLoginFlow.run(username: str, password: str) -> FlowResult`.
- The flow returns only these safe screen/status combinations:
  - `running / LOGIN_SUCCEEDED`
  - `completed / LOGIN_NOT_REQUIRED`
  - `waiting_human / OAUTH_CONSENT`
  - `waiting_human / SECURITY_CHALLENGE`
  - `needs_confirmation / UNKNOWN`

- [ ] **Step 1: Create deterministic XML fixtures and RED tests**

Use synthetic hierarchy fixtures with only non-secret UI text. The login fixture contains two distinct `android.widget.EditText` nodes: one with content description `Username, phone or email`, one with content description `Password`, plus a clickable `Log in` button. The consent fixture contains `Allow` / `Continue` text but no login fields. The challenge fixture contains text `Confirm it's you`.

Test behavior:

```python
def test_recognized_login_form_fills_username_and_password_then_submits():
    result = flow.run("user1", "example-secret")
    self.assertEqual("running", result.status)
    self.assertEqual("LOGIN_SUCCEEDED", result.screen)
    self.assertEqual(["username", "password", "login"], fake_driver.actions)
    self.assertNotIn("example-secret", repr(result))


def test_oauth_consent_is_human_only():
    result = flow.run("user1", "example-secret")
    self.assertEqual("waiting_human", result.status)
    self.assertEqual("OAUTH_CONSENT", result.screen)
    self.assertEqual([], fake_driver.actions)


def test_security_challenge_is_human_only():
    result = flow.run("user1", "example-secret")
    self.assertEqual("waiting_human", result.status)
    self.assertEqual("SECURITY_CHALLENGE", result.screen)
    self.assertEqual([], fake_driver.actions)


def test_unknown_screen_never_types_secret():
    result = flow.run("user1", "example-secret")
    self.assertEqual("needs_confirmation", result.status)
    self.assertEqual([], fake_driver.actions)
```

- [ ] **Step 2: Run and verify RED**

```bash
python -m unittest tests.test_factory_v2_browser_login_flow -v
```

Expected: FAIL because the browser package does not exist.

- [ ] **Step 3: Implement browser screen detection without weakening SafeUiDriver**

`browser/screens.py` should classify only known states based on normalized labels/content descriptions:

```python
BROWSER_LOGIN = "BROWSER_LOGIN"
OAUTH_CONSENT = "OAUTH_CONSENT"
SECURITY_CHALLENGE = "SECURITY_CHALLENGE"
UNKNOWN = "UNKNOWN"
```

Recognize `BROWSER_LOGIN` only when both username and password field signatures plus the login button are present. Recognize challenge before login to avoid typing into ambiguous screens. Recognize OAuth consent without clicking it.

- [ ] **Step 4: Implement a dedicated `BrowserSecretDriver`**

Do not edit `_PROTECTED_TEXT_SEMANTICS` in `SafeUiDriver`. Create a separate driver with exactly these methods:

```python
class BrowserSecretDriver:
    def __init__(self, adb, detector, *, hierarchy_reader=None): ...
    def detect_screen(self) -> DetectedScreen: ...
    def set_username(self, value: str) -> ActionResult: ...
    def set_password(self, value: str) -> ActionResult: ...
    def tap_login(self) -> ActionResult: ...
```

`set_password()` may type only when `detect_screen().kind == "BROWSER_LOGIN"` and the password selector is uniquely found. It must not store the password on `self` and must not include it in an exception or return value.

- [ ] **Step 5: Implement `BrowserLoginFlow.run()` fail-closed**

Pseudo-code to implement exactly:

```python
screen = driver.detect_screen()
if screen.kind == "OAUTH_CONSENT":
    return FlowResult("waiting_human", "OAUTH_CONSENT", "HUMAN_CONSENT_REQUIRED")
if screen.kind == "SECURITY_CHALLENGE":
    return FlowResult("waiting_human", "SECURITY_CHALLENGE", "HUMAN_VERIFICATION_REQUIRED")
if screen.kind != "BROWSER_LOGIN":
    return FlowResult("needs_confirmation", screen.kind or "UNKNOWN", "UI_CHANGED")

if driver.set_username(username).status not in {"completed", "noop"}:
    return FlowResult("needs_confirmation", "BROWSER_LOGIN", "USERNAME_FIELD_UNVERIFIED")
if driver.set_password(password).status not in {"completed", "noop"}:
    return FlowResult("needs_confirmation", "BROWSER_LOGIN", "PASSWORD_FIELD_UNVERIFIED")
if driver.tap_login().status != "completed":
    return FlowResult("needs_confirmation", "BROWSER_LOGIN", "LOGIN_SUBMIT_UNVERIFIED")

after = driver.detect_screen()
if after.kind == "OAUTH_CONSENT":
    return FlowResult("running", "LOGIN_SUCCEEDED", "OAUTH_CONSENT_REACHED")
if after.kind == "SECURITY_CHALLENGE":
    return FlowResult("waiting_human", "SECURITY_CHALLENGE", "HUMAN_VERIFICATION_REQUIRED")
return FlowResult("needs_confirmation", after.kind or "UNKNOWN", "LOGIN_POSTCHECK_FAILED")
```

- [ ] **Step 6: Run browser login tests and UI-driver regressions**

```bash
python -m unittest \
  tests.test_factory_v2_browser_login_flow \
  tests.test_factory_v2_ui_hierarchy \
  tests.test_factory_v2_ui_detector \
  tests.test_factory_v2_ui_driver -v
```

Expected: PASS and `SafeUiDriver` still rejects protected password semantics.

- [ ] **Step 7: Commit**

```bash
git add core/factory_v2/ui_automation/browser tests/fixtures/browser_*.xml tests/test_factory_v2_browser_login_flow.py
git commit -m "feat: add fail-closed browser login automation"
```

---

### Task 6: Wire the browser login flow into the AVD worker

**Files:**
- Modify: `workers/account_factory_worker.py`
- Modify: `tests/test_factory_v2_avd_worker_agent.py`

**Interfaces:**
- Consumes `BrowserLoginFlow` from Task 5.
- `TRANSIENT_BROWSER_LOGIN` must require `command.account_id == oauth_browser_account_id` before any secret is typed.

- [ ] **Step 1: Add RED worker tests**

Add tests asserting:

```python
def test_transient_browser_login_requires_same_oauth_browser_account():
    agent.oauth_browser_account_id = "acc-a"
    with self.assertRaisesRegex(ValueError, "browser account"):
        agent.execute(WorkerCommand(
            "secret-1", "TRANSIENT_BROWSER_LOGIN", "acc-b",
            {"username": "userb", "password": "example-secret"},
        ))


def test_transient_browser_login_result_never_echoes_secret():
    agent.oauth_browser_account_id = "acc-a"
    response = agent.execute(WorkerCommand(
        "secret-2", "TRANSIENT_BROWSER_LOGIN", "acc-a",
        {"username": "usera", "password": "example-secret"},
    ))
    self.assertNotIn("example-secret", repr(response))
    self.assertNotIn("usera", repr(response.get("result", {})))
```

- [ ] **Step 2: Run and verify RED**

```bash
python -m unittest tests.test_factory_v2_avd_worker_agent.AvdWorkerAgentTests -v
```

Expected: FAIL for missing browser-flow binding/behavior.

- [ ] **Step 3: Construct `BrowserLoginFlow` in `WorkerAgent.__init__`**

Instantiate it with `BrowserSecretDriver(self.adb_client, build_browser_detector())` when no injected `browser_login_flow` is supplied. Keep `browser_login_flow` injectable for tests.

- [ ] **Step 4: Implement the transient action without retaining credentials**

Inside the action handler:

```python
if action == "TRANSIENT_BROWSER_LOGIN":
    account_id = str(command.account_id or "").strip()
    if not account_id or self.oauth_browser_account_id != account_id:
        raise ValueError("browser account binding mismatch")
    username = str(command.payload.get("username") or "").strip()
    password = str(command.payload.get("password") or "")
    if not username or not password:
        raise ValueError("login credential is incomplete")
    result = self.browser_login_flow.run(username, password)
    username = ""
    password = ""
    return self._flow_response("oauth_browser", result)
```

Do not put username/password into `prepared_text`, heartbeat fields, `last_safe_step`, or result metadata.

- [ ] **Step 5: Run worker regressions**

```bash
python -m unittest \
  tests.test_factory_v2_avd_worker_agent \
  tests.test_factory_v2_transient_login_secret -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add workers/account_factory_worker.py tests/test_factory_v2_avd_worker_agent.py
git commit -m "feat: execute transient browser login on bound AVD account"
```

---

### Task 7: Integrate automatic login with OAuth activation runtime

**Files:**
- Modify: `core/factory_v2/runtime.py`
- Modify: `tests/test_factory_v2_runtime_activation.py`
- Modify: `tests/test_factory_v2_runtime_remote.py`

**Interfaces:**
- Consumes `get_account_password()` and `RunnerGateway.send_transient_login_secret()`.
- Keeps existing activation states: `ACP_CONNECTING -> ACP_ACTIVE | RETRY_PENDING | ERROR`.
- Keeps human OAuth consent checkpoint `ACP_OAUTH`.

- [ ] **Step 1: Add RED runtime tests for the new command ordering**

For a REMOTE_AVD account with a stored credential, assert this sequence during `START_ACP`:

```text
OPEN_URL
TRANSIENT_BROWSER_LOGIN
```

Assert the transient call receives the account username and decrypted password, but the persisted `factory_runner_command` table remains empty for the secret action. Assert a worker result of `waiting_human/OAUTH_CONSENT` leaves the job in `WAITING_HUMAN` with `desired_action='WAIT_ACP'`.

Add another test where credential is missing: runtime must not guess a password; it creates/updates the `ACP_OAUTH` checkpoint with an operator-visible message, leaves the account at `ACP_CONNECTING`, and does not call transient login.

- [ ] **Step 2: Run and verify RED**

```bash
python -m unittest tests.test_factory_v2_runtime_activation -v
```

Expected: FAIL because `_start_activation()` currently opens the URL then immediately waits without attempting credential login.

- [ ] **Step 3: Add one helper for remote OAuth browser preparation**

In `runtime.py` import:

```python
from .account_credentials import get_account_password
```

Add:

```python
def _prepare_remote_oauth_login(self, job, account) -> dict | None:
    if not self._is_remote(job):
        return None
    password = get_account_password(self.repo.conn, account["id"])
    if password is None:
        return {"status": "missing_credential"}
    return self.runner_gateway.send_transient_login_secret(
        job,
        username=account["username"],
        password=password,
    )
```

The local variable `password` must not be interpolated into any message or log.

- [ ] **Step 4: Update `_start_activation()` ordering**

After `OPEN_URL` completes for a REMOTE_AVD, call `_prepare_remote_oauth_login()`. Handle results:

```python
if login_result is not None and login_result.get("status") == "missing_credential":
    checkpoint = self._activation_checkpoint(account["id"])
    if checkpoint is not None:
        self.repo.conn.execute(
            "UPDATE factory_checkpoint SET message=? WHERE id=?",
            ("OAuth browser cần đăng nhập nhưng account chưa có credential đã mã hóa.", checkpoint["id"]),
        )
    # continue to WAIT_ACP so operator can log in manually; do not fail/retry automatically
```

For worker statuses `waiting_human`, `running`, or `completed`, transition job/worker to the existing `WAIT_ACP` waiting state. For `needs_confirmation`, keep the OAuth checkpoint open with message `Không xác minh được màn đăng nhập OAuth; kiểm tra thủ công.` and still wait human. Do not change OAuth callback reconciliation.

- [ ] **Step 5: Run activation + mismatch regressions**

```bash
python -m unittest \
  tests.test_factory_v2_runtime_activation \
  tests.test_factory_v2_runtime_remote \
  tests.test_factory_v2_oauth_bridge \
  tests.test_account_factory -v
```

Expected: PASS, including existing account-mismatch tests proving `/me.username` verification remains fail-closed.

- [ ] **Step 6: Commit**

```bash
git add core/factory_v2/runtime.py tests/test_factory_v2_runtime_activation.py tests/test_factory_v2_runtime_remote.py
git commit -m "feat: auto-fill stored credentials before Threads OAuth consent"
```

---

### Task 8: CI coverage, operator configuration, and full verification

**Files:**
- Modify: `.github/workflows/account-factory-ci.yml`
- Modify: `README.md` only if it already contains Account Factory environment configuration; otherwise create `docs/account-factory-runtime.md`.

**Interfaces:**
- CI must execute the new credential/browser tests.
- Operator docs must name `ACP_DEFAULT_ACCOUNT_PASSWORD` but must not contain a concrete password value.

- [ ] **Step 1: Add new test modules to the backend CI command**

Append these modules to the existing unittest list:

```text
tests.test_factory_v2_account_credentials
tests.test_factory_v2_default_account_password
tests.test_factory_v2_oauth_browser_session
tests.test_factory_v2_transient_login_secret
tests.test_factory_v2_browser_login_flow
```

Do not add the real default password to workflow `env:`.

- [ ] **Step 2: Document local secret configuration**

Document:

```bash
export ACP_MASTER_KEY='<base64-32-byte-key>'
export ACP_DEFAULT_ACCOUNT_PASSWORD='<operator-secret>'
```

State explicitly that the password environment variable is read only when creating new accounts and the stored DB value is encrypted.

- [ ] **Step 3: Run the focused feature suite**

```bash
python -m unittest \
  tests.test_factory_v2_account_credentials \
  tests.test_factory_v2_default_account_password \
  tests.test_factory_v2_oauth_browser_session \
  tests.test_factory_v2_transient_login_secret \
  tests.test_factory_v2_browser_login_flow \
  tests.test_factory_v2_avd_worker_agent \
  tests.test_factory_v2_runtime_activation \
  tests.test_factory_v2_oauth_bridge \
  tests.test_account_factory -v
```

Expected: all PASS.

- [ ] **Step 4: Run the same backend suite listed by `.github/workflows/account-factory-ci.yml` locally**

Run the exact multiline `python -m unittest ... -v` command from the workflow with these env values only:

```bash
ACP_ADAPTER=mock
ACP_SOURCE=mock
ACP_ENV=development
ACP_FACTORY_API_KEY=test-key
ACP_PUBLIC_BASE_URL=https://factory.example.com
ACP_DEFAULT_ACCOUNT_PASSWORD=test-only-password
```

Use a temporary valid `ACP_MASTER_KEY` in the shell environment. Expected: all backend tests PASS.

- [ ] **Step 5: Verify no committed secret literal**

Run:

```bash
git grep -n "ACP_DEFAULT_ACCOUNT_PASSWORD" -- ':!docs/superpowers/plans/*' ':!docs/superpowers/specs/*'
```

Review matches and verify they contain only the environment variable name, not a concrete password. Also run:

```bash
git diff --check
```

Expected: no whitespace errors.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/account-factory-ci.yml README.md docs/account-factory-runtime.md 2>/dev/null || true
git commit -m "test: cover encrypted account credentials and OAuth browser login"
```

- [ ] **Step 7: Final live verification on one non-critical pilot account**

With controller callback server running and controller scheduling enabled only for the pilot worker:

```text
create new account
-> credential row exists and DB does not expose plaintext
-> Instagram/Threads setup reaches OAuth
-> Chrome resets for the new account
-> recognized login form is filled automatically
-> OTP/CAPTCHA/security challenge, if any, stops for human
-> OAuth consent remains human click
-> callback verifies expected username
-> account reaches ACP_ACTIVE
-> job releases COMPLETED
-> same AVD can accept the next account and Chrome resets again
```

Do not claim end-to-end completion until this live pilot and the full local regression suite both pass.
