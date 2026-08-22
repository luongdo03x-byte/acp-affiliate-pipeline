# Portable Single-Machine Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one trusted Ubuntu machine hand Account Factory state to another through a private GitHub Release so the receiving machine can `git clone`/`git pull` and run `./setup.sh` without re-entering `ACP_MASTER_KEY`, provider secrets, or stored account passwords.

**Architecture:** Keep source code in Git, durable runtime state in immutable generation assets on a private GitHub Release, and machine runtime (`.venv`, AVD process, worker/browser process) disposable. `manage.sh handoff-out` quiesces the runtime, snapshots SQLite safely, packages `shared/.env.local` + DB + avatars, uploads and verifies a generation, then marks the source machine `HANDED_OFF`. `setup.sh` bootstraps Python, downloads and validates the newest generation, restores shared state, normalizes machine-local paths, runs a fail-closed doctor, claims ownership, and starts the dedicated Account Factory service only after bounded stale-state reconciliation.

**Tech Stack:** Python 3.12, stdlib `sqlite3`/`tarfile`/`hashlib`/`json`/`pathlib`, Bash, GitHub CLI `gh`, existing Flask Account Factory launcher, existing AVD/worker supervisor.

**Spec:** `docs/superpowers/specs/2026-08-22-portable-single-machine-handoff-design.md`

## Global Constraints

- Deployment model is one operator, two trusted Ubuntu machines, exactly one active machine at a time.
- Private GitHub authentication is the only access boundary for portable state; there is no second encryption layer around the release asset.
- Never commit real `.env.local`, SQLite DBs, passwords, OAuth tokens, callback query strings, or provider secrets into the Git tree.
- Release tag is exactly `acp-portable-state`.
- State asset names are exactly `acp-state-gNNNNNN.tar.gz` and generations are monotonic.
- `ACP_MASTER_KEY` and all non-path secrets restored from `.env.local` must remain byte-for-byte unchanged.
- Normalize only machine-local paths, specifically `ACP_DB=${ACP_BASE}/shared/var/acp-live.db` and `ACP_AVATAR_DIR=${ACP_BASE}/shared/avatars`.
- SQLite snapshot must use `sqlite3.Connection.backup()` and pass `PRAGMA integrity_check` plus `PRAGMA foreign_key_check` before upload/restore.
- Unknown bundle format versions, unsafe tar paths, checksum mismatch, DB corruption, stale remote generation, bad credential decrypt, missing callback prerequisites, or ownership mismatch fail closed.
- Chrome first-run/Terms, OAuth consent, OTP, CAPTCHA, identity/security challenges remain human-only.
- Portability code never marks Instagram, Threads, or OAuth successful by inference from emulator UI.
- `CREDENTIAL_DECRYPT_FAILED` remains terminal for the current job attempt; no repeated `START_ACP`/Chrome loop.
- Do not start an unbounded controller loop until restore, doctor, and stale-state reconciliation complete.

---

## File Structure

Create focused modules instead of placing portability logic into `manage.sh`:

- `core/factory_v2/portable_state.py` — machine ownership file, generation parsing, SQLite snapshot/integrity, manifest/checksums, safe tar validation/build/restore, portable env path normalization.
- `core/factory_v2/portable_release.py` — `gh`-backed private Release discovery/create/upload/download/verification; command runner is injectable for unit tests.
- `core/factory_v2/portable_doctor.py` — readiness checks that return sanitized check results and never expose secret values.
- `core/factory_v2/portable_resume.py` — bounded reconciliation before the dedicated controller starts; uses existing scheduler/activation semantics only.
- `core/factory_v2/portable_cli.py` — thin CLI used by Bash: `handoff-out`, `handoff-in`, `doctor`, `resume`.
- `setup.sh` — first-clone/later-switch one-command bootstrap and handoff-in entry point.
- `manage.sh` — add `handoff-out`, `factory-start`, `factory-stop`, factory PID tracking, and ownership guard on runtime starts.
- `account_factory_server.py` — call the same ownership guard before starting a controller when invoked directly.
- `tests/test_factory_v2_portable_state.py` — archive/snapshot/restore/ownership tests.
- `tests/test_factory_v2_portable_release.py` — fake-`gh` transport tests.
- `tests/test_factory_v2_portable_doctor.py` — sanitized readiness tests.
- `tests/test_factory_v2_portable_resume.py` — durable-state reconciliation tests.
- `tests/test_factory_v2_portable_cli.py` — CLI orchestration tests with fake transport/runtime hooks.
- `tests/test_factory_v2_launcher.py` — direct launcher ownership regression.
- `.github/workflows/account-factory-ci.yml` — include new portability tests and shell syntax checks.
- `README.md` and `docs/ACP_RUNBOOK.md` — document weekday/weekend switch workflow and explicit security trade-off.

