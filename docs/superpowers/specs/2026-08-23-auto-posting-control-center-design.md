# Auto Posting Control Center + Dynamic Topics Design

**Date:** 2026-08-23

## Goal

Nâng ACP từ cơ chế Auto chỉ biết slot/status thành một hệ thống vận hành có thể nhìn trước 48 giờ: account nào sẽ đăng lúc nào, sản phẩm/ảnh/caption nào sẽ dùng, vẫn tự động 100% nhưng operator có thể can thiệp trước giờ publish. Đồng thời Product Pool có enrich-all chạy nền và hệ chủ đề động tự phát hiện từ dữ liệu Shopee.

## Approved product decisions

1. Auto Posting chạy tự động 100%; operator có thể xem/sửa/đổi/hủy trước giờ chạy.
2. Luôn pre-create kế hoạch 48 giờ phía trước.
3. Plan có thể được Auto cập nhật có chọn lọc khi dữ liệu thay đổi.
4. Nếu sản phẩm mất eligibility, tự thay sản phẩm khác trong cùng slot.
5. Enrich toàn bộ ảnh chạy bằng queue nền, có progress + Pause/Resume + Retry Failed.
6. Tạo trang riêng `Auto Posting`; `Vận hành` giữ vai trò technical operations.
7. Chỉ ẩn `Sản phẩm` và `Shopee Affiliate` khỏi sidebar; route cũ vẫn hoạt động.
8. Thêm favicon/icon local cho web.
9. Dynamic Topics tự phát hiện từ Product Pool; nếu đủ điều kiện thì tự tạo nhưng operator có thể rename/merge/disable/delete.
10. Topic có phân cấp. Chọn topic cha ở Channel tự động kế thừa topic con hiện tại và tương lai; có thể exclude riêng một nhánh.
11. Dynamic Topic classification chạy nền ngay sau CSV import.
12. Ngưỡng auto-create: `cluster_size >= 5` và `confidence >= 0.80`.
13. Nếu similarity với topic hiện có `>= 0.92`, tự merge và lưu alias.

## Non-goals / safety

- Không tạo scheduler/timer thứ hai. Dùng scheduler + `job_queue` + worker hiện có.
- Không publish Threads thật trong verification.
- Không đổi `ACP_ADAPTER` sang live.
- Không xóa route cũ `/sanpham` hoặc `/sanpham/shopee-bulk`.
- Không xóa/ghi đè production DB/secrets.
- System topics hiện có trong `core/niche.py` vẫn là nguồn safety rules; Dynamic Topics không được thay thế các `extra_banned_phrases`/include/exclude rule hiện có.

## Architecture

### 1. Topic model

Thêm các bảng:

- `topic`: `id`, `code`, `name`, `topic_type` (`SYSTEM|AUTO|MANUAL`), `parent_id`, `status`, `confidence`, `product_count`, `duplicate_candidate_of`, timestamps.
- `topic_alias`: alias -> canonical topic.
- `product_topic`: many-to-many Product <-> Topic, có confidence/source.
- `channel_topic_rule`: `INCLUDE|EXCLUDE` theo channel/topic.

`core/niche.py` vẫn giữ các System Topic và content safety. Khi migrate/init, các key trong `niche.NICHES` được mirror vào `topic` với `topic_type='SYSTEM'`.

### 2. Channel inheritance semantics

- Không có INCLUDE rule = nhận mọi topic.
- INCLUDE topic cha = nhận tất cả descendant hiện tại và tương lai.
- EXCLUDE topic con luôn thắng INCLUDE ancestor.
- Dynamic topic mới mặc định không tự thêm explicit row vào channel; inheritance được tính runtime nên không cần reconfigure.
- Giữ `channel.niches` để backward compatibility; runtime topic layer là nguồn routing mới.

### 3. Dynamic Topic discovery

Sau Shopee CSV confirm:

