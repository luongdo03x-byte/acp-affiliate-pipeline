# ACP 2.0 — Thiết kế nhập link affiliate Shopee từ client

**Ngày:** 2026-08-07
**Trạng thái:** Thiết kế đã được chốt trong hội thoại; chờ review bản spec trước khi lập implementation plan.

## 1. Mục tiêu

Bổ sung vào trang `/sanpham` một luồng cho phép operator dán **link affiliate Shopee có sẵn**, để ACP tạo một bài nháp từ đúng sản phẩm đó mà **không đi qua ACCESSTRADE để tạo tracking link**.

Luồng phải:

1. nhận link affiliate Shopee từ giao diện web;
2. resolve redirect an toàn để tìm URL sản phẩm cuối;
3. thử lấy metadata công khai của sản phẩm;
4. luôn hiện màn hình xác nhận để operator kiểm tra/chỉnh;
5. giữ nguyên link affiliate do operator nhập;
6. tạo product/post bằng pipeline nội dung hiện có;
7. dừng ở `PENDING_REVIEW`;
8. không tạo hành vi publish trước khi operator duyệt.

## 2. Phạm vi

### Trong phạm vi

- UI trên `/sanpham`.
- Tab/chế độ `Nhập link affiliate`.
- Resolve redirect của link Shopee.
- Đọc metadata công khai không cần đăng nhập:
  - tên sản phẩm;
  - giá hiện tại;
  - giá gốc nếu có;
  - ảnh sản phẩm;
  - tên shop nếu có;
  - URL sản phẩm cuối.
- Fallback nhập/chỉnh tay khi metadata thiếu hoặc sai.
- Chọn kênh Threads trước khi tạo bài.
- Upsert một product nguồn thủ công.
- Giữ nguyên affiliate URL đã nhập trong `post.affiliate_link`.
- Tạo ảnh/caption bằng pipeline hiện tại.
- Validate nội dung.
- Chuyển bài sang `PENDING_REVIEW`.
- Redirect sang `/duyet` hoặc màn hình preview sau khi tạo thành công.
- Unit/integration/web tests cho luồng mới.

### Ngoài phạm vi

- Không tạo affiliate link qua ACCESSTRADE.
- Không browser automation/headless browser.
- Không bypass anti-bot, CAPTCHA hoặc đăng nhập Shopee.
- Không tự đăng bài lên Threads.
- Không auto-approve.
- Không tự chèn `sub1=post_id` vào link affiliate có sẵn.
- Không cam kết attribution theo từng post nếu link do Shopee tạo sẵn không hỗ trợ custom sub-id.
- Không thay đổi cơ chế publish/retry hiện tại ngoài phần cần thiết để nhận post được tạo từ nguồn mới.

## 3. UX đã chốt

Trang `/sanpham` có hai chế độ:

```text
[ Tìm sản phẩm ]    [ Nhập link affiliate ]
```

Chế độ `Nhập link affiliate` có form đầu tiên:

```text
Link affiliate Shopee
[ https://...                                  ]

[ Phân tích link ]
```

Sau khi phân tích, **luôn** hiện màn hình xác nhận:

```text
Ảnh         [ preview ]
Tên         [...............................]
Giá         [...............................]
Giá gốc     [...............................]
Shop        [...............................]
Link gốc    [ readonly ]
Affiliate   [ readonly ]
Kênh        [ Threads channel ▼ ]

[ Tạo bài nháp ]
```

Quy tắc:

- `Tên`, `Giá`, `Ảnh`, `Affiliate URL`, `Kênh` là bắt buộc.
- `Giá gốc` và `Shop` là tùy chọn.
- Operator được sửa mọi metadata ngoại trừ affiliate URL ở bước xác nhận; muốn đổi affiliate URL thì quay lại bước phân tích.
- Nếu metadata tự động lấy không đầy đủ, form vẫn mở với trường thiếu để nhập tay.
- `Tạo bài nháp` không publish và không auto-approve.

## 4. Luồng nghiệp vụ

