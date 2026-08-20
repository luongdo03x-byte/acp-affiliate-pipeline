# Account Factory Dual-Runner P1 Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detach Account Factory from the ACP publishing web app and make the Factory V2 backend runner-neutral so both physical Android devices and Ubuntu AVDs can receive jobs from one authoritative controller.

**Architecture:** Keep the current Factory V2 repository/service/scheduler foundation, but introduce `RunnerType.LOCAL_DEVICE|REMOTE_AVD`, migrate worker/job persistence away from AVD-only assumptions, and expose a factory-only Flask application. The controller remains the single source of truth; runners report observations and heartbeats but never directly mark account stages successful.

**Tech Stack:** Python 3, Flask 3, SQLite, `unittest`, existing `core/factory_v2` modules.

## Global Constraints

- Account Factory must boot without ACP publishing tables such as `post`, `product`, or `campaign`.
- Do not register ACP publishing/dashboard routes in the Account Factory process.
- Preserve existing AVD behavior and persisted rows during migration.
- Runner types are exactly `LOCAL_DEVICE` and `REMOTE_AVD`.
- One active job per account and one active job per runner.
- `WAITING_HUMAN` still occupies the assigned runner.
- Controller is authoritative for stages; runner observations cannot directly write `IG_CREATED`, `THREADS_CREATED`, or `ACP_ACTIVE`.
- No CAPTCHA/OTP solving, password capture, security-check bypass, proxy/fingerprint evasion, or live Threads publishing.
- Keep `X-ACP-Factory-Key` for P0/P1 API authentication.

---

## File Structure

- Create `web/factory_app.py` — factory-only Flask application entrypoint.
- Modify `account_factory_server.py` — build from `web.factory_app`, not `web.server`.
- Modify `core/factory_v2/models.py` — add runner-neutral enums/constants.
- Modify `core/factory_v2/schema.py` — migration-safe worker/job runner fields.
- Modify `core/factory_v2/repository.py` — runner registration/query/heartbeat methods.
- Modify `core/factory_v2/service.py` — local runner lifecycle and execution-target validation.
- Modify `core/factory_v2/scheduler.py` — runner-neutral assignment.
- Modify `core/factory_v2/runtime.py` — workflow dispatch through runner transport boundary.
- Modify `web/factory_v2.py` — runner endpoints and safe serializers.
- Create `tests/test_factory_v2_factory_app.py`.
- Create `tests/test_factory_v2_runner_schema.py`.
- Create `tests/test_factory_v2_runner_api.py`.
- Modify scheduler/runtime/service tests where AVD-only assumptions exist.

### Task 1: Factory-only Flask application

**Files:**
- Create: `web/factory_app.py`
- Modify: `account_factory_server.py`
- Create: `tests/test_factory_v2_factory_app.py`

**Interfaces:**
- Produces `create_factory_app() -> Flask`.
- `account_factory_server.build_app(start_controller=False, runtime_factory=build_default_runtime)` must call `create_factory_app()` and then register only Factory V2 + Account Factory OAuth routes.

- [ ] **Step 1: Write failing boot-isolation tests**

```python
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from account_factory_server import build_app


class FactoryOnlyAppTests(unittest.TestCase):
    def test_factory_root_does_not_require_post_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "factory.db")
            with patch.dict(os.environ, {"ACP_DB": db_path, "ACP_FACTORY_API_KEY": "test-key"}, clear=False):
                app = build_app(start_controller=False)
                res = app.test_client().get("/")
                self.assertEqual(200, res.status_code)
                body = res.get_json()
                self.assertEqual("account-factory", body["service"])

                conn = sqlite3.connect(db_path)
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                conn.close()
                self.assertNotIn("post", tables)
                self.assertNotIn("product", tables)

    def test_factory_app_does_not_register_publish_routes(self):
        app = build_app(start_controller=False)
        rules = {rule.rule for rule in app.url_map.iter_rules()}
        self.assertNotIn("/sanpham", rules)
        self.assertNotIn("/duyet", rules)
        self.assertIn("/api/factory/v2/dashboard", rules)
```

- [ ] **Step 2: Run tests and verify RED**

Run:
```bash
python3 -m unittest tests.test_factory_v2_factory_app -v
```
Expected: FAIL because `build_app()` currently uses `acp.web.server.create_app()` and `/` executes the publishing dashboard.

- [ ] **Step 3: Implement minimal factory-only app**

Create `web/factory_app.py`:

```python
from flask import Flask, jsonify


def create_factory_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def root():
        return jsonify({"ok": True, "service": "account-factory"})

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True, "service": "account-factory"})

    return app
```