---

### Task 1: Portable state primitives and ownership guard

**Files:**
- Create: `core/factory_v2/portable_state.py`
- Create: `tests/test_factory_v2_portable_state.py`

**Interfaces:**
- Produces: `generation_from_asset(name: str) -> int | None`
- Produces: `next_generation(remote_names: list[str], local_generation: int) -> int`
- Produces: `MachineState(machine_id: str, last_imported_generation: int, ownership: str)`
- Produces: `load_machine_state(path: Path) -> MachineState | None`
- Produces: `write_machine_state(path: Path, state: MachineState) -> None`
- Produces: `require_active_ownership(path: Path) -> MachineState`
- Produces: `snapshot_sqlite(source: Path, destination: Path) -> None`
- Produces: `validate_sqlite(path: Path) -> None`
- Produces: `build_bundle(..., generation: int, ...) -> Path`
- Produces: `validate_bundle(archive: Path, expected_generation: int) -> dict`
- Produces: `restore_bundle(archive: Path, base: Path, expected_generation: int) -> Path`
- Produces: `normalize_portable_env(env_path: Path, base: Path) -> None`

- [ ] **Step 1: Write failing generation and ownership tests**

```python
class PortableStateTests(unittest.TestCase):
    def test_generation_parser_accepts_only_contract_name(self):
        self.assertEqual(42, generation_from_asset("acp-state-g000042.tar.gz"))
        self.assertIsNone(generation_from_asset("state-latest.tar.gz"))
        self.assertIsNone(generation_from_asset("acp-state-g42.tar.gz"))

    def test_handed_off_machine_cannot_start(self):
        write_machine_state(self.machine_file, MachineState("m1", 7, "HANDED_OFF"))
        with self.assertRaisesRegex(RuntimeError, "MACHINE_HANDED_OFF"):
            require_active_ownership(self.machine_file)
```

- [ ] **Step 2: Run RED**

Run:
```bash
./.venv/bin/python -m unittest tests.test_factory_v2_portable_state -v
```
Expected: import/module failure because `portable_state.py` does not exist.

- [ ] **Step 3: Implement generation + machine state minimally**

```python
_ASSET_RE = re.compile(r"^acp-state-g([0-9]{6})\.tar\.gz$")
_ALLOWED_OWNERSHIP = {"ACTIVE", "HANDED_OFF"}

@dataclass(frozen=True)
class MachineState:
    machine_id: str
    last_imported_generation: int
    ownership: str


def generation_from_asset(name: str) -> int | None:
    match = _ASSET_RE.fullmatch(str(name or ""))
    return int(match.group(1)) if match else None


def require_active_ownership(path: Path) -> MachineState:
    state = load_machine_state(path)
    if state is None or state.ownership != "ACTIVE":
        raise RuntimeError("MACHINE_HANDED_OFF")
    return state
```

- [ ] **Step 4: Add RED snapshot/integrity tests**

Create a WAL-capable SQLite fixture, insert a row, call `snapshot_sqlite()`, then assert the copied DB contains the row and `validate_sqlite()` passes. Corrupt a second snapshot with bytes and assert `RuntimeError("SQLITE_INTEGRITY_FAILED")`.

- [ ] **Step 5: Implement snapshot and DB validation**

