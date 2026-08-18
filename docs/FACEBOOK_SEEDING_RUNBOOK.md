# Facebook Seeding Assistant — Operator Runbook

Tài liệu này hướng dẫn chạy thử nghiệm Facebook Seeding Assistant trên ACP mà không đụng dữ liệu/secrets live ngoài những cấu hình operator chủ động đặt.

## 1. Phạm vi MVP

MVP chỉ xử lý **Facebook target URL do operator/company nhập sẵn**. Tool không tự tìm group/post, không tạo/rotate account, không giữ cookie Facebook, không bypass checkpoint/CAPTCHA/rate limit và không dùng proxy/fingerprint spoofing.

Luồng chuẩn:

```text
Target URL đã nhập
→ extension mở đúng URL
→ đọc context đang render
→ ACP chọn template + tạo draft
→ risk/confidence gate
→ AUTO_READY: re-check pause + shift rồi submit một lần
→ REVIEW_REQUIRED: operator duyệt/sửa/skip
→ verify comment
→ POSTED hoặc UNKNOWN
→ KPI/report
```

`UNKNOWN` là terminal cho auto execution: tool **không click submit lần hai**.

## 2. Chuẩn bị ACP

Dùng release/branch chứa feature và chạy qua interface chuẩn:

```bash
cd ~/Downloads/ACP
./manage.sh test
./manage.sh start
```

Không đổi `ACP_ADAPTER=live` để thử feature này. Release tests ép `ACP_ADAPTER=mock`, `ACP_SOURCE=mock` và không mở Facebook thật.

## 3. Tạo token local cho extension

Thêm một giá trị ngẫu nhiên vào `~/Downloads/ACP/shared/.env.local`:

```text
ACP_SEEDING_EXTENSION_TOKEN=<random-local-secret>
```

Sau đó:

```bash
cd ~/Downloads/ACP
./manage.sh restart
```

Không commit giá trị thật. `.env.example` chỉ chứa tên biến trống.

## 4. Load extension

Chrome/Chromium:

1. Mở `chrome://extensions`.
2. Bật **Developer mode**.
3. Chọn **Load unpacked**.
4. Chọn thư mục:

```text
extensions/facebook-seeding-assistant/
```

5. Mở Facebook. Panel `ACP` xuất hiện ở góc dưới phải.
6. Điền:

```text
ACP URL: http://127.0.0.1:5000
Token:   cùng giá trị ACP_SEEDING_EXTENSION_TOKEN
```

Extension chỉ xin quyền `storage`, host Facebook và loopback ACP. Không xin `cookies`, `debugger` hay `<all_urls>`.

## 5. Tạo campaign thử nghiệm

Mở:

```text
http://127.0.0.1:5000/seeding
```

Tạo campaign với:

- tên/brand;
- brief;
- allowed claims;
- prohibited topics;
- disclosure/promotion policy;
- confidence threshold (mặc định `0.90`, không cho dưới `0.85`);
- **auto-submit OFF** ở lần kiểm tra selector đầu tiên.

Thêm template theo intent, ví dụ:

```text
recommendation_request
price_question
service_question
generic
```

Template không được chứa trải nghiệm cá nhân/testimonial giả.

## 6. Import target

Mỗi dòng một Facebook HTTPS URL:

```text
https://www.facebook.com/groups/.../posts/.../
```

MVP chỉ chấp nhận host Facebook hợp lệ. URL trùng trong cùng campaign được bỏ qua.

## 7. Dry-run bắt buộc trước auto-submit

1. Giữ `auto-submit OFF`.
2. Import một target test/được phép sử dụng.
3. Start shift.
4. Để extension mở target và đọc context.
5. Kiểm tra:
   - target đúng;
   - draft đúng brief;
   - không bịa claim/trải nghiệm;
   - extension tìm đúng composer;
   - panel review hoạt động.
6. Chỉ sau khi selector/context đúng mới cân nhắc bật `auto-submit` cho campaign được phép.

## 8. Điều kiện AUTO_READY

ACP chỉ cho phép auto khi đồng thời:

- global pause OFF;
- shift hiện tại vẫn ACTIVE;
- campaign ACTIVE và `auto_submit=1`;
- URL hiện tại khớp target đã nhập;
- risk LOW;
- confidence đạt threshold;
- factual claims nằm trong allowed claims;
- không có complaint/refund/legal/medical/fraud/sensitive/ambiguous label;
- không có testimonial/trải nghiệm cá nhân bị cấm;
- comment không quá giống recent posted comment;
- disclosure policy đã cấu hình.

Extension vẫn có quyền **downgrade** AUTO_READY thành review nếu DOM/composer/nút submit mơ hồ.

## 9. Kill switch / pause

Có hai tầng:

- **STOP NOW · Global pause**: chặn toàn bộ auto-submit.
- **Pause shift**: dừng riêng shift hiện tại.

Extension re-check trạng thái ngay trước click submit và yêu cầu `active_shift_id` đúng shift đang xử lý. Nếu operator pause shift ở thời điểm đó, click không diễn ra.

## 10. Facebook checkpoint/rate restriction

Nếu Facebook hiển thị identity verification, temporary block, rate restriction hoặc trạng thái tương tự:

```text
DỪNG
→ không bypass
→ không CAPTCHA solver
→ không đổi proxy/account để tiếp tục
→ operator tự xử lý trạng thái account/platform
```

## 11. UNKNOWN

Nếu extension đã click submit đúng một lần nhưng không thể xác minh comment xuất hiện trong cửa sổ verify:

```text
result = UNKNOWN
→ ghi activity
→ clear auto execution cho target
→ không click lại
```

Operator kiểm tra Facebook bằng tay trước khi quyết định xử lý tiếp để tránh duplicate.

## 12. Test dành cho developer

Python domain/web contract:

```bash
ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= python3 -m acp.tests.test_seeding
ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= python3 -m acp.tests.test_seeding_web
```

Extension pure tests:

```bash
node --test extensions/facebook-seeding-assistant/tests/*.test.cjs
```

Manager regression tests khi `manage.sh` thay đổi:

```bash
python3 tests/test_manage.py
```

Release gate:

```bash
./manage.sh test
```

`./manage.sh test` chạy pipeline, pilot, seeding domain, seeding web contracts và doctor ở mock mode.

## 13. Không làm trong MVP

- tự tìm target/post/group;
- tự tạo/nuôi/rotate account;
- giả khách hàng độc lập hoặc fake testimonial/review;
- CAPTCHA/checkpoint bypass;
- anti-detection/fingerprint spoofing;
- proxy/VPN rotation để né limit;
- retry submit tự động sau `UNKNOWN`;
- TikTok/Threads/Instagram.
