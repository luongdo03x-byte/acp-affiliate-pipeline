# Threads OAuth từ `/kenh`

Mục tiêu: thêm hoặc kết nối lại nhiều Threads account vào ACP mà không copy access token, không sửa DB và không nhập token bằng tay. Khi Meta App chưa publish, ACP còn có wizard để giảm thao tác tester xuống mức tối thiểu.

## 1. Điều kiện trước khi OAuth

Với Meta App đang ở chế độ tester/development, account Threads vẫn phải được thêm vào tester và account đó vẫn phải accept invitation. ACP không bypass hai hành động Meta này.

ACP chỉ lưu hai mốc operator đã xác nhận trên chính `factory_account`:

```text
tester_invited_at
tester_accepted_at
```

Không có bảng account thứ hai. Account Factory V2 tiếp tục là registry authoritative.

## 2. Cấu hình runtime ACP

Điền các biến sau vào `shared/.env.local` của release đang chạy:

```dotenv
THREADS_APP_ID=<Threads App ID>
THREADS_APP_SECRET=<Threads App Secret>
ACP_PUBLIC_BASE_URL=https://<public-domain-cua-acp>
META_APP_TESTERS_URL=https://developers.facebook.com/apps/<APP_ID>/app-roles/
```

`META_APP_TESTERS_URL` là tuỳ chọn nhưng nên đặt đúng URL màn App Roles/Testers của app để nút **Mở Meta Tester** đi thẳng đến nơi add tester.

Không commit `.env.local` thật. `THREADS_APP_SECRET`, access token và `ACP_MASTER_KEY` chỉ ở server ACP.

Sau khi đổi env:

```bash
cd ~/Downloads/ACP
./manage.sh restart
```

## 3. Redirect URI trên Meta App

Nếu dùng cả kết nối đơn lẻ và wizard hàng loạt, thêm chính xác hai URI:

```text
https://<public-domain-cua-acp>/oauth/threads/connect/callback
https://<public-domain-cua-acp>/oauth/threads/onboarding/callback
```

`ACP_PUBLIC_BASE_URL` được ưu tiên khi ACP tạo `redirect_uri`. Nếu biến này để trống, ACP dùng host của request hiện tại.

Ví dụ với ngrok cố định:

```text
ACP_PUBLIC_BASE_URL=https://hardener-nearest-poser.ngrok-free.dev
```

thì callback wizard là:

```text
https://hardener-nearest-poser.ngrok-free.dev/oauth/threads/onboarding/callback
```

Chỉ thêm `/vanhanh` vào `ACP_PUBLIC_BASE_URL` nếu toàn bộ ACP thực sự được reverse-proxy dưới prefix `/vanhanh`.

## 4. Flow ít thao tác nhất trước khi publish

Từ `/kenh`, dùng nút **Thêm Threads hàng loạt**:

```text
/kenh
  -> Thêm Threads hàng loạt
  -> ACP lấy account THREADS_CREATED từ Factory V2
  -> hiện đúng account tiếp theo
```

Trạng thái wizard được suy ra tự động:

```text
NEEDS_TESTER_INVITE
  -> NEEDS_TESTER_ACCEPT
  -> READY_FOR_OAUTH
  -> OAUTH_IN_PROGRESS
  -> ACTIVE
```

Nếu operator đã add tester và account đã accept luôn, không cần bấm xác nhận invite riêng:

```text
Mở Meta Tester
  -> add @account
  -> account accept invitation
  -> ACP: Đã accept → Kết nối ngay
  -> OAuth mở ngay
```

Nút **Đã accept → Kết nối ngay** tự backfill cả `tester_invited_at` và `tester_accepted_at` rồi bắt đầu OAuth trong cùng một thao tác ACP.

Nếu OAuth bị hủy/từ chối, account quay về retry OAuth nhưng mốc tester acceptance được giữ lại, nên không cần accept tester lần nữa.

## 5. OAuth sau khi tester đã sẵn sàng

Wizard dùng OAuth account-bound của Factory V2, vì ACP đã biết username phải kết nối:

```text
ACP tạo one-time OAuth state
  -> threads.net OAuth
  -> user đăng nhập đúng Threads account + Allow
  -> /oauth/threads/onboarding/callback
  -> authorization code -> short-lived token
  -> short-lived -> long-lived token
  -> GET /me lấy Threads user id + username
  -> kiểm tra username đúng account Factory V2
  -> mã hóa token bằng ACP_MASTER_KEY
  -> upsert channel
  -> Factory V2 ACP_ACTIVE
  -> channel ACTIVE
  -> redirect lại wizard
```

Scopes hiện dùng:

```text
threads_basic,threads_content_publish
```

Token không xuất hiện trong browser và không cần nhập vào ACP.

## 6. Kết nối Threads đơn lẻ

`/kenh` vẫn giữ nút **Kết nối Threads đơn lẻ** cho account không đến từ Account Factory:

```text
/oauth/threads/start
  -> Threads OAuth
  -> /oauth/threads/connect/callback
  -> tự discover user id + username
  -> mã hóa token
  -> upsert channel
  -> ACTIVE
```

Nếu OAuth lại đúng account đã tồn tại, ACP cập nhật token/user id/handle trên channel cũ thay vì tạo channel trùng.

## 7. Bảo vệ đang có

- OAuth state ngẫu nhiên, server-side và chỉ dùng một lần.
- OAuth session có thời hạn.
- Account Factory OAuth kiểm tra `expected_username`, tránh gắn token của account khác vào account đang onboarding.
- Token không trả về browser sau callback.
- Long-lived token được mã hóa trước khi lưu vào `channel.token_encrypted`.
- `THREADS_APP_SECRET` và `ACP_MASTER_KEY` không đưa vào APK/browser.
- Các POST của wizard đi qua CSRF guard của ACP khi chạy trong main web app.
- Callback guided dùng one-time OAuth state để hoàn tất handshake; khi redirect lại `/kenh/...`, dashboard auth của ACP tiếp tục được áp dụng.

## 8. Khi app đã publish

Hai mốc tester chỉ phục vụ giai đoạn development/test. Khi app được publish và account bình thường được phép authorize, wizard có thể bỏ phần tester và đi thẳng:

```text
account -> OAuth -> Allow -> ACTIVE
```

Không cần đổi cơ chế token/channel phía sau.

## 9. Kiểm thử an toàn

Không bật publish thật chỉ để test OAuth. Có thể giữ:

```dotenv
ACP_ADAPTER=mock
ACP_SOURCE=mock
```

OAuth account và lưu token là bước riêng; publish Threads thật vẫn cần operator chủ động bật live adapter và duyệt/publish theo quy trình ACP.