```python
def snapshot_sqlite(source: Path, destination: Path) -> None:
    src = sqlite3.connect(str(source))
    dst = sqlite3.connect(str(destination))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    validate_sqlite(destination)


def validate_sqlite(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign = conn.execute("PRAGMA foreign_key_check").fetchall()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError("SQLITE_INTEGRITY_FAILED") from exc
    finally:
        conn.close()
    if integrity != "ok" or foreign:
        raise RuntimeError("SQLITE_INTEGRITY_FAILED")
```

- [ ] **Step 6: Add RED archive traversal/checksum/env-normalization tests**

Tests must cover: absolute tar member, `../` member, unknown top-level member, manifest generation mismatch, checksum mismatch, and a source env containing `/home/source/.../acp-live.db` while `ACP_MASTER_KEY=unchanged-secret` remains unchanged after normalization.

- [ ] **Step 7: Implement safe build/validate/restore**

Implementation must inspect `TarInfo.name` with `PurePosixPath`, allow only `state/manifest.json`, `state/checksums.sha256`, `state/shared/.env.local`, `state/shared/var/acp-live.db`, and descendants of `state/shared/avatars/`, reject links/devices, extract only after validation, verify SHA-256, validate SQLite before live replacement, then atomically `os.replace()` env/DB and replace avatar tree from staging.

`normalize_portable_env()` rewrites only exact `ACP_DB=` and `ACP_AVATAR_DIR=` lines and preserves all other lines byte-for-byte apart from final newline normalization.

- [ ] **Step 8: Run GREEN and commit**

```bash
./.venv/bin/python -m unittest tests.test_factory_v2_portable_state -v
git add core/factory_v2/portable_state.py tests/test_factory_v2_portable_state.py
git commit -m "feat: add portable state primitives"
```

---

### Task 2: Private GitHub Release transport

**Files:**
- Create: `core/factory_v2/portable_release.py`
- Create: `tests/test_factory_v2_portable_release.py`

**Interfaces:**
- Consumes: `generation_from_asset()` from Task 1.
- Produces: `CommandResult(returncode: int, stdout: str, stderr: str)`
- Produces: `GitHubReleaseTransport(repo: str, runner=...)`
- Produces methods: `assert_authenticated()`, `list_assets() -> list[dict]`, `ensure_release()`, `upload(path: Path)`, `download_generation(generation: int, destination: Path) -> Path`, `verify_remote_asset(path: Path)`, `prune_keep_latest(keep: int = 5)`.

- [ ] **Step 1: Write fake-runner RED tests**

```python
def test_list_assets_filters_generation_assets(self):
    runner = FakeRunner({("gh", "release", "view", "acp-portable-state", "--repo", "o/r", "--json", "assets"):
        CommandResult(0, '{"assets":[{"name":"acp-state-g000002.tar.gz","size":10},{"name":"notes.txt","size":1}]}', "")})
    transport = GitHubReleaseTransport("o/r", runner=runner)
    self.assertEqual(["acp-state-g000002.tar.gz"], [x["name"] for x in transport.list_assets()])
```

Also test auth failure returns domain error `GITHUB_AUTH_REQUIRED`, release 404 triggers `gh release create acp-portable-state --repo ... --title ...`, upload never uses `--clobber`, and verify requires exact remote size match.

- [ ] **Step 2: Run RED**

```bash
./.venv/bin/python -m unittest tests.test_factory_v2_portable_release -v
```

- [ ] **Step 3: Implement `gh` transport with no secret output**

Use argv lists only; no shell interpolation. `assert_authenticated()` uses `gh auth status --hostname github.com`. `list_assets()` uses `gh release view ... --json assets`. `upload()` uses `gh release upload <tag> <path> --repo <repo>`. `download_generation()` uses `gh release download <tag> --pattern <exact-name> --dir <dest> --repo <repo>`.

Errors are normalized to short domain messages; do not include command stdout/stderr in messages returned to operator when it could contain URLs or auth metadata.

- [ ] **Step 4: Run GREEN and commit**

```bash
./.venv/bin/python -m unittest tests.test_factory_v2_portable_release -v
git add core/factory_v2/portable_release.py tests/test_factory_v2_portable_release.py
git commit -m "feat: add private release state transport"
```

