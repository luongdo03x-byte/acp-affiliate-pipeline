# Account Factory runtime secrets

Account Factory keeps reusable account passwords outside public account rows and encrypts them before SQLite persistence.

## Required production secrets

Set these in the controller environment before creating new automated accounts:

```bash
export ACP_MASTER_KEY='<base64-32-byte-key>'
export ACP_DEFAULT_ACCOUNT_PASSWORD='<operator-secret>'
```

`ACP_MASTER_KEY` is the AES-GCM master key already used by ACP secret storage. `ACP_DEFAULT_ACCOUNT_PASSWORD` is read by the controller when a new factory batch/account is created. The plaintext value is not added to `factory_account`, API responses, logs, heartbeats, or persisted runner-command payloads. Only the encrypted value is stored in `factory_account_credential`.

In `ACP_ENV=production`, new factory account creation fails before database writes when `ACP_DEFAULT_ACCOUNT_PASSWORD` is missing. Existing accounts without a credential row continue to load; their OAuth browser login falls back to manual login rather than guessing a password.

## Existing-account credential migration

Do not backfill every historical account automatically. For an account whose password is known to the operator, set `ACP_DEFAULT_ACCOUNT_PASSWORD` in the local shell and run a controlled one-account migration:

```bash
python3 - <<'PY'
import os
from core.db import connect
from core.factory_v2.account_credentials import (
    has_account_password,
    store_account_password,
)

ACCOUNT_ID = '<factory-account-id>'
password = os.environ.get('ACP_DEFAULT_ACCOUNT_PASSWORD')
if not password:
    raise SystemExit('ACP_DEFAULT_ACCOUNT_PASSWORD is required')

conn = connect()
try:
    store_account_password(conn, ACCOUNT_ID, password)
    print({
        'account_id': ACCOUNT_ID,
        'credential_stored': has_account_password(conn, ACCOUNT_ID),
    })
finally:
    conn.close()
PY
```

The command deliberately does not print the password or ciphertext.

## OAuth browser behavior

For REMOTE_AVD workers, the OAuth browser is explicitly `com.android.chrome`. The worker binds the browser session to the current factory account. Switching to another account clears Chrome app data before opening that account's OAuth URL; retrying the same account keeps its current browser session.

After the OAuth URL opens, the controller-side runner gateway checks for an encrypted credential. When one exists, it decrypts it only long enough to send a `TRANSIENT_BROWSER_LOGIN` command over the in-memory worker stdio transport. That secret command is never written to `factory_runner_command.payload_json`.

The browser worker types credentials only when it positively recognizes the expected two-field login form. OTP, CAPTCHA, identity/security challenges, suspicious-login approval, Threads legal terms, and OAuth permission consent are human-only and must stop automation.

The OAuth callback remains authoritative: `/me.username` must still match the factory account's expected username before a Threads channel can become active.
