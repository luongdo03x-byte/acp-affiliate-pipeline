# Threads Tester Batch Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a Factory V2 batch pause once for a single manual Meta tester invite covering every pending username, then accept the invitations on-device and finish Threads OAuth to `ACP_ACTIVE` without further human input.

**Architecture:** A gate in `FactoryControllerRuntime._start_activation()` holds each account at `THREADS_CREATED` behind a `TESTER_INVITE` checkpoint and a new `WAIT_TESTER` job action. The operator clears the whole batch from the existing onboarding wizard. Two new fail-closed UI flows then run on the account's own device: one accepts the Threads tester invitation, one taps the OAuth authorization control. Both reuse the existing `ScreenDetector` priority band so protected screens still stop automation.

**Tech Stack:** Python 3, Flask, SQLite, unittest, the project's `core/factory_v2/ui_automation` driver stack.

**Spec:** `docs/superpowers/specs/2026-09-10-threads-tester-batch-automation-design.md`

## Global Constraints

- Run tests with `ACP_ADAPTER=mock PYTHONPATH=. .venv/bin/python3 -m unittest tests.<module>`. There is no pytest in this venv.
- No schema migration. `factory_checkpoint.account_id` is `NOT NULL`; every checkpoint is per-account.
- Never touch `~/Downloads/ACP/shared/.env.local`, `var/acp-live.db`, or any real device during development.
- Threads package constant is `com.instagram.barcelona`, already exported as `PACKAGE` from `core/factory_v2/ui_automation/threads/screens.py`.
- New screen signatures must use `priority` above 60 so every protected signature (priority 10-17) and every error signature (priority 30-34) wins first.
- `SafeUiDriver.set_text()` is not used by any new flow. These flows only tap.
- Commit messages are Vietnamese, imperative, prefixed `feat:` or `fix:`, and end with the two attribution lines used on this branch.

---

### Task 1: Refuse OAuth for accounts with no tester acceptance

**Files:**
- Modify: `core/factory_v2/oauth_bridge.py:33-60`
- Modify: `web/factory_v2.py:291-312`
- Test: `tests/test_factory_v2_oauth_bridge.py`
- Test: `tests/test_factory_v2_api.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `start_account_oauth(conn, account_id, redirect_uri, provider)` now raises `ValueError("account has not accepted the Threads tester invitation")` when `factory_account.tester_accepted_at` is falsy. Task 2 and Task 5 rely on this being the single enforcement point.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_factory_v2_oauth_bridge.py`:

```python
    def test_start_rejects_account_without_tester_acceptance(self):
        account_id = self._seed_account(stage="THREADS_CREATED")
        self.conn.execute(
            "UPDATE factory_account SET tester_invited_at=NULL, tester_accepted_at=NULL WHERE id=?",
            (account_id,),
        )
        with self.assertRaises(ValueError) as caught:
            start_account_oauth(self.conn, account_id, "https://acp.test/cb", self.provider)
        self.assertIn("tester", str(caught.exception))
        row = self.conn.execute(
            "SELECT stage, oauth_session_id FROM factory_account WHERE id=?", (account_id,)
        ).fetchone()
        self.assertEqual("THREADS_CREATED", row["stage"])
        self.assertIsNone(row["oauth_session_id"])
```

If the existing test class has no `_seed_account` helper, reuse whatever seeding helper the file already defines and set the two milestone columns to `NULL` the same way.

- [ ] **Step 2: Run test to verify it fails**

Run: `ACP_ADAPTER=mock PYTHONPATH=. .venv/bin/python3 -m unittest tests.test_factory_v2_oauth_bridge`
Expected: FAIL. The call succeeds and creates a session instead of raising.

- [ ] **Step 3: Write minimal implementation**

In `core/factory_v2/oauth_bridge.py`, inside `start_account_oauth()`, immediately after the `if account is None: raise KeyError(account_id)` line:

```python
    if not account.get("tester_accepted_at"):
        raise ValueError("account has not accepted the Threads tester invitation")
```

- [ ] **Step 4: Fix the existing tests that assumed no gate**

Every existing test that drives `start_account_oauth()` or `POST /api/factory/v2/accounts/<id>/oauth/start` on a `THREADS_CREATED` account must now set the milestone first. In `tests/test_factory_v2_oauth_bridge.py`, `tests/test_factory_v2_oauth_expiry.py` and `tests/test_factory_v2_api.py`, add this to the seeding path used by those tests:

```python
        conn.execute(
            "UPDATE factory_account SET tester_invited_at=?, tester_accepted_at=? WHERE id=?",
            ("2026-09-10T00:00:00+00:00", "2026-09-10T00:00:00+00:00", account_id),
        )
```

Do not weaken the new test from Step 1 by giving it the milestone.

- [ ] **Step 5: Map the error to HTTP 409**

`web/factory_v2.py` already converts `ValueError` from `start_account_oauth()` into a 409, so no code change is required there. Add a test to `tests/test_factory_v2_api.py` proving it:

```python
    def test_oauth_start_returns_409_without_tester_acceptance(self):
        account_id = self._seed_threads_created_account()
        self.conn.execute(
            "UPDATE factory_account SET tester_accepted_at=NULL WHERE id=?", (account_id,)
        )
        response = self.client.post(
            f"/api/factory/v2/accounts/{account_id}/oauth/start",
            headers={"X-ACP-Factory-Key": self.key},
        )
        self.assertEqual(409, response.status_code)
        self.assertIn("tester", response.get_json()["error"])
```

- [ ] **Step 6: Run the three suites to verify they pass**

