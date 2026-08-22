# Portable Single-Machine Handoff Design

**Date:** 2026-08-22  
**Branch:** `feat/account-factory-android`  
**Status:** Proposed / user-approved architecture, implementation not started

## 1. Goal

Make the Account Factory workspace portable between two Ubuntu machines while preserving the exact durable workflow state. Only one machine is active at a time.

The desired operator experience is:

```bash
# Before leaving the currently active machine
./manage.sh handoff-out

# On the other machine, first time only
git clone -b feat/account-factory-android git@github.com:luongdo03x-byte/acp-affiliate-pipeline.git
cd acp-affiliate-pipeline
./setup.sh

# On later switches to a machine that is already cloned
 git pull --ff-only
./setup.sh
```

`setup.sh` must restore the newest transferable runtime state from a GitHub Private Release, validate the machine, reconstruct local runtime prerequisites, and resume only from durable Account Factory state. The operator must not re-enter `ACP_MASTER_KEY`, stored account passwords, Threads application credentials, or other `.env.local` secrets.

## 2. Constraints and explicit trade-off

This design intentionally uses GitHub authentication as the only access boundary for the portable state bundle. There is **no second encryption layer** around the release asset.

The state asset therefore contains sensitive data in an ordinary tar archive stored in a **private GitHub repository release**, including `.env.local` and its `ACP_MASTER_KEY`. Anyone who can download private release assets for the repository can obtain those secrets.

This is an explicit usability/security trade-off accepted for this deployment model:

- one operator;
- two trusted Ubuntu machines;
- one active machine at a time;
- private GitHub repository;
- GitHub account security (2FA/passkey strongly recommended) is the primary access control;
- no plaintext runtime secrets are committed into the Git tree.

The existing `.gitignore` rules for `.env*`, SQLite databases, virtualenvs, logs, and backups remain in force. Release assets are not Git working-tree content.

## 3. Why this is not a plain `git clone`

The repository contains source code, but the current live Account Factory state also depends on data outside Git:

1. SQLite live database, including account stages, jobs, checkpoints, OAuth sessions, and encrypted account credentials.
2. `shared/.env.local`, including the `ACP_MASTER_KEY` required to decrypt previously stored credentials/tokens and provider configuration.
3. Avatar files referenced by persisted accounts.
4. Local process state and Android runtime prerequisites.

The portable handoff therefore separates:

- **code**: Git branch;
- **durable runtime state**: GitHub Private Release asset;
- **disposable machine runtime**: `.venv`, emulator process, worker process, browser process, temporary files.

The AVD itself is not treated as authoritative durable state. The system must resume from the database and re-establish app/browser sessions when required.

## 4. Reuse existing deployment layout

The solution extends the existing `manage.sh` deployment model rather than replacing it.

Existing layout remains authoritative:

```text
~/Downloads/ACP/
├── acp -> active release/worktree
├── shared/
│   ├── .env.local
│   ├── var/
│   ├── avatars/
│   └── run/
├── logs/
├── backups/
└── releases/
```

`manage.sh setup` already creates shared state directories, creates/links `.env.local` and `var`, installs a virtualenv, and performs schema migration. Portable handoff will reuse those concepts but restore shared state **before** generating any new key or database.

## 5. GitHub Release layout

Use one long-lived private release tag:

```text
acp-portable-state
```

Assets are immutable per generation:

```text
acp-state-g000001.tar.gz
acp-state-g000002.tar.gz
acp-state-g000003.tar.gz
...
```

No mutable `state-latest.tar.gz` is required. `setup.sh` lists the assets on the `acp-portable-state` release, parses generation numbers, and downloads the highest valid generation.

This avoids races around deleting/replacing an asset with the same name and leaves several previous recovery points available.

A retention policy may delete assets older than the newest 5 generations after a successful upload. Retention must never delete the newly uploaded generation before its validation completes.

## 6. State bundle contents

Each state asset has this structure:

```text
state/
├── manifest.json
├── shared/
│   ├── .env.local
│   ├── var/
│   │   └── acp-live.db
│   └── avatars/
│       └── ...
└── checksums.sha256
```

The bundle does **not** contain:

- `.git`;
- `.venv`;
- Android SDK;
- an AVD disk image;
- Chrome profile directory copied from the host;
- PID files;
- worker/controller logs;
- temporary OAuth callback URLs;
- plaintext password exports outside the SQLite credential store.

The SQLite credential table remains encrypted using the `ACP_MASTER_KEY` found in the bundled `.env.local`.

## 7. Manifest contract

`manifest.json` is machine-readable and contains only metadata, never raw secrets:

```json
{
  "format_version": 1,
  "generation": 42,
  "created_at": "2026-08-22T11:00:00+00:00",
  "source_machine_id": "weekday-laptop-<stable-suffix>",
  "source_git_commit": "<full-sha>",
  "source_branch": "feat/account-factory-android",
  "db_relative_path": "shared/var/acp-live.db",
  "db_sha256": "<sha256>",
  "env_sha256": "<sha256>",
  "avatars_digest": "<deterministic-tree-digest>",
  "handoff_state": "READY_FOR_IMPORT"
}
```

`format_version` is validated before restore. Unknown future versions fail closed.

`generation` is monotonic. The next generation is `max(remote generations, local last generation) + 1`.

## 8. Local machine identity and ownership guard

Each clone/machine gets a local file outside Git, for example:

```text
~/Downloads/ACP/shared/machine.json
```

It contains:

```json
{
  "machine_id": "weekend-laptop-<stable-suffix>",
  "last_imported_generation": 42,
  "ownership": "ACTIVE"
}
```

Allowed ownership values:

- `ACTIVE`: this machine may start Account Factory runtime;
- `HANDED_OFF`: local runtime must not start until `setup.sh` imports/claims a generation again.

`manage.sh start` and Account Factory pilot/controller startup entry points must call the ownership guard. A `HANDED_OFF` machine fails closed with a clear message.

`handoff-out` changes local ownership to `HANDED_OFF` only after the remote state upload has completed and been re-downloaded/validated or otherwise verified through GitHub metadata.

This is a cooperative single-machine guard, not a distributed lock. It prevents normal accidental restart after handoff, but it cannot defend against an operator manually editing local guard files or bypassing the wrapper.

## 9. `handoff-out` workflow

New command:

```bash
./manage.sh handoff-out
```

The command performs these stages in order.

### 9.1 Preconditions

Fail before changing state unless all are true:

- GitHub CLI `gh` exists and is authenticated to the repository.
- Git worktree has no uncommitted tracked production changes, unless an explicit future override is introduced.
- `shared/.env.local` exists.
- configured `ACP_DB` exists and points to the live shared database.
- current local ownership is `ACTIVE`.
- release `acp-portable-state` can be queried or created.

### 9.2 Quiesce runtime

Stop all processes that can mutate durable state:

- web app / ngrok through existing `manage.sh stop` where appropriate;
- Account Factory controller;
- Account Factory worker processes.

Do not kill the Android emulator solely for snapshot consistency because the AVD is not part of the durable snapshot. It may be stopped after the snapshot as cleanup.

After stop, verify no known Account Factory controller/worker process remains. If quiescence cannot be proven, abort without uploading a bundle.

### 9.3 Reconcile process-owned transient state

Before snapshot, run a bounded reconciliation step:

- expired leases may be reconciled according to existing scheduler logic;
- do not synthesize success for human/OAuth checkpoints;
- do not advance social stages based only on emulator appearance;
- keep durable `last_safe_stage` unchanged unless existing authoritative code proves a transition.

The snapshot is allowed to contain `RETRY_PENDING`, `NEEDS_CONFIRMATION`, or an expired OAuth session. The receiving machine will resume from durable state.

### 9.4 SQLite-consistent snapshot

Never copy the live `.db` file with plain `cp` while relying on WAL behavior.

Use Python `sqlite3.Connection.backup()` into a temporary snapshot database after runtime quiescence. Run at least:

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

on the snapshot. A failed check aborts handoff.

### 9.5 Build bundle

Create a temporary directory with `umask 077` and copy:

- SQLite snapshot as `shared/var/acp-live.db`;
- `shared/.env.local` with mode `0600`;
- avatar tree;
- generated manifest and checksums.

Do not print environment file values, OAuth secrets, account passwords, tokens, callback query strings, or bundle contents to stdout.

