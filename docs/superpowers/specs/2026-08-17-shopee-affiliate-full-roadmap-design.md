# ACP Shopee Affiliate — Full Roadmap Design

**Date:** 2026-08-17
**Status:** Approved in conversation; written for implementation planning

## 1. Goal

Hoàn thiện Shopee Affiliate trong ACP theo toàn bộ roadmap đã chốt, bắt đầu từ Phase 1 bulk direct-link generation và tiếp tục qua metadata helper, product reliability, content/review UX, observability và release hardening.

Giữ nguyên stack hiện tại: Python 3 + Flask 3 + SQLite + Jinja2 + requests + Pillow. Không thêm frontend framework và không biến ACP thành công cụ browser automation.

## 2. Phase model

Roadmap cũ dùng tên `P0`/`P1` cho các phần sau Phase 1. Để triển khai tuần tự và kiểm thử độc lập, chuẩn hóa thành bốn phase như sau mà không thay đổi semantics cũ.

### Phase 1 — Bulk Affiliate Factory

Đầu vào là tối đa 500 URL sản phẩm Shopee và một `SHOPEE_AFFILIATE_ID` cấu hình ở backend.

ACP:

- chuẩn hóa URL sản phẩm Shopee;
- tạo direct affiliate link theo redirect contract `s.shopee.vn/an_redir`;
- tạo `sub_id` năm segment ổn định;
- xử lý lỗi theo từng dòng;
- dedupe trong batch;
- gắn affiliate link vào Product DB khi nhận diện được sản phẩm đã tồn tại;
- không login Shopee, không gọi private API, không publish.

Phase 1 hiện được phát triển trong `feat/shopee-bulk-affiliate` và là dependency nền cho các phase sau.

### Phase 2 — Shopee Metadata Helper

Mục tiêu là giảm nhập metadata thủ công khi request server-side bị Shopee chặn bởi CAPTCHA/403.

Thêm Chrome Extension nhỏ tên `ACP Shopee Helper` và một helper channel localhost an toàn.

Flow:

```text
URL/link Shopee
→ ACP canonicalize product URL
→ ACP thử metadata server-side
→ đủ metadata? dùng AUTO_COMPLETE/AUTO_PARTIAL
→ bị CAPTCHA/403? BROWSER_HELPER_REQUIRED
→ operator bấm "Mở Shopee & lấy thông tin"
→ Chrome mở trang Shopee chính thức
→ operator bấm extension "Gửi vào ACP"
→ extension đọc metadata đang render trong DOM
→ gửi metadata về ACP localhost
→ ACP validate + ghép đúng product
→ form tự điền
→ operator xác nhận
```

Extension chỉ được gửi:

- canonical/product URL;
- product name;
- current price;
- original price nếu có;
- shop name;
- image URL.

Extension không được đọc/gửi:

- Shopee cookies;
- session tokens;
- password;
- credential token;
- localStorage/sessionStorage auth data;
- browser profile secrets.

Helper endpoint phải:

- chỉ nhận loopback/localhost;
- dùng one-time pairing token/nonce;
- chống replay;
- validate Shopee URL và canonical product identity;
- giới hạn payload;
- không cho extension gửi arbitrary field;
- không tạo draft/post/publish tự động.

Metadata states chuẩn:

- `AUTO_COMPLETE` — các field bắt buộc có đủ từ server metadata;
- `AUTO_PARTIAL` — có một phần metadata, operator/helper bổ sung phần còn thiếu;
- `BROWSER_HELPER_REQUIRED` — server fetch gặp anti-bot/CAPTCHA/403 hoặc không có usable metadata;
- `MANUAL_REQUIRED` — helper không khả dụng/thất bại và operator phải nhập tay.

Manual fallback luôn phải tồn tại.

## 3. Phase 3 — Product Intelligence & Reliability

### 3.1 Metadata cache

Cache theo canonical Shopee identity `(shop_id, item_id)`:

- name;
- image URL/local image reference;
- shop;
- current price;
- original price nếu có;
- metadata source;
- fetched/confirmed timestamp.

