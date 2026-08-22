#!/usr/bin/env bash
set -Eeuo pipefail

BASE="${ACP_BASE:-$HOME/Downloads/ACP}"
ACTIVE="$BASE/acp"
SHARED="$BASE/shared"
RUN_DIR="$SHARED/run"
LOG_DIR="$BASE/logs"
BACKUP_DIR="$BASE/backups"
RELEASES_DIR="$BASE/releases"
APP_PID="$RUN_DIR/acp.pid"
NGROK_PID="$RUN_DIR/ngrok.pid"
FACTORY_PID="$RUN_DIR/account-factory.pid"
PREVIOUS_FILE="$RUN_DIR/previous_release"
SCRIPT_REAL="$(readlink -f "$0")"

mkdir -p "$RUN_DIR" "$LOG_DIR" "$BACKUP_DIR" "$RELEASES_DIR"

info() { printf '%s\n' "$*"; }
warn() { printf '⚠ %s\n' "$*" >&2; }
die() { printf '✗ %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Cách dùng:
  ./manage.sh setup        # lần đầu trên máy mới, sau khi git clone
  ./manage.sh encrypt-secrets  # mã hoá shared/.env.local để commit vào git
  ./manage.sh start
  ./manage.sh stop
  ./manage.sh restart
  ./manage.sh factory-start
  ./manage.sh factory-stop
  ./manage.sh handoff-out
  ./manage.sh status
  ./manage.sh test
  ./manage.sh upgrade <file.zip> <version>
  ./manage.sh rollback

Biến tùy chọn:
  ACP_BASE=/duong/dan/ACP   # mặc định ~/Downloads/ACP
EOF
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Thiếu lệnh bắt buộc: $1"
}

current_release() {
    [[ -e "$ACTIVE" || -L "$ACTIVE" ]] || die "Không tìm thấy bản đang chạy: $ACTIVE"
    readlink -f "$ACTIVE"
}

release_version() {
    local release="$1"
    basename "$(dirname "$release")"
}

load_env_from() {
    local release="$1"
    local env_file="$release/.env.local"
    [[ -f "$env_file" ]] || die "Không tìm thấy $env_file"
    set +u
    # shellcheck disable=SC1090
    source "$env_file"
    set -u
}

pid_matches() {
    local pid_file="$1"
    local needle="$2"
    [[ -f "$pid_file" ]] || return 1
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    local cmd
    cmd="$(ps -p "$pid" -o args= 2>/dev/null || true)"
    [[ "$cmd" == *"$needle"* ]]
}

stop_pid() {
    local pid_file="$1"
    local needle="$2"
    local label="$3"

    if ! pid_matches "$pid_file" "$needle"; then
        rm -f "$pid_file"
        return 0
    fi

    local pid
    pid="$(cat "$pid_file")"
    kill "$pid" 2>/dev/null || true
    for _ in {1..20}; do
        if ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$pid_file"
            info "✓ Đã dừng $label"
            return 0
        fi
        sleep 0.25
    done

    warn "$label chưa dừng sau 5 giây; gửi SIGKILL"
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$pid_file"
}

derive_ngrok_url() {
    local candidate="${ACP_NGROK_URL:-}"
    if [[ -z "$candidate" && "${ACP_PUBLIC_BASE_URL:-}" == *ngrok* ]]; then
        candidate="$ACP_PUBLIC_BASE_URL"
    fi
    if [[ -z "$candidate" && "${ACP_MEDIA_BASE_URL:-}" == *ngrok* ]]; then
        candidate="${ACP_MEDIA_BASE_URL%/media}"
    fi
    printf '%s' "$candidate"
}

wait_http() {
    local port="$1"
    local code
    for _ in {1..30}; do
        code="$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:${port}/" 2>/dev/null || true)"
        if [[ -n "$code" && "$code" != "000" ]]; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

secrets_blob_path() {
    local release="$1"
    printf '%s' "$release/secrets/env.local.gpg"
}

