# Account Factory V2 P0 Workers and Adaptive AVD Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build independent AVD worker processes, heartbeat/recovery supervision, job leases, and adaptive worker scaling for Account Factory V2 P0.

**Architecture:** Controller owns queue and leases; each Android Studio AVD has one worker agent. Workers report observed state and never select their own account. Resource policy computes GREEN/YELLOW/RED from host metrics and the supervisor starts/drains workers conservatively.

**Tech Stack:** Python 3, Android SDK emulator/adb CLI, `subprocess`, `psutil` if already available or `/proc` + standard library fallback, existing Factory V2 repository/service, `unittest` with fakes.

## Global Constraints

- Android Studio AVD is the only P0 emulator backend.
- Phone never talks directly to ADB/emulator ports.
- ADB and emulator console remain local to Ubuntu.
- One account may have at most one active job lease.
- `WAITING_HUMAN` AVDs still count against RAM/capacity.
- Default resource policy: GREEN CPU <65% and RAM available >6 GB with near-zero swap pressure; YELLOW CPU 65–85% or RAM 3–6 GB; RED CPU >85% or RAM <3 GB or sustained swap pressure.
- Scale decisions use a 30–60 second rolling/stability window.
- Normal scale-up starts at most one AVD at a time; maximum concurrent STARTING defaults to 1.
- Emergency threshold includes RAM available below approximately 1.5 GB.
- Human verification is not automated; worker pauses and reports a checkpoint.

---

## File Structure

- Create `core/factory_v2/resource_policy.py` — pure capacity classification and desired-worker calculation.
- Create `core/factory_v2/host_metrics.py` — host CPU/RAM/swap/load sampling.
- Create `core/factory_v2/avd.py` — AVD discovery/start/stop/readiness/ADB helpers.
- Create `core/factory_v2/worker_protocol.py` — dataclasses for worker command/heartbeat/observed state.
- Create `core/factory_v2/scheduler.py` — lease assignment and READY-worker scheduling.
- Create `core/factory_v2/supervisor.py` — worker process lifecycle/recovery/drain.
- Create `workers/account_factory_worker.py` — standalone worker agent entrypoint.
- Create tests `tests/test_factory_v2_resource_policy.py`, `tests/test_factory_v2_scheduler.py`, `tests/test_factory_v2_avd.py`, `tests/test_factory_v2_supervisor.py`.

### Task 1: Pure resource policy

**Files:**
- Create: `core/factory_v2/resource_policy.py`
- Create: `tests/test_factory_v2_resource_policy.py`

**Interfaces:**
- Produces `HostSample(cpu_percent, ram_available_mb, swap_used_mb, swap_in_rate, load_1m, load_5m)`.
- Produces `CapacityState` enum `GREEN|YELLOW|RED|EMERGENCY`.
- Produces `classify_capacity(sample) -> CapacityState`.
- Produces `next_worker_target(current_total, waiting_human, stable_state, learned_avd_ram_mb) -> int`.

- [ ] **Step 1: Write failing policy tests**

```python
from core.factory_v2.resource_policy import HostSample, CapacityState, classify_capacity


def test_capacity_thresholds():
    assert classify_capacity(HostSample(40, 8192, 0, 0, 1, 1)) == CapacityState.GREEN
    assert classify_capacity(HostSample(70, 5000, 0, 0, 1, 1)) == CapacityState.YELLOW
    assert classify_capacity(HostSample(90, 2500, 0, 0, 4, 4)) == CapacityState.RED
    assert classify_capacity(HostSample(40, 1200, 0, 0, 1, 1)) == CapacityState.EMERGENCY
```

Also test that `waiting_human >= min(3, ceil(active_pool * 0.4))` blocks scale-up.

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m unittest tests.test_factory_v2_resource_policy -v`

Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement policy as pure functions**

No subprocess calls in this file. Make thresholds a dataclass `ResourceThresholds` with exact defaults from the spec. `next_worker_target` may return only `current`, `current + 1`, or a lower drain target; never jump by multiple workers on normal GREEN scale-up.

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest tests.test_factory_v2_resource_policy -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/factory_v2/resource_policy.py tests/test_factory_v2_resource_policy.py
git commit -m "feat: add adaptive avd resource policy"
```

