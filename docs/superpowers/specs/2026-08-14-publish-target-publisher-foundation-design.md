# ACP 2.0 — Thiết kế nền tảng PublishTarget & Publisher (Sub-project A)

**Ngày:** 2026-08-14
**Trạng thái:** Thiết kế đã được chốt trong hội thoại; chờ review bản spec trước khi lập implementation plan.
**Thuộc:** Sub-project A trong 4 phần (A → B → C → D) chia nhỏ từ
`PTYC_ACP_FACEBOOK_INSTAGRAM_MULTI_ACCOUNT.md`. A là nền tảng bắt buộc trước khi
làm B (MetaConnectionService + `/kenh` multi-platform), C (FacebookPublisher/
InstagramPublisher thật), D (multi-select, caption per-platform, media library,
UI đa kênh).

## 1. Mục tiêu

Tái cấu trúc lớp publish hiện tại (một post = một channel = một job) thành đơn
vị `publish_target` độc lập, và thay `PublishingChannel` cố định bằng
`Publisher` registry theo platform — **mà không phá bất kỳ hành vi nào của
Threads/Shopee/ACCESSTRADE hiện tại**. Đây là nền để B/C/D thêm Facebook/
Instagram sau này chỉ bằng cách đăng ký thêm publisher, không phải sửa lại
`core/pipeline.py`.

Kết thúc A, hệ thống vẫn hoạt động giống hệt bên ngoài (operator không thấy gì
khác ở `/sanpham`, `/duyet`, dashboard), nhưng bên trong:

- mỗi lượt publish là một `publish_target` có trạng thái/retry riêng, không
  còn gắn cứng vào `post.status`/`post.thread_id`;
- publisher được chọn qua `publisher_for(platform)` thay vì luôn dùng
  `ctx["channel"]` cố định;
- `Publisher.publish()` nhận danh sách media (`list[str]`) thay vì một
  `image_url` đơn — chuẩn bị cho carousel ở C mà không cần đổi chữ ký lần hai.

## 2. Phạm vi

### Trong phạm vi

- Bảng mới `publish_target` (additive, không đụng schema `post`/`channel`).
- Đổi tên `PublishingChannel` → `Publisher` trong `adapters/base.py`.
- Registry `publisher_for(platform)` trong `adapters/factory.py`, hiện chỉ
  đăng ký `"threads"`.
- Đổi chữ ký `Publisher.publish(channel_row, caption, media: list[str])`.
- `pipeline.approve_post()` tạo `publish_target` cùng transaction với việc
  set `post.status='SCHEDULED'`.
- Handler `PUBLISH_POST` đọc/ghi theo `publish_target_id`, idempotency khoá
  theo `publish_target`, đồng thời vẫn cập nhật `post` (status/thread_id/
  published_at) y hệt logic cũ để dashboard/attribution/`/duyet` không cần
  sửa.
- Hàm `retry_publish_target()` + route tối giản `/vanhanh/<target_id>/retry`
  + bảng liệt kê `publish_target` trong `ops.html` với nút "Thử lại" cho
  target FAILED.
- Cập nhật test hiện có theo payload/schema mới; test mới cho idempotency và
  retry theo target.

### Ngoài phạm vi (dành cho B/C/D)

- `MediaAsset`/`PostMedia`/media library, ảnh thật nhiều tấm (Threads vẫn
  đúng 1 ảnh trong A).
- Meta OAuth, `ChannelConnection`/`ChannelAccount`, `/kenh` multi-platform,
  Meta sync.
- `FacebookPublisher`/`InstagramPublisher` thật (Graph API), media
  validation theo giới hạn Meta, native/partnership label.
- `AccountGroup`/preset, `caption_facebook`/`caption_instagram`, override
  caption theo account, multi-select ở `/sanpham` và `/duyet`, schedule
  override theo platform.
- UI card đẹp cho `/duyet`/`/vanhanh` đa kênh (A chỉ cần bảng thô để chứng
  minh cơ chế retry theo target hoạt động).

## 3. Bối cảnh hiện tại (đã khảo sát code)

```text
post
  - channel_id      : 1 FK duy nhất
  - status           : DRAFT/PENDING_REVIEW/SCHEDULED/PUBLISHED/REJECTED
  - scheduled_at, published_at, thread_id : gắn thẳng vào post

adapters/base.py
  - PublishingChannel.publish(channel_row, caption, image_url: str)

adapters/factory.py
  - get_channel() trả về MỘT instance cố định (Mock hoặc Live), không phân
    biệt platform vì chỉ có Threads

core/pipeline.py
  - publish_post(): ctx["channel"].publish(channel, post["caption_final"],
    post["image_url_composited"])
  - idempotency chống đăng trùng dựa vào post["thread_id"]
```

