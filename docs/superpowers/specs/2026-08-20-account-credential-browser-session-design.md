# Account Credential + Browser Session Isolation Design

## Goal

Allow one Android AVD worker to process many ACP accounts sequentially while preserving a reusable username/password credential for each account and preventing OAuth from reusing the previous account's browser session.

The operator-selected default account password is supplied through runtime configuration and is never committed to Git. New accounts persist only an encrypted password value. Passwords, OTPs, CAPTCHAs, identity challenges, and security challenges must never appear in logs, heartbeats, job payload persistence, or OAuth callback URLs.

## Current Context

- `factory_account` already stores the account username and lifecycle state.
- `core.crypto` already provides AES-GCM encryption/decryption using `ACP_MASTER_KEY`.
- Remote AVD workers currently receive generic commands over JSON-lines IPC.
- `RunnerGateway` may persist local runner command payloads in SQLite, so plaintext secrets must not travel through that generic persisted path.
- `OPEN_URL` currently launches the default Android browser with `ACTION_VIEW`, with no account-bound browser session concept.

## Configuration

Add one runtime setting:

- `ACP_DEFAULT_ACCOUNT_PASSWORD`

Rules:

1. The selected password value lives only in the operator environment or another local secret store; it is not checked into the repository.
2. Creating a new account in `ACP_ENV=production` requires `ACP_DEFAULT_ACCOUNT_PASSWORD` to be present unless an explicit password is supplied through a future trusted secret input path.
3. Existing accounts without a stored credential remain readable and must not crash migrations or list/detail views.
4. Password values must never be returned by account APIs.

## Persistent Credential Storage

Create a dedicated table instead of adding plaintext/semi-secret fields to `factory_account`:

```sql
CREATE TABLE IF NOT EXISTS factory_account_credential (
    account_id TEXT PRIMARY KEY REFERENCES factory_account(id) ON DELETE CASCADE,
    password_encrypted BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

The controller is the only component that reads or writes this table.

Provide a focused module, `core/factory_v2/account_credentials.py`, with this interface:

```python
store_account_password(conn, account_id: str, password: str) -> None
get_account_password(conn, account_id: str) -> str | None
has_account_password(conn, account_id: str) -> bool
```

`store_account_password()` validates that the password is non-empty, encrypts it with `core.crypto.encrypt()`, and writes only ciphertext to SQLite. `get_account_password()` decrypts on demand and returns `None` when the account has no credential row.

## Account Creation

When `FactoryService.create_batch()` or `create_single_account()` creates a new account intended for automated execution, the service reads `ACP_DEFAULT_ACCOUNT_PASSWORD` once, validates it, and stores an encrypted credential for each new account.

The generated account record continues to contain only username/profile metadata. No password field is added to public account dictionaries or API responses.

If the default password is unavailable in production, account creation fails before an executable account is committed. Development/test environments may inject the password explicitly through test configuration.

## Secret Handoff Boundary

Do not put passwords into `RunnerGateway.send()` generic payloads because local-runner commands may persist `payload_json`.

Introduce a remote-AVD-only transient secret method on `WorkerProcessManager` / `RunnerGateway` that is not stored in SQLite. Conceptual interface:

```python
runner_gateway.send_transient_login_secret(
    job,
    username: str,
    password: str,
) -> dict
```

Constraints:

- Allowed only for `REMOTE_AVD` workers.
- Uses the existing in-memory stdio child-process channel.
- Never writes the credential into `factory_runner_command`, logs, heartbeat metadata, exceptions, or result payloads.
- The worker response contains only status/screen/reason metadata.
- The worker does not retain the password after the command completes.

## Browser Session Isolation

Each AVD worker keeps only an in-memory `oauth_browser_account_id` binding.

When opening a Threads OAuth URL:

1. Require a non-empty `account_id`.
2. If `oauth_browser_account_id == account_id`, reuse the current browser data so a retry for the same account can preserve the login session.
3. If the account changes, clear the OAuth browser app data before opening the new URL.
4. Open the URL explicitly in the same browser package that was cleared, rather than relying on an arbitrary default handler.
5. Set `oauth_browser_account_id = account_id` only after the reset succeeds.

The initial supported browser package is `com.android.chrome`. Browser package selection should be a small constant/configuration point so another supported package can be introduced later without changing OAuth business logic.

Clearing the browser is an isolation boundary, not a login mechanism. It prevents account B from inheriting account A's cookies.

## Automated Login Flow

Add a narrow browser-login UI flow that runs only when the OAuth page is detected as requiring authentication.

Data flow:

```text
controller
  -> decrypt credential for account
  -> transient secret handoff to assigned REMOTE_AVD worker
  -> worker fills username + password on recognized login form
  -> worker submits Login
  -> worker drops secret references
  -> browser reaches OAuth consent OR a protected challenge
