#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

echo "==> Python syntax check"
python3 -m compileall -q core/factory_v2 web account_factory_server.py

echo "==> Account Factory backend tests"
ACP_ADAPTER=mock \
ACP_SOURCE=mock \
ACP_ENV=development \
ACP_FACTORY_API_KEY="${ACP_FACTORY_API_KEY:-test-key}" \
ACP_PUBLIC_BASE_URL="${ACP_PUBLIC_BASE_URL:-https://factory.example.com}" \
python3 -m unittest \
  tests.test_account_factory \
  tests.test_factory_v2_schema \
  tests.test_factory_v2_state_machine \
  tests.test_factory_v2_identity \
  tests.test_factory_v2_service \
  tests.test_factory_v2_resource_policy \
  tests.test_factory_v2_avd \
  tests.test_factory_v2_scheduler \
  tests.test_factory_v2_scheduler_recovery \
  tests.test_factory_v2_supervisor \
  tests.test_factory_v2_supervisor_local \
  tests.test_factory_v2_worker_process \
  tests.test_factory_v2_runtime \
  tests.test_factory_v2_runtime_atomicity \
  tests.test_factory_v2_runtime_resume \
  tests.test_factory_v2_runtime_activation \
  tests.test_factory_v2_restart_recovery \
  tests.test_factory_v2_runner_schema \
  tests.test_factory_v2_runner_service \
  tests.test_factory_v2_dual_scheduler \
  tests.test_factory_v2_runner_gateway \
  tests.test_factory_v2_runner_api \
  tests.test_factory_v2_runner_commands_api \
  tests.test_factory_v2_create_account \
  tests.test_factory_v2_factory_app \
  tests.test_factory_v2_api \
  tests.test_factory_v2_oauth_bridge \
  tests.test_factory_v2_oauth_expiry \
  tests.test_factory_v2_activation \
  tests.test_factory_v2_launcher -v

if [[ -x android/account-factory/gradlew ]]; then
  GRADLE=(android/account-factory/gradlew -p android/account-factory)
elif command -v gradle >/dev/null 2>&1; then
  GRADLE=(gradle -p android/account-factory)
elif [[ -x "$HOME/.local/gradle/gradle-8.13/bin/gradle" ]]; then
  GRADLE=("$HOME/.local/gradle/gradle-8.13/bin/gradle" -p android/account-factory)
else
  echo "ERROR: Gradle 8.13 is required but no gradle/gradlew was found." >&2
  echo "Install Gradle 8.13 user-locally or generate android/account-factory/gradlew, then rerun this script." >&2
  exit 127
fi

echo "==> Android unit tests + debug APK"
"${GRADLE[@]}" \
  testDebugUnitTest assembleDebug \
  --no-daemon --max-workers=2 --console=plain

echo "==> Verification completed"
echo "APK: android/account-factory/app/build/outputs/apk/debug/app-debug.apk"