`channel.platform` đã tồn tại (default `'threads'`) nên registry theo
platform không cần đổi schema `channel`.

## 4. Data model

```sql
CREATE TABLE publish_target (
    id                TEXT PRIMARY KEY,
    post_id           TEXT NOT NULL REFERENCES post(id),
    channel_id        TEXT NOT NULL REFERENCES channel(id),
    status            TEXT NOT NULL DEFAULT 'PENDING',
        -- PENDING / SCHEDULED / RUNNING / SUCCESS / FAILED / CANCELLED
    scheduled_at      TEXT,
    external_post_id  TEXT,
    last_error        TEXT,
    attempt_count     INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX idx_publish_target_post   ON publish_target(post_id);
CREATE INDEX idx_publish_target_status ON publish_target(status, scheduled_at);
```

Thêm trực tiếp vào `SCHEMA` trong `core/db.py` (bảng mới hoàn toàn, không cần
mục trong `MIGRATIONS`). `post_id` cố tình **không UNIQUE** để B/D thêm nhiều
target/post sau này không phải migrate schema lần hai.

`post` giữ nguyên 100% — không xoá, không đổi kiểu cột nào.

## 5. Publisher abstraction

`adapters/base.py`:

```python
class Publisher:
    platform: str = "base"
    def publish(self, channel_row, caption: str, media: list) -> PublishResult: ...
    def remaining_quota(self, channel_row) -> int: ...
    def fetch_insights(self, channel_row, external_post_id: str) -> dict: ...
```

`MockThreads`/`ThreadsChannel` kế thừa `Publisher`, nhận `media: list[str]`,
dùng `media[0]`, assert đúng 1 phần tử (raise lỗi rõ ràng nếu nhiều hơn —
Threads không hỗ trợ carousel, không âm thầm bỏ ảnh).

`adapters/factory.py` thêm:

```python
_PUBLISHERS = {}  # platform -> factory fn

def register_publisher(platform, factory_fn): ...
def publisher_for(platform) -> Publisher: ...
```

Đăng ký `"threads"` trỏ về `get_channel()` hiện có (giữ nguyên logic chọn
mock/live theo `ACP_ADAPTER`). B/C đăng ký thêm `"facebook"`/`"instagram"`
mà không sửa gì trong `core/pipeline.py`.

## 6. Luồng pipeline

### `approve_post()`

Giữ nguyên phần tính slot + `post.status='SCHEDULED'`. Thêm, cùng transaction:

```text
tạo publish_target (status=SCHEDULED, scheduled_at, channel_id=post.channel_id)
enqueue("PUBLISH_POST", {"publish_target_id": target_id},
        idempotency_key=f"pub:{target_id}")
```

Payload job đổi từ `{post_id, channel_id}` → `{publish_target_id}`.

### Handler `publish_post`

```text
đọc publish_target theo id → join post + channel
nếu publish_target.status == 'SUCCESS' → return (idempotent, khoá theo target)
set publish_target.status = 'RUNNING'
gọi publisher_for(channel["platform"]).publish(
        channel, post["caption_final"], media=[post["image_url_composited"]])
thành công:
    publish_target: status=SUCCESS, external_post_id, updated_at
    post: status=PUBLISHED, thread_id=external_post_id, published_at, updated_at
lỗi (RateLimitError/ContentViolationError/AuthError/PublishError):
    publish_target: status=FAILED, last_error, attempt_count += 1
    cơ chế backoff/retry của jobs.py (_defer/_fail) GIỮ NGUYÊN hoàn toàn —
    publish_target chỉ là lớp quan sát thêm, không thay cơ chế job_queue.
```

Idempotency check theo `publish_target.status` thay vì `post["thread_id"]` —
chính xác hơn khi một post có nhiều target độc lập (D).

### Retry thủ công (nền cho §16/17 của PTYC gốc)

```python
def retry_publish_target(conn, target_id) -> dict:
    """Chỉ retry khi FAILED. Reset PENDING, enqueue lại đúng target đó."""
```