1. Import Product transaction hoàn tất.
2. Queue image enrichment như hiện tại.
3. Queue một background topic-classification/discovery job.
4. Worker mirror System Topic match cho Product.
5. Với Product chưa có subtopic phù hợp, trích candidate phrase từ tên/category, gom cluster toàn Product Pool.
6. Chỉ auto-create nếu cluster >= 5 và confidence >= 0.80.
7. Candidate similarity >= 0.92 với canonical topic hiện có -> merge vào topic đó và thêm alias.
8. Similarity 0.80-0.91 -> giữ riêng nhưng set `duplicate_candidate_of` để operator thấy.

Classifier phải deterministic/offline-capable; có seam cho LLM/classifier tương lai nhưng không bắt buộc external API để hệ thống chạy.

### 4. Product Pool

Bộ lọc topic đổi từ flat static niche list sang tree từ DB. Mỗi item hiển thị topic path/tags.

Thêm `Enrich toàn bộ`:

- Bấm một lần -> backfill tất cả sản phẩm thiếu ảnh + đánh bulk run `RUNNING`.
- Existing worker xử lý `SHOPEE_ENRICH_PRODUCT` theo batch/job hiện có; không giữ HTTP request mở.
- Progress: total / ready / pending / needs-helper / failed.
- Pause: không queue thêm generation mới; job đang chạy được hoàn thành.
- Resume: tiếp tục queue pending.
- Retry Failed: reset retry budget và queue lại FAILED.

### 5. Auto Post Plan

Thêm `auto_post_plan` làm lớp quản trị giữa scheduler và publisher:

- `id`, `channel_id`, `scheduled_at`, `product_id`, `post_id`, `publish_target_id`, `state`, `content_revision`, `generated_at`, `last_reconciled_at`, `replacement_count`, `last_change_reason`, timestamps.
- States: `PLANNED`, `READY`, `REGENERATING`, `PUBLISHING`, `PUBLISHED`, `CANCELLED`, `FAILED`.
- Unique live slot theo `(channel_id, scheduled_at)`.

Khi existing auto scheduler tạo/approve target, runtime upsert plan tương ứng. Không tạo scheduler mới.

### 6. 48-hour Control Center

Route `/auto-posting` hiển thị 48 giờ tới, group theo account + thời gian. Mỗi card hiển thị:

- account/channel
- scheduled time
- product + topic
- image preview
- full caption preview
- affiliate URL
- plan state/revision
- audit/change reason

Actions:

- sửa caption
- đổi product
- đổi giờ
- hủy plan/target

Không action nào tự publish ngay.

### 7. Reconciliation

Trước publish của auto target và trong periodic Auto scheduler pass:

- Product vẫn hợp lệ -> giữ nguyên.
- Giá thay đổi -> refresh caption khi caption chứa giá cũ / regenerate deterministic caption nếu cần.
- Ảnh không usable -> refresh/enrich image; chưa READY thì chưa publish.
- Caption fail validation -> regenerate caption.
- Product mất eligibility -> tìm Product khác phù hợp channel topic + slot; giữ nguyên slot; regenerate full artifacts; tăng `replacement_count`.

Mọi thay đổi ghi audit với sanitized reason; không log token/full provider error secret.

### 8. Web navigation

Sidebar mới:

- Tổng quan
- Shopee CSV Import
- Shopee Product Pool
- Auto Posting
- Thư viện ảnh
- Kênh
- ...
- Vận hành

Ẩn khỏi sidebar nhưng giữ route:

- Sản phẩm
- Shopee Affiliate

Thêm local `web/static/favicon.svg` và `<link rel="icon">` trong `base.html`.

## Verification

- Unit tests cho topic hierarchy/inheritance, cluster threshold, similarity merge, bulk enrich state, plan lifecycle/replacement.
- Web tests cho hidden nav + favicon + `/auto-posting` + Product Pool bulk controls + Channel topic tree.
- Regression tests cho Shopee CSV import vẫn idempotent và queue enrichment + topic job sau commit.
- Regression tests cho non-Shopee/provider behavior không bị đổi.
- Compile check.
- Release-level `./manage.sh test` chỉ được claim nếu chạy thật và exit 0; GitHub Actions hiện không được coi là bằng chứng nếu job fail trước steps.