Archive to `acp-state-gNNNNNN.tar.gz` with mode `0600`.

### 9.6 Upload and verify

Upload the generation asset to the private `acp-portable-state` release using authenticated GitHub CLI/API.

After upload:

- confirm the asset exists remotely;
- confirm remote asset size matches local file size;
- where practical, download to a temporary path and verify SHA-256/checksums before declaring success.

Only then write local `machine.json` ownership=`HANDED_OFF` and print:

```text
HANDOFF_OK generation=42
```

If upload or verification fails, ownership stays `ACTIVE` and the temporary archive is removed.

## 10. `setup.sh` / handoff-in workflow

Root `setup.sh` is the one-command entry point on the receiving machine.

It is idempotent and safe both for first clone and later switches.

### 10.1 Bootstrap prerequisites

It verifies or bootstraps only what can be done safely and deterministically:

- `python3` / `venv`;
- `git`;
- `gh` authentication;
- application Python dependencies;
- Android SDK/ADB/emulator availability;
- expected AVD creation/boot when tooling and system image prerequisites are available.

If installation requires OS-level package-manager privilege or a GUI license acceptance, `setup.sh` may stop with one explicit prerequisite command rather than silently changing system security settings.

### 10.2 Select newest generation

Query assets from `acp-portable-state`, select the highest generation matching exactly:

```text
^acp-state-g[0-9]{6}\.tar\.gz$
```

Ignore unexpected assets.

If local `last_imported_generation` equals the newest generation and ownership is already `ACTIVE`, restore is skipped and setup proceeds to doctor/resume.

If local generation is newer than remote, fail closed; never overwrite newer local state with an older remote bundle.

### 10.3 Safe restore

Download into a temporary directory, then validate before touching live shared state:

- tar paths cannot be absolute;
- no `..` traversal;
- expected top-level structure only;
- manifest `format_version` supported;
- generation matches asset filename;
- checksum file valid;
- SQLite integrity and foreign-key checks pass;
- `.env.local` exists and is non-empty.

Restore is transactional at the filesystem level as far as practical:

1. back up current `shared/` runtime state locally;
2. stage new state under a temporary sibling directory;
3. atomically replace database/env files;
4. sync avatar directory;
5. preserve rollback backup until doctor passes.

Set `.env.local` mode to `0600`.

### 10.4 Portable path normalization

A transferred `.env.local` may contain an absolute `ACP_DB` path from the source machine. The receiving machine must not require identical Linux usernames/home directories.

Therefore setup normalizes machine-local path variables after restore, without altering cryptographic/provider secrets:

- `ACP_DB` -> `${ACP_BASE}/shared/var/acp-live.db`;
- `ACP_AVATAR_DIR` -> `${ACP_BASE}/shared/avatars` where supported;
- other machine-specific paths are normalized only if explicitly classified as portable path fields.

`ACP_MASTER_KEY`, provider secrets, OAuth application IDs/secrets, default account password, admin secrets, and public provider credentials are preserved byte-for-byte.

## 11. Doctor gate

Before any controller/worker starts, `setup.sh` runs a new portable doctor.

Required checks:

```text
Git branch/commit              OK
GitHub private repo auth       OK
State generation               OK
.env.local permissions         OK
SQLite integrity               OK
Factory schema                 OK
ACP_MASTER_KEY present         OK
Known stored credential decrypt OK (when credential exists)
Threads OAuth config           OK (for ACP_ACTIVE mode)
ADB                            OK
AVD definition                 OK
AVD boot/serial                OK
Callback/public URL probe      OK when activation requires it
Single-machine ownership       OK
```

A failed credential decrypt check reports only a domain-safe result such as `CREDENTIAL_DECRYPT_FAILED`; it never prints ciphertext, master key, or plaintext password.

Doctor failure prevents automatic resume.

## 12. Runtime/session reconstruction

The portable bundle does not copy AVD/browser state.

On the receiving machine:

- create or reuse local AVD `acp-worker-01`;
- app sessions are considered reconstructable;
- persisted Account Factory stages remain authoritative;
- existing session-isolation protections remain in force;
- Chrome first-run/Terms, OAuth consent, OTP, CAPTCHA, identity/security challenges remain human-only.

