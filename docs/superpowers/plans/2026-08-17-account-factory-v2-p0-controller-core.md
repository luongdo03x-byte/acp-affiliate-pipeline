# Account Factory V2 P0 Controller Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the authoritative Ubuntu-side persistence, state machine, identity generator, and batch service for Account Factory V2 P0.

**Architecture:** Add a focused `core/factory_v2` package. SQLite on the Ubuntu controller becomes the source of truth; the Android Room database becomes cache-only in a later plan. Repository methods own transactions, the state machine owns legal transitions, and the batch service composes profile generation with persistence.

**Tech Stack:** Python 3, SQLite, existing `core.db` helpers, `unittest`, standard library only for P0 core.

## Global Constraints

- Controller on Ubuntu is the authoritative state store.
- P0 uses SQLite; do not introduce PostgreSQL.
- Default batch size is 50.
- Vietnamese synthetic profiles target approximately 35 female-name profiles and 15 male-name profiles.
- Default niche counts are Beauty 9, Fashion 9, Tech 8, Home 8, Fitness 8, Food 8.
- Usernames must be normalized, unique inside a batch, natural-looking, and must not use simple sequential factory patterns.
- Do not store Instagram passwords, OTPs, CAPTCHA results, selfie data, Threads app secret, ACP master key, or plaintext Threads access tokens in Factory V2 tables.
- Human-required platform verification remains a checkpoint; no state transition may imply successful verification without an explicit post-check.
- Existing Threads token encryption and `channel` ownership remain in ACP backend code.

---

## File Structure

- Create `core/factory_v2/__init__.py` — package exports only.
- Create `core/factory_v2/schema.py` — Factory V2 DDL and `ensure_schema(conn)`.
- Create `core/factory_v2/models.py` — enums/dataclasses shared across controller code.
- Create `core/factory_v2/state_machine.py` — legal account transitions and safe-stage rules.
- Create `core/factory_v2/repository.py` — transactional CRUD for batch/account/worker/job/checkpoint/resource rows.
- Create `core/factory_v2/identity.py` — deterministic-with-seed Vietnamese synthetic profile generation.
- Create `core/factory_v2/service.py` — batch creation and account-level controller service.
- Modify `core/db.py` — call Factory V2 schema initialization from `init_db()` without changing `connect()` semantics.
- Create `tests/test_factory_v2_schema.py`.
- Create `tests/test_factory_v2_state_machine.py`.
- Create `tests/test_factory_v2_identity.py`.
- Create `tests/test_factory_v2_service.py`.

### Task 1: Factory V2 schema and repository foundation

**Files:**
- Create: `core/factory_v2/schema.py`
- Create: `core/factory_v2/models.py`
- Create: `core/factory_v2/repository.py`
- Create: `tests/test_factory_v2_schema.py`

**Interfaces:**
- Produces: `ensure_schema(conn) -> None`
- Produces: `FactoryRepository(conn)`
- Produces: repository methods `create_batch(row)`, `get_batch(id)`, `insert_accounts(rows)`, `get_account(id)`, `list_accounts(batch_id)`, `update_account_stage(...)`, `insert_worker(row)`, `upsert_worker_heartbeat(...)`, `create_job_lease(...)`, `get_active_job_for_account(...)`, `create_checkpoint(...)`, `resolve_checkpoint(...)`, `insert_resource_sample(...)`.

- [ ] **Step 1: Write failing schema tests**

```python
# tests/test_factory_v2_schema.py
import sqlite3
import unittest

from core.factory_v2.schema import ensure_schema


class FactoryV2SchemaTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def tearDown(self):
        self.conn.close()

    def test_schema_creates_required_tables(self):
        ensure_schema(self.conn)
        names = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        self.assertTrue({
            "factory_batch", "factory_account", "factory_worker",
            "factory_job", "factory_checkpoint", "factory_resource_sample",
        } <= names)

    def test_one_active_job_per_account(self):
        ensure_schema(self.conn)
        indexes = {r[1] for r in self.conn.execute("PRAGMA index_list(factory_job)")}
        self.assertIn("uq_factory_job_active_account", indexes)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest tests.test_factory_v2_schema -v`