```text
GET /sanpham
  ↓
chọn "Nhập link affiliate"
  ↓
POST phân tích URL
  ↓
AffiliateUrlResolver
  ↓
validate host + chống SSRF
  ↓
follow redirect có giới hạn
  ↓
ProductMetadataResolver
  ↓
OpenGraph / JSON-LD / metadata công khai
  ↓
render form xác nhận
  ↓
operator kiểm tra/chỉnh
  ↓
POST tạo bài nháp
  ↓
validate form + channel
  ↓
NormalizedProduct(source=manual_shopee)
  ↓
upsert product
  ↓
create post_id
  ↓
affiliate_link = link operator đã nhập
  ↓
compose image
  ↓
generate caption
  ↓
content.validate()
  ↓
PENDING_REVIEW
  ↓
/duyet
```

Không có bước `PUBLISH_POST` trong request tạo bài nháp.

## 5. Kiến trúc

Không đặt toàn bộ logic vào `web/server.py`. Tách tối thiểu ba trách nhiệm.

### 5.1. Affiliate URL Resolver

Trách nhiệm:

- nhận URL operator nhập;
- chỉ chấp nhận `http`/`https`;
- kiểm tra hostname thuộc allowlist Shopee cấu hình;
- chống SSRF;
- follow redirect với số hop giới hạn;
- trả:
  - `affiliate_url_original`;
  - `resolved_product_url`;
  - redirect chain đã lọc để debug nội bộ, không chứa secret.

Interface khái niệm:

```python
resolve_affiliate_url(url) -> ResolvedAffiliateUrl
```

### 5.2. Product Metadata Resolver

Trách nhiệm:

- tải trang công khai từ URL sản phẩm đã resolve;
- không dùng browser/headless;
- ưu tiên metadata có cấu trúc:
  1. JSON-LD product;
  2. OpenGraph;
  3. metadata HTML công khai tương đương;
- trả metadata có thể thiếu trường;
- không coi thiếu metadata là lỗi kết thúc flow.

Interface khái niệm:

```python
resolve_product_metadata(resolved_url) -> ProductMetadata
```

### 5.3. Manual Shopee Product/Post Service

Trách nhiệm:

- validate dữ liệu operator đã xác nhận;
- chuẩn hóa thành model product mà pipeline hiện tại sử dụng;
- upsert product;
- tạo post;
- giữ nguyên affiliate URL operator nhập;
- gọi logic compose ảnh/caption hiện có;
- validate caption;
- lưu `PENDING_REVIEW`;
- tuyệt đối không gọi publisher.

Interface khái niệm:

```python
create_post_from_manual_affiliate_product(
    confirmed_product,
    affiliate_url,
    channel_id,
) -> post_id
```

Tên cụ thể có thể điều chỉnh theo convention source hiện tại, nhưng ranh giới trách nhiệm phải giữ nguyên.

## 6. Mô hình dữ liệu P0

Ưu tiên **không migration schema nếu source hiện tại đã đủ field**.

### `product`

Dùng:

```text
source              = manual_shopee
merchant            = shopee.vn
external_product_id = Shopee item/product ID nếu parse được
                      nếu không: ID deterministic từ canonical product URL
name                = giá trị operator xác nhận
current_price       = giá trị operator xác nhận
original_price      = nullable
image_url_original  = URL ảnh operator xác nhận
product_url         = resolved/canonical Shopee product URL
is_available        = true tại thời điểm import
```

Để tránh duplicate:

- ưu tiên Shopee item/product ID khi URL chứa ID ổn định;
- nếu không lấy được ID, dùng hash deterministic của canonical product URL;
- unique key hiện có `(source, merchant, external_product_id)` tiếp tục được sử dụng.

Không bịa `rating`, `review_count`, `sold_count`, commission hoặc category nếu metadata không cung cấp.

### `post`

Dùng:

```text
affiliate_link = CHÍNH XÁC link affiliate operator đã nhập
status         = PENDING_REVIEW sau khi nội dung hợp lệ
thread_id      = NULL
```