Change launcher import from `acp.web.server import create_app` to `acp.web.factory_app import create_factory_app`, then use `create_factory_app()` inside `build_app()` before registering OAuth and Factory V2 routes.

- [ ] **Step 4: Run focused + launcher tests**

```bash
python3 -m unittest tests.test_factory_v2_factory_app tests.test_factory_v2_launcher tests.test_factory_v2_api -v
```
Expected: PASS; no test may require publishing tables merely to build the factory app.

- [ ] **Step 5: Commit**

```bash
git add web/factory_app.py account_factory_server.py tests/test_factory_v2_factory_app.py
git commit -m "refactor: isolate account factory server"
```

### Task 2: Runner-neutral model and schema migration

**Files:**
- Modify: `core/factory_v2/models.py`
- Modify: `core/factory_v2/schema.py`
- Create: `tests/test_factory_v2_runner_schema.py`

**Interfaces:**
- Produces `RunnerType(StrEnum)` with `LOCAL_DEVICE` and `REMOTE_AVD`.
- Produces neutral stage `RUNNER_ASSIGNED` while preserving compatibility with persisted `AVD_ASSIGNED` rows.
- `factory_worker` stores `runner_type`, nullable `avd_name`, nullable `adb_serial`, nullable `device_id`, nullable `device_name`.
- `factory_job` stores `runner_type` copied from its worker when leased.

- [ ] **Step 1: Write failing schema migration tests**

```python
import sqlite3
import unittest

from core.factory_v2.schema import ensure_schema


class RunnerSchemaTests(unittest.TestCase):
    def test_local_worker_does_not_require_avd_name(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        conn.execute(
            """INSERT INTO factory_worker
               (id, runner_type, device_id, device_name, state)
               VALUES ('phone-1','LOCAL_DEVICE','android-id-1','Pixel','READY')"""
        )
        row = conn.execute("SELECT * FROM factory_worker WHERE id='phone-1'").fetchone()
        self.assertIsNone(row["avd_name"])

    def test_existing_avd_worker_is_backfilled_remote_avd(self):
        # Build the old worker table shape, insert a row, then run ensure_schema.
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE factory_worker (
            id TEXT PRIMARY KEY,
            avd_name TEXT NOT NULL UNIQUE,
            adb_serial TEXT UNIQUE,
            state TEXT NOT NULL,
            current_account_id TEXT,
            current_job_id TEXT,
            pid INTEGER,
            started_at TEXT,
            last_heartbeat_at TEXT,
            last_progress_at TEXT,
            processed_count INTEGER NOT NULL DEFAULT 0,
            recovery_count INTEGER NOT NULL DEFAULT 0,
            estimated_ram_mb INTEGER,
            current_ram_mb INTEGER,
            current_cpu_percent REAL,
            draining INTEGER NOT NULL DEFAULT 0,
            last_error TEXT
        )""")
        conn.execute("INSERT INTO factory_worker(id,avd_name,state) VALUES('w1','acp-worker-01','READY')")
        ensure_schema(conn)
        row = conn.execute("SELECT * FROM factory_worker WHERE id='w1'").fetchone()
        self.assertEqual("REMOTE_AVD", row["runner_type"])
        self.assertEqual("acp-worker-01", row["avd_name"])
```

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_factory_v2_runner_schema -v
```
Expected: FAIL because `runner_type/device_id/device_name` do not exist and `avd_name` is required.

- [ ] **Step 3: Implement migration-safe schema**

Add to `models.py`:

```python
class RunnerType(StrEnum):
    LOCAL_DEVICE = "LOCAL_DEVICE"
    REMOTE_AVD = "REMOTE_AVD"
