# ACP Account Factory — Runbook

## Scope

Account Factory helps an operator track Instagram/Threads profiles on Android and connect completed Threads profiles to ACP through official Threads OAuth.

It does **not** bypass OTP/CAPTCHA, identity checks or Android security permissions. The operator completes any verification required by Meta in the official apps. Android Accessibility must be enabled manually once because Android does not allow an app to grant itself that permission.

`REMOTE_AVD` additionally supports fail-closed UI assistance for known safe screens. It may fill only approved non-sensitive profile fields (`username`, `display_name`, `bio`) and tap only positively matched known controls. Unknown UI, password/OTP/CAPTCHA, contact verification, selfie/identity, recovery and security challenges stop automation and require the operator.

## Security boundaries

Android never receives or stores:

- Threads user access tokens
- `THREADS_APP_SECRET`
- `ACP_MASTER_KEY`
- Instagram/Threads passwords
- email passwords

For zero-config Controller access, Android receives a random **per-device credential** only at enrollment time. The Controller stores only its SHA-256 hash. Android encrypts the raw credential with an AES/GCM key held by Android Keystore.

`ACP_FACTORY_API_KEY` remains a server-side operator/fallback credential and is **not embedded in the APK**.

ACP receives the OAuth authorization code, exchanges it server-side, verifies `id,username`, converts to a long-lived token, encrypts it with the existing `core.crypto` routine, and stores only the encrypted channel token.

For `REMOTE_AVD`, raw Android hierarchy XML is parsed in memory and is not stored in Factory DB/logs. Password-like and verification-code EditText values are redacted by the hierarchy reader before selectors/flows see the snapshot. Screenshots are not part of the normal decision loop.

## Required ACP environment

Keep the existing values for `THREADS_APP_ID`, `THREADS_APP_SECRET`, `ACP_MASTER_KEY`, database configuration and OAuth public URL. For the dedicated Account Factory Controller add:

```bash
ACP_FACTORY_API_KEY=<random operator/fallback key>
ACP_PUBLIC_BASE_URL=https://your-public-acp-host.example

# Android zero-config LAN discovery
ACP_HOST=0.0.0.0
ACP_PORT=5001
ACP_FACTORY_LAN_AUTO_ENROLL=true
```

`ACP_FACTORY_LAN_AUTO_ENROLL=true` should only be enabled on a private/trusted LAN. Enrollment requests from public IP addresses are rejected, and regular Factory V2 endpoints still require either a valid per-device credential or the legacy Factory Key.

Generate a Factory Key locally without putting it in shell history as an argument:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Store secrets in `shared/.env.local` using the repository's normal secret-management workflow. Never commit the plaintext value.

## Meta Threads redirect URI

Configure the Threads app callback exactly as:

```text
https://your-public-acp-host.example/oauth/account-factory/threads/callback
```

`ACP_PUBLIC_BASE_URL` must use the same externally reachable HTTPS origin.

## Run Account Factory Controller

```bash
cd ~/Downloads/ACP/worktrees/account-factory-android
source .venv/bin/activate
set -a
source ~/Downloads/ACP/shared/.env.local
set +a

export ANDROID_HOME="$HOME/Android/Sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"

export ACP_FACTORY_CONTROLLER=1
export ACP_FACTORY_TICK_SECONDS=2
export ACP_HOST=0.0.0.0
export ACP_PORT=5001

ACP_ADAPTER=mock ACP_SOURCE=mock python3 account_factory_server.py
```

With the LAN settings above the dedicated service listens on port `5001`. The Android app discovers it on the current private Wi-Fi subnet. Public OAuth callback traffic may still arrive through the approved HTTPS tunnel/reverse proxy using `ACP_PUBLIC_BASE_URL`.

The launcher does not publish a social post by itself.

## Android build

Open `android/account-factory` in Android Studio, or build from the repository root when Android SDK 36 + JDK 17 + Gradle are available:

```bash
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
~/.local/gradle/gradle-8.13/bin/gradle \
  -p android/account-factory \
  testDebugUnitTest assembleDebug \
  --no-daemon --max-workers=2 --console=plain
```

APK output:

```text
android/account-factory/app/build/outputs/apk/debug/app-debug.apk
```

## First Android setup — zero-config

Prerequisites:

1. Phone and Ubuntu Controller are on the same private Wi-Fi/LAN.
2. Controller is running with `ACP_HOST=0.0.0.0`, `ACP_PORT=5001`, `ACP_FACTORY_LAN_AUTO_ENROLL=true`.
3. Install the newly built APK.

Normal first launch:

```text
Open ACP Account Factory
  → foreground runner service starts
  → app detects the phone's private IPv4
  → scans only that /24 on port 5001
  → verifies GET /api/factory/discovery
  → POST /api/factory/enroll
  → receives a per-device credential
  → stores it encrypted with Android Keystore
  → starts LOCAL_DEVICE runner
  → later launches reconnect automatically
```

There is no required Controller URL or Factory Key input on the phone. The Settings dialog remains available only as a manual troubleshooting fallback.

### Accessibility — still manual once

Before a LOCAL_DEVICE action that needs Accessibility, Android may open:

```text
Settings → Accessibility → Installed apps → ACP Account Factory
```

Enable the service once. The app cannot legally or technically auto-grant this permission itself.

## Zero-config API contract

### Discovery

`GET /api/factory/discovery`

No authentication header is required. The response contains only non-secret service metadata:

```json
{
  "ok": true,
  "service": "account-factory",
  "api_version": 2
}
```

### Enrollment

`POST /api/factory/enroll`

