# Threads OAuth từ `/kenh`

Mục tiêu của flow này: thêm hoặc kết nối lại nhiều Threads account vào ACP mà không copy access token, không sửa DB và không nhập token bằng tay.

## 1. Điều kiện trước khi OAuth

Với Meta App đang ở chế độ tester/development, account Threads cần được thêm vào danh sách tester phù hợp và người dùng phải accept invitation trước khi thử OAuth.

ACP không tự accept Meta tester invitation. Việc này xảy ra bên ngoài ACP.

## 2. Cấu hình runtime ACP

Điền các biến sau vào `shared/.env.local` của release đang chạy:

```dotenv
THREADS_APP_ID=<Threads App ID>
THREADS_APP_SECRET=<Threads App Secret>
ACP_PUBLIC_BASE_URL=https://<public-domain-cua-acp>
```

Không commit `.env.local` thật. `THREADS_APP_SECRET`, access token và `ACP_MASTER_KEY` chỉ ở server ACP.

Sau khi đổi env:

```bash
cd ~/Downloads/ACP
./manage.sh restart
```

## 3. Redirect URI trên Meta App

Thêm chính xác URI sau vào danh sách OAuth redirect URI hợp lệ của Threads app:

```text
https://<public-domain-cua-acp>/oauth/threads/connect/callback
```

`ACP_PUBLIC_BASE_URL` được ưu tiên khi ACP tạo `redirect_uri`. Nếu biến này để trống, ACP dùng host của request hiện tại.

## 4. Flow trên `/kenh`

```text
ACP /kenh
  -> Kết nối Threads
  -> /oauth/threads/start
  -> ACP tạo OAuth state một lần dùng
  -> threads.net OAuth
  -> người dùng đăng nhập đúng Threads account và cấp quyền
  -> /oauth/threads/connect/callback
  -> đổi authorization code lấy short-lived token
  -> đổi sang long-lived token
  -> GET /me để lấy Threads user id + username
  -> mã hóa token bằng ACP_MASTER_KEY
  -> upsert channel
  -> status = ACTIVE
  -> redirect về /kenh
```

Scopes hiện dùng:

```text
threads_basic,threads_content_publish
```

## 5. Thêm nhiều account

Lặp lại nút **Kết nối Threads** cho từng account:

```text
account A accept tester -> Kết nối Threads -> login A -> ACTIVE
account B accept tester -> Kết nối Threads -> login B -> ACTIVE
account C accept tester -> Kết nối Threads -> login C -> ACTIVE
```

Không cần tạo channel trước và không cần nhập username trước. ACP lấy identity từ account đã authorize.

Nếu OAuth lại đúng account đã tồn tại, ACP cập nhật token/user id/handle trên channel cũ và đưa channel về `ACTIVE`, thay vì tạo channel trùng.

## 6. Bảo vệ đang có

- OAuth state ngẫu nhiên, lưu server-side và chỉ dùng một lần.
- Session OAuth hết hạn sau thời gian giới hạn.
- Callback quản trị yêu cầu ACP login khi `ACP_ADMIN_PASSWORD` được bật.
- Token không trả về browser sau callback.
- Long-lived token được mã hóa trước khi lưu vào `channel.token_encrypted`.
- Flow Account Factory vẫn giữ kiểm tra `expected_username` khi onboarding một account đã biết trước; generic `/kenh` mới chỉ bỏ yêu cầu username cho flow operator chủ động đăng nhập.

## 7. Trạng thái chưa nằm trong phase này

Phase này giải quyết **kết nối account bằng OAuth và tự tạo/upsert channel**.

Nó chưa thêm một registry riêng để `/kenh` hiển thị account chưa OAuth với các trạng thái:

```text
PENDING_META_INVITE
READY_TO_CONNECT
```

Do đó account chưa authorize sẽ chưa xuất hiện trong danh sách `channel`. Sau OAuth thành công, account xuất hiện với `ACTIVE`.

Nếu cần quản lý tester trước OAuth ngay trong ACP, triển khai tiếp một bảng/nguồn `threads_account_candidate` (hoặc nối với Account Factory account registry) rồi map các trạng thái pre-OAuth vào `/kenh`.

## 8. Kiểm thử an toàn

Không bật publish thật chỉ để test OAuth. Có thể giữ:

```dotenv
ACP_ADAPTER=mock
ACP_SOURCE=mock
```

OAuth account và lưu token là một bước riêng; việc publish Threads thật vẫn cần operator chủ động bật live adapter và duyệt/publish theo quy trình của ACP.
