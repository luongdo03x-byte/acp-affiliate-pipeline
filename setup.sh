#!/usr/bin/env bash
set -Eeuo pipefail

BASE="${ACP_BASE:-$HOME/Downloads/ACP}"
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
PORTABLE_REPO="${ACP_PORTABLE_REPO:-luongdo03x-byte/acp-affiliate-pipeline}"
PYTHON="$REPO_ROOT/.venv/bin/python"

info() { printf '%s\n' "$*"; }
die() { printf '✗ %s\n' "$*" >&2; exit 1; }

[[ -f "$REPO_ROOT/requirements.txt" ]] || die "Thiếu requirements.txt trong $REPO_ROOT"
[[ -f "$REPO_ROOT/manage.sh" ]] || die "Thiếu manage.sh trong $REPO_ROOT"

[[ -n "${ACP_PORTABLE_BUNDLE_KEY:-}" ]] || die "PORTABLE_BUNDLE_KEY_REQUIRED"

if [[ ! -x "$PYTHON" ]]; then
    command -v python3 >/dev/null 2>&1 || die "Thiếu python3 để tạo virtualenv"
    python3 -m venv "$REPO_ROOT/.venv"
fi

"$PYTHON" -m pip install --disable-pip-version-check -r "$REPO_ROOT/requirements.txt"

# Keep the previous durable state until the imported generation passes doctor.
# The rollback directory stays outside shared/ so handoff-in can replace shared
# without destroying the recovery copy.  It is temporary and never committed.
mkdir -p "$BASE"
ROLLBACK_ROOT="$(mktemp -d "$BASE/.portable-rollback.XXXXXX")"
HAD_SHARED=0
ROLLBACK_ARMED=0
if [[ -e "$BASE/shared" ]]; then
    cp -a "$BASE/shared" "$ROLLBACK_ROOT/shared"
    HAD_SHARED=1
fi
ROLLBACK_ARMED=1

rollback_shared_state() {
    local original_status=$?
    local rollback_status=0
    trap - ERR
    set +e

    if [[ "$ROLLBACK_ARMED" == "1" ]]; then
        rm -rf "$BASE/shared" || rollback_status=1
        if [[ "$HAD_SHARED" == "1" ]]; then
            cp -a "$ROLLBACK_ROOT/shared" "$BASE/shared" || rollback_status=1
        fi
    fi
    rm -rf "$ROLLBACK_ROOT" || rollback_status=1

    if [[ "$rollback_status" != "0" ]]; then
        printf 'PORTABLE_ROLLBACK_FAILED\n' >&2
        exit 70
    fi
    exit "$original_status"
}
trap rollback_shared_state ERR

# Restore durable state before manage.sh setup can create any new local key/state.
# Disable the inherited ERR trap inside subshells so rollback runs exactly once
# in the parent shell if a step fails.
(
    trap - ERR
    cd "$REPO_ROOT"
    "$PYTHON" -m core.factory_v2.portable_cli handoff-in \
        --base "$BASE" \
        --repo "$PORTABLE_REPO"
)

ACP_BASE="$BASE" "$REPO_ROOT/manage.sh" setup

# A portable receiving machine needs the configured factory AVD before the
# doctor can prove it is bootable.  Do not create images, accept licenses, or
# change host security settings implicitly; stop with a stable prerequisite.
(
    trap - ERR
    cd "$REPO_ROOT"
    "$PYTHON" -c '
from core.factory_v2.avd import AvdManager
import sys
try:
    avds = set(AvdManager().list_avds())
except Exception:
    avds = set()
if "acp-worker-01" not in avds:
    print("ANDROID_AVD_PREREQUISITE: acp-worker-01 missing", file=sys.stderr)
    raise SystemExit(18)
' setup-avd-prereq
)

# Doctor owns the Android/AVD/callback readiness probes and fails closed with a
# stable code.  Do not auto-accept Android licenses or change host security.
(
    trap - ERR
    cd "$REPO_ROOT"
    "$PYTHON" -m core.factory_v2.portable_cli doctor \
        --base "$BASE" \
        --repo-root "$REPO_ROOT"
)

# Imported durable state is now validated.  From this point onward a resume
# failure must preserve that authoritative state rather than roll it back.
trap - ERR
ROLLBACK_ARMED=0
rm -rf "$ROLLBACK_ROOT"
ROLLBACK_ROOT=""

(
    cd "$REPO_ROOT"
    "$PYTHON" -m core.factory_v2.portable_cli resume \
        --base "$BASE"
)

if [[ "${ACP_PORTABLE_NO_START:-0}" == "1" ]]; then
    info "PORTABLE_SETUP_OK no-start"
    exit 0
fi

if [[ -x "$BASE/manage.sh" ]]; then
    ACP_BASE="$BASE" "$BASE/manage.sh" factory-start
else
    ACP_BASE="$BASE" "$REPO_ROOT/manage.sh" factory-start
fi

info "PORTABLE_SETUP_OK"