Allowed only when LAN auto-enroll is enabled and the request source is private/link-local/loopback.

```json
{
  "device_id": "local-<stable-uuid>",
  "device_name": "Samsung SM-..."
}
```

The successful response contains a newly issued `device_token`. Re-enrolling the same `device_id` rotates the previous credential.

### Authenticated Factory V2 calls

New clients can authenticate with:

```text
X-ACP-Device-Token: <device credential>
```

The Android app keeps compatibility with its existing networking layer; the Controller auth bridge also recognizes an enrolled credential presented in the existing `X-ACP-Factory-Key` slot. A real `ACP_FACTORY_API_KEY` continues to work unchanged.

## LOCAL_DEVICE operator flow

1. Open Account Factory. Controller discovery/enrollment and LOCAL_DEVICE runner startup happen automatically.
2. Create/select the account work item.
3. Open Instagram/Threads and complete any signup or verification steps required by the official app.
4. If Android requests Accessibility, enable ACP Account Factory once in Settings.
5. Continue the workflow until Threads is created.
6. Start the official ACP OAuth connection.
7. Meta redirects to ACP; ACP validates the expected username and activates the channel only on a matching identity.
8. When ACP reports `ACTIVE`, the account reaches `ACP_ACTIVE`.

## REMOTE_AVD fail-closed automation

`REMOTE_AVD` runs in the isolated Python worker process and is intentionally narrower than general Android automation.

Allowed behavior:

```text
known screen detection
known control tap with verified successor
username/display_name/bio fill only
open official Instagram/Threads package
observe protected checkpoint until a known safe successor appears
open the existing official ACP OAuth URL
```

Never automated:

```text
password entry or generation
OTP/email/SMS verification code retrieval or entry
CAPTCHA
phone/email verification input
selfie/identity/security/recovery challenges
fingerprint/proxy/device-identity evasion
Threads compose/publish
blind tap on UNKNOWN UI
```

Normal flow:

```text
PROFILE_READY
  -> RUNNER_ASSIGNED
  -> PREPARE_INSTAGRAM
  -> AUTOMATE_INSTAGRAM

protected Instagram UI
  -> IG_READY_FOR_HUMAN
  -> WAITING_HUMAN
  -> OBSERVE_CHECKPOINT only
  -> known successor
  -> IG_CREATED

IG_CREATED
  -> AUTOMATE_THREADS

protected Threads UI
  -> THREADS_READY_FOR_HUMAN
  -> WAITING_HUMAN
  -> OBSERVE_CHECKPOINT only
  -> known successor
  -> THREADS_CREATED

THREADS_CREATED
  -> existing ACP activation/OAuth path
  -> ACP_ACTIVE
```

Unknown UI becomes `NEEDS_CONFIRMATION`; rate-limit/action-block/network conditions become bounded retry states; account-disabled becomes terminal error for the current account. A normal safe UI action receives at most three total attempts. Replayed commands first detect current UI, so an already-reached successor is treated as completion rather than replaying the previous mutation.

### AVD pilot: `acp-worker-01`

From the feature worktree:

```bash
cd ~/Downloads/ACP/worktrees/account-factory-android

export ANDROID_HOME="$HOME/Android/Sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"

$ANDROID_HOME/emulator/emulator -accel-check
$ANDROID_HOME/emulator/emulator -list-avds
adb devices
```

Expected prerequisites:

```text
KVM installed and usable
AVD named acp-worker-01 exists
Controller is running
worker becomes REMOTE_AVD/READY
```

Once the emulator is booted, verify official packages:

```bash
SERIAL=$(adb devices | awk '/^emulator-[0-9]+[[:space:]]+device$/ {print $1; exit}')
test -n "$SERIAL"
adb -s "$SERIAL" shell pm list packages | grep -E 'com.instagram.android|com.instagram.barcelona'
```

Both must be installed through normal Android/Play Store means. Do not sideload modified social-app packages for this pilot.

Create one account targeting `AUTO_AVD` or the specific ready `REMOTE_AVD` worker from the Account Factory UI/API. During the pilot verify:

```text
Instagram opens automatically
known safe screens may be handled
contact/password/OTP/CAPTCHA/security screen immediately stops mutation
operator completes the protected step manually in the official app
controller keeps sending observation-only checkpoint checks
known successor automatically resumes
Threads follows the same fail-closed policy
THREADS_CREATED starts the existing ACP OAuth path
UNKNOWN never receives an exploratory tap
```

If the dashboard reports `capacity_state=RED` and `desired_workers=0`, no new AVD worker will be booted. Free host RAM/swap pressure or stop other emulators/processes before retrying the pilot; do not bypass the resource safety policy just to force an AVD start.

## Verification commands

Run the complete repository gate from the feature branch:

```bash
cd ~/Downloads/ACP/worktrees/account-factory-android

JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
bash scripts/verify_account_factory_dual_runner.sh
```

The script covers existing Factory V2 regressions plus the AVD hierarchy, detector, driver, Instagram/Threads flows, worker agent, runtime routing and Android unit/build gates.

For Python-only diagnosis:

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock ACP_ENV=development \
python3 -m unittest discover -s tests -p 'test*.py' -v
```

For Android-only diagnosis:

```bash
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
~/.local/gradle/gradle-8.13/bin/gradle \
  -p android/account-factory \
  testDebugUnitTest assembleDebug \
  --no-daemon --max-workers=2 --console=plain
```

Do not claim the release gate is green unless these commands produce fresh PASS/`BUILD SUCCESSFUL` output. Do not run a live Threads publish as part of Account Factory verification.