Run:
```bash
ACP_ADAPTER=mock PYTHONPATH=. .venv/bin/python3 -m unittest tests.test_factory_v2_oauth_bridge tests.test_factory_v2_oauth_expiry tests.test_factory_v2_api
```
Expected: PASS, no errors.

- [ ] **Step 7: Commit**

```bash
git add core/factory_v2/oauth_bridge.py tests/test_factory_v2_oauth_bridge.py tests/test_factory_v2_oauth_expiry.py tests/test_factory_v2_api.py
git commit -m "fix: chặn OAuth khi account chưa chấp nhận lời mời tester"
```

---

### Task 2: Hold accounts at a TESTER_INVITE checkpoint

**Files:**
- Modify: `core/factory_v2/runtime.py:502` (`_start_activation`)
- Modify: `core/factory_v2/runtime.py:694-707` (`tick` job query)
- Modify: `core/factory_v2/runtime.py:646-676` (`_drive_job`)
- Test: `tests/test_factory_v2_tester_gate.py` (create)

**Interfaces:**
- Consumes: `start_account_oauth()` guard from Task 1.
- Produces:
  - `FactoryControllerRuntime._open_tester_checkpoint(job, account) -> None` — creates one open `TESTER_INVITE` checkpoint per account and parks the job as `WAITING_HUMAN` with `desired_action='WAIT_TESTER'`.
  - `FactoryControllerRuntime._tester_checkpoint(account_id)` — returns the open `TESTER_INVITE` row or `None`.
  - `FactoryControllerRuntime._drive_tester_invite(job, account) -> None` — the `WAIT_TESTER` handler. Task 5 extends its middle branch.

- [ ] **Step 1: Write the failing test**

Create `tests/test_factory_v2_tester_gate.py` with this fixture, adapted from `tests/test_factory_v2_runtime_activation.py:13-128`:

```python
import sqlite3
import unittest

from core.account_factory import ensure_schema as ensure_oauth_schema
from core.db import now
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.runtime import FactoryControllerRuntime
from core.factory_v2.scheduler import Scheduler
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService
from core.factory_v2.models import AccountStage


class FakeSupervisor:
    def tick(self):
        return None


class FakeProcesses:
    def stop_all(self):
        pass


class FakeGateway:
    def __init__(self):
        self.commands = []
        self.responses = {}

    @property
    def sent(self):
        return [action for action, _ in self.commands]

    def send(self, job, action, payload=None):
        self.commands.append((action, payload or {}))
        return self.responses.get(action, {"ok": True})


class FakeActivation:
    def __init__(self, repo, service):
        self.repo = repo
        self.service = service
        self.start_calls = 0

    def start(self, account_id):
        self.start_calls += 1
        account = self.repo.get_account(account_id)
        if account["stage"] != AccountStage.ACP_CONNECTING.value:
            self.service.transition_account(account_id, AccountStage.ACP_CONNECTING)
        return {
            "session_id": "oauth-1",
            "status": "WAITING_AUTH",
            "authorization_url": "https://threads.example/authorize?state=x",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }

    def reconcile(self, account_id):
        return self.repo.get_account(account_id)


class TesterGateTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        ensure_schema(self.conn)
        ensure_oauth_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)
        self.scheduler = Scheduler(self.repo, self.service)
        self.gateway = FakeGateway()
        self.runtime = FactoryControllerRuntime(
            self.repo,
            self.service,
            self.scheduler,
            FakeSupervisor(),
            FakeProcesses(),
            runner_gateway=self.gateway,
            activation_service=FakeActivation(self.repo, self.service),
        )
        batch = self.service.create_batch("tester-gate", count=1, seed=7)
        self.batch_id = batch["id"]
        account = self.repo.list_accounts(batch["id"])[0]
        self.account_id = account["id"]
        self.worker = self.repo.insert_worker({
            "id": "avd-1",
            "runner_type": "REMOTE_AVD",
            "avd_name": "acp-worker-01",
            "state": "RUNNING",
        })
        self.conn.execute(
            """UPDATE factory_account
               SET stage='THREADS_CREATED', last_safe_stage='THREADS_CREATED',
                   tester_invited_at=NULL, tester_accepted_at=NULL,
                   assigned_worker_id=?, current_job_id='job-1'
               WHERE id=?""",
            (self.worker["id"], self.account_id),
        )

    def tearDown(self):
        self.conn.close()

    def _job_for(self, account_id, *, desired_action):
        self.conn.execute("DELETE FROM factory_job WHERE id='job-1'")
        self.conn.execute(
            """INSERT INTO factory_job
               (id,account_id,worker_id,runner_type,lease_token,state,desired_action,command_id,
                leased_at,lease_expires_at,heartbeat_at,started_at)
               VALUES ('job-1',?,?, 'REMOTE_AVD','lease','RUNNING',?,'cmd-1',?,?,?,?)""",
            (
                account_id, self.worker["id"], desired_action,
                now(), "2099-01-01T00:00:00+00:00", now(), now(),
            ),
        )
        return dict(self.conn.execute("SELECT * FROM factory_job WHERE id='job-1'").fetchone())

    def test_threads_created_without_acceptance_opens_checkpoint(self):
        job = self._job_for(self.account_id, desired_action="START_ACP")
        self.runtime._start_activation(job, self.repo.get_account(self.account_id))

        account = self.repo.get_account(self.account_id)
        self.assertEqual(AccountStage.THREADS_CREATED.value, account["stage"])
        self.assertIsNone(account["oauth_session_id"])

        checkpoint = self.runtime._tester_checkpoint(self.account_id)
        self.assertIsNotNone(checkpoint)
        self.assertEqual("TESTER_INVITE", checkpoint["type"])
        self.assertEqual("WAITING_EXTERNAL", checkpoint["status"])

        row = self.repo.conn.execute(
            "SELECT state, desired_action FROM factory_job WHERE id=?", (job["id"],)
        ).fetchone()
        self.assertEqual("WAITING_HUMAN", row["state"])
        self.assertEqual("WAIT_TESTER", row["desired_action"])

    def test_checkpoint_is_not_duplicated(self):
        job = self._job_for(self.account_id, desired_action="START_ACP")
        self.runtime._start_activation(job, self.repo.get_account(self.account_id))
        self.runtime._start_activation(job, self.repo.get_account(self.account_id))
        count = self.repo.conn.execute(
            "SELECT COUNT(*) AS n FROM factory_checkpoint WHERE account_id=? AND type='TESTER_INVITE'",
            (self.account_id,),
        ).fetchone()["n"]
        self.assertEqual(1, count)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ACP_ADAPTER=mock PYTHONPATH=. .venv/bin/python3 -m unittest tests.test_factory_v2_tester_gate`
