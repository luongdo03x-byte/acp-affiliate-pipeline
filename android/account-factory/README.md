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
- launches the OAuth URL returned by ACP
- polls ACP until the channel is ACTIVE or the OAuth attempt fails

The operator still completes Instagram/Threads signup, OTP/CAPTCHA and identity/security checks in the official apps.

## Secret boundary

The APK does not contain `THREADS_APP_SECRET` or `ACP_MASTER_KEY` and does not receive/store Threads access tokens or account passwords. The only ACP-specific credential entered in the app is the Account Factory pairing key (`ACP_FACTORY_API_KEY`).

## Build

Requirements: Android SDK 36, JDK 17, Gradle 8.13.

```bash
gradle -p android/account-factory testDebugUnitTest assembleDebug
```

Debug APK:

```text
android/account-factory/app/build/outputs/apk/debug/app-debug.apk
```

See `docs/ACP_ACCOUNT_FACTORY_RUNBOOK.md` for ACP server and Meta callback configuration.