`sub_id_payload` không được giả vờ là post-bound attribution. Nếu schema cho phép giá trị rỗng thì lưu representation rỗng theo convention hiện có. Nếu schema/pipeline bắt buộc structure attribution, implementation phải dùng một representation rõ ràng cho `shopee_direct/prebuilt` và test rằng conversion logic không coi nó như `sub1=post_id`.

Không thay đổi attribution semantics âm thầm.

## 7. Attribution

Link affiliate Shopee có sẵn được coi là một provider độc lập với ACCESSTRADE.

```text
provider: shopee_direct
link: prebuilt
post-bound sub-id: không bảo đảm
```

Hệ quả:

- bài vẫn có thể chứa link affiliate và publish bình thường;
- ACP không được tuyên bố đơn Shopee đã map chính xác về `post_id` nếu link không có custom sub-id;
- dashboard/reconcile chỉ được gắn conversion về post khi provider thực sự trả identifier đủ tin cậy;
- không bọc link Shopee trong link ACCESSTRADE;
- không gọi API ACCESSTRADE trong flow này.

## 8. Bảo mật URL / SSRF

Vì server sẽ request URL do operator nhập, đây là yêu cầu bắt buộc.

### Validation

- scheme chỉ `http` hoặc `https`;
- hostname phải nằm trong allowlist Shopee;
- default allowlist:
  - `shopee.vn`
  - `s.shopee.vn`
- host khác bị reject cho đến khi được thêm rõ ràng vào cấu hình sau khi kiểm chứng;
- redirect mới cũng phải qua cùng validation;
- giới hạn redirect, ví dụ tối đa 5 hop;
- timeout connect/read ngắn;
- giới hạn kích thước response HTML;
- chỉ chấp nhận content type HTML cho bước metadata.

### Chống truy cập mạng nội bộ

Trước mỗi request/redirect:

- resolve DNS;
- từ chối loopback;
- từ chối private/link-local/reserved IP;
- không follow redirect sang IP literal hoặc host không allowlist;
- không cho `file:`, `ftp:`, `data:` hoặc scheme khác;
- không gửi cookie/session/operator credentials;
- không forward header nhạy cảm từ request client.

Không log query string đầy đủ nếu nó có khả năng chứa tracking identifier nhạy cảm; log URL ở dạng redact phù hợp.

## 9. Error handling

### Link sai/không hỗ trợ

Hiển thị:

```text
Link affiliate Shopee không hợp lệ hoặc host chưa được hỗ trợ.
```

Không tạo product/post.

### Resolve redirect thất bại

Giữ nguyên dữ liệu người dùng đã nhập và báo lỗi ở bước phân tích. Không tự suy đoán destination.

### Metadata tải thất bại hoặc thiếu

Không chặn flow.

Render màn hình xác nhận với:

- affiliate URL giữ nguyên;
- resolved URL nếu đã có;
- metadata lấy được;
- trường thiếu để operator điền tay.

### Ảnh lỗi

`Tạo bài nháp` bị chặn cho đến khi có URL ảnh hợp lệ và bước xử lý ảnh thành công.

### Pipeline caption/image lỗi

Không publish. Không tạo publish job. Báo lỗi rõ ràng, secret-free; product đã upsert có thể giữ lại nếu transaction boundary hiện tại yêu cầu, nhưng post không được để ở trạng thái có thể publish ngoài ý muốn.

## 10. Web routes/forms

Tên route cuối cùng nên bám convention Flask hiện có. Hành vi yêu cầu:

```text
GET  /sanpham
POST /sanpham/affiliate/resolve
POST /sanpham/affiliate/create
```

Hoặc tương đương nếu current `web/server.py` đang dùng naming khác.

Cả hai POST:

- yêu cầu đăng nhập dashboard;
- yêu cầu CSRF như các form quản trị hiện tại;
- không expose stack trace;
- redirect/render lỗi theo pattern hiện có.

Không cần xây REST API riêng cho P0 nếu server-rendered form hiện tại đã đủ.

## 11. Tương tác với pipeline hiện có

Luồng mới phải tái sử dụng:

- DB helpers/upsert product hiện có;
- image composer hiện có;
- caption generator hiện có;
- content validation hiện có;
- review state machine hiện có;
- channel selection/niche logic hiện có khi phù hợp.