cmd_encrypt_secrets() {
    # Mã hoá shared/.env.local (secret thật) thành 1 file nhị phân AN TOÀN
    # ĐỂ COMMIT vào git -- không bao giờ commit .env.local dạng chữ thường.
    # Passphrase KHÔNG được truyền qua đối số/biến môi trường (sẽ lộ qua
    # history shell/ps); gpg tự hỏi qua pinentry.
    require_cmd gpg
    local release
    release="$(current_release)"
    [[ -f "$SHARED/.env.local" ]] || die "Chưa có $SHARED/.env.local để mã hoá"

    local blob
    blob="$(secrets_blob_path "$release")"
    mkdir -p "$(dirname "$blob")"
    local tmp_blob="$blob.tmp"
    rm -f "$tmp_blob"
    gpg --symmetric --cipher-algo AES256 --s2k-digest-algo SHA512 \
        -o "$tmp_blob" "$SHARED/.env.local" || { rm -f "$tmp_blob"; die "Mã hoá thất bại"; }
    mv "$tmp_blob" "$blob"

    info "SECRETS_ENCRYPTED=$blob"
    info "Nhớ: git add \"$blob\" && git commit -- rồi lưu passphrase vừa nhập ở nơi"
    info "khác git (trình quản lý mật khẩu...). Mất passphrase là mất luôn nội dung."
}

cmd_setup() {
    # Bootstrap một lần trên máy mới, chạy từ bên trong release vừa clone
    # (releases/<version>/acp/manage.sh setup), TRƯỚC KHI $ACTIVE tồn tại --
    # nên không dùng current_release() ở đây, mà lấy release từ chính vị
    # trí của script này.
    require_cmd python3
    local release
    release="$(dirname "$SCRIPT_REAL")"
    [[ -f "$release/run.py" ]] || die "Không thấy run.py cạnh manage.sh -- chạy setup từ trong thư mục release (releases/<version>/acp)"
    [[ -f "$release/.env.example" ]] || die "Thiếu .env.example trong $release"

    mkdir -p "$SHARED/var"

    # venv trước, vì bước sinh ACP_MASTER_KEY bên dưới cần `run.py genkey`
    # chạy được (cần gói cryptography đã cài).
    if [[ -x "$release/.venv/bin/python" ]]; then
        info "Đã có virtualenv -- bỏ qua cài đặt."
    else
        install_release "$release"
        info "Đã tạo virtualenv + cài dependencies."
    fi

    # Không bao giờ ghi đè .env.local đã có -- có thể là bản đã copy
    # nguyên từ máy cũ (kèm token/khoá thật) để giữ nguyên kết nối Threads.
    local secrets_blob
    secrets_blob="$(secrets_blob_path "$release")"
    if [[ -f "$SHARED/.env.local" ]]; then
        info "Đã có $SHARED/.env.local -- giữ nguyên, không ghi đè."
    elif [[ -f "$secrets_blob" ]]; then
        require_cmd gpg
        info "Tìm thấy $secrets_blob -- nhập passphrase để giải mã (gpg sẽ tự hỏi):"
        local tmp_env="$SHARED/.env.local.tmp"
        rm -f "$tmp_env"
        if gpg --decrypt -o "$tmp_env" "$secrets_blob"; then
            mv "$tmp_env" "$SHARED/.env.local"
            chmod 600 "$SHARED/.env.local"
            info "Đã giải mã $SHARED/.env.local -- giữ nguyên toàn bộ secret/kết nối như máy cũ."
        else
            rm -f "$tmp_env"
            die "Giải mã $secrets_blob thất bại (sai passphrase?) -- chạy lại setup để thử lại."
        fi
    else
        cp "$release/.env.example" "$SHARED/.env.local"
        local db_path="$SHARED/var/acp.db"
        local master_key
        master_key="$("$release/.venv/bin/python" "$release/run.py" genkey)"
        # portable sed -i: dùng file tạm thay vì -i '' (khác nhau giữa GNU/BSD sed).
        local tmp_env="$SHARED/.env.local.tmp"
        sed -e "s#^ACP_DB=\$#ACP_DB=$db_path#" \
            -e "s#^ACP_MASTER_KEY=\$#ACP_MASTER_KEY=$master_key#" \
            "$SHARED/.env.local" >"$tmp_env"
        mv "$tmp_env" "$SHARED/.env.local"
        chmod 600 "$SHARED/.env.local"
        info "Đã tạo $SHARED/.env.local từ .env.example (ACP_DB + ACP_MASTER_KEY tự điền)."
        warn "BẮT BUỘC điền tay trước khi dùng thật: ACP_ADMIN_PASSWORD, ACP_SECRET_KEY,"
        warn "  và tuỳ nhu cầu: ACCESSTRADE_API_TOKEN (catalog), ACP_GEMINI_API_KEY (caption LLM),"
        warn "  ACP_PUBLIC_BASE_URL/ACP_MEDIA_BASE_URL (cần cho Threads publish thật -- không phải localhost)."
        warn "  Đăng bài Threads thật còn cần re-auth OAuth riêng (token không tự sinh được) -- xem docs/ACP_RUNBOOK.md."
    fi

    [[ -L "$release/.env.local" || -f "$release/.env.local" ]] || ln -s "$SHARED/.env.local" "$release/.env.local"
    [[ -L "$release/var" || -d "$release/var" ]] || ln -s "$SHARED/var" "$release/var"

    # Chỉ tạo schema, không seed dữ liệu demo -- an toàn kể cả khi
    # shared/var đã có DB được copy/restore từ máy khác.
    migrate_release "$release"
    info "Đã đảm bảo schema CSDL."

    if [[ ! -e "$ACTIVE" ]]; then
        ln -s "$release" "$ACTIVE"
        info "Đã tạo $ACTIVE -> $release"
    fi
    if [[ ! -e "$BASE/manage.sh" ]]; then
        ln -s "$ACTIVE/manage.sh" "$BASE/manage.sh"
        info "Đã tạo $BASE/manage.sh"
    fi

    info "SETUP_OK"
    info "Tiếp theo: kiểm tra $SHARED/.env.local rồi chạy: $BASE/manage.sh start"
}