---

### Task 3: Portable doctor

**Files:**
- Create: `core/factory_v2/portable_doctor.py`
- Create: `tests/test_factory_v2_portable_doctor.py`

**Interfaces:**
- Consumes: `validate_sqlite()`, `load_machine_state()`, `get_account_password()`, `AvdManager`.
- Produces: `DoctorCheck(name: str, ok: bool, code: str)`.
- Produces: `run_portable_doctor(base: Path, repo_root: Path, *, avd=None, http_probe=None) -> list[DoctorCheck]`.
- Produces: `require_portable_doctor(...) -> None` raising `RuntimeError("PORTABLE_DOCTOR_FAILED:<code>")` on the first failed required check.

- [ ] **Step 1: Write RED tests for sanitized credential check**

Create an in-memory/live temp DB containing one encrypted factory credential. With the matching master key assert `CREDENTIAL_DECRYPT=OK`; with a different key assert the returned check code is exactly `CREDENTIAL_DECRYPT_FAILED` and `repr(checks)` contains neither key nor password.

- [ ] **Step 2: Add required-check tests**

Cover `.env.local` mode not `0600`, missing `ACP_MASTER_KEY`, missing AVD, unbooted serial, invalid SQLite, ownership not ACTIVE, and callback probe failure when an ACP-active-mode account is at/after `THREADS_CREATED` and needs OAuth.

- [ ] **Step 3: Implement doctor**

Read env values without printing them. Use `core.factory_v2.schema.ensure_schema()` only after SQLite integrity passes. For credential proof, query one `factory_account_credential.account_id`; call `get_account_password()` and immediately discard the plaintext. Callback probe should request only the configured callback base/route and store HTTP success/failure, never echo query strings.

- [ ] **Step 4: Run GREEN and commit**

```bash
./.venv/bin/python -m unittest tests.test_factory_v2_portable_doctor -v
git add core/factory_v2/portable_doctor.py tests/test_factory_v2_portable_doctor.py
git commit -m "feat: add portable machine doctor"
```

---

### Task 4: Bounded resume reconciliation

**Files:**
- Create: `core/factory_v2/portable_resume.py`
- Create: `tests/test_factory_v2_portable_resume.py`
- Modify: `tests/test_factory_v2_scheduler.py`

**Interfaces:**
- Consumes existing `FactoryRepository`, `FactoryService`, `Scheduler`, `FactoryActivationService`, and `build_default_runtime()` behavior.
- Produces: `reconcile_for_portable_resume(conn, now_iso: str) -> dict[str, int | str]`.

- [ ] **Step 1: Write RED expired-lease/OAuth tests**

Test a dead `RECOVERING` job with expired lease becomes released through existing scheduler semantics and preserves the prior durable `last_safe_stage`. Test expired `WAITING_AUTH` in `ACP_CONNECTING` reconciles through `FactoryActivationService.reconcile()` to `RETRY_PENDING/OAUTH_FAILED`, not raw SQL stage mutation.

- [ ] **Step 2: Write RED schedulability tests**

Extend scheduler regression so:

```python
# approved durable retry
stage="RETRY_PENDING"; last_safe_stage="THREADS_CREATED"; last_error_code=None
# => START_ACP

# gated OAuth failure
stage="RETRY_PENDING"; last_safe_stage="THREADS_CREATED"; last_error_code="OAUTH_FAILED"
# => no assignment
```

- [ ] **Step 3: Implement bounded reconciliation**

`reconcile_for_portable_resume()` performs only:

1. `Scheduler.reconcile_expired_leases(now_iso)`;
2. for accounts currently `ACP_CONNECTING` with an OAuth session, call `FactoryActivationService.reconcile(account_id)` once;
3. return sanitized counts (`leases_reconciled`, `oauth_reconciled`, `oauth_gated`) and never call `runtime.run_forever()`.

Do not clear `OAUTH_FAILED`; explicit retry remains operator-owned.

- [ ] **Step 4: Run GREEN and commit**