```

Safe outcomes:

- `LOGIN_SUCCEEDED`: recognized OAuth permission screen is reached.
- `LOGIN_NOT_REQUIRED`: browser session for the same account is already authenticated.
- `WAITING_HUMAN`: OTP, CAPTCHA, security challenge, identity challenge, suspicious-login approval, or any legal/permission consent is detected.
- `NEEDS_CONFIRMATION`: UI is unknown or the worker cannot prove it is on the expected account/login screen.

The worker must not automatically click Threads/Meta legal consent or OAuth permission approval. Those remain human actions.

## Username/Account Verification

Even with browser isolation and autofill, the OAuth callback remains the authoritative account-binding check.

The existing callback must continue to compare `/me.username` with the expected factory account username before storing the long-lived token or activating the channel. A mismatch must fail closed and must not update/create the account's active Threads channel.

## Lifecycle for One AVD Processing Multiple Accounts

```text
Account A
  -> Instagram/Threads
  -> OAuth browser reset for A
  -> login with A credential if needed
  -> human Authorize
  -> callback verifies A
  -> ACP_ACTIVE
  -> job release

Account B assigned to same AVD
  -> OAuth detects account change A -> B
  -> clear Chrome data
  -> login with B credential if needed
  -> human Authorize
  -> callback verifies B
  -> ACP_ACTIVE
```

A retry for Account B reuses B's current browser session instead of clearing it again.

## Error Handling

- Missing account credential: do not guess a password; transition to an operator-visible retry/confirmation state.
- Browser reset failure: do not open OAuth; fail closed.
- Login UI not recognized: do not type credentials blindly; return `NEEDS_CONFIRMATION`.
- OTP/CAPTCHA/security/identity challenge: return `WAITING_HUMAN`; do not automate.
- OAuth username mismatch: preserve existing mismatch handling and do not activate the channel.
- Worker crash after receiving a transient secret: the child process is terminated/restarted; no secret is persisted for recovery.

## Logging and Redaction

No logs may include:

- plaintext password
- encrypted password bytes
- OAuth authorization code/state query strings
- access tokens
- OTP/CAPTCHA/security challenge values

Diagnostics may include only account ID, worker ID, sanitized username, state, screen kind, and safe error code.

## Testing Strategy

Use TDD for each layer.

1. Credential storage tests prove ciphertext is stored, plaintext is absent from SQLite rows, decryption round-trips, and missing credentials return `None`.
2. Account creation tests prove new accounts receive encrypted default credentials without exposing password fields in returned account dictionaries.
3. Browser isolation tests prove first account resets Chrome, same-account retry does not reset, account switch resets, missing account ID fails closed, and the OAuth URL is opened explicitly in Chrome.
4. Transient handoff tests prove secrets do not enter persisted `factory_runner_command.payload_json` or returned worker metadata.
5. Login-flow tests prove recognized username/password fields are filled, unknown screens fail closed, and OTP/CAPTCHA/security/legal-consent screens remain human-only.
6. Runtime tests prove `START_ACP`/`WAIT_ACP` integrate browser preparation without weakening the existing `/me` account mismatch check.
7. Regression suite must include existing OAuth token exchange/profile tests and Account Factory V2 tests.

## Migration and Compatibility

`ensure_schema()` creates the credential table idempotently. Existing accounts are not backfilled automatically because their historical password is unknown. They remain usable for already-active OAuth channels, but any future credential-based browser re-login for an account without a credential requires an explicit operator-provided credential or a controlled migration action.

## Out of Scope

- Automatically solving OTP, CAPTCHA, identity verification, or security challenges.
- Automatically accepting Threads legal terms or OAuth permission consent.
- Storing plaintext credentials in SQLite, files, Git, logs, or command history.
- Supporting arbitrary third-party browsers in the first implementation.
- Rotating passwords on Instagram/Threads automatically.