```

Add `RUNNER_ASSIGNED = "RUNNER_ASSIGNED"` to `AccountStage`; retain `AVD_ASSIGNED` as a legacy accepted value during migration.

In `schema.py`, change fresh schema to the runner-neutral shape and add an idempotent migration helper that rebuilds only `factory_worker` when the old `avd_name NOT NULL` definition is detected. During copy, set `runner_type='REMOTE_AVD'`. Add missing `runner_type` to `factory_job` with `ALTER TABLE` when necessary and backfill it from the assigned worker.

- [ ] **Step 4: Run schema + legacy suites**

```bash
python3 -m unittest tests.test_factory_v2_runner_schema tests.test_factory_v2_schema tests.test_factory_v2_scheduler tests.test_factory_v2_supervisor -v
```
Expected: PASS and existing AVD rows remain valid.

- [ ] **Step 5: Commit**

```bash
git add core/factory_v2/models.py core/factory_v2/schema.py tests/test_factory_v2_runner_schema.py
git commit -m "feat: make factory workers runner neutral"
```

### Task 3: Local runner registration and heartbeat service

**Files:**
- Modify: `core/factory_v2/repository.py`
- Modify: `core/factory_v2/service.py`
- Create: `tests/test_factory_v2_runner_service.py`

**Interfaces:**
- Produces `FactoryService.register_local_runner(device_id: str, device_name: str) -> dict`.
- Produces `FactoryService.heartbeat_runner(worker_id: str, *, current_account_id: str | None, current_job_id: str | None) -> dict`.
- Re-registering the same `device_id` is idempotent and returns the same worker id.

- [ ] **Step 1: Write failing service tests**

```python
def test_register_local_runner_is_idempotent(self):
    first = self.service.register_local_runner("android-id-1", "Pixel 8")
    second = self.service.register_local_runner("android-id-1", "Pixel 8 Pro")
    self.assertEqual(first["id"], second["id"])
    saved = self.repo.get_worker(first["id"])
    self.assertEqual("LOCAL_DEVICE", saved["runner_type"])
    self.assertEqual("Pixel 8 Pro", saved["device_name"])
    self.assertEqual("READY", saved["state"])


def test_local_runner_heartbeat_updates_last_heartbeat(self):
    worker = self.service.register_local_runner("android-id-2", "Phone")
    updated = self.service.heartbeat_runner(worker["id"], current_account_id=None, current_job_id=None)
    self.assertIsNotNone(updated["last_heartbeat_at"])
```

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_factory_v2_runner_service -v
```
Expected: FAIL for missing service methods.

- [ ] **Step 3: Implement repository/service methods**

Repository additions:

```python
get_worker_by_device_id(device_id: str)
insert_worker(values: dict)
update_worker_fields(worker_id: str, **fields)
```

Service behavior:
- generate worker id with existing ULID helper;
- `runner_type=LOCAL_DEVICE`;
- no `avd_name`, no `adb_serial`, no `pid` requirement;
- re-registration updates `device_name`, heartbeat, and restores idle STOPPED/ERROR local runner to READY only when it has no active job;
- heartbeat must reject mismatched `current_account_id/current_job_id` rather than overwrite authoritative assignment.

- [ ] **Step 4: Run service tests**

```bash
python3 -m unittest tests.test_factory_v2_runner_service tests.test_factory_v2_service -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/factory_v2/repository.py core/factory_v2/service.py tests/test_factory_v2_runner_service.py
git commit -m "feat: register physical factory runners"
```

### Task 4: Runner-neutral scheduler assignment

**Files:**
- Modify: `core/factory_v2/scheduler.py`
- Modify: `core/factory_v2/service.py`
- Create: `tests/test_factory_v2_dual_scheduler.py`

**Interfaces:**
- `Scheduler.assign_next(worker_id: str) -> dict | None` must work for either runner type.
- Account/batch creation stores an execution target selector: `THIS_PHONE`, `AUTO_AVD`, or exact worker id.
- Leased jobs copy `worker.runner_type` into `factory_job.runner_type`.

- [ ] **Step 1: Write failing dual-runner lease tests**