Expected: FAIL because `core.factory_v2.schema` does not exist.

- [ ] **Step 3: Implement schema and data models**

`models.py` must define these exact enums:

```python
from enum import StrEnum

class BatchStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

class AccountStage(StrEnum):
    NEW = "NEW"
    PROFILE_READY = "PROFILE_READY"
    AVD_ASSIGNED = "AVD_ASSIGNED"
    IG_READY_FOR_HUMAN = "IG_READY_FOR_HUMAN"
    WAITING_HUMAN = "WAITING_HUMAN"
    NEEDS_VERIFICATION = "NEEDS_VERIFICATION"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    USERNAME_UNAVAILABLE = "USERNAME_UNAVAILABLE"
    IG_CREATED = "IG_CREATED"
    THREADS_READY_FOR_HUMAN = "THREADS_READY_FOR_HUMAN"
    THREADS_CREATED = "THREADS_CREATED"
    ACP_CONNECTING = "ACP_CONNECTING"
    ACP_ACTIVE = "ACP_ACTIVE"
    COOLDOWN = "COOLDOWN"
    RETRY_PENDING = "RETRY_PENDING"
    ERROR = "ERROR"
    DISABLED = "DISABLED"

class WorkerState(StrEnum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_HUMAN = "WAITING_HUMAN"
    RECOVERING = "RECOVERING"
    DRAINING = "DRAINING"
    ERROR = "ERROR"
```

`schema.py` must create the six P0 tables from the approved spec with foreign keys and indexes. Enforce one active lease per account with a partial unique index:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_factory_job_active_account
ON factory_job(account_id)
WHERE state IN ('LEASED','RUNNING','WAITING_HUMAN','RECOVERING');
```

- [ ] **Step 4: Implement repository transaction boundaries**

Use existing `core.db.transaction(conn)` for multi-row writes. `create_job_lease()` must use `BEGIN IMMEDIATE` through that helper and must fail cleanly on the partial unique index instead of silently creating a second lease.

Return rows as plain dicts so Flask/API code does not depend on `sqlite3.Row`.

- [ ] **Step 5: Run schema tests**

Run: `python3 -m unittest tests.test_factory_v2_schema -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add core/factory_v2/schema.py core/factory_v2/models.py core/factory_v2/repository.py tests/test_factory_v2_schema.py
git commit -m "feat: add factory v2 controller schema"
```

### Task 2: Account state machine and safe-stage semantics

**Files:**
- Create: `core/factory_v2/state_machine.py`
- Create: `tests/test_factory_v2_state_machine.py`

**Interfaces:**
- Consumes: `AccountStage` from `models.py`.
- Produces: `can_transition(from_stage, to_stage) -> bool`
- Produces: `require_transition(from_stage, to_stage) -> None`
- Produces: `safe_stage_after_transition(previous_safe, new_stage) -> AccountStage`

- [ ] **Step 1: Write failing tests**

```python
from core.factory_v2.models import AccountStage as S
from core.factory_v2.state_machine import can_transition, safe_stage_after_transition


def test_happy_path_and_no_shortcut():
    assert can_transition(S.NEW, S.PROFILE_READY)
    assert can_transition(S.IG_CREATED, S.THREADS_READY_FOR_HUMAN)
    assert can_transition(S.THREADS_CREATED, S.ACP_CONNECTING)
    assert can_transition(S.ACP_CONNECTING, S.ACP_ACTIVE)
    assert not can_transition(S.NEW, S.ACP_ACTIVE)


def test_waiting_human_does_not_advance_safe_stage():
    assert safe_stage_after_transition(S.IG_CREATED, S.WAITING_HUMAN) == S.IG_CREATED
    assert safe_stage_after_transition(S.IG_CREATED, S.THREADS_CREATED) == S.THREADS_CREATED
