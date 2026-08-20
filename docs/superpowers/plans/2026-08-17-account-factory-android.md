# ACP Account Factory Android Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an Android operator app for batching Instagram/Threads onboarding and connect each completed Threads profile to ACP through official Threads OAuth without storing account passwords or plaintext long-lived tokens on Android.

**Architecture:** The Android app is offline-first (Compose + Room) and tracks a 50-account batch through `PLANNED -> IG_CREATED -> THREADS_CREATED -> ACP_CONNECTING -> ACP_ACTIVE`. ACP exposes public-but-keyed onboarding endpoints under `/oauth/account-factory/...`; ACP creates one-time OAuth sessions, exchanges the authorization code server-side, verifies the returned Threads username, encrypts the long-lived token using the existing `core.crypto`, and upserts a `channel` row. Android only receives session/status metadata.

**Tech Stack:** ACP Python/Flask/SQLite/requests/cryptography; Android Kotlin 2.3.21, Jetpack Compose BOM 2026.06.00, Room 2.8.4, OkHttp, AGP 9.3.0, Gradle 9.5, JDK 17.

## Global Constraints

- Never store or log Threads access tokens, `THREADS_APP_SECRET`, `ACP_MASTER_KEY`, Instagram passwords, or email passwords in Android.
- Use only official Threads OAuth for token acquisition.
- No CAPTCHA/OTP bypass, proxy rotation, automated signup submission, or credential-based Threads login automation.
- `ACP_ADAPTER=mock` remains the verification default; this feature must not publish a Threads post.
- Account mismatch is a hard failure: a token for account B must never activate expected account A.
- Callback state is one-time and expires.
- Android P0 has three primary screens: Batch Dashboard, Account Workflow, All Accounts.

---

### Task 1: Contract tests and CI

**Files:**
- Create: `.github/workflows/account-factory-ci.yml`
- Create: `tests/test_account_factory.py`
- Create: `android/account-factory/app/src/test/java/com/acp/accountfactory/WorkflowRulesTest.kt`

- [ ] Add failing backend tests for schema creation, OAuth session lifecycle, username mismatch, and encrypted channel activation.
- [ ] Add failing Android domain tests for legal state transitions and 50-account grouping.
- [ ] Run CI and confirm RED because production modules are missing.

### Task 2: ACP OAuth onboarding backend

**Files:**
- Create: `core/account_factory.py`
- Create: `web/account_factory.py`
- Modify: `web/__init__.py`

**Interfaces:**
- `POST /oauth/account-factory/start`
- `GET /oauth/account-factory/session/<session_id>`
- `GET /oauth/account-factory/threads/callback`

- [ ] Create persistent OAuth session schema non-destructively.
- [ ] Require `X-ACP-Factory-Key` on start/status APIs.
- [ ] Build Threads authorization URL using `threads_basic,threads_content_publish`.
- [ ] Exchange code, then exchange short-lived token for long-lived token server-side.
- [ ] Fetch `/me?fields=id,username`, enforce exact normalized username match.
- [ ] Encrypt token using existing `core.crypto.encrypt`, calculate expiry from actual `expires_in`, and upsert `channel` atomically.
- [ ] Mark session terminal and never return token in API/status/log output.
- [ ] Run backend tests GREEN.

### Task 3: Android P0 application

**Files:**
- Create Android project under `android/account-factory/`.

**Interfaces:**
- Local Room entities `BatchEntity`, `AccountEntity`.
- `AccountState` transition rules.
- ACP client consumes the three backend endpoints above.

- [ ] Implement batch generator for 50 accounts, 5 accounts per group.
- [ ] Implement Room persistence/resume.
- [ ] Implement Dashboard, Account Workflow, All Accounts/filter.
- [ ] Implement clipboard helpers and explicit Android intents for Instagram/Threads.
- [ ] Implement ACP pairing settings (base URL + factory key) and OAuth browser launch.
- [ ] Poll session status while workflow screen is active; map ACTIVE or error states to local state.
- [ ] Do not persist OAuth access tokens.
- [ ] Run unit tests and `assembleDebug` GREEN in CI.

### Task 4: Verification and operator docs

**Files:**
- Create: `docs/ACP_ACCOUNT_FACTORY_RUNBOOK.md`
- Modify: `README.md` only if necessary after review.

- [ ] Verify backend unit tests.
- [ ] Verify Android unit tests and debug APK build.
- [ ] Verify no secret/token strings are logged or persisted by Android.
- [ ] Verify git diff contains no `.env`, DB, token, generated production media, or password data.
- [ ] Leave PR unmerged for human review; do not enable a live publishing adapter or publish a post.