```python
def test_this_phone_account_only_leases_to_requested_local_worker(self):
    phone = self.seed_worker("phone-1", runner_type="LOCAL_DEVICE", state="READY")
    avd = self.seed_worker("avd-1", runner_type="REMOTE_AVD", state="READY")
    account = self.seed_account(execution_target="phone-1")
    self.assertIsNone(self.scheduler.assign_next(avd["id"]))
    job = self.scheduler.assign_next(phone["id"])
    self.assertEqual(account["id"], job["account_id"])
    self.assertEqual("LOCAL_DEVICE", job["runner_type"])


def test_auto_avd_does_not_lease_to_phone(self):
    phone = self.seed_worker("phone-1", runner_type="LOCAL_DEVICE", state="READY")
    avd = self.seed_worker("avd-1", runner_type="REMOTE_AVD", state="READY")
    self.seed_account(execution_target="AUTO_AVD")
    self.assertIsNone(self.scheduler.assign_next(phone["id"]))
    self.assertIsNotNone(self.scheduler.assign_next(avd["id"]))
```

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_factory_v2_dual_scheduler -v
```
Expected: FAIL because scheduler currently assumes AVD workers/stages.

- [ ] **Step 3: Implement execution-target filtering**

Add an `execution_target` field to `factory_account` (migration-safe nullable text; null means existing behavior/AUTO_AVD for legacy rows). Filter eligible accounts before leasing:
- exact worker id -> only that worker;
- `THIS_PHONE:<worker_id>` normalized to exact worker id at create time;
- `AUTO_AVD` -> only `REMOTE_AVD` workers;
- `AUTO` may be parsed but is not enabled in UI in this phase.

On lease transition the account to `RUNNER_ASSIGNED`; when reading legacy `AVD_ASSIGNED`, treat it equivalently for recovery.

- [ ] **Step 4: Run scheduler/recovery suites**

```bash
python3 -m unittest tests.test_factory_v2_dual_scheduler tests.test_factory_v2_scheduler tests.test_factory_v2_scheduler_recovery tests.test_factory_v2_restart_recovery -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/factory_v2/schema.py core/factory_v2/service.py core/factory_v2/scheduler.py tests/test_factory_v2_dual_scheduler.py
git commit -m "feat: schedule jobs across phone and avd runners"
```

### Task 5: Runner registration/heartbeat API

**Files:**
- Modify: `web/factory_v2.py`
- Create: `tests/test_factory_v2_runner_api.py`

**Interfaces:**
- `GET /api/factory/v2/runners`.
- `POST /api/factory/v2/runners/local/register` body `{device_id, device_name}`.
- `POST /api/factory/v2/runners/<worker_id>/heartbeat` body `{current_account_id, current_job_id}`.
- Responses expose safe metadata only.

- [ ] **Step 1: Write failing API tests**

```python
def test_register_local_runner(self):
    res = self.client.post(
        "/api/factory/v2/runners/local/register",
        headers=self.auth,
        json={"device_id": "android-id-1", "device_name": "Pixel 8"},
    )
    self.assertEqual(201, res.status_code)
    body = res.get_json()
    self.assertEqual("LOCAL_DEVICE", body["runner"]["runner_type"])
    self.assertNotIn("adb_serial", body["runner"])
    self.assertNotIn("pid", body["runner"])


def test_heartbeat_rejects_wrong_assignment(self):
    worker = self.seed_local_worker(current_account_id="a1", current_job_id="j1")
    res = self.client.post(
        f"/api/factory/v2/runners/{worker['id']}/heartbeat",
        headers=self.auth,
        json={"current_account_id": "a2", "current_job_id": "j2"},
    )
    self.assertEqual(409, res.status_code)
```

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_factory_v2_runner_api -v
```
Expected: FAIL for missing routes.

- [ ] **Step 3: Implement authenticated routes**

Reuse the existing Factory V2 auth helper. Validate `device_id` and `device_name` as bounded non-empty strings. Serializer fields:

```python
{
  "id", "runner_type", "device_id", "device_name", "avd_name",
  "state", "current_account_id", "current_job_id", "last_heartbeat_at",
  "draining", "last_error"
}
```

Omit `adb_serial`, `pid`, tokens, secrets, and provider bodies from phone responses.

- [ ] **Step 4: Run API suites**

```bash
python3 -m unittest tests.test_factory_v2_runner_api tests.test_factory_v2_api -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/factory_v2.py tests/test_factory_v2_runner_api.py
git commit -m "feat: expose dual runner controller api"
```

### Task 6: Runner-neutral workflow command boundary

**Files:**
- Create: `core/factory_v2/runner_gateway.py`
- Modify: `core/factory_v2/runtime.py`
- Modify: `core/factory_v2/worker_process.py`
- Create: `tests/test_factory_v2_runner_gateway.py`

**Interfaces:**
- Produces `RunnerGateway.send(job, action: str, payload: dict | None = None) -> dict`.
- `REMOTE_AVD` delegates to existing `WorkerProcessManager.request`.
- `LOCAL_DEVICE` queues a controller command row returned by API polling; it does not attempt ADB.

- [ ] **Step 1: Write failing gateway tests**