Expected: FAIL with `AttributeError: 'FactoryControllerRuntime' object has no attribute '_tester_checkpoint'`.

- [ ] **Step 3: Write minimal implementation**

In `core/factory_v2/runtime.py`, add the two helpers next to `_activation_checkpoint`:

```python
    def _tester_checkpoint(self, account_id: str):
        return self.repo.conn.execute(
            """SELECT * FROM factory_checkpoint
               WHERE account_id=? AND type='TESTER_INVITE'
                 AND status IN ('WAITING_EXTERNAL','OPEN','SNOOZED')
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            (account_id,),
        ).fetchone()

    def _open_tester_checkpoint(self, job, account) -> None:
        timestamp = now()
        if self._tester_checkpoint(account["id"]) is None:
            self.repo.create_checkpoint({
                "id": ulid(),
                "batch_id": account["batch_id"],
                "account_id": account["id"],
                "worker_id": job["worker_id"],
                "type": "TESTER_INVITE",
                "status": "WAITING_EXTERNAL",
                "message": "Chờ mời tài khoản này làm Threads Tester trên Meta.",
                "created_at": timestamp,
            })
        self.repo.conn.execute(
            """UPDATE factory_job
               SET state='WAITING_HUMAN', desired_action='WAIT_TESTER', heartbeat_at=?, lease_expires_at=?
               WHERE id=?""",
            (timestamp, _lease_extension(), job["id"]),
        )
        self.repo.conn.execute(
            "UPDATE factory_worker SET state='WAITING_HUMAN', last_progress_at=? WHERE id=?",
            (timestamp, job["worker_id"]),
        )
```

Add the gate as the first statement of `_start_activation()`:

```python
        if not account.get("tester_accepted_at"):
            self._open_tester_checkpoint(job, account)
            return
```

Add the `WAIT_TESTER` handler:

```python
    def _drive_tester_invite(self, job, account) -> None:
        self.repo.conn.execute(
            "UPDATE factory_job SET heartbeat_at=?, lease_expires_at=? WHERE id=?",
            (now(), _lease_extension(), job["id"]),
        )
        if account.get("tester_accepted_at"):
            checkpoint = self._tester_checkpoint(account["id"])
            if checkpoint is not None:
                self.repo.resolve_checkpoint(
                    checkpoint["id"], resolved_at=now(), resolution="TESTER_ACCEPTED"
                )
            self._start_activation(job, self.repo.get_account(account["id"]))
```

Wire it in `_drive_job()` next to the `WAIT_ACP` branch:

```python
        elif action == "WAIT_TESTER":
            self._drive_tester_invite(job, account)
```

Add `'WAIT_TESTER'` to the `desired_action IN (...)` list in `tick()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `ACP_ADAPTER=mock PYTHONPATH=. .venv/bin/python3 -m unittest tests.test_factory_v2_tester_gate`
Expected: PASS.

- [ ] **Step 5: Run the runtime suites for regressions**

Run:
```bash
ACP_ADAPTER=mock PYTHONPATH=. .venv/bin/python3 -m unittest tests.test_factory_v2_activation tests.test_factory_v2_launcher tests.test_factory_cloud_pairing
```
Expected: PASS. If an activation test now stops at the gate, give its seeded account `tester_accepted_at` rather than removing the gate.

- [ ] **Step 6: Commit**

```bash
git add core/factory_v2/runtime.py tests/test_factory_v2_tester_gate.py tests/test_factory_v2_activation.py
git commit -m "feat: giữ account ở checkpoint TESTER_INVITE trước khi chạy OAuth"
```

---

### Task 3: One button clears the whole batch's invite list

**Files:**
- Modify: `web/threads_onboarding.py:104-168`
- Modify: `web/templates/threads_onboarding.html`
- Test: `tests/test_threads_onboarding_web.py`

**Interfaces:**
- Consumes: `TESTER_INVITE` checkpoints from Task 2, `mark_tester_invited()` from `core/factory_v2/threads_onboarding.py`.
- Produces: route `POST /kenh/threads/onboarding/batch/<batch_id>/tester-invited`, endpoint name `threads_onboarding_batch_tester_invited`. Template variable `pending_invite_batches`, a list of dicts shaped `{"batch_id": str, "usernames": list[str], "account_ids": list[str]}`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_threads_onboarding_web.py`:

```python
    def test_batch_invite_marks_every_pending_account(self):
        first = self._seed_account(batch_id="B1", username="acc_one")
        second = self._seed_account(batch_id="B1", username="acc_two")
        other = self._seed_account(batch_id="B2", username="acc_three")
        for account_id in (first, second, other):
            self._open_tester_checkpoint(account_id)

        response = self.client.post("/kenh/threads/onboarding/batch/B1/tester-invited")
        self.assertEqual(302, response.status_code)

        for account_id in (first, second):
            row = self.conn.execute(
                "SELECT tester_invited_at, tester_accepted_at FROM factory_account WHERE id=?",
                (account_id,),
            ).fetchone()
            self.assertIsNotNone(row["tester_invited_at"])
            self.assertIsNone(row["tester_accepted_at"])

        untouched = self.conn.execute(
            "SELECT tester_invited_at FROM factory_account WHERE id=?", (other,)
        ).fetchone()
        self.assertIsNone(untouched["tester_invited_at"])

        open_rows = self.conn.execute(
            """SELECT COUNT(*) AS n FROM factory_checkpoint
               WHERE batch_id='B1' AND type='TESTER_INVITE' AND status='WAITING_EXTERNAL'"""
        ).fetchone()["n"]
        self.assertEqual(0, open_rows)
```

Add these two helpers to `ThreadsOnboardingWebTests`. The existing `setUp()` already seeds one
batch through `FactoryService`, so these follow the same pattern:

```python
    def _seed_account(self, *, batch_id, username):
        conn = db.connect()
        try:
            repo = FactoryRepository(conn)
            service = FactoryService(repo)
            batch = repo.get_batch(batch_id)
            if batch is None:
                conn.execute(
                    """INSERT INTO factory_batch (id,name,target_count,completion_mode,state,created_at)
                       VALUES (?,?,?,'ACP_ACTIVE','RUNNING',?)""",
                    (batch_id, batch_id, 5, now()),
                )
            created = service.create_batch(f"seed-{username}", count=1, seed=11)
            account = repo.list_accounts(created["id"])[0]
            conn.execute(
                """UPDATE factory_account
                   SET username=?, batch_id=?, stage='THREADS_CREATED',
                       last_safe_stage='THREADS_CREATED',
                       tester_invited_at=NULL, tester_accepted_at=NULL
                   WHERE id=?""",
                (username, batch_id, account["id"]),
            )
            conn.commit()
            return account["id"]
        finally:
            conn.close()

    def _open_tester_checkpoint(self, account_id):
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT batch_id FROM factory_account WHERE id=?", (account_id,)
            ).fetchone()
            conn.execute(
                """INSERT INTO factory_checkpoint
                   (id,batch_id,account_id,type,status,message,created_at)
                   VALUES (?,?,?,'TESTER_INVITE','WAITING_EXTERNAL',?,?)""",
                (
                    f"cp-{account_id}", row["batch_id"], account_id,
                    "Chờ mời tài khoản này làm Threads Tester trên Meta.", now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
```

Import `now` from `core.db` at the top of the test module if it is not already imported.

- [ ] **Step 2: Run test to verify it fails**

Run: `ACP_ADAPTER=mock PYTHONPATH=. .venv/bin/python3 -m unittest tests.test_threads_onboarding_web`
Expected: FAIL with a 404, because the route does not exist.

- [ ] **Step 3: Write minimal implementation**

Add to `web/threads_onboarding.py`, above `register_threads_onboarding_routes`:

```python
def pending_invite_batches(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT c.batch_id AS batch_id, c.account_id AS account_id, a.username AS username
           FROM factory_checkpoint c
           JOIN factory_account a ON a.id = c.account_id
           WHERE c.type='TESTER_INVITE' AND c.status IN ('WAITING_EXTERNAL','OPEN','SNOOZED')
           ORDER BY c.batch_id, a.sequence, a.id"""
    ).fetchall()
    grouped: dict[str, dict] = {}
    for row in rows:
        entry = grouped.setdefault(
            row["batch_id"], {"batch_id": row["batch_id"], "usernames": [], "account_ids": []}
        )
        entry["usernames"].append(row["username"])
        entry["account_ids"].append(row["account_id"])
    return list(grouped.values())
```

Add the route inside `register_threads_onboarding_routes`:

```python
    @app.post("/kenh/threads/onboarding/batch/<batch_id>/tester-invited")
    def threads_onboarding_batch_tester_invited(batch_id):
        auth_redirect = _login_redirect(admin_password)
        if auth_redirect is not None:
            return auth_redirect

        conn = connect()
        try:
            ensure_factory_schema(conn)
            batches = [b for b in pending_invite_batches(conn) if b["batch_id"] == batch_id]
            if not batches:
                return redirect(url_for("threads_onboarding", err="Batch không có account chờ mời"))
            target = batches[0]
            for account_id in target["account_ids"]:
                mark_tester_invited(conn, account_id)
            conn.execute(
                """UPDATE factory_checkpoint
                   SET status='RESOLVED', resolved_at=?, resolution='TESTER_INVITED'
                   WHERE batch_id=? AND type='TESTER_INVITE'
                     AND status IN ('WAITING_EXTERNAL','OPEN','SNOOZED')""",
                (now(), batch_id),
            )
            conn.commit()
            count = len(target["account_ids"])
        finally:
            conn.close()
        return redirect(url_for(
            "threads_onboarding",
            summary=f"Đã ghi nhận invite cho {count} account trong batch {batch_id}",
        ))
```

Pass `pending_invite_batches=pending_invite_batches(conn)` into the `render_template()` call in `threads_onboarding()`, computing it while the connection is still open.

- [ ] **Step 4: Add the template block**

In `web/templates/threads_onboarding.html`, above the existing per-account list:

```html
{% for batch in pending_invite_batches %}
<section class="card">
  <h3>Batch {{ batch.batch_id }} — {{ batch.usernames|length }} account chờ mời tester</h3>
  <textarea readonly rows="{{ batch.usernames|length }}" style="width:100%">{{ batch.usernames|join('\n') }}</textarea>
  <p><a href="{{ meta_testers_url }}" target="_blank" rel="noopener">Mở App roles trên Meta</a></p>
  <form method="post" action="{{ url_for('threads_onboarding_batch_tester_invited', batch_id=batch.batch_id) }}">
    <button type="submit">Đã thêm trên Meta</button>
  </form>
</section>
{% endfor %}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `ACP_ADAPTER=mock PYTHONPATH=. .venv/bin/python3 -m unittest tests.test_threads_onboarding_web tests.test_factory_v2_threads_onboarding`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web/threads_onboarding.py web/templates/threads_onboarding.html tests/test_threads_onboarding_web.py
git commit -m "feat: gom danh sách chờ mời tester theo batch trên dashboard"
```

---

### Task 4: Tester invite acceptance flow

**Files:**
- Modify: `core/factory_v2/ui_automation/threads/selectors.py`
- Modify: `core/factory_v2/ui_automation/threads/screens.py`
- Create: `core/factory_v2/ui_automation/threads/tester_flow.py`
- Test: `tests/test_factory_v2_threads_tester_flow.py` (create)

**Interfaces:**
- Consumes: `FlowResult`, `DetectedScreen`, `SafeUiDriver` from the existing ui_automation package.
- Produces: `ThreadsTesterFlow(driver).accept_invite() -> FlowResult`. Statuses used by Task 5: `"completed"` with `screen="THREADS_TESTER_INVITE_CONFIRM"`, `"needs_confirmation"` with `reason="NO_TESTER_INVITE"`, `"waiting_human"`, `"needs_confirmation"` with `reason="UI_CHANGED"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_factory_v2_threads_tester_flow.py`. Copy the `FakeDriver` class verbatim from `tests/test_factory_v2_threads_flow.py:9-46` so this file stands alone.

```python
import unittest
from types import SimpleNamespace

from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.driver import ActionResult
from core.factory_v2.ui_automation.threads.tester_flow import ThreadsTesterFlow


class ThreadsTesterFlowTests(unittest.TestCase):
    def test_protected_screen_stops_before_any_tap(self):
        driver = FakeDriver([DetectedScreen("OTP_REQUIRED", 0.9, ("otp",), True)])
        result = ThreadsTesterFlow(driver).accept_invite()
        self.assertEqual("waiting_human", result.status)
        self.assertEqual([], driver.mutations)

    def test_accepts_invitation_from_settings(self):
        driver = FakeDriver(
            [
                DetectedScreen("THREADS_SETTINGS", 0.96, ("settings",)),
                DetectedScreen("THREADS_WEBSITE_PERMISSIONS", 0.96, ("website_permissions",)),
                DetectedScreen("THREADS_TESTER_INVITE_LIST", 0.96, ("tester_invite",)),
                DetectedScreen("THREADS_TESTER_INVITE_CONFIRM", 0.96, ("tester_accept",)),
            ],
            available=("website_permissions", "tester_invites", "tester_accept"),
        )
        result = ThreadsTesterFlow(driver).accept_invite()
        self.assertEqual("completed", result.status)
        self.assertIn(("tap", "tester_accept"), driver.mutations)

    def test_empty_invite_list_reports_missing_invite(self):
        driver = FakeDriver(
            [DetectedScreen("THREADS_TESTER_INVITE_LIST", 0.96, ("tester_invite",))],
            available=("website_permissions",),
        )
        result = ThreadsTesterFlow(driver).accept_invite()
        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("NO_TESTER_INVITE", result.reason)
        self.assertEqual([], driver.mutations)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ACP_ADAPTER=mock PYTHONPATH=. .venv/bin/python3 -m unittest tests.test_factory_v2_threads_tester_flow`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.factory_v2.ui_automation.threads.tester_flow'`.

- [ ] **Step 3: Add the selectors**

Append to `core/factory_v2/ui_automation/threads/selectors.py`:

```python
SETTINGS_ENTRY = Selector(semantic="settings", texts=("Settings", "Cài đặt"), require_clickable=True)
ACCOUNT_ENTRY = Selector(semantic="account", texts=("Account", "Tài khoản"), require_clickable=True)
WEBSITE_PERMISSIONS = Selector(semantic="website_permissions", texts=("Website permissions", "Quyền trang web"), require_clickable=True)
TESTER_INVITES = Selector(semantic="tester_invites", texts=("Invites", "Lời mời"), require_clickable=True)
TESTER_ACCEPT = Selector(semantic="tester_accept", texts=("Accept", "Chấp nhận"), require_clickable=True)
OAUTH_CONSENT_MARKER = Selector(semantic="oauth_consent", texts=("Allow", "Cho phép", "Authorize", "Ủy quyền"))
OAUTH_CONSENT_ALLOW = Selector(semantic="oauth_allow", texts=("Allow", "Cho phép", "Authorize", "Ủy quyền"), require_clickable=True)
```

- [ ] **Step 4: Add the screen signatures**

In `core/factory_v2/ui_automation/threads/screens.py`, import the new selectors and append these entries to the signature tuple returned by `build_threads_detector()`:

```python
        ScreenSignature("THREADS_SETTINGS", PACKAGE, (SETTINGS_ENTRY, ACCOUNT_ENTRY), 1, 0.95, False, 84),
        ScreenSignature("THREADS_WEBSITE_PERMISSIONS", PACKAGE, (WEBSITE_PERMISSIONS,), 1, 0.95, False, 85),
        ScreenSignature("THREADS_TESTER_INVITE_LIST", PACKAGE, (TESTER_INVITES,), 1, 0.95, False, 86),
        ScreenSignature("THREADS_TESTER_INVITE_CONFIRM", PACKAGE, (TESTER_ACCEPT,), 1, 0.95, False, 87),
```