```bash
ACP_DEFAULT_ACCOUNT_PASSWORD='test-only-password' ./.venv/bin/python -m unittest \
  tests.test_factory_v2_portable_resume \
  tests.test_factory_v2_scheduler \
  tests.test_factory_v2_activation -v
git add core/factory_v2/portable_resume.py tests/test_factory_v2_portable_resume.py tests/test_factory_v2_scheduler.py
git commit -m "feat: reconcile imported factory state"
```

---

### Task 5: Portable CLI orchestration

**Files:**
- Create: `core/factory_v2/portable_cli.py`
- Create: `tests/test_factory_v2_portable_cli.py`

**Interfaces:**
- Consumes Tasks 1-4.
- Produces CLI:
  - `python -m core.factory_v2.portable_cli handoff-out --base PATH --repo OWNER/REPO --git-commit SHA --git-branch BRANCH`
  - `python -m core.factory_v2.portable_cli handoff-in --base PATH --repo OWNER/REPO`
  - `python -m core.factory_v2.portable_cli doctor --base PATH --repo-root PATH`
  - `python -m core.factory_v2.portable_cli resume --base PATH`

- [ ] **Step 1: RED handoff-out orchestration test**

Inject fake release transport and temp base. Seed `machine.json` ACTIVE and a valid DB/env/avatar. Assert successful handoff uploads exactly one `acp-state-g000001.tar.gz`, verifies it, and only then writes ownership `HANDED_OFF`.

Add failure test where upload verification raises: machine ownership must remain `ACTIVE` and no `HANDOFF_OK` is emitted.

- [ ] **Step 2: RED handoff-in orchestration test**

Fake remote generations `g000004`, `g000005`; assert g5 is selected, validated/restored, machine state becomes ACTIVE generation 5. If local generation is 6, assert `REMOTE_STATE_OLDER_THAN_LOCAL` and live DB is untouched.

- [ ] **Step 3: Implement CLI with dependency-injection entry functions**

Keep `main(argv=None)` thin. Implement testable functions `handoff_out(...)`, `handoff_in(...)`, `doctor(...)`, and `resume(...)`. Print only stable status lines such as `HANDOFF_OK generation=5`, `IMPORT_OK generation=5`, `DOCTOR_OK`, `RESUME_RECONCILED`; never dump manifests/env/URLs.

- [ ] **Step 4: Run GREEN and commit**

```bash
./.venv/bin/python -m unittest tests.test_factory_v2_portable_cli -v
git add core/factory_v2/portable_cli.py tests/test_factory_v2_portable_cli.py
git commit -m "feat: add portable handoff cli"
```

---

### Task 6: `manage.sh` handoff and dedicated factory lifecycle

**Files:**
- Modify: `manage.sh`
- Create: `tests/test_factory_v2_manage_portable.py`
- Modify: `account_factory_server.py`
- Modify: `tests/test_factory_v2_launcher.py`

**Interfaces:**
- `manage.sh handoff-out`
- `manage.sh factory-start`
- `manage.sh factory-stop`
- `manage.sh status` includes Account Factory controller status.
- Direct `account_factory_server.py` start checks local ownership before starting its controller.

- [ ] **Step 1: Write shell-wrapper RED tests**

From Python `subprocess`, run `bash manage.sh` against a temp `ACP_BASE` and fake executables placed first in `PATH`. Assert usage exposes `handoff-out`, `factory-start`, `factory-stop`. Assert `factory-start` refuses `machine.json` ownership `HANDED_OFF` before launching Python.

- [ ] **Step 2: Add launcher RED ownership test**

Patch ownership guard to raise `MACHINE_HANDED_OFF`; verify `build_app(start_controller=True, ...)` does not construct/start runtime and surfaces the failure before controller thread creation.

- [ ] **Step 3: Implement lifecycle**

Add `FACTORY_PID="$RUN_DIR/account-factory.pid"`. `factory-start` loads env, checks ownership, and runs:

```bash
nohup "$release/.venv/bin/python" "$release/account_factory_server.py" \
  >>"$LOG_DIR/account-factory.log" 2>&1 &
```

