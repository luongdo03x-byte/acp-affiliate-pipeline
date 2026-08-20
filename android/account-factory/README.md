# ACP Account Factory Android

Android operator app for tracking a 50-profile Instagram → Threads onboarding batch and connecting completed Threads profiles to ACP with official Threads OAuth.

## P0 screens

- Batch Dashboard
- Account Workflow
- All Accounts + filters

## What the app automates

- generates local profile metadata and 10 groups × 5
- persists progress with Room
- copies username/display name/bio
- opens Instagram and Threads
- auto-discovers the Account Factory Controller on the same private Wi-Fi
- auto-enrolls the phone for a revocable per-device credential
- encrypts that credential with Android Keystore and reconnects automatically
- starts the LOCAL_DEVICE runner automatically after enrollment
- launches the OAuth URL returned by ACP
- polls ACP until the channel is ACTIVE or the OAuth attempt fails

The operator still completes Instagram/Threads signup, OTP/CAPTCHA and identity/security checks in the official apps. Android Accessibility must also be enabled manually once because Android does not allow an app to grant itself that permission.

## Zero-config first launch

On a new install the normal flow is:

```text
Open APK
  → find Controller on private Wi-Fi (/24, port 5001)
  → enroll this phone
  → save encrypted device credential
  → start LOCAL_DEVICE runner
  → reconnect automatically on later launches
```

No Controller URL or Factory Key is required on the phone when LAN auto-enrollment is enabled on the Controller. The manual connection dialog remains only as a troubleshooting fallback.

Controller requirements:

```bash
ACP_HOST=0.0.0.0
ACP_PORT=5001
ACP_FACTORY_LAN_AUTO_ENROLL=true
```

Enable auto-enrollment only on a private/trusted LAN. The Controller stores only a SHA-256 hash of each device credential; the raw token is returned once during enrollment and is encrypted on Android with Android Keystore.

## Secret boundary

The APK does not contain `ACP_FACTORY_API_KEY`, `THREADS_APP_SECRET` or `ACP_MASTER_KEY` and does not receive/store Threads access tokens or account passwords. `ACP_FACTORY_API_KEY` remains a server-side operator/fallback credential only.

## Build

Requirements: Android SDK 36, JDK 17, Gradle 8.13.

```bash
gradle -p android/account-factory testDebugUnitTest assembleDebug
```

Debug APK:

```text
android/account-factory/app/build/outputs/apk/debug/app-debug.apk
```

See `docs/ACP_ACCOUNT_FACTORY_RUNBOOK.md` for ACP server, zero-config LAN enrollment and Meta callback configuration.