Priorities 84-87 sit below every protected signature (10-17) and every error signature (30-34), so those still win.

- [ ] **Step 5: Write the flow**

Create `core/factory_v2/ui_automation/threads/tester_flow.py`:

```python
"""Fail-closed acceptance of the Meta Threads tester invitation."""
from __future__ import annotations

from ..flow_result import FlowResult
from .selectors import TESTER_ACCEPT, TESTER_INVITES, WEBSITE_PERMISSIONS

_STEPS = (
    ("THREADS_SETTINGS", WEBSITE_PERMISSIONS, ("THREADS_WEBSITE_PERMISSIONS",)),
    ("THREADS_WEBSITE_PERMISSIONS", TESTER_INVITES, ("THREADS_TESTER_INVITE_LIST",)),
    ("THREADS_TESTER_INVITE_LIST", TESTER_ACCEPT, ("THREADS_TESTER_INVITE_CONFIRM",)),
)


class ThreadsTesterFlow:
    def __init__(self, driver):
        self.driver = driver

    def accept_invite(self) -> FlowResult:
        for _ in range(len(_STEPS) + 1):
            detected = self.driver.detect_screen()
            if detected.protected:
                return FlowResult("waiting_human", detected.kind, "HUMAN_VERIFICATION_REQUIRED")
            if detected.kind == "THREADS_TESTER_INVITE_CONFIRM":
                return FlowResult(
                    "completed", detected.kind, last_safe_step="THREADS_TESTER_ACCEPTED"
                )
            step = next((item for item in _STEPS if item[0] == detected.kind), None)
            if step is None:
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            _, selector, expected = step
            if self.driver.find(selector) is None:
                reason = "NO_TESTER_INVITE" if detected.kind == "THREADS_TESTER_INVITE_LIST" else "UI_CHANGED"
                return FlowResult("needs_confirmation", detected.kind, reason)
            action = self.driver.tap(selector, expected_screens=expected, timeout=8.0)
            if action.status != "completed":
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
        return FlowResult("needs_confirmation", "THREADS_TESTER_INVITE_LIST", "UI_CHANGED")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `ACP_ADAPTER=mock PYTHONPATH=. .venv/bin/python3 -m unittest tests.test_factory_v2_threads_tester_flow tests.test_factory_v2_threads_flow`
Expected: PASS for both, proving the new signatures did not shadow the existing ones.

- [ ] **Step 7: Commit**

```bash
git add core/factory_v2/ui_automation/threads/ tests/test_factory_v2_threads_tester_flow.py
git commit -m "feat: luồng tự chấp nhận lời mời Threads tester trên thiết bị"
```

---

### Task 5: Run the accept flow from the runtime

**Files:**
- Modify: `workers/account_factory_worker.py:268-330`
- Modify: `core/factory_v2/runtime.py` (`_drive_tester_invite` from Task 2)
- Test: `tests/test_factory_v2_tester_gate.py`

**Interfaces:**
- Consumes: `ThreadsTesterFlow.accept_invite()` from Task 4, `_drive_tester_invite()` from Task 2.
- Produces: worker action `ACCEPT_THREADS_TESTER` returning the standard flow response dict `{"ok": True, "status": ..., "result": {"screen": ..., "reason": ..., "last_safe_step": ...}}`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_factory_v2_tester_gate.py`:

```python
    def test_invited_account_runs_accept_flow_then_activates(self):
        self.repo.conn.execute(
            "UPDATE factory_account SET tester_invited_at=? WHERE id=?",
            ("2026-09-10T00:00:00+00:00", self.account_id),
        )
        job = self._job_for(self.account_id, desired_action="WAIT_TESTER")
        self.gateway.responses["ACCEPT_THREADS_TESTER"] = {
            "ok": True,
            "status": "completed",
            "result": {"screen": "THREADS_TESTER_INVITE_CONFIRM", "reason": None},
        }

        self.runtime._drive_tester_invite(job, self.repo.get_account(self.account_id))

        account = self.repo.get_account(self.account_id)
        self.assertIsNotNone(account["tester_accepted_at"])
        self.assertEqual("ACP_CONNECTING", account["stage"])

    def test_missing_invite_keeps_account_waiting(self):
        self.repo.conn.execute(
            "UPDATE factory_account SET tester_invited_at=? WHERE id=?",
            ("2026-09-10T00:00:00+00:00", self.account_id),
        )
        job = self._job_for(self.account_id, desired_action="WAIT_TESTER")
        self.gateway.responses["ACCEPT_THREADS_TESTER"] = {
            "ok": True,
            "status": "needs_confirmation",
            "result": {"screen": "THREADS_TESTER_INVITE_LIST", "reason": "NO_TESTER_INVITE"},
        }

        self.runtime._drive_tester_invite(job, self.repo.get_account(self.account_id))

        account = self.repo.get_account(self.account_id)
        self.assertIsNone(account["tester_accepted_at"])
        self.assertEqual("THREADS_CREATED", account["stage"])
        checkpoint = self.runtime._tester_checkpoint(self.account_id)
        self.assertIsNotNone(checkpoint)
        self.assertIn("Meta", checkpoint["message"])
```

`FakeGateway.responses` and `FakeGateway.sent` come from the fixture written in Task 2.

- [ ] **Step 2: Run test to verify it fails**

Run: `ACP_ADAPTER=mock PYTHONPATH=. .venv/bin/python3 -m unittest tests.test_factory_v2_tester_gate`
Expected: FAIL. `tester_accepted_at` stays `None` because nothing sends the command.