Cache không được giả vờ là realtime. UI phải cho biết dữ liệu gần nhất và nguồn của dữ liệu.

Resolution priority khi đã biết product:

```text
fresh helper/server metadata
→ valid cache
→ manual confirmation
```

### 3.2 Canonical product upsert

Natural identity cho Shopee manual/direct product là Shopee item identity, không phải affiliate URL.

Mục tiêu:

- một canonical Product record cho cùng Shopee item;
- nhiều affiliate link / tracking context có thể tham chiếu product đó;
- nhiều post vẫn được phép;
- không tạo product giả từ URL khi chưa đủ metadata bắt buộc;
- không phá dữ liệu legacy.

Schema/migration phải idempotent và backward-compatible.

### 3.3 Price history and refresh

Khi giá được xác nhận từ helper/server/manual source:

- update last known product price;
- append price history khi giá thay đổi;
- lưu source + observed timestamp.

Thêm action `Làm mới giá` trên product/confirmation UI.

Priority:

```text
Chrome Helper
→ server/cache nếu hợp lệ
→ manual fallback
```

Không auto-crawl Shopee liên tục và không chạy headless browser.

## 4. Phase 4 — Content/Review UX, Observability & Release

### 4.1 Confirmation preview

Trước `Tạo bài nháp`, hiển thị compact preview gồm:

- final product image;
- product name/price;
- caption preview;
- affiliate disclosure;
- affiliate link + copy action;
- canonical product link + copy/open action;
- selected channel;
- metadata source/state;
- validation warnings.

UI tiếp tục dùng Dark Premium hiện có, không thêm React/Vue/Tailwind.

### 4.2 Review page polish

Nâng `/duyet` nhưng không thay state machine:

- image lớn hơn/hợp lý hơn;
- caption preview gần Threads hơn;
- character counter;
- affiliate badge;
- source badge;
- product link;
- copy affiliate link;
- validation message rõ;
- primary review action nổi bật;
- responsive/mobile tốt hơn;
- focus-visible và keyboard usability giữ nguyên.

### 4.3 Shopee source observability

Audit event không chứa secret/full affiliate URL:

- `resolve_success`;
- `canonicalized`;
- `html_metadata_success`;
- `html_captcha`;
- `json_api_403`;
- `helper_metadata_success`;
- `cache_hit`;
- `cache_stale`;
- `manual_fallback`;
- `price_refresh_success`;
- `price_refresh_failed`.

Audit chỉ lưu product identity/sanitized metadata cần thiết, error category và timestamp. Không log browser/session credentials hoặc full affiliate tracking URL.

### 4.4 Release hardening

Trước merge/release:

- focused Shopee tests;
- core pipeline regression;
- pilot tests;
- manager tests;
- template/Jinja parse;
- `git diff --check`;
- secret/runtime artifact scan;
- browser pilot ở mock mode;
- một controlled real Shopee metadata pilot khi operator chủ động thực hiện;
- không publish Threads trong automated verification.

## 5. End-to-end data flow

```text
Shopee Product URL
        ↓
Canonicalize shop_id/item_id
        ↓
Bulk/direct affiliate link generation
        ↓
Known canonical Product?
   ┌────┴────┐
   │         │
  yes        no
   │         │
metadata     try metadata
cache        server-side
   │         │
   └────┬────┘
        ↓
metadata complete?
 ┌──────┴────────┐
 yes             no / blocked
  │                ↓
  │         Chrome Helper
  │                ↓
  │        helper success?
  │          ┌─────┴─────┐
  │         yes          no
  │          │            ↓
  │          │      manual fallback
  └──────────┴───────┬────┘
                     ↓
             operator confirmation
                     ↓
          canonical Product upsert
                     ↓
          price/cache/history update
                     ↓
             content preview
                     ↓
              create draft
                     ↓
             PENDING_REVIEW
                     ↓
                  /duyet
                     ↓
        explicit operator approval
                     ↓
           schedule/publish flow
```

## 6. Attribution rules

Shopee Direct và ACCESSTRADE là hai nguồn độc lập.

Không được:

