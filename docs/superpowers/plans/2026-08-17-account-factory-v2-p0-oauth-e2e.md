# Account Factory V2 P0 OAuth Bridge and End-to-End Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bridge the V2 controller account lifecycle to the existing secure Threads OAuth implementation and verify one complete account flow through `ACP_ACTIVE`, recovery, and APK/controller integration.

**Architecture:** Reuse `core.account_factory` for official Threads OAuth/token exchange and `channel` upsert. Add a V2 bridge that starts OAuth from an authoritative `factory_account`, records the session id, and updates that account only after the existing OAuth session reaches a terminal status. The callback never trusts Android/worker claims of identity; username verification remains server-side.

**Tech Stack:** Existing Flask OAuth routes, `core.account_factory`, Factory V2 service/repository, Python `unittest`, Android client, Gradle, one real Android Studio AVD for final P0 verification.

## Global Constraints

- Official Threads OAuth remains mandatory for ACP connection.
- `actual_username != expected_username` must result in `ACCOUNT_MISMATCH`; no mismatched channel may be created or updated.
- Threads long-lived tokens remain inside ACP backend and are encrypted before persistence.
- Android phone and worker receive only safe metadata such as session status, Threads user id, and channel code.
- Do not expose access tokens, app secret, ACP master key, password, OTP, CAPTCHA, or provider raw response bodies.
- Do not test live publishing as part of Account Factory P0.
- P0 acceptance begins with one account, then a small batch; do not start with 50 live account attempts.

---

## File Structure

- Create `core/factory_v2/oauth_bridge.py` — controller/OAuth synchronization.
- Modify `web/factory_v2.py` — V2 account OAuth start/status endpoints.
- Modify `web/account_factory.py` — notify V2 bridge after callback terminal result without changing token ownership.
- Modify `tests/test_account_factory.py` — preserve existing OAuth guarantees.
- Create `tests/test_factory_v2_oauth_bridge.py`.
- Modify Android `FactoryV2Api.kt`, DTOs, and ViewModel for V2 OAuth start/status.
- Modify `.github/workflows/account-factory-ci.yml` only after local verification, keeping existing backend and Android build checks.
- Modify `docs/ACP_ACCOUNT_FACTORY_RUNBOOK.md` with V2 P0 startup/test sequence.

### Task 1: V2 OAuth bridge service

**Files:**
- Create: `core/factory_v2/oauth_bridge.py`
- Create: `tests/test_factory_v2_oauth_bridge.py`

**Interfaces:**
- Consumes `FactoryRepository`, `FactoryService`, existing `create_oauth_session`, `get_session`.
- Produces `start_account_oauth(conn, account_id: str, redirect_uri: str, provider) -> dict`.
- Produces `sync_account_from_oauth_session(conn, session_id: str) -> dict`.

- [ ] **Step 1: Write failing start test**

```python
def test_start_oauth_uses_authoritative_username_and_marks_connecting(self):
    account = self.seed_account(stage="THREADS_CREATED", username="maianh.le")
    result = start_account_oauth(self.conn, account["id"], self.redirect_uri, self.provider)
    updated = self.repo.get_account(account["id"])
    self.assertEqual("ACP_CONNECTING", updated["stage"])
    self.assertEqual(result["session_id"], updated["oauth_session_id"])
    oauth = get_session(self.conn, result["session_id"])
    self.assertEqual("maianh.le", oauth["expected_username"])
```

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m unittest tests.test_factory_v2_oauth_bridge -v`

Expected: FAIL because bridge does not exist.

- [ ] **Step 3: Implement start bridge**

Load the account from Controller DB; reject any stage other than `THREADS_CREATED` or an explicitly retryable OAuth error state. Call existing `create_oauth_session` with `expected_username=account['username']`, `batch_id=account['batch_id']`, and `account_local_id=account['id']`. Persist `oauth_session_id` and transition through the V2 state machine to `ACP_CONNECTING` in the same controller transaction after the session is created.

Return only:

```python
{
    "session_id": session["id"],
    "status": session["status"],
    "authorization_url": provider.authorization_url(session["state"], redirect_uri),
    "expires_at": session["expires_at"],
}
```

- [ ] **Step 4: Add sync tests**

Test these mappings:

```text
OAuth ACTIVE           -> factory account ACP_ACTIVE
ACCOUNT_MISMATCH       -> factory account ERROR, error_code ACCOUNT_MISMATCH
OAUTH_ERROR            -> factory account RETRY_PENDING
SESSION_EXPIRED        -> factory account RETRY_PENDING
WAITING_AUTH           -> no account stage change
```

For ACTIVE, assert `threads_user_id` and `channel_code` are copied from OAuth session metadata, not from client input.

- [ ] **Step 5: Run tests and commit**

```bash
python3 -m unittest tests.test_factory_v2_oauth_bridge -v
git add core/factory_v2/oauth_bridge.py tests/test_factory_v2_oauth_bridge.py
git commit -m "feat: bridge factory v2 accounts to threads oauth"
```

### Task 2: V2 OAuth API endpoints

**Files:**
- Modify: `web/factory_v2.py`
- Modify: `tests/test_factory_v2_api.py`

**Interfaces:**
- Produces `POST /api/factory/v2/accounts/<account_id>/oauth/start`.
- Produces `GET /api/factory/v2/accounts/<account_id>/oauth/status`.

- [ ] **Step 1: Write failing route tests**

```python
def test_oauth_start_ignores_client_supplied_username(self):
    account = self.seed_threads_created(username="maianh.le")
    res = self.client.post(
        f"/api/factory/v2/accounts/{account['id']}/oauth/start",
        json={"expected_username": "wrong.user"},
        headers=self.auth,
    )
    self.assertEqual(201, res.status_code)
    oauth = self.lookup_oauth(res.get_json()["session_id"])
    self.assertEqual("maianh.le", oauth["expected_username"])