- [ ] **Step 3: Add the worker action**

In `workers/account_factory_worker.py`, construct the flow next to `self.threads_flow` in `__init__`:

```python
        self.threads_tester_flow = ThreadsTesterFlow(self.threads_flow.driver)
```

Import it at the top:

```python
from core.factory_v2.ui_automation.threads.tester_flow import ThreadsTesterFlow
```

Add the branch in `execute()`, directly after the `AUTOMATE_THREADS` branch:

```python
            if action == "ACCEPT_THREADS_TESTER":
                self.threads_tester_flow.driver.open_package(_THREADS_PACKAGE)
                return self._flow_response(
                    "threads",
                    self.threads_tester_flow.accept_invite(),
                )
```

- [ ] **Step 4: Extend the runtime handler**

Replace the body of `_drive_tester_invite()` written in Task 2 with:

```python
    def _drive_tester_invite(self, job, account) -> None:
        self.repo.conn.execute(
            "UPDATE factory_job SET heartbeat_at=?, lease_expires_at=? WHERE id=?",
            (now(), _lease_extension(), job["id"]),
        )
        if account.get("tester_accepted_at"):
            self._resolve_tester_checkpoint(account["id"], "TESTER_ACCEPTED")
            self._start_activation(job, self.repo.get_account(account["id"]))
            return
        if not account.get("tester_invited_at"):
            return

        response = self._command(job, "ACCEPT_THREADS_TESTER")
        if _pending(response):
            return
        result = response.get("result") or {}
        if response.get("status") == "completed":
            from .threads_onboarding import mark_tester_accepted

            mark_tester_accepted(self.repo.conn, account["id"])
            self._resolve_tester_checkpoint(account["id"], "TESTER_ACCEPTED")
            self._start_activation(job, self.repo.get_account(account["id"]))
            return
        if result.get("reason") == "NO_TESTER_INVITE":
            checkpoint = self._tester_checkpoint(account["id"])
            if checkpoint is not None:
                self.repo.conn.execute(
                    "UPDATE factory_checkpoint SET message=? WHERE id=?",
                    ("Chưa thấy lời mời trên Meta cho account này.", checkpoint["id"]),
                )
```

Add the small resolver helper next to `_tester_checkpoint()`:

```python
    def _resolve_tester_checkpoint(self, account_id: str, resolution: str) -> None:
        checkpoint = self._tester_checkpoint(account_id)
        if checkpoint is not None:
            self.repo.resolve_checkpoint(
                checkpoint["id"], resolved_at=now(), resolution=resolution
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
ACP_ADAPTER=mock PYTHONPATH=. .venv/bin/python3 -m unittest tests.test_factory_v2_tester_gate tests.test_factory_v2_launcher
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add core/factory_v2/runtime.py workers/account_factory_worker.py tests/test_factory_v2_tester_gate.py
git commit -m "feat: runtime tự chạy luồng chấp nhận tester rồi kích hoạt ACP"
```

---

### Task 6: Tap the OAuth authorization control

**Files:**
- Modify: `core/factory_v2/ui_automation/threads/screens.py`
- Create: `core/factory_v2/ui_automation/threads/consent_flow.py`
- Modify: `workers/account_factory_worker.py`
- Modify: `core/factory_v2/runtime.py:502-538` (`_start_activation`)
- Test: `tests/test_factory_v2_threads_consent_flow.py` (create)
- Test: `tests/test_factory_v2_tester_gate.py`

**Interfaces:**
- Consumes: selectors `OAUTH_CONSENT_MARKER` and `OAUTH_CONSENT_ALLOW` added in Task 4.
- Produces: `ThreadsConsentFlow(driver).confirm() -> FlowResult` and worker action `CONFIRM_THREADS_OAUTH`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_factory_v2_threads_consent_flow.py`, again copying `FakeDriver` from `tests/test_factory_v2_threads_flow.py:9-46`.

```python
import unittest
from types import SimpleNamespace

from core.factory_v2.ui_automation.detector import DetectedScreen
from core.factory_v2.ui_automation.driver import ActionResult
from core.factory_v2.ui_automation.threads.consent_flow import ThreadsConsentFlow


class ThreadsConsentFlowTests(unittest.TestCase):
    def test_taps_allow_once_on_consent_screen(self):
        driver = FakeDriver(
            [DetectedScreen("THREADS_OAUTH_CONSENT", 0.95, ("oauth_consent",))],
            available=("oauth_allow",),
        )
        result = ThreadsConsentFlow(driver).confirm()
        self.assertEqual("completed", result.status)
        self.assertEqual([("tap", "oauth_allow")], driver.mutations)

    def test_security_consent_is_never_tapped(self):
        driver = FakeDriver(
            [DetectedScreen("CONSENT_WITH_SECURITY_IMPACT", 0.9, ("security_consent",), True)],
            available=("oauth_allow",),
        )
        result = ThreadsConsentFlow(driver).confirm()
        self.assertEqual("waiting_human", result.status)
        self.assertEqual([], driver.mutations)

    def test_unrecognized_screen_falls_back_without_tapping(self):
        driver = FakeDriver(
            [DetectedScreen("UNKNOWN", 0.0, ())] * 3,
            available=("oauth_allow",),
        )
        result = ThreadsConsentFlow(driver).confirm()
        self.assertEqual("needs_confirmation", result.status)
        self.assertEqual("CONSENT_NOT_DETECTED", result.reason)
        self.assertEqual([], driver.mutations)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ACP_ADAPTER=mock PYTHONPATH=. .venv/bin/python3 -m unittest tests.test_factory_v2_threads_consent_flow`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Add the consent signature**

In `core/factory_v2/ui_automation/threads/screens.py`, append:

```python
        ScreenSignature("THREADS_OAUTH_CONSENT", "*", (OAUTH_CONSENT_MARKER, OAUTH_CONSENT_ALLOW), 2, 0.95, False, 90),