- wrap Shopee Direct affiliate link bằng ACCESSTRADE;
- thay đổi prebuilt affiliate URL operator cung cấp;
- tự thêm fake `sub1=post_id` vào prebuilt link;
- khẳng định post-level conversion attribution nếu provider không cung cấp identifier tương ứng.

Với link ACP tự tạo ở Phase 1, `sub_id` chỉ được dùng theo contract đã thiết kế và không chứa secret.

## 7. Publish safety boundary

Mọi flow import/helper/product phải dừng ở:

```text
Import / Generate
→ Resolve metadata
→ Confirm
→ Create draft/review
→ PENDING_REVIEW (hoặc DRAFT nếu validation fail)
→ /duyet
```

Không tạo `PUBLISH_POST`, không gọi publisher và không auto-approve trước explicit operator approval.

## 8. Browser/security boundary

Không triển khai:

- CAPTCHA bypass;
- headless anti-bot bypass;
- Selenium/Playwright automation để đăng nhập hoặc scrape Shopee;
- cookie/session extraction;
- browser credential reuse;
- proxy credential injection;
- private API reverse engineering;
- public unauthenticated helper endpoint.

Chrome Helper là user-assisted DOM metadata transfer từ tab Shopee do user chủ động mở.

## 9. Branch strategy

Dùng stacked branches để từng phase review/test độc lập:

```text
main
 └─ feat/shopee-bulk-affiliate
      └─ feat/shopee-metadata-helper
           └─ feat/shopee-product-intel
                └─ feat/shopee-affiliate-polish
```

Phase sau base trên phase trước cho tới khi stack được merge tuần tự. Không force-push main.

## 10. Testing strategy

### Unit/focused

- URL canonicalization and validation;
- affiliate link builder/sub_id;
- helper pairing nonce lifecycle/replay rejection;
- helper payload validation;
- metadata state mapping;
- metadata cache freshness;
- product upsert/idempotency;
- price history append rules;
- audit sanitization.

### Integration

- server metadata success → AUTO_COMPLETE;
- partial metadata → AUTO_PARTIAL;
- CAPTCHA/403 → BROWSER_HELPER_REQUIRED;
- helper submit → form/product metadata update;
- invalid helper token/origin/product mismatch rejected;
- helper failure → MANUAL_REQUIRED/manual fallback;
- cache reuse and refresh;
- draft creation preserves affiliate semantics;
- no publish job before approval.

### Regression

Run with mock adapters unless an explicit controlled live pilot is requested:

```text
focused Shopee tests
pipeline tests
pilot tests
manager tests
./manage.sh test
```

## 11. Definition of Done

Toàn bộ Shopee Affiliate roadmap chỉ xem là hoàn thành khi:

- Phase 1 bulk direct-link generation hoạt động và không làm mất manual prebuilt flow;
- short/OPA product URLs canonicalize không giữ `credential_token`;
- server metadata hoạt động khi Shopee cho phép;
- CAPTCHA/403 chuyển đúng sang browser-helper state;
- Chrome Helper tự điền metadata mà không đọc/gửi cookie/session secret;
- manual fallback vẫn hoạt động;
- canonical product upsert không tạo duplicate product sai;
- metadata cache có source/timestamp rõ;
- price refresh và history hoạt động;
- confirmation/review UX được polish;
- observability có event cần thiết nhưng không log full affiliate URL/secret;
- không gọi ACCESSTRADE cho Shopee Direct;
- không fake attribution;
- không tạo publish job trước approval;
- focused/core/pilot/manager/release verification pass trong môi trường ACP Ubuntu;
- `git diff --check` sạch;
- không commit `.env.local`, DB, var/runtime media, token hoặc secret;
- browser pilot được operator duyệt;
- các stacked PR được merge tuần tự sau verification.

## 12. Explicit non-goals

- Tự tạo hàng loạt tài khoản Shopee/Threads;
- tự login Shopee;
- tự vượt CAPTCHA/identity verification;
- lấy toàn bộ catalog Shopee bằng crawler;
- auto publish content trong import flow;
- thay thế review state machine hiện có;
- xây frontend SPA mới;
- thay đổi attribution semantics không có bằng chứng từ provider.