```

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m unittest tests.test_factory_v2_state_machine -v`

Expected: FAIL because functions do not exist.

- [ ] **Step 3: Implement explicit transition table**

Use a single immutable mapping. Include happy-path transitions plus recovery transitions to `WAITING_HUMAN`, `NEEDS_CONFIRMATION`, `RETRY_PENDING`, and `ERROR`. Do not allow `CONTINUE` itself to move a state; API/service code must call a post-check result before invoking `require_transition`.

Safe stages must advance only on confirmed durable milestones: `PROFILE_READY`, `IG_CREATED`, `THREADS_CREATED`, `ACP_ACTIVE`. `AVD_ASSIGNED`, `*_READY_FOR_HUMAN`, `WAITING_HUMAN`, `NEEDS_*`, `ACP_CONNECTING`, `RETRY_PENDING`, and `ERROR` must preserve the previous safe stage.

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest tests.test_factory_v2_state_machine -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/factory_v2/state_machine.py tests/test_factory_v2_state_machine.py
git commit -m "feat: add factory v2 account state machine"
```

### Task 3: Vietnamese synthetic identity generator

**Files:**
- Create: `core/factory_v2/identity.py`
- Create: `tests/test_factory_v2_identity.py`

**Interfaces:**
- Produces: `generate_profiles(count: int = 50, seed: int | None = None) -> list[GeneratedProfile]`
- `GeneratedProfile` fields: `display_name`, `username`, `gender_profile`, `primary_niche`, `secondary_interest`, `personality_style`, `content_tone`, `bio`, `avatar_type`, `avatar_theme`, `avatar_prompt`.

- [ ] **Step 1: Write failing generator tests**

```python
from collections import Counter
from core.factory_v2.identity import generate_profiles


def test_default_batch_has_required_distribution():
    rows = generate_profiles(50, seed=17082026)
    assert len(rows) == 50
    assert len({r.username for r in rows}) == 50
    genders = Counter(r.gender_profile for r in rows)
    assert genders == {"female": 35, "male": 15}
    niches = Counter(r.primary_niche for r in rows)
    assert niches == {
        "beauty": 9, "fashion": 9, "tech": 8,
        "home": 8, "fitness": 8, "food": 8,
    }


def test_usernames_are_normalized_and_not_factory_sequences():
    rows = generate_profiles(50, seed=17082026)
    assert all(r.username == r.username.lower() for r in rows)
    assert all(" " not in r.username for r in rows)
    assert all(not r.username.startswith("acp") for r in rows)
    assert all(not r.username.startswith("user00") for r in rows)
```

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m unittest tests.test_factory_v2_identity -v`

Expected: FAIL because generator does not exist.

- [ ] **Step 3: Implement deterministic generator**

Use curated synthetic Vietnamese surname/given-name pools and `random.Random(seed)`. Normalize usernames with `unicodedata.normalize('NFD', value)` and strip combining marks. Generate candidates from patterns such as `given.surname`, `given.surname_initial`, `surname.given`, `full_name_compact`; score candidates by length, no digits, direct name match, and structural diversity against already-selected usernames.

For `count == 50`, enforce exactly the approved gender and niche counts. For other counts, scale proportions and assign any rounding remainder deterministically.

Use avatar mix target 60/40 by assigning exactly 30 `illustration` and 20 `object` for the default 50 batch.

- [ ] **Step 4: Add collision test**

Add a test that monkeypatches or supplies a reduced name pool so collisions occur, then asserts the generator selects another natural candidate before using digits.

- [ ] **Step 5: Run tests**

Run: `python3 -m unittest tests.test_factory_v2_identity -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add core/factory_v2/identity.py tests/test_factory_v2_identity.py
git commit -m "feat: generate Vietnamese factory profiles"
```

### Task 4: Batch service and authoritative account creation

**Files:**
- Create: `core/factory_v2/service.py`
- Create: `tests/test_factory_v2_service.py`