```

- [ ] **Step 2: Run and verify RED**

Run: `python3 -m unittest tests.test_factory_v2_api -v`

Expected: FAIL for missing endpoint.

- [ ] **Step 3: Implement routes**

The start route takes only account id from path; ignore/reject username in body. The status route calls `sync_account_from_oauth_session` before serializing the authoritative account. Keep P0 `X-ACP-Factory-Key` auth.

- [ ] **Step 4: Run tests and commit**

```bash
python3 -m unittest tests.test_factory_v2_api tests.test_factory_v2_oauth_bridge -v
git add web/factory_v2.py tests/test_factory_v2_api.py
git commit -m "feat: add factory v2 account oauth api"
```

### Task 3: Callback-to-controller synchronization

**Files:**
- Modify: `web/account_factory.py`
- Modify: `tests/test_factory_v2_oauth_bridge.py`
- Modify: `tests/test_account_factory.py`

**Interfaces:**
- Existing callback remains `/oauth/account-factory/threads/callback`.
- After `complete_oauth_session` terminal outcome, synchronize a matching V2 account by the OAuth row `account_local_id`.

- [ ] **Step 1: Add failing callback success test**

Seed a V2 account, start its OAuth through the bridge, invoke the existing callback with fake provider success, then assert both:

```text
account_factory_oauth_session.status == ACTIVE
factory_account.stage == ACP_ACTIVE
```

and `factory_account.channel_code` matches the channel/OAuth session code.

- [ ] **Step 2: Add mismatch regression test**

Use provider username `bob` for expected `alice`; assert:

```text
channel count unchanged
OAuth status ACCOUNT_MISMATCH
factory account stage ERROR
factory account last_error_code ACCOUNT_MISMATCH
```

- [ ] **Step 3: Run and verify RED**

Run: `python3 -m unittest tests.test_factory_v2_oauth_bridge tests.test_account_factory -v`

Expected: V2 synchronization assertions FAIL while existing P0 OAuth tests still pass.

- [ ] **Step 4: Implement callback sync in finally-safe form**

Do not move token exchange/encryption into V2 code. After terminal OAuth state is persisted, call bridge synchronization by session id/account_local_id. If V2 synchronization fails after OAuth success, log only an allowlisted internal error and leave the OAuth/channel success durable; the account can reconcile on the next status poll/controller restart.

- [ ] **Step 5: Run tests and commit**

```bash
python3 -m unittest tests.test_factory_v2_oauth_bridge tests.test_account_factory -v
git add web/account_factory.py tests/test_factory_v2_oauth_bridge.py tests/test_account_factory.py
git commit -m "feat: sync threads oauth completion to factory v2"
```

### Task 4: Android V2 OAuth flow

**Files:**
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/network/FactoryV2Api.kt`
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/network/FactoryV2Dtos.kt`
- Modify: `android/account-factory/app/src/main/java/com/acp/accountfactory/ui/FactoryViewModel.kt`
- Modify: relevant workflow/checkpoint screen created in the API/phone plan.
- Create/modify Android unit tests for OAuth action mapping.

**Interfaces:**
- `startOAuth(accountId) -> StartedOAuthDto(sessionId, authorizationUrl, status, expiresAt)`.
- `oauthStatus(accountId) -> FactoryAccountDto`.

- [ ] **Step 1: Write failing ViewModel test**

Fake API returns an OAuth URL. Assert ViewModel emits an `OpenExternalUrl(url)` one-shot event and does not store/access any token. After status returns `ACP_ACTIVE`, UI state refreshes account stage/channel code from server.

- [ ] **Step 2: Run and verify RED**

Run: `gradle -p android/account-factory testDebugUnitTest --tests '*FactoryViewModelTest'`

Expected: FAIL for missing OAuth methods/events.

- [ ] **Step 3: Implement flow**

Phone asks Controller to start OAuth, opens returned official authorization URL in browser, then polls only that account status while stage is `ACP_CONNECTING`. Poll interval may remain 3 seconds for this bounded OAuth session only; global dashboard continues manual/normal refresh in P0.

- [ ] **Step 4: Run Android tests and build**

```bash
gradle -p android/account-factory testDebugUnitTest assembleDebug --no-daemon --max-workers=2 --console=plain
```

Expected: `BUILD SUCCESSFUL`; APK exists.

- [ ] **Step 5: Commit**

```bash
git add android/account-factory/app/src/main/java/com/acp/accountfactory android/account-factory/app/src/test/java/com/acp/accountfactory
git commit -m "feat: connect factory v2 phone to threads oauth"
```

### Task 5: Local full regression suite

**Files:**
- Modify `.github/workflows/account-factory-ci.yml` only if local commands differ from current workflow.

**Interfaces:**
- No new runtime interface; this is the P0 verification gate.

- [ ] **Step 1: Run backend tests**

```bash
python3 -m unittest \
  tests.test_account_factory \
  tests.test_factory_v2_schema \
  tests.test_factory_v2_state_machine \
  tests.test_factory_v2_identity \
  tests.test_factory_v2_service \
  tests.test_factory_v2_resource_policy \
  tests.test_factory_v2_avd \
  tests.test_factory_v2_scheduler \
  tests.test_factory_v2_supervisor \
  tests.test_factory_v2_api \
  tests.test_factory_v2_oauth_bridge -v