The receiving machine may therefore need a one-time human Chrome first-run action even though no secrets need to be re-entered.

This is acceptable because the goal is no secret/key re-entry, not bypassing third-party consent/security UX.

## 13. Durable resume behavior

After doctor passes, `setup.sh` invokes a bounded `resume-factory` operation.

It first reconciles stale leases and OAuth session expiration with existing authoritative services. Then scheduling follows existing durable state:

```text
PROFILE_READY
    -> PREPARE_INSTAGRAM

RETRY_PENDING + last_safe_stage=PROFILE_READY
    -> PREPARE_INSTAGRAM

RETRY_PENDING + last_safe_stage=IG_CREATED
    -> PREPARE_THREADS

RETRY_PENDING + last_safe_stage=THREADS_CREATED
    + completion_mode=ACP_ACTIVE
    + last_error_code=NULL
    -> START_ACP
```

An OAuth error remains gated until the existing explicit retry semantics clear `OAUTH_FAILED`.

No portability code may directly mark Instagram/Threads/OAuth successful merely to make a handoff resume.

## 14. Avoid the previous Chrome retry loop

Portable resume must not reintroduce the prior failure mode where repeated controller ticks created/restarted worker processes and repeatedly cleared/opened Chrome.

Rules:

1. `CREDENTIAL_DECRYPT_FAILED` is terminal for the current job attempt and releases the job rather than retaining an uncontrolled `START_ACP` loop.
2. For an interactive OAuth attempt, keep one worker process alive across browser open/login/consent observation where possible.
3. Browser account binding is reset only at the intended account isolation boundary.
4. Chrome first-run/unknown UI becomes a human checkpoint or bounded stop, never a high-frequency retry loop.
5. `setup.sh` does not start an unbounded controller loop until the doctor and stale-state reconciliation are complete.

A future separate improvement may persist browser account binding across worker restart, but portability does not require copying Chrome state.

## 15. Public callback / ngrok portability

`ACP_PUBLIC_BASE_URL` is transferred in `.env.local`, but an ngrok CLI installation/auth configuration may be machine-local.

To meet one-command portability, setup/doctor must classify the callback mode:

- if the public URL is already reachable independently of local ngrok, only probe it;
- if local ngrok is required, verify `ngrok` exists and is authenticated/configured;
- if ngrok requires a token not currently represented in portable `.env.local`, add a dedicated environment field during implementation and configure the CLI without printing the token.

OAuth activation is not started unless the public callback probe succeeds.

## 16. New/changed interfaces

Expected implementation surface:

```text
setup.sh                              # new root one-command bootstrap/restore/resume
manage.sh                             # add handoff-out and ownership guard integration
core/factory_v2/portable_state.py     # bundle manifest, snapshot, validation, restore helpers
core/factory_v2/portable_doctor.py    # portable readiness checks
core/factory_v2/portable_resume.py    # bounded reconcile/resume orchestration
scripts/                              # optional small OS/Android bootstrap helpers
.env.example                          # document any new machine-portable config fields
docs/portable-handoff.md              # operator runbook
```

Exact file split may be refined during implementation planning, but security-sensitive bundle validation should live in testable Python rather than a large shell-only implementation.

## 17. GitHub API/CLI behavior

Implementation should prefer `gh` because private repository authentication is already required for cloning/operations.

Required capabilities:

- verify current authenticated user/repository access;
- create `acp-portable-state` release if absent;
- list release assets;
- upload a new immutable generation asset;
- download a selected asset;
- optionally delete generations beyond retention.

Commands must never use tokens in command-line arguments if avoidable, and logs must not dump `gh auth token`.

## 18. Failure semantics

### Handoff-out failure

- runtime may already have been stopped;
- no remote generation is considered valid until upload validation succeeds;
- local ownership remains `ACTIVE`;
- operator can restart the original machine safely.

### Restore failure

- do not start controller/worker;
- keep previous local shared-state backup;
- ownership remains non-active until restore/doctor completes;
- print a bounded recovery instruction.

### Doctor failure

- state remains restored but runtime does not start;
- secrets are never printed;
- operator fixes prerequisite and reruns `./setup.sh`.

### Resume failure