### Task 2: AVD/ADB adapter with fakeable command runner

**Files:**
- Create: `core/factory_v2/avd.py`
- Create: `tests/test_factory_v2_avd.py`

**Interfaces:**
- Produces `CommandRunner.run(argv: list[str], timeout: int) -> CompletedCommand`.
- Produces `AvdManager.list_avds() -> list[str]`.
- Produces `AvdManager.list_online_devices() -> list[str]`.
- Produces `AvdManager.start(avd_name: str, port: int) -> subprocess.Popen`.
- Produces `AvdManager.is_boot_completed(serial: str) -> bool`.
- Produces `AvdManager.stop(serial: str) -> None`.
- Produces `AvdManager.open_url(serial: str, url: str) -> None` and `open_package(serial, package)` for safe navigation only.

- [ ] **Step 1: Write failing parser tests**

```python
def test_parse_adb_devices_ignores_offline():
    runner = FakeRunner({("adb", "devices"): "List of devices attached\nemulator-5554\tdevice\nemulator-5556\toffline\n"})
    manager = AvdManager(runner=runner)
    assert manager.list_online_devices() == ["emulator-5554"]
```

Also test `is_boot_completed` only returns true when `adb -s SERIAL shell getprop sys.boot_completed` returns exactly `1` after stripping.

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m unittest tests.test_factory_v2_avd -v`

Expected: FAIL.

- [ ] **Step 3: Implement adapter**

Resolve emulator path from `$ANDROID_HOME/emulator/emulator`, then PATH fallback. Resolve adb from `$ANDROID_HOME/platform-tools/adb`, then PATH fallback. `start()` command must include the chosen AVD name and explicit port; do not add fingerprint spoofing, proxy rotation, or security-bypass flags.

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest tests.test_factory_v2_avd -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/factory_v2/avd.py tests/test_factory_v2_avd.py
git commit -m "feat: add android avd adapter"
```

### Task 3: Lease scheduler

**Files:**
- Create: `core/factory_v2/scheduler.py`
- Create: `tests/test_factory_v2_scheduler.py`

**Interfaces:**
- Consumes `FactoryRepository`, `FactoryService`, `WorkerState`, `AccountStage`.
- Produces `Scheduler.assign_next(worker_id: str) -> dict | None`.
- Produces `Scheduler.release_job(job_id: str, final_state: str) -> None`.
- Produces `Scheduler.reconcile_expired_leases(now_iso: str) -> list[str]`.

- [ ] **Step 1: Write failing lease test**

```python
def test_two_workers_cannot_receive_same_account(self):
    first = self.scheduler.assign_next("worker-01")
    second = self.scheduler.assign_next("worker-02")
    self.assertNotEqual(first["account_id"], second["account_id"])
    active = self.repo.get_active_job_for_account(first["account_id"])
    self.assertEqual("worker-01", active["worker_id"])
```

Add a test where an expired lease with a live heartbeat is not blindly reassigned; it must enter reconciliation first.

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m unittest tests.test_factory_v2_scheduler -v`

Expected: FAIL.

- [ ] **Step 3: Implement assignment transaction**

Eligible accounts are non-terminal batch accounts whose stage is schedulable and which have no active lease. Eligible workers must be `READY` and not draining. In one transaction create job, set account `assigned_worker_id/current_job_id`, and set worker current references/state.

- [ ] **Step 4: Implement release/reconciliation**

On successful completion release references and return worker to READY. On unknown worker/AVD disappearance preserve account safe stage and set job/account to `RETRY_PENDING` or `NEEDS_CONFIRMATION` according to whether the crash occurred during a human checkpoint.

- [ ] **Step 5: Run tests and commit**

Run: `python3 -m unittest tests.test_factory_v2_scheduler -v`

Expected: PASS.

```bash
git add core/factory_v2/scheduler.py tests/test_factory_v2_scheduler.py
git commit -m "feat: add factory v2 lease scheduler"
```

### Task 4: Worker protocol and standalone worker agent

**Files:**
- Create: `core/factory_v2/worker_protocol.py`
- Create: `workers/account_factory_worker.py`
- Create: `tests/test_factory_v2_supervisor.py`

**Interfaces:**
- Produces `WorkerCommand(command_id, action, account_id, payload)`.
- Produces `WorkerHeartbeat(worker_id, adb_serial, state, current_account_id, current_job_id, observed_state, last_progress_at)`.
- Worker process accepts controller-provided worker id, AVD name, serial, and controller local endpoint/IPC parameters.

- [ ] **Step 1: Write failing idempotency test**

Test a pure `CommandLedger` helper so executing the same `command_id` twice returns the stored result the second time and does not run the action again.

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m unittest tests.test_factory_v2_supervisor.WorkerProtocolTests -v`