Không duplicate logic `product -> post -> image -> caption -> validate`.

Điểm khác biệt duy nhất ở source/tracking stage:

```text
TikTok/API source
  → provider tạo tracking link

Manual Shopee
  → operator cung cấp affiliate link sẵn
```

Sau khi có normalized product + affiliate link, hai luồng nên hội tụ vào cùng content pipeline.

## 12. Safety khi publish

Luồng mới chỉ tạo `PENDING_REVIEW`.

Yêu cầu kiểm thử:

- submit link không publish;
- resolve metadata không publish;
- create draft không publish;
- không có `thread_id`;
- không gọi Threads adapter;
- không tự approve;
- không tạo publish job trước action approve hiện có.

Publish thật vẫn đi qua `/duyet` và cơ chế approve/publish hiện tại.

## 13. Test plan

### Unit tests

`AffiliateUrlResolver`:

- nhận `https://shopee.vn/...`;
- nhận `https://s.shopee.vn/...`;
- reject scheme không phải HTTP(S);
- reject host ngoài allowlist;
- reject redirect ra host ngoài allowlist;
- reject private/loopback/link-local IP;
- giới hạn redirect;
- timeout/response size được xử lý thành lỗi an toàn.

`ProductMetadataResolver`:

- JSON-LD đầy đủ;
- OpenGraph fallback;
- thiếu giá;
- thiếu shop;
- thiếu ảnh;
- HTML hỏng;
- upstream HTTP lỗi;
- không bịa field thiếu.

Manual product normalization:

- parse product ID khi có;
- deterministic fallback ID;
- giá > 0;
- original price nullable;
- no fabricated rating/commission.

### Web tests

- `/sanpham` yêu cầu login theo security hiện có;
- tab nhập affiliate render;
- POST resolve cần CSRF;
- resolve hợp lệ render màn hình xác nhận;
- metadata thiếu vẫn render form;
- POST create thiếu tên/giá/ảnh/channel bị reject;
- create thành công redirect tới bài review;
- affiliate link trong post giữ đúng input;
- post là `PENDING_REVIEW`;
- `thread_id` null;
- không có publish side effect.

### Regression

Phải tiếp tục chạy:

```bash
python3 tests/test_manage.py
./manage.sh test
```

và các suite ACP hiện tại theo layout release.

Trong test, adapter publish phải ở mock; không chạy controlled live publish như một phần của automated test.

## 14. Acceptance criteria

Feature được coi là hoàn thành khi:

```text
✓ Operator mở /sanpham và chọn Nhập link affiliate
✓ Paste một link Shopee được allow
✓ ACP resolve an toàn
✓ ACP tự điền được metadata khi trang cung cấp metadata công khai
✓ Thiếu metadata thì operator nhập tay được
✓ Luôn có màn hình xác nhận trước tạo bài
✓ Affiliate URL trong post đúng nguyên link đã nhập
✓ Không gọi ACCESSTRADE trong flow này
✓ Product được upsert không duplicate theo natural ID/fallback ID
✓ Ảnh/caption đi qua pipeline hiện tại
✓ Post ở PENDING_REVIEW
✓ Không có thread_id
✓ Không publish trước khi operator duyệt
✓ Existing pipeline/manage tests vẫn pass
```

## 15. Quyết định đã chốt

1. Nhập link trên client, không yêu cầu CLI cho thao tác thường ngày.
2. Chọn phương án A: ACP tự lấy metadata, thiếu thì fallback nhập tay.
3. Chọn A1: luôn hiện bước xác nhận, kể cả metadata lấy đủ.
4. Không dùng ACCESSTRADE cho link affiliate Shopee đã có sẵn.
5. Không dùng headless browser hoặc anti-bot bypass.
6. Giữ link affiliate nguyên bản.
7. Dừng ở `PENDING_REVIEW`.
8. Shopee direct attribution và ACCESSTRADE attribution là hai provider độc lập.
9. P0 ưu tiên không migration database nếu source hiện tại cho phép.
10. URL fetching bắt buộc có SSRF protection.
