# ACP 2.0 — Thiết kế FacebookPublisher & InstagramPublisher thật (Sub-project C)

**Ngày:** 2026-08-15
**Trạng thái:** Thiết kế đã được chốt trong hội thoại; chờ review bản spec trước khi lập implementation plan.
**Thuộc:** Sub-project C trong 4 phần (A → B → C → D) chia nhỏ từ
`PTYC_ACP_FACEBOOK_INSTAGRAM_MULTI_ACCOUNT.md`. C build trên nền
`publish_target`/`Publisher` registry (A) và `MetaConnectionService`/`/kenh`
(B), cả hai đã merge vào `feat/shopee-affiliate-import`. C là nền cho D
(multi-select, caption per-platform, media library, UI duyệt đa kênh).

## 1. Mục tiêu

Cho phép ACP thực sự đăng bài lên Facebook Page và Instagram Professional
account (không còn dừng ở kết nối/import như B) — bằng cách thêm
`FacebookPublisher`/`InstagramPublisher` đúng interface `Publisher` đã có từ
A, đăng ký vào registry, xử lý ảnh đơn/carousel, và thử áp native Meta label
theo đúng scope PTYC §29 — **không sửa logic routing/exception-handling của
`core/pipeline.py::publish_post`, không sửa `core/jobs.py`**, vì cả hai đã
generic theo platform từ A. Phần MỞ RỘNG duy nhất trong `publish_post` là
một đoạn nhỏ, cộng thêm (đọc `result.native_label_status`, ghi audit) —
xem §7; không đụng routing/exception-handling hiện có.

## 2. Phạm vi

### Trong phạm vi

- `MockFacebookPublisher`, `MockInstagramPublisher` (fixture xác định, test
  ngay không cần mạng, mirror `MockThreads`).
- `FacebookPublisher`, `InstagramPublisher` thật (Graph API): publish ảnh
  đơn, publish nhiều ảnh (Facebook multi-photo, Instagram carousel).
- Đăng ký `"facebook"`/`"instagram"` vào `adapters/factory.py::get_publishers()`.
- Media validation trước khi gọi API: đếm ảnh đúng giới hạn platform, raise
  `ContentViolationError` (non-retryable) nếu sai.
- Thử áp native Meta label sau khi publish thành công (best-effort,
  không chặn publish nếu thất bại).
- `PublishResult` thêm field `native_label_status` (additive, có default).
- `core/pipeline.py::publish_post` đọc `native_label_status` sau khi
  `publisher.publish()` trả về, ghi audit tương ứng.
- Unit/integration tests cho toàn bộ luồng trên (mock).

### Ngoài phạm vi

- `caption_facebook`/`caption_instagram` riêng theo platform (D).
- Multi-select account, `AccountGroup`/preset (D).
- Media library, nhiều ảnh thật trong một post (nguồn ảnh vẫn chỉ có
  `image_url_composited` đơn từ `imaging.compose()` — D mới cho nhiều ảnh
  thật; C chỉ đảm bảo publisher XỬ LÝ ĐÚNG khi nhận `media` nhiều phần tử,
  chưa có đường nào trong hệ thống hiện tại tạo ra danh sách đó).
- UI `/duyet`/`/vanhanh` đa kênh, cổng tương tác "Quay lại chỉnh"/"Vẫn
  đăng" thật (D) — xem quyết định §7.
- Live pilot thật với Meta (cần App được duyệt đủ quyền — ngoài phạm vi
  session phát triển này, giống B).

## 3. Bối cảnh hiện tại (đã khảo sát code, post-B)

```text
core/pipeline.py::publish_post (từ A, không đổi trong C)
  publisher = ctx["publishers"][channel["platform"]]
  media = [post["image_url_composited"]] if post["image_url_composited"] else []
  result = publisher.publish(channel, post["caption_final"], media=media)
  -- hoàn toàn generic theo platform, C chỉ cần publisher mới đúng interface

adapters/base.py::Publisher (từ A)
  def publish(self, channel_row, caption: str, media: list) -> PublishResult

channel (từ B)
  mỗi Page/IG account có token RIÊNG trong channel.token_encrypted
  -- publisher không giữ credential, nhận qua channel_row như Threads

core/imaging.py::compose()
  luôn tạo ĐÚNG 1 ảnh/post -- media luôn là list rỗng hoặc 1 phần tử hiện
  tại; carousel thật (>1 ảnh) chưa có đường nào tạo ra tới khi D xong
```

## 4. Publish flow theo platform

### 4.1. Facebook Page

