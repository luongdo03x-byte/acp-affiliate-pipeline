# Chạy ACP trên máy mới bằng Portable State

Tài liệu ngắn gọn để chuyển Account Factory/ACP từ máy cũ sang máy mới.

## Nguyên tắc

- Chỉ **1 máy ACTIVE** tại một thời điểm.
- Máy cũ phải `handoff-out` thành công trước khi chạy Factory trên máy mới.
- Máy mới phải dùng **đúng cùng portable bundle key** với máy cũ.
- Không commit `portable_bundle_key` dạng plaintext lên Git.

## 1. Trên máy cũ

Load key và handoff state:

```bash
export ACP_PORTABLE_BUNDLE_KEY="$(cat ~/.config/acp/portable_bundle_key)"
cd ~/Downloads/ACP
./manage.sh handoff-out
```

Thành công phải có dạng:

```text
HANDOFF_OK generation=N
```

Kiểm tra:

```bash
cat ~/Downloads/ACP/shared/machine.json
```

Máy cũ phải là:

```json
"ownership": "HANDED_OFF"
```

Sau đó **không chạy `factory-start` trên máy cũ** cho tới khi state được handoff trở lại.

## 2. Portable bundle key ở đâu?

Trên máy cũ:

```text
~/.config/acp/portable_bundle_key
```

Ví dụ:

```text
/home/<user>/.config/acp/portable_bundle_key
```

Copy file này sang máy mới bằng USB, `scp`, password manager hoặc cách truyền file bảo mật khác.

**Không tạo key mới trên máy mới.**

Trên máy mới lưu đúng path:

```bash
mkdir -p ~/.config/acp
chmod 700 ~/.config/acp
chmod 600 ~/.config/acp/portable_bundle_key
export ACP_PORTABLE_BUNDLE_KEY="$(cat ~/.config/acp/portable_bundle_key)"
```

Validate key:

```bash
python3 - <<'PY'
import base64
import os

raw = base64.b64decode(
    os.environ["ACP_PORTABLE_BUNDLE_KEY"],
    validate=True,
)
assert len(raw) == 32, len(raw)
print("PORTABLE_KEY_OK bytes=32")
PY
```

## 3. Prerequisite trên máy mới

Cần có:

- `python3` + khả năng tạo virtualenv
- Git + SSH access tới repo
- GitHub CLI `gh` đã login
- Android SDK / `adb` / emulator
- AVD tên chính xác `acp-worker-01`

Kiểm tra nhanh:

```bash
gh auth status
emulator -list-avds
```

Danh sách AVD phải có:

```text
acp-worker-01
```

Nếu thiếu AVD này, `setup.sh` sẽ dừng và rollback state import.

## 4. Clone ACP trên máy mới

Clone branch portable vào thư mục **kết thúc bằng `/acp`**:

```bash
mkdir -p ~/Downloads/ACP/releases/current

git clone \
  -b feat/account-factory-android \
  git@github.com:luongdo03x-byte/acp-affiliate-pipeline.git \
  ~/Downloads/ACP/releases/current/acp

cd ~/Downloads/ACP/releases/current/acp
```

## 5. Chạy setup

Load key rồi chạy đúng một lệnh:

```bash
export ACP_PORTABLE_BUNDLE_KEY="$(cat ~/.config/acp/portable_bundle_key)"
./setup.sh
```

`setup.sh` sẽ tự động:

1. cài dependencies nếu cần;
2. tải generation portable mới nhất từ GitHub Release;
3. giải mã bundle;
4. restore DB, `.env.local`, avatar/state dùng chung;
5. chạy schema setup;
6. kiểm tra AVD và doctor;
7. resume state;
8. chạy `factory-start`.

Thành công phải có:

```text
PORTABLE_SETUP_OK
```

## 6. Kiểm tra sau setup

```bash
cat ~/Downloads/ACP/shared/machine.json
~/Downloads/ACP/manage.sh status
```

Máy mới phải là:

```json
"ownership": "ACTIVE"
```

và `last_imported_generation` phải bằng generation vừa handoff từ máy cũ.

## Flow ngắn nhất

Nếu máy mới đã có GitHub CLI, Android SDK và AVD `acp-worker-01`, thao tác thực tế chỉ còn:

```text
copy ~/.config/acp/portable_bundle_key
        ↓
git clone branch vào .../acp
        ↓
export ACP_PORTABLE_BUNDLE_KEY=...
        ↓
./setup.sh
        ↓
ACTIVE
```

## Chuyển ngược về máy cũ

Trên máy đang ACTIVE:

```bash
export ACP_PORTABLE_BUNDLE_KEY="$(cat ~/.config/acp/portable_bundle_key)"
cd ~/Downloads/ACP
./manage.sh handoff-out
```

Sau đó trên máy nhận state chạy lại flow `handoff-in/setup.sh` với **cùng key**.