- retain authoritative DB error/stage semantics;
- do not overwrite `last_safe_stage`;
- do not auto-approve human/security checkpoints.

## 19. Security requirements

Even though the release asset is intentionally not encrypted a second time, implementation must still satisfy:

- repository remains private;
- state bundle never committed to Git history;
- temporary bundle files mode `0600` under `umask 077`;
- `.env.local` mode `0600`;
- no secrets/passwords/tokens in stdout, manifest, command history generated by scripts, or logs;
- no OAuth callback query (`code`, `state`) logged by portability helpers;
- no account password added to persisted generic runner-command payloads;
- extracted tar paths validated before extraction;
- SHA-256 validation before restore;
- SQLite snapshot consistency checks;
- fail closed on wrong/missing master key.

## 20. Testing strategy

Implementation follows TDD.

### Unit tests

Cover at minimum:

- release asset generation parser/order;
- manifest schema validation;
- generation monotonicity;
- tar traversal rejection;
- checksum mismatch rejection;
- environment portable-path normalization preserves secrets;
- wrong master key fails doctor without secret leakage;
- machine ownership state transitions;
- stale/newer generation refusal;
- resume mapping from durable stages;
- OAuth error gate preserved;
- bundle contents exclude known runtime/temp paths.

### Integration tests

Use temporary directories and SQLite databases to prove:

1. create source shared state;
2. snapshot using SQLite backup;
3. build bundle;
4. restore into a different fake home/base path;
5. normalize `ACP_DB`;
6. decrypt a stored credential with transferred key;
7. run schema/doctor;
8. verify account stage/checkpoint/job fields survive exactly.

GitHub operations should be abstracted behind a thin command/client adapter so tests use a fake backend rather than uploading real secrets.

### Regression tests

Run existing focused Account Factory suites including:

- account credentials;
- activation;
- remote runtime;
- Threads Compose identity;
- Threads consent boundary;
- Threads session isolation;
- worker agent/browser login plumbing;
- scheduler/recovery behavior.

## 21. Acceptance criteria

The design is complete when the following scenario works without re-entering project secrets:

1. Weekday machine is active with live Account Factory state.
2. Operator runs `./manage.sh handoff-out`.
3. Command stops mutating runtime, snapshots consistent state, uploads a new generation, verifies it, and marks weekday machine `HANDED_OFF`.
4. Weekend machine has no prior shared state and only has authenticated access to the private GitHub repository.
5. Operator clones the branch and runs `./setup.sh`.
6. Setup downloads newest state generation, restores `.env.local`/DB/avatars, installs application/runtime dependencies or reports a bounded prerequisite, boots/validates AVD, and passes doctor.
7. Stored account credential decrypt succeeds with no password/key prompt.
8. Durable Account Factory account stage, checkpoint, OAuth session status, and `last_safe_stage` equal the source snapshot after restore/reconciliation semantics are applied.
9. Resume starts only the next safe action; completed Instagram/Threads stages are not replayed.
10. Human-only legal/OAuth/security boundaries remain human-only.
11. Starting the old machine through supported wrappers after successful handoff is rejected until it imports/claims a newer generation.
12. At no point is `.env.local`, `ACP_MASTER_KEY`, account password, OAuth token, or callback code/state printed or committed into Git.

## 22. Non-goals

This phase does not:

- support two simultaneously active controllers against the same state;
- migrate SQLite to PostgreSQL;
- synchronize live writes continuously between machines;
- clone/copy a running AVD disk image;
- bypass Chrome Terms, OAuth consent, OTP, CAPTCHA, or identity/security checks;
- make GitHub Release assets confidential from users who already have private repository download permission;
- recover secrets if the private release history and both local copies are lost.

## 23. Operator workflow after implementation

Normal switch from machine A to B:

```bash
# A
cd <repo>
git pull --ff-only
./manage.sh handoff-out

# B
cd <repo>          # or clone once if first use
git pull --ff-only
./setup.sh
```

First use on B:

```bash
git clone -b feat/account-factory-android \
  git@github.com:luongdo03x-byte/acp-affiliate-pipeline.git
cd acp-affiliate-pipeline
./setup.sh
```

No project secret/key/password entry is required after GitHub authentication is already configured on that machine.