```text
1 ảnh:
  POST /{page-id}/photos  (url=<image_url>, caption=<caption>,
                            published=true, access_token=<page_token>)
  -> {id: photo_id, post_id: post_id}

Nhiều ảnh (khi media có >1 phần tử):
  với mỗi ảnh:
    POST /{page-id}/photos (url=.., published=false, access_token=..)
    -> {id: media_fbid}
  rồi:
    POST /{page-id}/feed (message=<caption>,
                           attached_media[i]={"media_fbid": media_fbid_i},
                           access_token=..)
    -> {id: post_id}
```

### 4.2. Instagram Professional

```text
1 ảnh:
  POST /{ig-user-id}/media (image_url=.., caption=.., access_token=..)
  -> {id: creation_id}
  poll GET /{creation_id}?fields=status_code tới FINISHED (giống Threads)
  POST /{ig-user-id}/media_publish (creation_id=.., access_token=..)
  -> {id: media_id}

Carousel (khi media có 2-10 phần tử):
  với mỗi ảnh:
    POST /{ig-user-id}/media (image_url=.., is_carousel_item=true,
                               access_token=..)
    -> {id: child_id}
  rồi:
    POST /{ig-user-id}/media (media_type=CAROUSEL,
                               children=<child_id_1,child_id_2,...>,
                               caption=.., access_token=..)
    -> {id: creation_id}
  poll + publish như nhánh 1 ảnh
```

`FacebookPublisher`/`InstagramPublisher` tự rẽ nhánh single/multi theo
`len(media)` — không cần tham số riêng.

## 5. Media validation

Validate **trước khi gọi API nào**, raise `ContentViolationError` (đúng
exception taxonomy đã có ở A, non-retryable) nếu sai:

```text
Facebook:  1..10 ảnh (giới hạn thực tế cần xác nhận lại với docs Meta lúc
           go-live — ghi rõ trong code comment, đúng tinh thần PTYC §32)
Instagram: 1 ảnh -> nhánh single
           2..10 ảnh -> nhánh carousel
           0 ảnh hoặc >10 -> ContentViolationError
```

Không tự crop/resize/transform ảnh trong scope này (đúng PTYC §32 — ảnh đã
qua `imaging.compose()` sẵn kích thước chuẩn).

## 6. `PublishResult` mở rộng

```python
@dataclass
class PublishResult:
    external_post_id: str
    published_at: str
    native_label_status: str = "not_attempted"  # MỚI, additive, có default
```

`ThreadsPublisher`/`MockThreads` không đổi — các lệnh gọi `PublishResult(
external_post_id=.., published_at=..)` hiện có vẫn hợp lệ nguyên trạng
(dataclass field mới có default không phá constructor cũ).

## 7. Native Meta label — đã chốt: vẫn đăng, không label + audit cảnh báo

Sau khi publish thành công, `FacebookPublisher`/`InstagramPublisher` thử áp
native label bằng một lệnh Graph API riêng (best-effort). **Lỗi ở bước này
không được làm hỏng post đã đăng thành công** — publish đã xong, label chỉ
là bước phụ. Set `native_label_status`:

```text
"applied"     -- áp thành công
"unavailable" -- API/account không hỗ trợ (permission thiếu, chưa duyệt)
"failed"      -- lỗi khi gọi (network, response bất thường)
```

`Publisher` **không tự ghi `conn`/`audit()`** — giữ đúng ranh giới kiến trúc
hiện có (chỉ `core/pipeline.py` chạm DB). `publish_post` đọc
`result.native_label_status` sau khi `publisher.publish()` trả về, ghi audit
`native_label_requested` kèm outcome nếu khác `"not_attempted"`.

Không chặn publish, không đẩy post về trạng thái review lại — operator đã
duyệt bài trước khi job publish chạy, nên không có chỗ hỏi tương tác tại
thời điểm này. Nút "Quay lại chỉnh"/"Vẫn đăng" tương tác thật (chặn TRƯỚC
publish, hỏi operator ngay tại `/duyet`) là việc của D khi D xây UI duyệt đa
kênh — cần thông tin capability account mà chỉ D's review flow mới có chỗ
hiển thị.

## 8. Mock adapters

`MockFacebookPublisher`/`MockInstagramPublisher` mirror `MockThreads`:
`fail_rate`, `rate_limited` constructor params để test được cả đường lỗi.
`native_label_status` mock trả cố định (vd `"applied"` mặc định, hoặc tham
số hoá để test được cả 3 nhánh outcome).

## 9. Factory wiring