cmd_start() {
    require_cmd curl
    local release
    release="$(current_release)"
    [[ -f "$release/run.py" ]] || die "Thiếu run.py trong $release"
    [[ -x "$release/.venv/bin/python" ]] || die "Thiếu virtualenv: $release/.venv"

    load_env_from "$release"
    local port="${PORT:-5000}"

    if pid_matches "$APP_PID" "run.py serve"; then
        info "ACP_ALREADY_RUNNING pid=$(cat "$APP_PID")"
    else
        rm -f "$APP_PID"
        (
            cd "$release"
            nohup "$release/.venv/bin/python" "$release/run.py" serve \
                >>"$LOG_DIR/acp.log" 2>&1 &
            echo $! >"$APP_PID"
        )

        if ! wait_http "$port"; then
            warn "ACP không trả HTTP trên 127.0.0.1:$port"
            tail -n 30 "$LOG_DIR/acp.log" 2>/dev/null || true
            stop_pid "$APP_PID" "run.py serve" "ACP" || true
            return 1
        fi
        info "ACP_STARTED pid=$(cat "$APP_PID") url=http://127.0.0.1:$port"
    fi

    local ngrok_url
    ngrok_url="$(derive_ngrok_url)"
    if [[ -n "$ngrok_url" ]]; then
        if ! command -v ngrok >/dev/null 2>&1; then
            warn "Có URL ngrok nhưng máy chưa có lệnh ngrok; ACP local vẫn đang chạy."
        elif pid_matches "$NGROK_PID" "ngrok http"; then
            info "NGROK_ALREADY_RUNNING pid=$(cat "$NGROK_PID") url=$ngrok_url"
        else
            rm -f "$NGROK_PID"
            nohup ngrok http "$port" --url "$ngrok_url" \
                >>"$LOG_DIR/ngrok.log" 2>&1 &
            echo $! >"$NGROK_PID"
            sleep 1
            if pid_matches "$NGROK_PID" "ngrok http"; then
                info "NGROK_STARTED pid=$(cat "$NGROK_PID") url=$ngrok_url"
            else
                warn "ngrok không khởi động được; xem $LOG_DIR/ngrok.log"
                rm -f "$NGROK_PID"
            fi
        fi
    fi
}

