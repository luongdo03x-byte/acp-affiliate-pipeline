# AGENTS.md — ACP development rules

## Scope
This file applies to the ACP repository and all subdirectories unless a deeper AGENTS.md overrides it.

## Read first
Before changing code, inspect:
- `README.md`
- `docs/ACP_RUNBOOK.md`
- relevant tests and existing adapter patterns

## Production data safety
Never modify, delete, reset, print, commit, or copy into source control:
- `.env.local`
- `~/Downloads/ACP/shared/.env.local`
- `~/Downloads/ACP/shared/var/acp-live.db`
- SQLite WAL/SHM files
- Threads access tokens
- ACCESSTRADE access keys
- `ACP_MASTER_KEY`
- dashboard/webhook secrets
- generated production media or backups

Do not generate a replacement `ACP_MASTER_KEY` for an existing live database.

## Runtime safety
- Use `./manage.sh test` for release verification.
- Keep `ACP_ADAPTER=mock` during tests unless the human explicitly requests a controlled live integration test.
- Do not publish a real Threads post without explicit human approval in the current task.
- Do not approve posts in bulk.
- Do not run demo/seed commands against the live database.
- Never run `run.py init` as an upgrade migration for an existing live database. `manage.sh upgrade` uses schema-only `init_db()`.

## Runtime commands
Preferred operator interface:

```bash
./manage.sh start
./manage.sh stop
./manage.sh restart
./manage.sh status
./manage.sh test
./manage.sh upgrade <zip> <version>
./manage.sh rollback
```

Do not replace these with ad-hoc process killing, manual DB copying, or manual symlink switching unless debugging the manager itself.

## Code conventions
- Follow existing project patterns before introducing new abstractions.
- Keep source adapters isolated from normalized product/post pipeline logic.
- Preserve backward compatibility for database migrations where practical.
- Do not silently change attribution semantics (`sub1`, campaign IDs, tracking URLs).
- Never log secrets or full access tokens.

## Required verification
Before claiming a task is complete:
1. Run the smallest relevant tests during development.
2. Run `python3 tests/test_manage.py` when `manage.sh` changes.
3. Run `./manage.sh test` for release-level changes when the deployment layout is available.
4. Inspect `git diff` and `git status`.
5. Report exact commands and pass/fail results; do not claim success without command output.

## Git workflow
- Start from an up-to-date `main`.
- Work on `feat/*`, `fix/*`, `chore/*`, or `upgrade/*` branches.
- Do not force-push `main`.
- Do not use destructive `git reset --hard` or `git clean -fdx` without explicit human approval.
- Review `git diff` before committing.
- Push the working branch, not unreviewed changes directly to `main`.
- `manage.sh upgrade` does not commit, push, merge, or rewrite Git history.

## Human confirmation boundaries
Ask before any action that would:
- publish publicly,
- mutate production data materially beyond an already-approved schema migration,
- rotate/delete credentials,
- change remote Git history,
- delete files/backups,
- enable a live affiliate/publishing adapter.
