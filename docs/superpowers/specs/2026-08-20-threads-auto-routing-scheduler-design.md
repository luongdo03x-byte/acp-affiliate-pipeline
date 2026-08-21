# ACP — Tự chọn account Threads và lịch đăng cuốn chiếu

**Ngày:** 2026-08-20<br>
**Trạng thái:** Đã chốt trong hội thoại; chờ review bản đặc tả trước khi lập kế hoạch triển khai.

## 1. Mục tiêu

Tự động biến catalog cập nhật thành tối đa 2–3 bài Threads chất lượng mỗi
ngày cho từng account, dựa duy nhất vào các danh mục đã tick ở trang **Kênh**.
Hệ thống tự chọn account phù hợp, tạo nội dung, lấp lịch trong 48 giờ tới và,
khi account được bật Auto, đăng đúng giờ qua worker hiện hữu.

Không lập lịch cố định cả tuần. Hàng thương mại điện tử có thể hết hàng, đổi
giá hoặc mất link affiliate; lịch cuốn chiếu ngắn và kiểm tra lại sát giờ đăng
ưu tiên tính chính xác hơn số lượng bài đã tạo sớm.

## 2. Phạm vi và nguyên tắc an toàn

### Trong phạm vi

- Bổ sung cấu hình Auto, quota 2–3 bài/ngày, timezone và cửa sổ giờ đăng cho
  từng channel Threads tại `/kenh`.
- Chọn một account Threads tự động từ danh mục channel đã lưu.
- Lập lịch cuốn chiếu cho tối đa 48 giờ tới, mỗi account tối đa 3 bài/ngày và
  mặc định nhắm 2 bài/ngày; bài thứ ba chỉ được thêm khi có ứng viên chất lượng
  cao.
- Dùng dữ liệu hiệu quả lịch sử của chính account theo giờ đăng để xếp hạng
  slot; thiếu dữ liệu thì dùng các slot cấu hình trên channel.
- Tạo post/post-specific affiliate link bằng đúng pipeline hiện có và đưa bài
  vào `SCHEDULED` khi Auto của channel bật; khi tắt thì giữ
  `PENDING_REVIEW`/`DRAFT` để operator tự duyệt.
- Kiểm tra freshness ngay trước publish và huỷ/đổi bài không còn hợp lệ.
- Hiển thị trạng thái Auto, quota và lịch sắp tới trên giao diện Kênh/Vận hành.

### Ngoài phạm vi

- Không bật `ACP_ADAPTER=live`, không bật công tắc worker publish toàn hệ
  thống, không đăng Threads thật trong test hoặc migration.
- Không tự sửa danh mục đã tick của operator; checkbox tại `/kenh` vẫn là nguồn
  sự thật duy nhất cho routing.
- Không đổi semantics attribution (`sub1`, campaign ID hoặc tracking URL).
- Không áp dụng cho Facebook/Instagram trong lần này.
- Không hứa một "giờ vàng" chung cho Threads: slot chỉ là kết quả từ cấu hình
  và insight của account đó.

## 3. Cấu hình channel và migration

Thêm các cột additive, idempotent vào bảng `channel`:

```sql
auto_schedule_enabled INTEGER NOT NULL DEFAULT 0,
daily_post_target    INTEGER NOT NULL DEFAULT 2,
daily_post_cap       INTEGER NOT NULL DEFAULT 3,
posting_timezone     TEXT NOT NULL DEFAULT 'Asia/Bangkok',
posting_slots        TEXT NOT NULL DEFAULT '["09:30","12:30","20:30"]'
```

`daily_post_cap` đã tồn tại nên migration chỉ hạ giá trị mặc định cho channel
mới xuống 3; không âm thầm ghi đè channel cũ. Người vận hành cấu hình target
2 hoặc 3; server luôn validate `1 <= target <= cap <= 3`. `posting_slots` là
JSON các giờ `HH:MM`, unique, theo `posting_timezone`; có từ hai đến ba slot
trong một ngày. Mặc định chỉ dùng hai slot đầu khi target=2.

`auto_schedule_enabled=0` là fail-safe. Cờ này độc lập với
`publish_worker_enabled`: Auto channel chỉ quyết định bài có được tự duyệt và
lên lịch hay không; worker toàn hệ thống vẫn phải được operator bật riêng mới
có thể publish.

Thêm `publish_target.auto_scheduled INTEGER NOT NULL DEFAULT 0`. Đây là marker
duy nhất cho preflight freshness: chỉ target được Auto tạo có giá trị `1`, nên
bài do operator tự duyệt giữ nguyên hành vi hiện tại. Migration phải additive
và idempotent.