```python
def test_avd_gateway_uses_worker_process_transport(self):
    result = self.gateway.send(self.avd_job, "OPEN_PACKAGE", {"package": "com.instagram.android"})
    self.assertEqual("ok", result["status"])
    self.assertEqual("OPEN_PACKAGE", self.fake_process.last_command.action)


def test_local_gateway_queues_command_without_adb(self):
    result = self.gateway.send(self.local_job, "OPEN_PACKAGE", {"package": "com.instagram.android"})
    queued = self.repo.get_runner_command(result["command_id"])
    self.assertEqual("LOCAL_DEVICE", queued["runner_type"])
    self.assertEqual("OPEN_PACKAGE", queued["action"])
```

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_factory_v2_runner_gateway -v
```
Expected: FAIL because gateway/runner command persistence does not exist.

- [ ] **Step 3: Implement gateway + local command persistence**

Add `factory_runner_command` table:

```text
id, worker_id, job_id, account_id, runner_type, action,
payload_json, status, created_at, delivered_at, completed_at, result_json
```

Statuses: `QUEUED|DELIVERED|COMPLETED|FAILED`.

Change runtime `_command()` to call `self.runner_gateway.send(...)`. Preserve existing synchronous AVD semantics. For local commands, runtime must not advance based on command enqueue; it waits for a completed observation/result submitted by the phone.

- [ ] **Step 4: Run runtime + transport suites**

```bash
python3 -m unittest tests.test_factory_v2_runner_gateway tests.test_factory_v2_runtime tests.test_factory_v2_worker_process tests.test_factory_v2_runtime_atomicity -v
```
Expected: PASS; AVD behavior remains unchanged.

- [ ] **Step 5: Commit**

```bash
git add core/factory_v2/runner_gateway.py core/factory_v2/runtime.py core/factory_v2/worker_process.py core/factory_v2/schema.py core/factory_v2/repository.py tests/test_factory_v2_runner_gateway.py
git commit -m "refactor: route factory workflow through runner gateway"
```

### Task 7: Local runner command polling/result API

**Files:**
- Modify: `web/factory_v2.py`
- Create: `tests/test_factory_v2_runner_commands_api.py`

**Interfaces:**
- `GET /api/factory/v2/runners/<worker_id>/commands/next` returns one queued command and marks it delivered atomically.
- `POST /api/factory/v2/runners/<worker_id>/commands/<command_id>/result` stores allowlisted observation result.

- [ ] **Step 1: Write failing command API tests**

```python
def test_next_command_is_delivered_once(self):
    command = self.seed_local_command(action="OPEN_PACKAGE")
    first = self.client.get(self.next_url, headers=self.auth)
    second = self.client.get(self.next_url, headers=self.auth)
    self.assertEqual(command["id"], first.get_json()["command"]["id"])
    self.assertIsNone(second.get_json()["command"])


def test_result_cannot_change_account_stage_directly(self):
    command = self.seed_local_command(action="OBSERVE_FOREGROUND")
    res = self.client.post(
        self.result_url(command["id"]),
        headers=self.auth,
        json={"status": "COMPLETED", "result": {"package": "com.instagram.android", "stage": "IG_CREATED"}},
    )
    self.assertEqual(202, res.status_code)
    account = self.repo.get_account(command["account_id"])
    self.assertNotEqual("IG_CREATED", account["stage"])
```

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_factory_v2_runner_commands_api -v
```
Expected: FAIL for missing endpoints.

- [ ] **Step 3: Implement delivery/result routes**

Allow result keys only from the runner protocol, e.g. `package`, `activity`, `waiting_human`, `error_code`. Ignore/reject `stage`, `token`, `password`, `otp`, `secret` keys. Persist the result and let controller runtime interpret it on its next tick.

- [ ] **Step 4: Run all controller tests**

```bash
python3 -m unittest \
  tests.test_factory_v2_factory_app \
  tests.test_factory_v2_runner_schema \
  tests.test_factory_v2_runner_service \
  tests.test_factory_v2_dual_scheduler \
  tests.test_factory_v2_runner_api \
  tests.test_factory_v2_runner_gateway \
  tests.test_factory_v2_runner_commands_api \
  tests.test_factory_v2_scheduler \
  tests.test_factory_v2_scheduler_recovery \
  tests.test_factory_v2_supervisor \
  tests.test_factory_v2_runtime \
  tests.test_factory_v2_runtime_atomicity \
  tests.test_factory_v2_runtime_resume \
  tests.test_factory_v2_restart_recovery \
  tests.test_factory_v2_api -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/factory_v2.py tests/test_factory_v2_runner_commands_api.py
git commit -m "feat: add local runner command channel"
```

## Completion Gate

This plan is complete only when:

```bash
python3 -m unittest tests.test_factory_v2_factory_app tests.test_factory_v2_runner_schema tests.test_factory_v2_runner_service tests.test_factory_v2_dual_scheduler tests.test_factory_v2_runner_api tests.test_factory_v2_runner_gateway tests.test_factory_v2_runner_commands_api tests.test_factory_v2_scheduler tests.test_factory_v2_scheduler_recovery tests.test_factory_v2_supervisor tests.test_factory_v2_runtime tests.test_factory_v2_runtime_atomicity tests.test_factory_v2_runtime_resume tests.test_factory_v2_restart_recovery tests.test_factory_v2_api -v
```

passes, and manually starting:

```bash
ACP_FACTORY_CONTROLLER=1 ACP_PORT=5001 python3 account_factory_server.py
```

allows `GET /` and `GET /healthz` without requiring a `post` table.