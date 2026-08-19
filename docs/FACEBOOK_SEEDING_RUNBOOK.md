# Facebook Seeding Assistant — Operator Runbook

Tài liệu này mô tả flow hiện tại của ACP cho **nhiệm vụ Facebook do operator nhập sẵn**. Luồng multi-account dùng Chrome Profile riêng và luôn giữ bước thao tác/submit Facebook ở người vận hành.

## 1. Phạm vi

Tool hỗ trợ:

- nhập tên nhiệm vụ + nguyên văn yêu cầu + link bài Facebook;
- parse LIKE, số main/reply, số account tối đa và từ cấm;
- kết nối nhiều Chrome Profile bằng nhãn `FB01`, `FB02`...;
- map account thật vào `Account slot 1..N`;
- đọc bài Facebook đang render;
- Gemini sinh một bộ comment khác nhau cho các account đã chọn;
- chống từ cấm và exact/near-duplicate;
- mỗi Chrome Profile chỉ nhận đúng phần việc của account đó;
- fill comment/reply vào composer đã xác định;
- ghi nội dung cuối thực tế sau khi operator đăng;
- tạo report B/C/D và tùy chọn append Google Sheets.

Tool **không**:

- tạo/nuôi/rotate account;
- lưu Facebook password/cookie/session trong ACP;
- tự click Like/Đăng trong flow multi-account;
- tự chọn người/comment để reply;
- bypass checkpoint/CAPTCHA/rate restriction;
- dùng proxy/fingerprint/anti-detection để né giới hạn;
- tự tìm group/post mục tiêu.

## 2. Chuẩn bị ACP

```bash
cd ~/Downloads/ACP/acp
git fetch origin
git switch feat/facebook-seeding-assistant
git pull --ff-only

cd ~/Downloads/ACP
./manage.sh test
```

Trong `~/Downloads/ACP/shared/.env.local` cần ít nhất:

```bash
ACP_SEEDING_EXTENSION_TOKEN=<random-secret>
ACP_CAPTION_LLM=gemini
ACP_GEMINI_API_KEY=<gemini-key>
```

Sau đó:

```bash
./manage.sh restart
```

## 3. Kết nối Facebook account

Mỗi Facebook account dùng **một Chrome Profile riêng** đã login bằng tay.

Trong từng profile:

1. Mở `chrome://extensions`.
2. Load/reload `extensions/facebook-seeding-assistant/`.
3. Mở Facebook.
4. Panel ACP xuất hiện.
5. Nhập:

```text
Tên/nhãn account: FB01
ACP URL:          http://127.0.0.1:5000
Token:            ACP_SEEDING_EXTENSION_TOKEN
```

6. Bấm **Lưu & kết nối**.

Làm tương tự `FB02`, `FB03`...

Mở:

```text
http://127.0.0.1:5000/seeding/accounts
```

để xem `ONLINE/OFFLINE`. Extension heartbeat mỗi phút và khi đang IDLE sẽ hỏi ACP lại sau khoảng 15 giây để nhận task mới.

## 4. Tạo nhiệm vụ

Mở:

```text
http://127.0.0.1:5000/seeding
```

Chỉ cần ba trường:

```text
Tên nhiệm vụ: A2GR-64

Nội dung/yêu cầu:
LIKE BÀI ĐĂNG
mỗi acc 3 CMT (1 cmt chính + 2 reply)
tối đa 3 acc
KHÔNG NHẮC SỮA

Link Facebook:
https://www.facebook.com/groups/.../permalink/.../?rdid=...
```

Tên nhiệm vụ được phép trùng; mỗi lần tạo vẫn có internal id riêng.

ACP lưu nguyên brief và URL gốc để dùng cho báo cáo.

## 5. Gán account

Vào **FB Accounts**, chọn nhiệm vụ rồi tick các account muốn dùng, tối đa theo `max_accounts` parser đã hiểu.

Ví dụ:

```text
Account slot 1 → FB01
Account slot 2 → FB02
Account slot 3 → FB03
```

Sau khi LIKE đã được xác nhận hoặc comment plan đã sinh, mapping bị khóa để nội dung không bị chuyển nhầm sang profile khác.

## 6. Profile nhận việc

Mỗi profile chỉ hỏi endpoint profile-scoped bằng `extensionInstanceId` của chính nó.

Flow:

```text
IDLE
→ task được map
→ tự mở đúng URL
→ LIKE nếu nhiệm vụ yêu cầu
→ NEEDS_CONTEXT nếu chưa có comment plan
→ COMMENT main/reply của đúng account slot
→ IDLE khi account đó xong
```

Account không được map không nhận task.

Nếu hai profile cùng yêu cầu sinh nội dung đúng lúc, plan đầu tiên ghi thành công sẽ được dùng chung; request còn lại reload plan đã thắng thay vì regenerate bộ khác.

## 7. LIKE

Nếu brief yêu cầu Like, panel hiển thị **LIKE · xác nhận thủ công**.

Operator:

1. Like bài trên Facebook bằng đúng profile.
2. Kiểm tra Facebook đã hiển thị trạng thái Like.
3. Bấm **Đã LIKE** trên panel ACP.

Extension không tự click Like.

## 8. Sinh nội dung

Profile đầu tiên đi tới `NEEDS_CONTEXT` sẽ đọc article mục tiêu và gửi context cho ACP.

Prompt dùng:

```text
brief gốc
+ parsed rules
+ từ cấm
+ nội dung bài Facebook
+ số account thực sự được map
```

Validator bắt buộc:

- đúng số main/reply;
- mọi câu có nội dung;
- không chứa từ cấm;
- không exact/near-duplicate giữa các account;
- không bịa trải nghiệm mua/dùng/khách hàng nếu brief không cung cấp thông tin thật.

Chỉ slot của account đã map được generate; slot chưa dùng vẫn `EMPTY`.

## 9. Comment chính

Panel hiển thị câu của đúng account.

1. Có thể sửa câu trong textarea ACP.
2. Bấm **Điền CMT chính**.
3. Kiểm tra ô comment Facebook.
4. **Operator tự bấm Đăng trên Facebook.**
5. Bấm **Đã đăng · xác nhận** trên ACP.

ACP chỉ ghi `DONE` khi:

- composer vừa được fill không còn giữ nguyên draft sau submit; và
- đúng text xuất hiện trong article Facebook mục tiêu, không tính text trong panel ACP.

Final text được server validate lại. Nếu operator sửa thành câu chứa từ cấm hoặc trùng/near-duplicate với account khác, ACP từ chối ghi `DONE`.

## 10. Reply

Reply không tự chọn comment/người cần trả lời.

Cho mỗi reply:

1. Trên Facebook, operator bấm **Reply** dưới comment phù hợp.
2. Extension nhớ composer Facebook vừa được focus.
3. Quay lại panel ACP và bấm **Điền vào ô đã chọn**.
4. Kiểm tra câu đã điền đúng reply composer.
5. Operator tự bấm Đăng.
6. Bấm **Đã đăng · xác nhận**.

Mỗi reply mới yêu cầu chọn lại comment Reply để tránh điền nhầm thread.

Nếu muốn dừng giữa chừng, bấm **Dừng · làm tiếp sau**. Slot không bị đổi trạng thái; mở/reload lại để tiếp tục. Không có nút Skip làm mất khả năng hoàn thành report.

## 11. Hoàn thành nhiệm vụ

Task chỉ `COMPLETE` khi các account **đã map** hoàn thành:

- toàn bộ LIKE nếu `like_required=true`;
- toàn bộ main comment;
- toàn bộ reply.

Slot của account không được chọn không tính vào completion.

## 12. Report B/C/D

Tại `/seeding/accounts`, mục **Tiến độ & báo cáo** hiển thị Account / Comment / Like / Sheet.

**Tải TSV B/C/D** luôn cho phép lấy dữ liệu cuối đã ghi.

Format:

```text
B                         C                       D
A2GR-64                   main account 1          reply 1 account 1
<link Facebook gốc>       main account 2          reply 2 account 1
                          main account 3          reply 1 account 2
                                                  reply 2 account 2
                                                  reply 1 account 3
                                                  reply 2 account 3
```

Cột C/D dùng `final_text` thực tế operator đã đăng; chỉ fallback về generated text khi phù hợp với record DONE.

## 13. Google Sheets tự động

Xem `docs/SEEDING_SHEET_SETUP.md` và deploy:

```text
integrations/google_sheets_seeding_webhook.gs
```

ACP env:

```bash
ACP_SEEDING_SHEET_WEBHOOK_URL=https://script.google.com/macros/s/.../exec
ACP_SEEDING_SHEET_SECRET=<same-secret-as-apps-script>
```

Khi task đủ điều kiện COMPLETE, ACP tự thử push một lần. Có hai tầng chống append trùng:

1. ACP lưu trạng thái `PUSHING/PUSHED` trước/sau network call.
2. Apps Script dedupe bằng `campaign_id` dưới `ScriptLock` và trả lại `sheet_ref` cũ khi retry.

Nếu Sheet lỗi, comment đã DONE vẫn giữ nguyên. Sửa cấu hình rồi dùng **Ghi Google Sheet** để retry; Facebook không bị làm lại.

## 14. Facebook checkpoint/rate restriction

Nếu extension thấy identity verification, temporary block, rate restriction hoặc tương tự:

```text
DỪNG
→ không bypass
→ không CAPTCHA solver
→ không đổi proxy/account để né
→ operator tự xử lý trạng thái tài khoản/platform
```

## 15. Test

Python:

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= python3 -m acp.tests.test_seeding
ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= python3 -m acp.tests.test_seeding_web
python3 tests/test_manage.py
```

Extension:

```bash
node --test extensions/facebook-seeding-assistant/tests/*.test.cjs
```

Release gate:

```bash
./manage.sh test
```

Automated tests không đăng Facebook thật.