```python
def get_publishers() -> dict:
    if is_live():
        from .live import ThreadsChannel, FacebookPublisher, InstagramPublisher
        return {"threads": ThreadsChannel(), "facebook": FacebookPublisher(),
                "instagram": InstagramPublisher()}
    from .mock import MockThreads, MockFacebookPublisher, MockInstagramPublisher
    return {"threads": MockThreads(...), "facebook": MockFacebookPublisher(),
            "instagram": MockInstagramPublisher()}
```

(Chữ ký chính xác theo `core/pipeline.py`/tests hiện có — chi tiết ở
implementation plan.)

## 10. Error handling

```text
Media sai giới hạn        -> ContentViolationError, non-retryable, đúng
                              cơ chế jobs.py hiện có (đẩy post về
                              PENDING_REVIEW, không retry)
Token hỏng/hết hạn        -> AuthError (đúng jobs.py hiện có -> channel
                              NEEDS_REAUTH)
Rate limit                -> RateLimitError (đúng jobs.py hiện có -> hoãn,
                              không tính retry)
Lỗi mạng/API tạm thời     -> PublishError (retry theo backoff hiện có)
Native label thất bại     -> KHÔNG raise gì, ghi vào PublishResult, publish
                              vẫn SUCCESS
```

Không sửa `core/jobs.py` — mọi exception mới (nếu có) phải dùng đúng 4 loại
đã có trong `adapters/base.py`.

## 11. Test plan

### Unit tests (mock)

```text
publish 1 ảnh Facebook/Instagram -> SUCCESS, external_post_id đúng
publish nhiều ảnh Facebook (attached_media) -> SUCCESS
publish carousel Instagram (2-10 ảnh) -> SUCCESS
media rỗng/>10 ảnh -> ContentViolationError
Instagram carousel với 1 ảnh -> rẽ nhánh single, không carousel
native_label_status các nhánh applied/unavailable/failed
publish_post ghi đúng audit native_label_requested theo outcome
factory đăng ký đúng facebook/instagram theo ACP_ADAPTER
```

### Regression

```text
python -m acp.tests.test_pipeline
python -m acp.tests.test_pilot
```
Phải tiếp tục pass nguyên trạng.

## 12. Acceptance criteria

```text
[ ] Facebook publish 1 ảnh hoạt động (mock)
[ ] Facebook publish nhiều ảnh hoạt động (mock)
[ ] Instagram publish 1 ảnh hoạt động (mock)
[ ] Instagram carousel hoạt động (mock)
[ ] Media sai giới hạn bị chặn với ContentViolationError, không gọi API
[ ] Native label thất bại không chặn publish, có audit
[ ] PublishResult mở rộng không phá ThreadsPublisher hiện có
[ ] factory.get_publishers() có đủ 3 platform theo đúng ACP_ADAPTER
[ ] core/jobs.py không bị sửa; core/pipeline.py chỉ thêm đúng đoạn đọc
    native_label_status + ghi audit, routing/exception-handling không đổi
[ ] Threads/Shopee/ACCESSTRADE/MetaConnectionService không regression
[ ] tests đạt, git diff --check sạch, không commit secrets
```

## 13. Quyết định đã chốt

1. Không sửa `core/jobs.py`; không sửa routing/exception-handling của
   `core/pipeline.py::publish_post` — interface `Publisher` từ A đã đủ
   generic. Ngoại lệ additive duy nhất: đoạn đọc `native_label_status` +
   ghi audit (§7), không đụng logic dispatch/exception hiện có.
2. Native label thất bại: **vẫn đăng, không label, ghi audit cảnh báo** —
   không chặn publish, không có cổng tương tác (D's việc).
3. `Publisher` không tự chạm `conn`/`audit()` — outcome trả qua
   `PublishResult.native_label_status`, `pipeline.py` ghi audit.
4. Mock-first (giống A/B): `MockFacebookPublisher`/`MockInstagramPublisher`
   test được ngay; `FacebookPublisher`/`InstagramPublisher` viết đúng chuẩn
   Graph API nhưng chưa live-test được trong session này.
5. Giữ nguyên convention 1-file-cho-mọi-live-adapter (`adapters/live.py`),
   không tách file mới.
6. Media validation dùng giới hạn Meta đã biết (IG carousel 2-10, FB tối đa
   hợp lý), ghi rõ cần xác nhận lại với docs Meta lúc go-live.
7. C không tạo đường nào sinh ra `media` nhiều phần tử thật (đó là D) —
   chỉ đảm bảo publisher xử lý đúng nếu nhận được.