Use existing PID matching/stop helpers. `handoff-out` performs preconditions, calls `factory-stop` and existing `cmd_stop`, verifies no matching controller/worker child processes remain, runs bounded portable `resume` reconciliation, then calls portable CLI `handoff-out`.

Do not stop the emulator merely to snapshot; worker/controller must be quiescent.

- [ ] **Step 4: Run GREEN and commit**

```bash
./.venv/bin/python -m unittest \
  tests.test_factory_v2_manage_portable \
  tests.test_factory_v2_launcher -v
bash -n manage.sh
git add manage.sh account_factory_server.py tests/test_factory_v2_manage_portable.py tests/test_factory_v2_launcher.py
git commit -m "feat: add single-machine handoff lifecycle"
```

---

### Task 7: One-command `setup.sh` restore, doctor, and resume

**Files:**
- Create: `setup.sh`
- Create: `tests/test_factory_v2_portable_setup.py`
- Modify: `core/factory_v2/avd.py` only if a deterministic `wait_until_booted()` helper is required by the tests; otherwise reuse existing methods.

**Interfaces:**
- Operator entry point: `./setup.sh`

- [ ] **Step 1: Write RED first-clone test using fakes**

Run `setup.sh` with temp `ACP_BASE`, fake `gh`, fake `adb`, fake `emulator`, and a repo-local fake Python hook. Assert ordering recorded by fakes is:

```text
bootstrap venv
-> handoff-in
-> manage.sh setup
-> doctor
-> resume
-> factory-start
```

Assert `handoff-in` occurs before any code path that could generate a fresh `ACP_MASTER_KEY`.

- [ ] **Step 2: Write RED idempotent same-generation test**

When machine state is ACTIVE generation 8 and remote newest is generation 8, assert bundle restoration is skipped but doctor/resume/factory-start still execute. When doctor fails, assert `factory-start` is not called.

- [ ] **Step 3: Implement `setup.sh`**

Required sequence:

```bash
set -Eeuo pipefail
BASE="${ACP_BASE:-$HOME/Downloads/ACP}"
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
python3 -m venv "$REPO_ROOT/.venv"   # only if absent
"$REPO_ROOT/.venv/bin/python" -m pip install --disable-pip-version-check -r "$REPO_ROOT/requirements.txt"
"$REPO_ROOT/.venv/bin/python" -m core.factory_v2.portable_cli handoff-in --base "$BASE" --repo "luongdo03x-byte/acp-affiliate-pipeline"
ACP_BASE="$BASE" "$REPO_ROOT/manage.sh" setup
# source restored env without echoing values
"$REPO_ROOT/.venv/bin/python" -m core.factory_v2.portable_cli doctor --base "$BASE" --repo-root "$REPO_ROOT"
"$REPO_ROOT/.venv/bin/python" -m core.factory_v2.portable_cli resume --base "$BASE"
ACP_BASE="$BASE" "$BASE/manage.sh" factory-start
```

Before doctor, ensure `acp-worker-01` exists; if emulator tooling exists but the AVD is absent, stop with a precise prerequisite message unless the configured Android system image can be created non-interactively. Do not auto-accept Android licenses or change host security settings.

- [ ] **Step 4: Run GREEN and commit**

```bash
./.venv/bin/python -m unittest tests.test_factory_v2_portable_setup -v
bash -n setup.sh
chmod +x setup.sh
git add setup.sh tests/test_factory_v2_portable_setup.py core/factory_v2/avd.py
git commit -m "feat: add one-command portable setup"
```

---

### Task 8: End-to-end regression, CI, and operator docs

**Files:**
- Modify: `.github/workflows/account-factory-ci.yml`
- Modify: `README.md`
- Modify: `docs/ACP_RUNBOOK.md`

**Interfaces:**
- CI must run all new portability tests without live GitHub/ADB/network dependencies.
- Docs expose exactly the weekday/weekend commands.

- [ ] **Step 1: Update CI paths and backend command**

Add `setup.sh`, `manage.sh`, and `tests/test_factory_v2_portable_*.py` to PR path filters. Add these modules to the unittest command:

```text
tests.test_factory_v2_portable_state
tests.test_factory_v2_portable_release
tests.test_factory_v2_portable_doctor
tests.test_factory_v2_portable_resume
tests.test_factory_v2_portable_cli
tests.test_factory_v2_manage_portable
tests.test_factory_v2_portable_setup
```

Add a shell syntax step:

```bash
bash -n manage.sh
bash -n setup.sh
```

- [ ] **Step 2: Document final operator workflow**

README/runbook must show only:

```bash
# leave active machine
./manage.sh handoff-out

# first use on other machine
git clone -b feat/account-factory-android git@github.com:luongdo03x-byte/acp-affiliate-pipeline.git
cd acp-affiliate-pipeline
./setup.sh

# later switches
git pull --ff-only
./setup.sh
```

State explicitly that private Release assets contain plaintext `.env.local` inside the tar and anyone with private repository release-download access can retrieve those secrets. Recommend GitHub 2FA/passkey. State that Chrome Terms/OAuth consent/security challenges can still require human interaction.

- [ ] **Step 3: Run focused full portability regression**

```bash
ACP_DEFAULT_ACCOUNT_PASSWORD='test-only-password' ./.venv/bin/python -m unittest \
  tests.test_factory_v2_portable_state \
  tests.test_factory_v2_portable_release \
  tests.test_factory_v2_portable_doctor \
  tests.test_factory_v2_portable_resume \
  tests.test_factory_v2_portable_cli \
  tests.test_factory_v2_manage_portable \
  tests.test_factory_v2_portable_setup \
  tests.test_factory_v2_scheduler \
  tests.test_factory_v2_activation \
  tests.test_factory_v2_launcher \
  tests.test_factory_v2_runtime \
  tests.test_factory_v2_runtime_remote \
  tests.test_factory_v2_supervisor \
  tests.test_factory_v2_worker_process -v
bash -n manage.sh
bash -n setup.sh
git diff --check
```

Expected: all tests `OK`; both shell syntax checks exit 0; `git diff --check` has no output.

- [ ] **Step 4: Run existing Threads/OAuth regression used for the pilot**

```bash
ACP_DEFAULT_ACCOUNT_PASSWORD='test-only-password' ./.venv/bin/python -m unittest \
  tests.test_factory_v2_threads_compose_home \
  tests.test_factory_v2_threads_checkpoint_identity_plumbing \
  tests.test_factory_v2_threads_flow \
  tests.test_factory_v2_threads_consent_boundary \
  tests.test_factory_v2_threads_session_isolation \
  tests.test_factory_v2_avd_worker_agent \
  tests.test_factory_v2_runtime_remote \
  tests.test_factory_v2_account_credentials \
  tests.test_factory_v2_activation -v
```

Expected: `OK` with no credential/token values printed.

- [ ] **Step 5: Manual dry-run against a temporary private Release generation**

Do not use the live DB first. Create a disposable temp `ACP_BASE` with a fixture DB/env/avatar, run `handoff-out`, then on a second temp base run `setup.sh` with controller start disabled through a documented test-only flag such as `ACP_PORTABLE_NO_START=1`. Verify imported generation, DB hash, env secret equality by boolean comparison only, and ownership flip. Delete only the disposable test asset after verification.

- [ ] **Step 6: Commit docs/CI**

```bash
git add .github/workflows/account-factory-ci.yml README.md docs/ACP_RUNBOOK.md
git commit -m "docs: document portable machine handoff"
```

---

## Final Verification Before Live Handoff

Before using the feature on `acp-live.db`, run:

```bash
git status --short
git diff --check
ACP_DEFAULT_ACCOUNT_PASSWORD='test-only-password' ./.venv/bin/python -m unittest discover -s tests -p 'test_factory_v2_*.py' -v
```

Do not claim completion if the broad suite cannot be run; report the exact focused suites that were observed GREEN.

For the first real handoff, keep the source machine powered on but runtime stopped until the receiving machine has imported the new generation and passed doctor. Do not run controllers on both machines. After receiving-machine verification, the source machine remains `HANDED_OFF` until it later imports a newer generation back from GitHub.