cmd_stop() {
    stop_pid "$NGROK_PID" "ngrok http" "ngrok"
    stop_pid "$APP_PID" "run.py serve" "ACP"
    info "ACP_STOPPED"
}

cmd_restart() {
    cmd_stop
    cmd_start
}

require_factory_ownership() {
    local release="$1"
    (
        cd "$release"
        "$release/.venv/bin/python" - "$SHARED/machine.json" <<'PY'
from pathlib import Path
import sys
from core.factory_v2.portable_state import require_active_ownership
try:
    require_active_ownership(Path(sys.argv[1]))
except RuntimeError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
PY
    )
}

cmd_factory_start() {
    local release
    release="$(current_release)"
    [[ -f "$release/account_factory_server.py" ]] || die "Thiếu account_factory_server.py trong $release"
    [[ -x "$release/.venv/bin/python" ]] || die "Thiếu virtualenv: $release/.venv"

    require_factory_ownership "$release"

    if pid_matches "$FACTORY_PID" "account_factory_server.py"; then
        info "FACTORY_ALREADY_RUNNING pid=$(cat "$FACTORY_PID")"
        return 0
    fi

    rm -f "$FACTORY_PID"
    (
        cd "$release"
        ACP_BASE="$BASE" nohup "$release/.venv/bin/python" "$release/account_factory_server.py" \
            >>"$LOG_DIR/account-factory.log" 2>&1 &
        echo $! >"$FACTORY_PID"
    )
    info "FACTORY_STARTED pid=$(cat "$FACTORY_PID")"
}

cmd_factory_stop() {
    stop_pid "$FACTORY_PID" "account_factory_server.py" "Account Factory"
    info "FACTORY_STOPPED"
}

github_repo_slug() {
    local remote="$1"
    remote="${remote%.git}"
    case "$remote" in
        git@github.com:*) printf '%s' "${remote#git@github.com:}" ;;
        https://github.com/*) printf '%s' "${remote#https://github.com/}" ;;
        ssh://git@github.com/*) printf '%s' "${remote#ssh://git@github.com/}" ;;
        *) return 1 ;;
    esac
}

require_factory_quiescent() {
    if pgrep -af 'account_factory_server.py' >/dev/null 2>&1; then
        die "FACTORY_NOT_QUIESCENT"
    fi
    if pgrep -af 'account_factory_worker.py' >/dev/null 2>&1; then
        die "FACTORY_NOT_QUIESCENT"
    fi
}

cmd_handoff_out() {
    local release remote repo git_commit git_branch
    release="$(current_release)"
    [[ -x "$release/.venv/bin/python" ]] || die "Thiếu virtualenv: $release/.venv"

    require_factory_ownership "$release"
    cmd_factory_stop
    cmd_stop
    require_factory_quiescent

    (
        cd "$release"
        "$release/.venv/bin/python" -m core.factory_v2.portable_cli resume \
            --base "$BASE"
    )

    remote="$(git -C "$release" remote get-url origin 2>/dev/null || true)"
    repo="$(github_repo_slug "$remote" 2>/dev/null || true)"
    [[ -n "$repo" ]] || die "GITHUB_REPO_UNAVAILABLE"
    git_commit="$(git -C "$release" rev-parse HEAD 2>/dev/null || true)"
    git_branch="$(git -C "$release" branch --show-current 2>/dev/null || true)"
    [[ -n "$git_commit" && -n "$git_branch" ]] || die "GIT_METADATA_UNAVAILABLE"

    (
        cd "$release"
        "$release/.venv/bin/python" -m core.factory_v2.portable_cli handoff-out \
            --base "$BASE" \
            --repo "$repo" \
            --git-commit "$git_commit" \
            --git-branch "$git_branch"
    )
}