```

Expected: all PASS.

- [ ] **Step 2: Run Android tests/build**

```bash
gradle -p android/account-factory testDebugUnitTest assembleDebug --no-daemon --max-workers=2 --console=plain
```

Expected: `BUILD SUCCESSFUL` and APK exists at `android/account-factory/app/build/outputs/apk/debug/app-debug.apk`.

- [ ] **Step 3: Inspect git diff/status**

```bash
git status --short
git diff --check
```

Expected: no unintended generated build outputs staged; `git diff --check` exits 0.

- [ ] **Step 4: Commit CI alignment only if needed**

If workflow already runs the same commands, do not edit it. If it lacks the new Python modules, change only the backend test command; keep Android `testDebugUnitTest assembleDebug` and APK artifact upload.

### Task 6: One-real-AVD P0 end-to-end verification and runbook

**Files:**
- Modify: `docs/ACP_ACCOUNT_FACTORY_RUNBOOK.md`

**Interfaces:**
- Operational verification only; no live publish.

- [ ] **Step 1: Start controller against normal ACP environment without printing secrets**

Use the existing environment-loading procedure, then start `python3 account_factory_server.py`. Confirm `/api/factory/v2/dashboard` returns 200 with the configured Factory Key.

- [ ] **Step 2: Start one AVD worker**

Verify `adb devices` shows the assigned emulator as `device`, worker becomes `READY`, and a generated account receives exactly one lease.

- [ ] **Step 3: Verify human checkpoint behavior**

Have the worker prepare/open the official flow and enter `WAITING_HUMAN`. Confirm Controller/phone show the same account/worker. Manually perform only the platform-required signup/verification step in that AVD, then press Continue and verify the post-check drives the next legal stage.

- [ ] **Step 4: Verify Threads OAuth to ACP_ACTIVE**

For the test account, complete official Threads OAuth. Confirm expected username equals actual username, account becomes `ACP_ACTIVE`, channel is active, Android never receives token data, and worker returns to READY for the next account.

- [ ] **Step 5: Verify recovery**

During a non-terminal test account, stop/restart the worker or AVD once. Confirm the controller preserves `last_safe_stage`, does not duplicate the lease, and does not mark a human checkpoint successful automatically.

- [ ] **Step 6: Update runbook with exact proven commands/statuses**

Document only commands and paths verified in the local environment. Explicitly state: P0 uses companion `account_factory_server.py`; standard `./manage.sh start` is not yet the V2 launcher unless separately integrated.

- [ ] **Step 7: Commit runbook**

```bash
git add docs/ACP_ACCOUNT_FACTORY_RUNBOOK.md
git commit -m "docs: add factory v2 p0 verification runbook"
```

## Completion Gate

P0 is complete only with fresh evidence that backend tests pass, Android tests/build pass, one real AVD can run a controller-owned account through a human checkpoint and official OAuth to `ACP_ACTIVE`, worker/account recovery preserves safe state, and no live publish is performed. Do not claim the full 50-account batch is production-ready from a one-account test; next verification is a small batch before any full-batch exercise.
