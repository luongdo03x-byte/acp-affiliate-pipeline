# Account Factory Dual-Runner P1B Account Creation API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the missing authenticated controller endpoint that creates a one-account Factory batch/job seed with an explicit execution target for `THIS_PHONE`, `AUTO_AVD`, or a specific READY AVD.

**Architecture:** Keep profile/account generation in the existing Factory service/repository layer. The API accepts only execution-target selection and optional batch label; the server owns ids, stage initialization, and account profile generation. The created account starts at `PROFILE_READY` and is later leased by the runner-neutral scheduler from P1.

**Tech Stack:** Python 3, Flask 3, SQLite, Factory V2 service/repository, `unittest`.

## Global Constraints

- This plan runs after P1 Tasks 1–5 and before Android P2 Task 1 is considered complete.
- No client-supplied workflow stage, worker assignment, OAuth session, password, token, OTP, or CAPTCHA data.
- `THIS_PHONE` must resolve to an existing READY `LOCAL_DEVICE` runner id.
- Exact AVD target must resolve to an existing READY `REMOTE_AVD` runner id.
- `AUTO_AVD` stores the selector and lets the scheduler choose a READY AVD.
- One HTTP request creates exactly one account in P1B.

---

### Task 1: Service method for one targeted account

**Files:**
- Modify: `core/factory_v2/service.py`
- Modify: `core/factory_v2/repository.py` only if a missing query helper is needed.
- Create: `tests/test_factory_v2_create_account.py`

**Interfaces:**
- Produces `FactoryService.create_single_account(*, execution_target: str, batch_name: str = "Phone/AVD Pilot") -> dict`.
- Return shape: `{ "batch": dict, "account": dict }`.

- [ ] **Step 1: Write failing service tests**

```python
def test_create_single_account_for_local_runner(self):
    phone = self.seed_worker(id="phone-1", runner_type="LOCAL_DEVICE", state="READY")
    result = self.service.create_single_account(execution_target=phone["id"])
    account = result["account"]
    self.assertEqual("PROFILE_READY", account["stage"])
    self.assertEqual(phone["id"], account["execution_target"])
    self.assertIsNone(account["assigned_worker_id"])


def test_create_single_account_rejects_wrong_runner_type(self):
    phone = self.seed_worker(id="phone-1", runner_type="LOCAL_DEVICE", state="READY")
    with self.assertRaises(ValueError):
        self.service.create_single_account(execution_target="AUTO_AVD:" + phone["id"])
```

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_factory_v2_create_account -v
```
Expected: FAIL because service method does not exist.

- [ ] **Step 3: Implement validation + creation**

Accepted `execution_target` values:

```text
AUTO_AVD
<existing LOCAL_DEVICE worker id>
<existing REMOTE_AVD worker id>
```

For exact worker ids, validate state is `READY` and `draining=0` at creation time; the scheduler still re-validates at lease time. Create a one-account batch through the same profile/account generation path used by existing Factory V2 batch creation, persist `execution_target` on the account, and leave assignment null until scheduler lease.

- [ ] **Step 4: Run service tests**

```bash
python3 -m unittest tests.test_factory_v2_create_account tests.test_factory_v2_service tests.test_factory_v2_dual_scheduler -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/factory_v2/service.py core/factory_v2/repository.py tests/test_factory_v2_create_account.py
git commit -m "feat: create runner-targeted factory account"
```

### Task 2: `POST /api/factory/v2/accounts`

**Files:**
- Modify: `web/factory_v2.py`
- Modify: `tests/test_factory_v2_create_account.py`

**Interfaces:**
- Produces `POST /api/factory/v2/accounts`.
- Request body: `{ "execution_target": "AUTO_AVD|<worker-id>", "batch_name": "optional label" }`.
- Success: HTTP 201 `{ "ok": true, "batch": {...}, "account": {...} }` using existing allowlisted serializers.

- [ ] **Step 1: Write failing API tests**

```python
def test_create_account_requires_execution_target(self):
    res = self.client.post("/api/factory/v2/accounts", headers=self.auth, json={})
    self.assertEqual(400, res.status_code)


def test_create_account_for_phone(self):
    phone = self.seed_worker(id="phone-1", runner_type="LOCAL_DEVICE", state="READY")
    res = self.client.post(
        "/api/factory/v2/accounts",
        headers=self.auth,
        json={"execution_target": phone["id"], "batch_name": "Phone pilot"},
    )
    self.assertEqual(201, res.status_code)
    body = res.get_json()
    self.assertEqual("PROFILE_READY", body["account"]["stage"])
    self.assertEqual(phone["id"], body["account"]["execution_target"])
```

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest tests.test_factory_v2_create_account -v
```
Expected: FAIL because POST route does not exist.

- [ ] **Step 3: Implement route**

Add `execution_target` to `_ACCOUNT_FIELDS`. Parse body as JSON, require bounded non-empty target, cap `batch_name` at 120 characters, delegate to `FactoryService.create_single_account`, map missing worker to 404 and invalid/not-ready target to 409. Ignore/reject extra sensitive/control fields rather than persisting them.

- [ ] **Step 4: Run API tests**

```bash
python3 -m unittest tests.test_factory_v2_create_account tests.test_factory_v2_api tests.test_factory_v2_runner_api -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/factory_v2.py tests/test_factory_v2_create_account.py
git commit -m "feat: expose runner-targeted account creation api"
```

### Task 3: Android transport contract alignment

**Files:**
- No production Android implementation here; verify P2 expects the same contract.

- [ ] **Step 1: Lock request/response example in backend test**

Expected request:

```json
{"execution_target":"phone-1","batch_name":"Phone pilot"}
```

Expected response fields used by Android:

```json
{
  "ok": true,
  "account": {
    "id": "...",
    "stage": "PROFILE_READY",
    "execution_target": "phone-1",
    "assigned_worker_id": null
  }
}
```

- [ ] **Step 2: Run completion gate**

```bash
python3 -m unittest tests.test_factory_v2_create_account tests.test_factory_v2_dual_scheduler tests.test_factory_v2_runner_api tests.test_factory_v2_api -v
```
Expected: PASS.

## Completion Gate

P2 Android may call:

```text
POST /api/factory/v2/accounts
```

with the exact `execution_target` selected in the app, and the controller—not Android—creates the authoritative `PROFILE_READY` account.