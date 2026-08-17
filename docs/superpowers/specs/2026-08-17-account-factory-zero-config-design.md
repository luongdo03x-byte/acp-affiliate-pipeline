# Account Factory Zero-Config Android Design

## Goal

Cài APK rồi mở app mà không phải nhập Controller URL hoặc Factory Key. App tự tìm Account Factory Controller trong cùng mạng LAN, tự đăng ký thiết bị, nhận credential riêng cho thiết bị, lưu credential an toàn và tự khởi động LOCAL_DEVICE runner. Android Accessibility vẫn cần người dùng bật thủ công một lần vì hệ điều hành không cho app tự cấp quyền này.

## Chosen approach

Dùng HTTP LAN discovery + device enrollment, không hardcode Factory Key vào APK.

1. Controller công khai `GET /api/factory/discovery` không yêu cầu secret và chỉ trả metadata không nhạy cảm.
2. Android ưu tiên URL/token đã lưu. Nếu chưa có hoặc credential cũ không còn hợp lệ, app quét subnet Wi-Fi hiện tại để tìm endpoint discovery trên port cấu hình mặc định 5001.
3. Controller cho phép `POST /api/factory/enroll` chỉ khi `ACP_FACTORY_LAN_AUTO_ENROLL=true` và request đến từ private/link-local address. Endpoint nhận `device_id` + `device_name`, tạo token ngẫu nhiên, chỉ lưu SHA-256 hash trong SQLite và trả raw token đúng ở response enrollment.
4. Android lưu token bằng Android Keystore AES/GCM; `base_url` có thể lưu plain SharedPreferences vì không phải secret.
5. Các API Factory V2 chấp nhận hoặc `X-ACP-Factory-Key` (legacy/operator) hoặc `X-ACP-Device-Token` hợp lệ. Để không phải sửa networking layer cũ của APK, auth bridge cũng nhận device credential trong slot `X-ACP-Factory-Key` rồi mới fallback sang real Factory Key.
6. Foreground `LocalRunnerService` khởi động ngay khi mở app, validate credential, tự rediscover/re-enroll khi credential/URL cũ hỏng, rồi mới start LOCAL_DEVICE runner.

## Backward compatibility

- Giữ `Factory Key` manual fallback cho máy cũ và troubleshooting.
- Không thay đổi constructor/networking contract hiện tại của `FactoryConnection`; `FactorySettingsStore` đưa credential đã enroll vào credential slot hiện có.
- Controller auth bridge thử giá trị đó như device credential trước, rồi mới dùng semantics `ACP_FACTORY_API_KEY` cũ.
- Existing API clients/tests dùng real `X-ACP-Factory-Key` không đổi semantics.
- Existing `factory_worker` / LOCAL_DEVICE registration flow không bị thay thế; enrollment chỉ cấp credential, runner registration vẫn là bước runner chuẩn.

## Controller data model

Thêm bảng `factory_device_credential`:

- `id` TEXT PRIMARY KEY
- `device_id` TEXT UNIQUE NOT NULL
- `device_name` TEXT
- `token_hash` TEXT UNIQUE NOT NULL
- `status` TEXT NOT NULL DEFAULT `ACTIVE`
- `created_at` TEXT NOT NULL
- `last_used_at` TEXT
- `revoked_at` TEXT

Không lưu raw token.

## Discovery behavior

Android xác định IPv4 Wi-Fi hiện tại và tạo candidate hosts cùng `/24`. Quét có giới hạn concurrency và timeout ngắn; dừng ngay khi response có `service = account-factory` và `api_version = 2`. Không quét Internet/public ranges.

Nếu không tìm thấy Controller, app vẫn mở bình thường; bấm refresh/create sẽ thử bootstrap lại. Manual settings được giữ làm fallback chủ động qua nút Settings, không hiện như bước onboarding bắt buộc.

## Enrollment security

- Auto-enroll mặc định OFF ở server để tránh bất kỳ thiết bị nào cùng LAN tự đăng ký ngoài ý muốn.
- Operator bật `ACP_FACTORY_LAN_AUTO_ENROLL=true` trên máy cá nhân khi muốn zero-config onboarding.
- Endpoint reject non-private remote addresses.
- Token sinh bằng `secrets.token_urlsafe(32)`; server compare hash constant-time.
- Device ID phải 8..160 ký tự, device name tối đa 120 ký tự.
- Re-enroll cùng device rotate token cũ thay vì tạo nhiều credential song song.

## Android startup flow

```text
App start
  -> start foreground LocalRunnerService
  -> credential exists?
       yes -> validate against controller
              -> valid: start LOCAL_DEVICE runner
              -> invalid/unreachable: rediscover + re-enroll
       no  -> discover controller
               -> enroll device
               -> save base URL + encrypted device token
               -> start LOCAL_DEVICE runner
  -> Accessibility needed for an action?
       open Accessibility Settings for one-time human enablement
```

## Testing

### Python

- discovery works without Factory Key and never exposes secrets
- enrollment disabled by default
- enrollment rejects non-private address
- enrollment creates token, stores only hash, rotates token on re-enroll
- device token authenticates Factory V2 routes
- enrolled token also works through the existing Android credential/header slot
- invalid/revoked token gets 401
- legacy Factory Key remains valid

### Kotlin/JVM

Pure helper tests cover:
- candidate `/24` host generation only for private IPv4
- discovery JSON validation
- enrollment JSON validation

Additional compile/static verification covers bootstrap state and recovery logic. Android Keystore, OkHttp/Android integration and foreground service startup still require the real Android SDK/Gradle build plus device verification.

## Out of scope

- Auto-enabling Accessibility
- bypassing Android permission/security dialogs
- hardcoding Factory Key in APK
- Internet-wide controller discovery
- public unauthenticated Factory V2 operations