Điều kiện: chỉ áp dụng khi `publish_target.status == 'FAILED'`. Reset về
`PENDING`, enqueue job mới với key `pub:{target_id}:retry:{attempt_count}`
(khác key cũ để không bị chặn bởi idempotency của lần thất bại trước). Không
tạo lại `publish_target` mới, không đụng các target khác của cùng post.

Route: `POST /vanhanh/<target_id>/retry` — yêu cầu login + CSRF như các form
quản trị khác. `ops.html` thêm bảng liệt kê `publish_target` gần đây (id
rút gọn, product, channel, status, last_error, nút "Thử lại" nếu FAILED).
Bảng `job_queue`/`channel` hiện có trong `ops.html` không đổi.

## 7. Error handling

Không thay đổi phân loại lỗi hiện có (`RateLimitError`/`ContentViolationError`
/`AuthError`/`PublishError` trong `adapters/base.py`) và cách `jobs.run_once()`
xử lý từng loại — A chỉ thêm việc `publish_target` phản ánh đúng trạng thái
song song với những gì `job_queue`/`post`/`channel` đã làm, không thay đổi
đường đi lỗi nào.

`retry_publish_target()` khi gọi trên target không FAILED (VD đã SUCCESS)
phải trả lỗi rõ ràng, không âm thầm no-op và không được enqueue thêm job.

## 8. Testing

- **`test_pipeline.py`**: nơi test hiện tại tự `jobs.enqueue("PUBLISH_POST",
  {"post_id":..., "channel_id":...})` đổi sang helper tạo `publish_target`
  trước rồi enqueue `{"publish_target_id":...}`. Assertion bổ sung kiểm
  `publish_target.status`/`external_post_id` song song với
  `post.status`/`thread_id` đã có.
- **Test mới**:
  - chạy job SUCCESS hai lần trên cùng `publish_target` → không publish lại
    (idempotency theo target).
  - `retry_publish_target()` trên target FAILED → tạo job mới, không đụng
    target khác của cùng post.
  - `retry_publish_target()` trên target không FAILED → lỗi, không enqueue.
  - `Publisher.publish()` nhận `media` nhiều hơn 1 phần tử ở ThreadsPublisher
    → lỗi rõ ràng, không lặng lẽ bỏ ảnh thừa.
- **`test_pilot.py`, `test_manage.py`**: chạy lại nguyên vẹn, không sửa
  assertion nào khác — xác nhận Threads/Shopee/ACCESSTRADE không regression.
- Không cần test tích hợp Meta thật ở A (thuộc B/C).

## 9. Definition of Done — Sub-project A

```text
[ ] publish_target tồn tại, được tạo đúng lúc approve_post()
[ ] Publisher registry (publisher_for) hoạt động, Threads đăng ký qua nó
[ ] Publisher.publish() nhận media: list[str], ThreadsPublisher dùng media[0]
[ ] publish_post dùng publish_target làm đơn vị idempotency
[ ] retry_publish_target() + route /vanhanh/<target_id>/retry hoạt động
[ ] /vanhanh hiển thị bảng publish_target với nút Thử lại cho target FAILED
[ ] test_pipeline.py cập nhật theo payload/schema mới, pass
[ ] test_pilot.py, test_manage.py pass không sửa assertion cũ
[ ] git diff --check sạch, không commit secrets/runtime data
```

## 10. Quyết định đã chốt

1. Chia PTYC gốc thành 4 sub-project (A→B→C→D); A là nền tảng bắt buộc trước.
2. `publish_target` additive hoàn toàn — `post` không đổi cột nào.
3. `post_id` trên `publish_target` không UNIQUE ngay từ A, dù A chỉ tạo đúng
   1 target/post — để D không phải migrate schema lần hai.
4. `Publisher.publish()` nhận `media: list[str]` ngay từ A dù Threads chỉ
   dùng 1 phần tử — tránh đổi chữ ký hai lần khi C thêm carousel.
5. Idempotency chuyển từ khoá theo `post` sang khoá theo `publish_target`.
6. Cơ chế backoff/retry của `job_queue` (jobs.py) giữ nguyên hoàn toàn;
   `publish_target` chỉ là lớp trạng thái/quan sát thêm, không thay thế nó.
7. `post.status/thread_id/published_at` tiếp tục được ghi song song để
   dashboard/attribution/`/duyet` không cần sửa trong A.
8. UI retry ở `/vanhanh` trong A chỉ cần bảng thô + nút bấm — UI đẹp và card
   đa kênh dành cho D.