```

Package is `"*"` because the authorization window may render in the browser or in the Threads app. `minimum_matches` is 2, so both the marker and a clickable allow control must be present. Priority 90 keeps it below every protected signature, including `CONSENT_WITH_SECURITY_IMPACT` at 17.

- [ ] **Step 4: Write the flow**

Create `core/factory_v2/ui_automation/threads/consent_flow.py`:

```python
"""Fail-closed confirmation of the Threads OAuth authorization window."""
from __future__ import annotations

from ..flow_result import FlowResult
from .selectors import OAUTH_CONSENT_ALLOW

_MAX_POLLS = 3


class ThreadsConsentFlow:
    def __init__(self, driver):
        self.driver = driver

    def confirm(self) -> FlowResult:
        detected = None
        for _ in range(_MAX_POLLS):
            detected = self.driver.detect_screen()
            if detected.protected:
                return FlowResult("waiting_human", detected.kind, "HUMAN_VERIFICATION_REQUIRED")
            if detected.kind == "THREADS_OAUTH_CONSENT":
                if self.driver.find(OAUTH_CONSENT_ALLOW) is None:
                    return FlowResult("needs_confirmation", detected.kind, "CONSENT_NOT_DETECTED")
                action = self.driver.tap(OAUTH_CONSENT_ALLOW)
                if action.status != "completed":
                    return FlowResult("needs_confirmation", detected.kind, "CONSENT_TAP_FAILED")
                return FlowResult("completed", detected.kind, last_safe_step="THREADS_OAUTH_CONSENT")
        kind = detected.kind if detected is not None else "UNKNOWN"
        return FlowResult("needs_confirmation", kind, "CONSENT_NOT_DETECTED")
```

- [ ] **Step 5: Add the worker action**

In `workers/account_factory_worker.py`, import and construct the flow the same way as Task 5, then add:

```python
            if action == "CONFIRM_THREADS_OAUTH":
                return self._flow_response(
                    "threads",
                    self.threads_consent_flow.confirm(),
                )
```

This branch must not call `open_package()`. The authorization window is already in the foreground after `OPEN_URL`.

- [ ] **Step 6: Wire it into activation**

In `core/factory_v2/runtime.py`, inside `_start_activation()`, replace the block that parks the job after `OPEN_URL` with:

```python
        opened = self._command(job, "OPEN_URL", {"url": started["authorization_url"]})
        if _pending(opened):
            return
        consent = self._command(job, "CONFIRM_THREADS_OAUTH")
        if _pending(consent):
            return
        self.repo.conn.execute(
            """UPDATE factory_job
               SET state='WAITING_HUMAN', desired_action='WAIT_ACP', heartbeat_at=?, lease_expires_at=?
               WHERE id=?""",
            (now(), _lease_extension(), job["id"]),
        )
        self.repo.conn.execute(
            """UPDATE factory_worker
               SET state='WAITING_HUMAN', last_progress_at=?
               WHERE id=?""",
            (now(), job["worker_id"]),
        )
```

The job still lands on `WAIT_ACP` whether the tap succeeded or not. When it succeeded, the callback has already arrived or will arrive shortly and `_reconcile_activation()` finishes the account on the next tick. When it did not, the existing `ACP_OAUTH` checkpoint is already open and the operator taps manually, exactly as today.

- [ ] **Step 7: Add the runtime regression test**

Add to `tests/test_factory_v2_tester_gate.py`:

```python
    def test_activation_requests_consent_after_opening_url(self):
        self.repo.conn.execute(
            "UPDATE factory_account SET tester_invited_at=?, tester_accepted_at=? WHERE id=?",
            ("2026-09-10T00:00:00+00:00", "2026-09-10T00:00:00+00:00", self.account_id),
        )
        job = self._job_for(self.account_id, desired_action="START_ACP")
        self.runtime._start_activation(job, self.repo.get_account(self.account_id))
        self.assertIn("OPEN_URL", self.gateway.sent)
        self.assertIn("CONFIRM_THREADS_OAUTH", self.gateway.sent)
        row = self.repo.conn.execute(
            "SELECT desired_action FROM factory_job WHERE id=?", (job["id"],)
        ).fetchone()
        self.assertEqual("WAIT_ACP", row["desired_action"])
```

`self.gateway.sent` is a list of action names recorded by the stub gateway.

- [ ] **Step 8: Run the full relevant suite**

Run:
```bash
ACP_ADAPTER=mock PYTHONPATH=. .venv/bin/python3 -m unittest \
  tests.test_factory_v2_threads_consent_flow tests.test_factory_v2_threads_tester_flow \
  tests.test_factory_v2_threads_flow tests.test_factory_v2_tester_gate \
  tests.test_factory_v2_activation tests.test_factory_v2_oauth_bridge \
  tests.test_factory_v2_api tests.test_threads_onboarding_web
```
Expected: PASS, no failures or errors.

- [ ] **Step 9: Run the release suite**

Run: `./manage.sh test`
Expected: PASS. If the deployment layout is unavailable, say so explicitly rather than claiming the suite ran.

- [ ] **Step 10: Commit**

```bash
git add core/factory_v2/ui_automation/threads/ core/factory_v2/runtime.py workers/account_factory_worker.py tests/
git commit -m "feat: tự xác nhận màn hình cấp quyền Threads OAuth trên thiết bị"
```