Mỗi lần sửa cấu hình Auto, quota hoặc slot phải ghi `audit_log` theo channel
và không ảnh hưởng các target đã `SUCCESS`.

## 4. Routing account theo danh mục Kênh

Tạo module thuần `core/auto_scheduler.py` để dễ test, không trộn logic chọn
account vào adapter hay content pipeline.

Với một product, router lấy tất cả channel thỏa đồng thời:

1. `platform='threads'`, `status='ACTIVE'`, `enabled=1`;
2. `niches` không rỗng và `niche.match_reasons(product, niches)` không có lý
   do loại — tức product thuộc ít nhất một danh mục đã tick;
3. account chưa đạt quota của ngày slot dự kiến, còn một slot trong rolling
   horizon và không có post active cho cùng product;
4. Auto channel được xét cho lịch tự động; channel tắt Auto không nhận vào
   batch tự động nhưng vẫn hoạt động y hệt trong luồng tạo/duyệt thủ công.

Nếu nhiều account khớp, router sắp hạng theo: số danh mục khớp (nhiều hơn tốt
hơn), slot có score hiệu quả cao hơn, ít bài đã lên trong ngày hơn, rồi `code`
để kết quả deterministic. Không có account phù hợp là trạng thái `skipped`,
không được fallback sang channel không có danh mục hoặc khác chủ đề.

Một product chỉ được đưa vào một account trong một lượt auto schedule. Điều đó
tránh đăng trùng cùng deal trên nhiều Threads account. Account có `niches=[]`
không được auto-route vì không thể xác nhận chủ đề; chúng vẫn hỗ trợ workflow
thủ công tương thích ngược.

## 5. Lịch cuốn chiếu 48 giờ và slot hiệu quả

`fill_auto_schedule(conn, campaign_code, now_utc)` chạy sau catalog sync/plan
hoặc từ lệnh CLI timer-safe mới. Nó xét hai ngày địa phương bắt đầu tại
`now_utc`, dựng slot từ `posting_slots`, và chỉ lấp slot tương lai chưa có
`publish_target` sống (`SCHEDULED`, `PENDING`, `RUNNING`, `SUCCESS`).

Với từng account/ngày:

- lấp trước `daily_post_target` slot; chỉ xét slot thứ ba khi target=3;
- một slot chứa tối đa một target;
- candidate được lấy từ `scoring.score_candidates(..., niches=channel_niches)`
  nên giữ toàn bộ hard filter hiện tại: tồn kho, rating/review tối thiểu, hoa
  hồng, giảm giá thật, tốc độ bán, cooldown, giới hạn danh mục/ngày;
- router không tái dùng product có post `DRAFT`, `PENDING_REVIEW`, `SCHEDULED`,
  `PENDING` hoặc `SUCCESS` còn hiệu lực;
- slot được xếp theo score `account_hour_score`: median EPC/click-rate khả dụng
  của bài Threads thành công trên chính account và cùng giờ địa phương; không
  đủ năm mẫu thì giữ đúng thứ tự `posting_slots`, không suy diễn dữ liệu từ
  account khác. Metric chỉ lấy post có `post.channel_id` là account đó; auto
  route luôn tạo một target/post nên không gán sai insight của manual fan-out.

Lịch tự lấp tối đa 48 giờ, không phải bảy ngày. Mỗi lần chạy sẽ bổ sung slot
trống; product mới tốt hơn có thể nhận slot chưa lấp nhưng không được tự hủy
bài đã lên lịch chỉ để thay bài khác.

## 6. Tạo bài và hai chế độ Auto

Luồng auto sử dụng hàm tạo post hiện tại để giữ media, caption safety,
attribution và idempotency. Sau khi content generation hoàn tất:

```text
product chất lượng + channel/slot đã chọn
  -> post-specific affiliate link + image + caption
  -> validate đúng niches của channel
  -> Auto ON: approve/schedule đúng slot, enqueue PUBLISH_POST
  -> Auto OFF: PENDING_REVIEW hoặc DRAFT, không có publish_target/job publish
```

`approve_post` nhận một cờ nội bộ rõ ràng cho automated approval thay vì giả
làm actor người dùng; audit ghi `actor='auto_scheduler'`, channel, product,
slot và lý do quyết định. Auto chỉ duyệt bài qua validator hiện có. Nếu caption
không đạt, link/ảnh lỗi hoặc thiếu channel hợp lệ thì không có publish job.