cmd_status() {
    local release version
    release="$(current_release)"
    version="$(release_version "$release")"
    info "Release : $version"
    info "Path    : $release"

    if pid_matches "$APP_PID" "run.py serve"; then
        info "ACP     : RUNNING (pid $(cat "$APP_PID"))"
    else
        info "ACP     : STOPPED"
    fi

    if pid_matches "$FACTORY_PID" "account_factory_server.py"; then
        info "Factory : RUNNING (pid $(cat "$FACTORY_PID"))"
    else
        info "Factory : STOPPED"
    fi

    if pid_matches "$NGROK_PID" "ngrok http"; then
        info "ngrok   : RUNNING (pid $(cat "$NGROK_PID"))"
    else
        info "ngrok   : STOPPED"
    fi

    if [[ -d "$release/.git" ]]; then
        info "Git     : $(git -C "$release" branch --show-current 2>/dev/null || true) @ $(git -C "$release" rev-parse --short HEAD 2>/dev/null || true)"
    fi
}

run_release_tests() {
    local release="$1"
    local parent
    parent="$(dirname "$release")"
    [[ -x "$release/.venv/bin/python" ]] || die "Thiếu virtualenv: $release/.venv"

    load_env_from "$release"
    (
        cd "$parent"
        ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= "$release/.venv/bin/python" -m acp.tests.test_pipeline
        ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= "$release/.venv/bin/python" -m acp.tests.test_pilot
    )
    (
        cd "$release"
        ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= "$release/.venv/bin/python" run.py doctor
    )
}

cmd_test() {
    local release
    release="$(current_release)"
    run_release_tests "$release"
    info "TEST_OK"
}

backup_db() {
    local release="$1"
    local version="$2"
    load_env_from "$release"
    [[ -n "${ACP_DB:-}" ]] || die "ACP_DB chưa được khai trong .env.local"
    [[ -f "$ACP_DB" ]] || die "Không tìm thấy database: $ACP_DB"

    local stamp backup
    stamp="$(date +%Y%m%d-%H%M%S)"
    backup="$BACKUP_DIR/acp-live-before-${version}-${stamp}.db"

    python3 - "$ACP_DB" "$backup" <<'PY'
import sqlite3
import sys
src_path, dst_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(src_path)
dst = sqlite3.connect(dst_path)
try:
    src.backup(dst)
finally:
    dst.close()
    src.close()
PY
    [[ -s "$backup" ]] || die "Backup database thất bại"
    info "DB_BACKUP=$backup"
}

extract_release() {
    local zip_file="$1"
    local version="$2"
    local release_root="$RELEASES_DIR/$version"
    local new="$release_root/acp"

    [[ -f "$zip_file" ]] || die "Không tìm thấy ZIP: $zip_file"
    [[ ! -e "$release_root" ]] || die "Release $version đã tồn tại: $release_root"
    require_cmd unzip

    local tmp stage nested run_py src_app
    tmp="$(mktemp -d)"
    stage="$tmp/stage"
    mkdir -p "$stage" "$release_root"
    trap 'rm -rf "${tmp:-}"' RETURN

    unzip -q "$zip_file" -d "$stage"
    run_py="$(find "$stage" -maxdepth 4 -type f -name run.py -print -quit)"

    if [[ -z "$run_py" ]]; then
        nested="$(find "$stage" -maxdepth 3 -type f -name '*.zip' -print -quit)"
        if [[ -n "$nested" ]]; then
            mkdir -p "$tmp/nested"
            unzip -q "$nested" -d "$tmp/nested"
            run_py="$(find "$tmp/nested" -maxdepth 4 -type f -name run.py -print -quit)"
        fi
    fi

    [[ -n "$run_py" ]] || die "ZIP không chứa ACP run.py"
    src_app="$(dirname "$run_py")"
    cp -a "$src_app" "$new"

    rm -rf "$new/.venv" "$new/var" "$new/.env.local" "$new/.git"
    ln -s "$SHARED/var" "$new/var"
    ln -s "$SHARED/.env.local" "$new/.env.local"

    if [[ ! -f "$new/manage.sh" ]]; then
        cp "$SCRIPT_REAL" "$new/manage.sh"
    fi
    chmod +x "$new/manage.sh"

    printf '%s' "$new"
}

