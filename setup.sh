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

if [[ ! -x "$PYTHON" ]]; then
    command -v python3 >/dev/null 2>&1 || die "Thiếu python3 để tạo virtualenv"
    python3 -m venv "$REPO_ROOT/.venv"
fi

"$PYTHON" -m pip install --disable-pip-version-check -r "$REPO_ROOT/requirements.txt"

# Restore durable state before manage.sh setup can create any new local key/state.
(
    cd "$REPO_ROOT"
    "$PYTHON" -m core.factory_v2.portable_cli handoff-in \
        --base "$BASE" \
        --repo "$PORTABLE_REPO"
)

ACP_BASE="$BASE" "$REPO_ROOT/manage.sh" setup

# Doctor owns the Android/AVD/callback readiness probes and fails closed with a
# stable code.  Do not auto-accept Android licenses or change host security.
(
    cd "$REPO_ROOT"
    "$PYTHON" -m core.factory_v2.portable_cli doctor \
        --base "$BASE" \
        --repo-root "$REPO_ROOT"
)

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