Worker publish hiện hữu vẫn là tuyến chặn cuối: chỉ publish khi target đến hạn,
channel ACTIVE/enabled, chưa đạt `daily_post_cap`, publisher đăng ký và công
tắc toàn hệ thống bật. Auto channel không thể bypass các kiểm tra này.

## 7. Freshness trước giờ publish

Khi `PUBLISH_POST` chạy cho target Auto, gọi kiểm tra thuần trước khi chạm
publisher:

1. product còn `is_available`, còn `has_inventory`, có affiliate URL hợp lệ;
2. product đã được catalog sync không quá 120 phút trước lúc publish;
3. giá/giá gốc vẫn hợp lệ và product tiếp tục qua hard filters/niche của channel;
4. post chưa có `thread_id` và target chưa `SUCCESS`.

Nếu một điều kiện sai, target chuyển `CANCELLED` với lý do đã sanitize, post
quay về `PENDING_REVIEW` để operator có thể xem lại, và audit ghi
`auto_stale_cancelled`. Worker không gọi Threads API. Lần fill tiếp theo mới
tìm product khác cho slot còn trống, không generate thay thế ngay trong handler
publish để giữ publish handler bounded và idempotent.

Kiểm tra này chạy cho target được đánh dấu Auto; target thủ công giữ hành vi
hiện tại để không thay đổi kỳ vọng operator đối với bài đã tự duyệt.

## 8. UI, CLI và vận hành

`/kenh` bổ sung khối "Tự động cho Threads" trên từng channel Threads: công tắc
Auto, target/cap 2–3 bài, timezone, 2–3 slot giờ. Form server-side validate;
channel không phải Threads không hiển thị khối này. Giao diện ghi rõ Auto chỉ
tạo/lên lịch; publish còn phụ thuộc công tắc worker toàn hệ thống.

`/vanhanh` hiển thị số slot còn trống trong 48 giờ, target Auto sắp tới và các
target bị freshness cancel mà không hiển thị URL/token hoặc lỗi provider thô.

CLI mới `python3 run.py auto-schedule` thực hiện một lượt fill an toàn, in
summary aggregate (`scheduled`, `review`, `skipped`, `cancelled`) và phù hợp
để gọi qua systemd timer sau catalog sync. Lệnh không bật worker hay live
adapter. Tài liệu runbook nêu thứ tự timer: sync catalog -> auto-schedule ->
worker-once; tất cả test dùng `ACP_ADAPTER=mock`/`ACP_SOURCE=mock`.

## 9. Kiểm thử và nghiệm thu

- Migration channel idempotent, mặc định Auto off, target/cap/slot validation
  chặn mọi giá trị vượt 3 hoặc timezone/giờ sai.
- Router chỉ chọn Threads ACTIVE/enabled có danh mục khớp; bỏ account
  `niches=[]`, full quota hoặc không còn slot; tie-break deterministic.
- Fill 48 giờ không vượt quota, không tạo hai target cùng slot, không tạo post
  trùng và chỉ dùng product qua `score_candidates` hard filter.
- Auto on tạo target/job đúng slot và audit `auto_scheduler`; Auto off tạo bài
  chờ duyệt, không có target/job publish.
- Slot ranking dùng data cùng account/giờ khi đủ mẫu, fallback slot cấu hình khi
  chưa đủ mẫu; không dùng metric của account khác.
- Preflight stale hủy target Auto trước publisher, không gọi publisher và để
  lần fill sau lấp slot bằng ứng viên mới; target thủ công không đổi hành vi.
- Route/form Kênh có CSRF, persist đúng cấu hình và không làm vỡ checkbox
  `niches` hiện hữu.
- Chạy test tập trung trong quá trình phát triển, sau đó `./manage.sh test`,
  `git diff` và `git status`. Không có live integration test hay publish thật.

## 10. Quyết định đã chốt

- Danh mục checkbox của Kênh là nguồn routing duy nhất.
- Auto ON = tự tạo, tự duyệt, tự lên lịch; publish chỉ xảy ra khi worker global
  được operator bật.
- Auto OFF = tạo bài chờ operator tự duyệt; không tự schedule/publish.
- Horizon = 48 giờ, không phải một tuần.
- Quota mặc định 2, tối đa 3 bài/account/ngày.
- Chất lượng + freshness có quyền loại bài; hệ thống ưu tiên không đăng sai/hết
  hàng hơn là cố lấp đủ quota.