prepare_git_metadata() {
    local old="$1"
    local new="$2"
    local version="$3"
    if [[ ! -d "$old/.git" ]]; then
        return 0
    fi
    cp -a "$old/.git" "$new/.git"
    local branch="upgrade/$version"
    git -C "$new" switch -c "$branch" >/dev/null 2>&1 || git -C "$new" switch "$branch" >/dev/null 2>&1 || true
}

install_release() {
    local new="$1"
    [[ -f "$new/requirements.txt" ]] || die "Thiếu requirements.txt trong release mới"
    python3 -m venv "$new/.venv"
    "$new/.venv/bin/python" -m pip install --disable-pip-version-check -r "$new/requirements.txt"
}

migrate_release() {
    local new="$1"
    local parent
    parent="$(dirname "$new")"
    load_env_from "$new"
    (
        cd "$parent"
        ACP_ADAPTER=mock ACP_SOURCE=mock "$new/.venv/bin/python" - <<'PY'
from acp.core.db import init_db
init_db()
print("SCHEMA_OK")
PY
    )
}

switch_active() {
    local target="$1"
    local temp_link="$BASE/.acp-next-$$"
    [[ -d "$target" ]] || die "Release không tồn tại: $target"

    if [[ -e "$ACTIVE" && ! -L "$ACTIVE" ]]; then
        die "$ACTIVE phải là symlink trước khi dùng upgrade/rollback tự động"
    fi

    ln -s "$target" "$temp_link"
    mv -Tf "$temp_link" "$ACTIVE"
}

cmd_upgrade() {
    local zip_file="${1:-}"
    local version="${2:-}"
    [[ -n "$zip_file" && -n "$version" ]] || { usage; return 2; }

    local old
    old="$(current_release)"
    if [[ -d "$old/.git" && -n "$(git -C "$old" status --porcelain --untracked-files=no 2>/dev/null)" ]]; then
        die "Git working tree hiện tại chưa sạch. Commit/stash trước khi upgrade."
    fi

    cmd_stop
    backup_db "$old" "$version"

    local new
    new="$(extract_release "$zip_file" "$version")"
    prepare_git_metadata "$old" "$new" "$version"
    install_release "$new"
    run_release_tests "$new"
    migrate_release "$new"

    printf '%s\n' "$old" >"$PREVIOUS_FILE"
    switch_active "$new"

    if ! cmd_start; then
        warn "Release mới không khởi động được; tự động quay lại release trước."
        switch_active "$old"
        cmd_start || true
        return 1
    fi

    info "UPGRADE_OK version=$version"
}

cmd_rollback() {
    [[ -f "$PREVIOUS_FILE" ]] || die "Chưa có release trước để rollback"
    local previous current
    previous="$(cat "$PREVIOUS_FILE")"
    [[ -d "$previous" ]] || die "Release rollback không còn tồn tại: $previous"
    current="$(current_release)"
    [[ "$previous" != "$current" ]] || die "Release trước trùng release hiện tại"

    cmd_stop
    switch_active "$previous"
    printf '%s\n' "$current" >"$PREVIOUS_FILE"
    if ! cmd_start; then
        warn "Rollback đã đổi symlink nhưng app chưa khởi động; kiểm tra log."
        return 1
    fi
    info "ROLLBACK_OK release=$(release_version "$previous")"
}

case "${1:-}" in
    setup) cmd_setup ;;
    encrypt-secrets) cmd_encrypt_secrets ;;
    start) cmd_start ;;
    stop) cmd_stop ;;
    restart) cmd_restart ;;
    factory-start) cmd_factory_start ;;
    factory-stop) cmd_factory_stop ;;
    handoff-out) cmd_handoff_out ;;
    status) cmd_status ;;
    test) cmd_test ;;
    upgrade) shift; cmd_upgrade "$@" ;;
    rollback) cmd_rollback ;;
    -h|--help|help|"") usage ;;
    *) usage >&2; exit 2 ;;
esac
