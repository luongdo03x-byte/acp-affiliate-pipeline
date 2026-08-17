# ACP Account Factory — P0 Runbook

## Scope

Account Factory helps an operator track a batch of Instagram/Threads profiles on Android and connect completed Threads profiles to ACP through official Threads OAuth.

It does **not** automate account signup submission, OTP/CAPTCHA, identity checks, IP/proxy rotation, or credential-based login. The operator completes Instagram/Threads signup and any verification in the official apps.

## Security boundaries

Android never receives or stores:

- Threads user access tokens
- `THREADS_APP_SECRET`
- `ACP_MASTER_KEY`
- Instagram/Threads passwords
- email passwords

ACP receives the OAuth authorization code, exchanges it server-side, verifies `id,username`, converts to a long-lived token, encrypts it with the existing `core.crypto` AES-GCM routine, and stores only the encrypted token in `channel.token_encrypted`.

A username mismatch is terminal for that OAuth attempt. ACP does not update any channel with the mismatched token.

## Required ACP environment

Keep the existing values for `THREADS_APP_ID`, `THREADS_APP_SECRET`, `ACP_MASTER_KEY` and database configuration. Add:

```bash
ACP_FACTORY_API_KEY=<random pairing key>
ACP_PUBLIC_BASE_URL=https://your-public-acp-host.example
```

Generate a new factory pairing key locally without placing it in shell history as an argument:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Store the generated value in `shared/.env.local` using the repository's normal secret-management workflow. Never commit the plaintext value.

## Meta Threads redirect URI

Configure the Threads app callback exactly as:

```text
https://your-public-acp-host.example/oauth/account-factory/threads/callback
```

`ACP_PUBLIC_BASE_URL` must use the same externally reachable HTTPS origin.

## Run ACP with Account Factory routes

The P0 branch intentionally avoids changing the existing publish/dashboard launcher. Start the same ACP Flask app plus the Account Factory routes with:

```bash
cd ~/Downloads/ACP/releases/2.0/acp
set -a
source ~/Downloads/ACP/shared/.env.local
set +a
ACP_ADAPTER=mock ACP_SOURCE=mock python3 account_factory_server.py
```

This launcher reuses `web.server.create_app()` and the existing ACP database. It does not publish a post by itself.

For a public callback, expose the server through the same approved HTTPS tunnel/reverse proxy used by ACP and ensure `ACP_PUBLIC_BASE_URL` matches that public origin.

## Android build

Open `android/account-factory` in Android Studio, or build from the repository root when Android SDK 36 + JDK 17 + Gradle are available:

```bash
gradle -p android/account-factory testDebugUnitTest assembleDebug
```

APK output:

```text
android/account-factory/app/build/outputs/apk/debug/app-debug.apk
```

GitHub workflow `.github/workflows/account-factory-ci.yml` performs the same test/build and uploads `acp-account-factory-debug` when GitHub Actions runners are available.

## First Android setup

Open **Cài đặt** and enter:

- **ACP Base URL:** the value of `ACP_PUBLIC_BASE_URL`
- **Factory Key:** the same value as `ACP_FACTORY_API_KEY`

The Factory Key authenticates the Android start/status API calls. It is not a Threads token.

## Operator flow

1. Create the default batch of 50 accounts. The app creates 10 local groups of 5.
2. For each account, copy the prepared username/display name/bio.
3. Tap **OPEN INSTAGRAM** and complete account creation manually in the official app. Resolve OTP/CAPTCHA/checkpoints manually if Meta requests them.
4. Return to Account Factory and tap **MARK IG CREATED**.
5. Tap **OPEN THREADS**, create the Threads profile manually, return, then tap **MARK THREADS CREATED**.
6. Tap **CONNECT ACP**.
7. Android calls `POST /oauth/account-factory/start`; ACP creates a one-time state and returns the Threads authorization URL.
8. Android opens that URL. Authorize the expected Threads profile.
9. Meta redirects to ACP. ACP exchanges/validates/encrypts the token and activates the channel only if the returned username matches the expected username.
10. Android polls `GET /oauth/account-factory/session/<id>`. When ACP returns `ACTIVE`, the local account becomes `ACP_ACTIVE` and the operator can continue to the next account.

## API contract

### Start

`POST /oauth/account-factory/start`

Header:

```text
X-ACP-Factory-Key: <pairing key>
```

JSON:

```json
{
  "expected_username": "example.01",
  "batch_id": "local-batch-id",
  "account_local_id": "local-account-id"
}
```

Response includes only `session_id`, `status`, `authorization_url`, `expires_at`. It never includes an access token.

### Status

`GET /oauth/account-factory/session/<session_id>` with the same pairing header.

Terminal statuses used by P0 include `ACTIVE`, `ACCOUNT_MISMATCH`, `OAUTH_ERROR`, and `SESSION_EXPIRED`.

### Callback

`GET /oauth/account-factory/threads/callback?code=...&state=...`

This is a browser/Meta callback and therefore does not use the Factory Key. The one-time OAuth `state` identifies and protects the pending onboarding session.

## Verification commands

Backend core behavior:

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock ACP_ENV=development python3 -m unittest tests.test_account_factory -v
```

Android domain/build:

```bash
gradle -p android/account-factory testDebugUnitTest assembleDebug --stacktrace
```

Do not run a live Threads publish as part of Account Factory verification.