Expected: FAIL.

- [ ] **Step 3: Implement protocol and minimal worker loop**

P0 worker actions are limited to safe orchestration primitives: heartbeat, report foreground package, open official app/URL, prepare clipboard text owned by ACP, report `WAITING_HUMAN`, and report post-check observations. Do not implement CAPTCHA/OTP solving, auto-submit security checks, anti-detection, or credential capture.

- [ ] **Step 4: Run protocol tests and commit**

```bash
python3 -m unittest tests.test_factory_v2_supervisor.WorkerProtocolTests -v
git add core/factory_v2/worker_protocol.py workers/account_factory_worker.py tests/test_factory_v2_supervisor.py
git commit -m "feat: add factory v2 worker protocol"
```

### Task 5: Worker supervisor and recovery

**Files:**
- Create: `core/factory_v2/host_metrics.py`
- Create: `core/factory_v2/supervisor.py`
- Modify: `tests/test_factory_v2_supervisor.py`

**Interfaces:**
- Produces `HostMetricsSampler.sample() -> HostSample`.
- Produces `WorkerSupervisor.tick() -> SupervisorDecision`.
- Produces `WorkerSupervisor.reconcile_on_boot() -> None`.

- [ ] **Step 1: Write failing recovery tests**

```python
def test_missing_heartbeat_moves_worker_to_recovering_not_ready(self):
    self.repo.insert_worker(worker_row("worker-03", state="RUNNING", account_id="a17"))
    self.supervisor.reconcile_missing_heartbeat("worker-03")
    worker = self.repo.get_worker("worker-03")
    self.assertEqual("RECOVERING", worker["state"])
    self.assertEqual("a17", worker["current_account_id"])
```

Add tests for: READY worker drained first on RED; WAITING_HUMAN worker preserved in EMERGENCY when possible; boot retry stops after 3 attempts and marks ERROR.

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m unittest tests.test_factory_v2_supervisor -v`

Expected: FAIL.

- [ ] **Step 3: Implement host sampling and supervisor tick**

Sample CPU/RAM/swap/load, persist `factory_resource_sample`, classify capacity, then decide only one structural pool change per tick. Boot one worker, hold, drain one, or enter emergency hold. Persist every worker state change before issuing the subprocess action so restart reconciliation has a durable intent.

- [ ] **Step 4: Implement restart reconciliation**

On Controller boot, discover online emulator serials and configured `acp-worker-*` AVDs, match them to persisted workers, validate current leases, and preserve ambiguous human-checkpoint accounts as `NEEDS_CONFIRMATION` instead of marking success.

- [ ] **Step 5: Run worker suite**

```bash
python3 -m unittest \
  tests.test_factory_v2_resource_policy \
  tests.test_factory_v2_avd \
  tests.test_factory_v2_scheduler \
  tests.test_factory_v2_supervisor -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add core/factory_v2/host_metrics.py core/factory_v2/supervisor.py tests/test_factory_v2_supervisor.py
git commit -m "feat: supervise adaptive factory avd workers"
```

## Completion Gate

This plan is complete when fake-worker tests prove lease exclusivity, resource policy, heartbeat recovery, and drain behavior; then one real AVD must be discoverable, boot to `READY`, send heartbeat, enter `WAITING_HUMAN` without stopping other fake/real workers, and drain safely. Do not proceed to full batch testing until these gates pass.