**Interfaces:**
- Consumes: `FactoryRepository`, `generate_profiles`, state-machine helpers.
- Produces: `FactoryService.create_batch(name: str, count: int = 50, seed: int | None = None) -> dict`
- Produces: `FactoryService.transition_account(account_id: str, to_stage: AccountStage, *, error_code: str | None = None, error_message: str | None = None) -> dict`
- Produces: `FactoryService.mark_postcheck_result(account_id: str, *, passed: bool, success_stage: AccountStage, failure_message: str) -> dict`

- [ ] **Step 1: Write failing service test**

```python
def test_create_batch_persists_50_profile_ready_accounts(self):
    batch = self.service.create_batch("Batch 01", seed=17082026)
    accounts = self.repo.list_accounts(batch["id"])
    self.assertEqual(50, len(accounts))
    self.assertTrue(all(a["stage"] == "PROFILE_READY" for a in accounts))
    self.assertEqual(50, len({a["username"] for a in accounts}))
```

Also assert that `last_safe_stage == PROFILE_READY` and no sensitive credential columns exist in the schema.

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m unittest tests.test_factory_v2_service -v`

Expected: FAIL because `FactoryService` does not exist.

- [ ] **Step 3: Implement batch creation transaction**

Create one `factory_batch` row and all account rows in one transaction. Account IDs use existing `core.db.ulid()`. Group numbers are `((sequence - 1) // 5) + 1`. Default batch status after generation is `READY`.

- [ ] **Step 4: Implement transition service**

Load account, call `require_transition`, derive safe stage with `safe_stage_after_transition`, then write both `stage` and `last_safe_stage` atomically. Error transitions must persist allowlisted error code/message only; never persist arbitrary provider response bodies.

- [ ] **Step 5: Run service tests**

Run: `python3 -m unittest tests.test_factory_v2_service -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add core/factory_v2/service.py tests/test_factory_v2_service.py
git commit -m "feat: add factory v2 batch service"
```

### Task 5: Integrate Factory V2 schema into ACP database initialization

**Files:**
- Modify: `core/db.py`
- Create: `core/factory_v2/__init__.py`
- Modify: `tests/test_factory_v2_schema.py`

**Interfaces:**
- Consumes: `core.factory_v2.schema.ensure_schema`.
- Produces: `core.db.init_db()` initializes existing ACP schema plus Factory V2 schema idempotently.

- [ ] **Step 1: Add failing idempotency test**

Create a temporary DB path, call the initialization sequence twice, then assert Factory V2 tables still exist and existing rows remain unchanged.

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m unittest tests.test_factory_v2_schema -v`

Expected: FAIL because `init_db()` does not initialize Factory V2 tables.

- [ ] **Step 3: Make the minimal integration**

At the end of `init_db()` after existing `SCHEMA` and migrations, import locally to avoid import cycles:

```python
from .factory_v2.schema import ensure_schema as ensure_factory_v2_schema
ensure_factory_v2_schema(conn)
```

Do not modify `connect()` to run DDL on every connection.

- [ ] **Step 4: Run the core P0 suite**

Run:

```bash
python3 -m unittest \
  tests.test_factory_v2_schema \
  tests.test_factory_v2_state_machine \
  tests.test_factory_v2_identity \
  tests.test_factory_v2_service -v
```

Expected: all PASS.

- [ ] **Step 5: Regression-check existing Account Factory OAuth tests**

Run: `python3 -m unittest tests.test_account_factory -v`

Expected: 4 existing tests PASS.

- [ ] **Step 6: Commit**

```bash
git add core/db.py core/factory_v2/__init__.py tests/test_factory_v2_schema.py
git commit -m "feat: initialize factory v2 schema"
```

## Completion Gate

This plan is complete only when the controller can create a persisted 50-account batch, reject illegal account transitions, preserve safe stages, restart against the same SQLite database without losing state, and all listed tests pass. Do not start AVD/process control until this gate is green.
