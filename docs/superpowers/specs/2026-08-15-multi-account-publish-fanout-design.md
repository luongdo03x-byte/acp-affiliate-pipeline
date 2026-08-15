# ACP 2.0 — Thiết kế chọn nhiều account + sinh N publish_target (Sub-project D1)

**Ngày:** 2026-08-15
**Trạng thái:** Thiết kế đã được chốt trong hội thoại; chờ review bản spec trước khi lập implementation plan.
**Thuộc:** D1 trong 4 phần (D1 → D2 → D3 → D4) chia nhỏ từ Sub-project D —
phần cuối của `PTYC_ACP_FACEBOOK_INSTAGRAM_MULTI_ACCOUNT.md`. D1 là nền tảng
bắt buộc trước khi làm D2 (caption per-platform + override), D3 (media
library + carousel), D4 (Account Group/preset, polish `/vanhanh`).

Xây trên nền `publish_target`/`Publisher` (A), `MetaConnectionService`/`/kenh`
multi-platform (B), `FacebookPublisher`/`InstagramPublisher` thật (C) — cả ba
đã merge vào `feat/shopee-affiliate-import`.

## 1. Mục tiêu

Hiện tại một `post` chỉ có thể nhắm tới đúng 1 kênh (`post.channel_id` NOT
NULL đơn), dù bảng `publish_target` đã được thiết kế từ Sub-project A để
không unique theo `post_id` — dành sẵn cho D. D1 hiện thực hoá điều đó: cho
phép operator chọn **nhiều account** (nhiều nền tảng: Threads/Facebook/
Instagram) khi tạo 1 bài ở `/sanpham`, và khi duyệt ở `/duyet`, sinh ra N
`publish_target` độc lập — mỗi kênh có lịch đăng, trạng thái, retry riêng,
đúng như A đã chuẩn bị.

Kết thúc D1: operator tick nhiều account cho 1 bài, có thể bỏ tick bớt lúc
duyệt, bấm Duyệt 1 lần → N job publish độc lập, mỗi kênh đăng/thất bại/retry
không phụ thuộc lẫn nhau.

## 2. Phạm vi

### Trong phạm vi

- Bảng mới `post_channel_selection(post_id, channel_id)` — lựa chọn account
  ban đầu lúc tạo bài.
- `/sanpham` (cả 2 chế độ Tìm kiếm và Affiliate): checklist chọn nhiều
  account, đa nền tảng (Threads/Facebook/Instagram), thay cho dropdown
  Threads-only hiện tại.
- `_create_post_from_raw_product()`, `create_post_for_product()`,
  `create_post_from_manual_affiliate_product()`: nhận `channel_ids: list[str]`
  thay vì (thêm cạnh) `channel_code: str`.
- `/duyet`: checklist account trên mỗi thẻ bài (tick sẵn theo lựa chọn ban
  đầu, operator được bỏ tick), 1 nút "Duyệt & lên lịch" cho cả tập được tick.
- `approve_post()`: nhận `channel_ids`, tạo N `publish_target` (mỗi kênh 1
  `_next_slot()` riêng), N job `PUBLISH_POST` riêng.
- Sửa 3 lỗi/lỗ hổng phát sinh trực tiếp từ việc có N target/post (chi tiết
  §5): race huỷ nhầm target khi target khác cùng post publish trước;
  `_next_slot`/`_published_today` tính rate-limit sai kênh; idempotency key
  `FETCH_INSIGHTS` đụng nhau giữa các kênh của cùng 1 post.
- `imaging.compose()`: bỏ watermark handle khi bài nhắm ≥ 2 account.
- Test cho mọi hành vi trên (§7).

### Ngoài phạm vi (dành cho D2/D3/D4 hoặc sau)

- Caption khác nhau theo từng platform/account, override caption theo từng
  target (D2).
- Media library, nhiều ảnh/carousel (D3).
- `AccountGroup`/preset chọn nhanh cả nhóm account (D4).
- `/vanhanh` hiển thị breakdown theo từng `publish_target` thay vì rollup
  `post.status` thô (D4 — polish, không chặn D1 hoạt động đúng).
- **Tracking link & quy kết doanh thu theo từng kênh riêng** (xem §6 — giới
  hạn đã biết, cố ý để lại cho một sub-project riêng sau D1, không phải D4).

## 3. Data model

### Bảng mới: `post_channel_selection`

```sql
CREATE TABLE IF NOT EXISTS post_channel_selection (
    post_id     TEXT NOT NULL REFERENCES post(id),
    channel_id  TEXT NOT NULL REFERENCES channel(id),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (post_id, channel_id)
);
```

Ghi đúng 1 lần khi tạo `post` — N dòng cho N account được tick ở `/sanpham`
(kể cả khi chỉ chọn 1 account, để `/duyet` có nguồn dữ liệu thống nhất, không
phải phân nhánh "bài cũ" vs "bài mới"). Đây là **lựa chọn gốc**, dùng để
tick sẵn checklist ở `/duyet` — **không bị ghi đè** khi operator bỏ tick ở
`/duyet` (giữ nguyên làm audit trail "lúc tạo định đăng đi những đâu"). Tập
cuối cùng dùng để tạo `publish_target` đến từ form submit lúc bấm Duyệt.

### `post.channel_id` — giữ nguyên ý nghĩa, thu hẹp vai trò

Không đổi schema `post`. `post.channel_id` = account **đầu tiên** trong danh
sách được chọn lúc tạo bài ("kênh chính"). Vai trò còn lại của nó sau D1:

- Watermark ảnh khi chỉ chọn 1 account (§6).
- Tracking link / attribution — vẫn dùng kênh chính, xem giới hạn đã biết §6.
- Toàn bộ query/báo cáo cũ dựa vào `post.channel_id` (funnel, `epc_by`,
  `/kenh` cột "published", `scoring.py`) tiếp tục chạy đúng như trước, không
  cần sửa gì trong D1.

Chi tiết từng kênh thực sự nhắm tới nằm ở `publish_target` (nguồn sự thật
cho việc đăng bài) và `post_channel_selection` (nguồn sự thật cho lựa chọn
ban đầu).

## 4. `/sanpham` — chọn nhiều account

Checklist chung thay cho `<select id="channel_code">` (hiện chỉ có ở chế độ
Affiliate, chỉ liệt kê Threads):

```sql
SELECT id, code, platform, handle FROM channel
WHERE status='ACTIVE' AND enabled=1
ORDER BY platform, code
```

(`_product_common_context()` đang lọc `platform='threads'` — bỏ điều kiện
đó; hiện cũng thiếu `enabled=1`, D1 thêm luôn — kênh bị tắt ở `/kenh` thì
không nên chọn được để tạo bài mới).

- Checkbox `name="channel_ids"` (multi-value), nhãn kèm platform để phân
  biệt, ví dụ `[Threads] @kenhcuaban`, `[Facebook] Page ABC`,
  `[Instagram] @kenhcuaban_ig`.
- Không tick gì → chặn submit, báo lỗi "Chọn ít nhất 1 kênh" (server-side;
  client-side JS là tiện ích thêm, không thay thế).
- **Chế độ Tìm kiếm** (`/sanpham/tao-bai`): hiện hoàn toàn không có UI chọn
  kênh, luôn rơi vào kênh Threads mặc định qua nhánh
  `channel_code=None` của `_create_post_from_raw_product`. D1 thêm checklist
  vào form của mỗi kết quả tìm kiếm; route đọc `request.form.getlist(
  "channel_ids")`, truyền xuống `create_post_for_product(channel_ids=...)`.
- **Chế độ Affiliate** (`/sanpham/affiliate/create`): thay `channel_code`
  bằng `channel_ids` tại đúng vị trí form xác nhận hiện có; route đọc
  `request.form.getlist("channel_ids")`.
- Nhãn "Kênh Threads" trong 2 chỗ (`products.html`, thông báo lỗi "Kênh
  Threads không tồn tại...") đổi thành trung tính ("Kênh", "Kênh không tồn
  tại hoặc không hoạt động") — không còn đúng khi đa nền tảng.

## 5. Luồng tạo post

`_create_post_from_raw_product()` thêm tham số `channel_ids: list[str] =
None`, giữ `channel_code: str = None` (tương thích ngược cho mọi lời gọi
trực tiếp/test hiện có không qua UI mới):

- Nếu `channel_ids` được truyền: kênh chính = phần tử đầu tiên, dùng lại
  đúng logic lookup hiện có (`SELECT * FROM channel WHERE code=? AND
  status='ACTIVE'`) cho từng id trong danh sách để validate **tất cả** tồn
  tại + `ACTIVE` + `enabled=1` — thiếu 1 kênh nào không hợp lệ thì trả lỗi
  liệt kê rõ tên, không tạo post.
- Nếu không truyền `channel_ids` (chỉ `channel_code` hoặc không gì cả):
  hành vi y hệt hiện tại (fallback Threads mặc định).
- Validate caption bằng **hợp (union)** niches của mọi kênh được chọn —
  chặt hơn so với chỉ validate theo kênh chính, tránh lọt caption vi phạm
  rule của kênh phụ.
- Sau khi insert `post` (không đổi schema/logic insert), ghi N dòng vào
  `post_channel_selection` — kể cả trường hợp chỉ 1 kênh, luôn ghi ít nhất
  1 dòng.

`create_post_for_product()` và `create_post_from_manual_affiliate_product()`
nhận thêm `channel_ids` và truyền thẳng xuống, giữ nguyên `channel_code` cho
tương thích ngược (test hiện có gọi cả hai hàm bằng `channel_code`).

## 6. `imaging.compose()` — watermark khi multi-select

`compose(product, out_dir, discount_pct=0.0, handle="@kenhcuaban")` hiện
luôn có `handle` (mặc định là chuỗi, không phải `None`). D1:

- `_create_post_from_raw_product` truyền `handle=channel["handle"] if
  len(channel_ids) == 1 else None` (dùng danh sách đã validate ở §5, không
  phải lại query).
- `compose()` nhận `handle: str = "@kenhcuaban"` → đổi default thành `None`
  và bỏ qua layer watermark khi `handle` falsy, thay vì luôn vẽ.

**Quyết định đã chốt (không phải để mở):** ảnh dùng chung cho N kênh thì
không đóng dấu handle của riêng kênh nào — tránh trường hợp đăng lên Page A
nhưng ảnh lại ghi handle Page B.

## 7. `/duyet` — checklist + duyệt nhiều account 1 lần

- `review()` (route `GET /duyet`): JOIN thêm `post_channel_selection` +
  `channel` để lấy danh sách account đã chọn ban đầu cho mỗi post (thay vì
  1 `JOIN channel ch ON ch.id = p.channel_id` như hiện tại).
- Card mỗi post: checklist account (tick sẵn theo `post_channel_selection`,
  nhãn kèm platform như ở `/sanpham`), operator bỏ tick được, giữ nguyên 1
  caption `<textarea>` (D2 mới có caption khác theo platform), giữ nguyên 1
  nút "Duyệt & lên lịch".
- Form Duyệt submit thêm `channel_ids[]` = tập đang tick tại thời điểm bấm
  nút. Không tick gì → chặn submit, lỗi tương tự §4.
- `review_action()` route (`POST /duyet/<post_id>/approve`): đọc
  `request.form.getlist("channel_ids")`, truyền vào `approve_post(...,
  channel_ids=channel_ids or None)`.

## 8. `approve_post()` — sinh N publish_target

```python
def approve_post(conn, post_id, actor="operator", caption_override=None,
                  channel_ids=None) -> dict:
```

- `channel_ids=None` → fallback `[post["channel_id"]]`, hành vi y hệt hiện
  tại (tương thích ngược cho mọi lời gọi trực tiếp/test không qua UI mới).
- Validate từng `channel_id` trong `channel_ids`: tồn tại + `enabled=1` —
  còn 1 kênh bị tắt thì trả lỗi liệt kê rõ tên, **không tạo target nào**
  (tất cả-hoặc-không-gì, tránh trạng thái nửa vời N-1 target).
- Caption validate 1 lần, niches = hợp của toàn bộ `channel_ids` **được
  submit** (không phải lựa chọn gốc lúc tạo — operator có thể đã bớt kênh ở
  `/duyệt`, dùng đúng tập cuối cùng).
- `post` UPDATE 1 lần (status/caption chung như hiện tại).
  `post.scheduled_at` = **sớm nhất** trong N giờ tính được (mỗi kênh có
  `_next_slot()` riêng) — chỉ để sort/hiển thị ở `/duyet`/`/vanhanh`, không
  còn là "giờ đăng" chính xác khi có nhiều kênh.
- Vòng lặp theo từng `channel_id`: `_next_slot(conn, channel_id)` riêng
  (xem §9 vì sao phải sửa nguồn dữ liệu của hàm này), insert 1
  `publish_target`, 1 `enqueue(PUBLISH_POST, ...)` riêng — idempotency key
  `f"pub:{target_id}"` như hiện tại (đã theo target, không đổi).
- 1 lần `audit(..., "approved", detail={"targets": [{"channel_id":...,
  "publish_target_id":...,"scheduled_at":...}, ...]})` thay vì audit rời rạc
  — dễ đọc lịch sử duyệt 1 bài multi-account hơn N dòng audit tách biệt.
- Trả về `{"ok": True, "targets": [{"channel_id", "publish_target_id",
  "scheduled_at"}, ...], "scheduled_at": <sớm nhất>,
  "publish_target_id": <target đầu tiên>}` — thêm `targets` (danh sách) làm
  nguồn đầy đủ, **đồng thời giữ nguyên 2 khoá cũ** `scheduled_at`/
  `publish_target_id` (trỏ vào target đầu tiên trong `channel_ids`) để mọi
  code/test hiện có đọc 2 khoá này không vỡ.

## 9. Sửa lỗi phát sinh trực tiếp từ N target/post

Đây không phải cải tiến tuỳ chọn — nếu không sửa, tính năng multi-account sẽ
**trông như hoạt động** (N `publish_target` được tạo) nhưng vỡ ở tầng chạy
job, nên nằm trong phạm vi bắt buộc của D1.

### 9.1. Race: target chạy sau tự huỷ vì target khác cùng post publish trước

`publish_post()` hiện tại:

```python
if post["status"] not in ("SCHEDULED", "APPROVED"):
    _cancel_target_stale_post(conn, target["id"], post["status"])
    return
```

và khi 1 target publish thành công: `UPDATE post SET status='PUBLISHED', ...`.
Với N target/post: target đầu tiên chạy xong đẩy `post.status` sang
`PUBLISHED`; mọi target còn lại (kênh khác), khi tới lượt job của nó chạy,
thấy `post.status='PUBLISHED'` — không nằm trong allowlist — và tự huỷ
(`CANCELLED`, "Bài không còn ở trạng thái có thể đăng"). Chỉ kênh chạy nhanh
nhất thực sự đăng được; các kênh còn lại bị huỷ âm thầm (không phải FAILED
nổi bật, dễ bỏ sót khi kiểm tra).

**Sửa:** đổi từ allowlist sang blocklist — chỉ huỷ khi `post.status` thực sự
không còn duyệt được nữa: `PENDING_REVIEW` (bị bounce do lỗi validate của 1
target khác — đúng ý định gốc A đã thiết kế cơ chế này), `REJECTED`,
`DRAFT`. `SCHEDULED` và `PUBLISHED` đều cho phép target tiếp tục chạy bình
thường. `("APPROVED")` trong allowlist cũ chưa từng thực sự được set ở bất
kỳ đâu trong code — bỏ luôn, không mất hành vi nào.

### 9.2. `_next_slot` / `_published_today` tính rate-limit sai kênh

Cả hai hiện query bảng `post` theo `post.channel_id`:

```python
last = conn.execute("""SELECT MAX(COALESCE(published_at, scheduled_at)) FROM post
                       WHERE channel_id=? AND status IN ('SCHEDULED','PUBLISHED')""", ...)
...
"SELECT COUNT(*) FROM post WHERE channel_id=? AND status='PUBLISHED' AND substr(published_at,1,10)=?"
```

Với N kênh/post, các kênh "phụ" (không phải `post.channel_id`) không bao
giờ được 2 hàm này nhìn thấy lịch sử đăng của chính chúng — giãn cách tối
thiểu (`min_gap_minutes`) và trần bài/ngày (`daily_post_cap`) bị tính sai
(luôn coi như chưa từng đăng gì) cho mọi kênh phụ.

**Sửa:** đổi nguồn dữ liệu sang `publish_target`. Lưu ý `PUBLISHED` không
phải trạng thái của `publish_target` (đó là trạng thái của `post`) — trạng
thái tương ứng bên `publish_target` là `SUCCESS`; và `updated_at` chỉ đúng
nghĩa "thời điểm publish" cho dòng `SUCCESS`, còn dòng `SCHEDULED` phải lấy
`scheduled_at` (giờ đặt trước), y hệt vai trò `COALESCE(published_at,
scheduled_at)` gốc:

```sql
SELECT MAX(CASE WHEN status='SUCCESS' THEN updated_at ELSE scheduled_at END)
FROM publish_target WHERE channel_id=? AND status IN ('SCHEDULED','SUCCESS')
```

```sql
SELECT COUNT(*) FROM publish_target WHERE channel_id=? AND status='SUCCESS' AND substr(updated_at,1,10)=?
```

Tương đương chính xác hành vi cũ cho trường hợp 1 kênh/1 post (post cũ trước
D1, hoặc post mới chỉ chọn 1 account), đúng thêm cho N kênh.

### 9.3. `FETCH_INSIGHTS` idempotency key đụng nhau giữa các kênh

```python
enqueue(conn, "FETCH_INSIGHTS", {"post_id": post["id"], "channel_id": channel["id"]},
        ..., idempotency_key=f"ins:{post['id']}")
```

Key chỉ theo `post_id` — với N kênh, job `FETCH_INSIGHTS` thứ 2 trở đi cho
cùng post bị hàng đợi coi là trùng lặp (theo đúng cơ chế idempotency đang có)
và không được enqueue, nên chỉ kênh đầu tiên thành công mới có insight được
lấy về.

**Sửa:** `idempotency_key=f"ins:{target['id']}"`. `fetch_insights()` (handler
đọc job) hiện dùng `post["thread_id"]` (cột dùng chung, chỉ phản ánh target
thành công đầu tiên — xem §3) để gọi API lấy insight — sai `external_post_id`
cho các kênh phụ. Payload `FETCH_INSIGHTS` thêm `publish_target_id`;
handler đọc `target["external_post_id"]` (đúng của từng kênh) thay vì
`post["thread_id"]`.

## 10. Giới hạn đã biết, cố ý để lại (không phải bug, là quyết định phạm vi)

**Tracking link & quy kết doanh thu theo kênh:** `_create_post_from_raw_product`
tạo **1 tracking link duy nhất** cho post, gắn `sub4=channel["code"]` của
kênh chính (§5). `attribution.epc_by('channel')` cũng JOIN qua
`post.channel_id` đơn. D1 **không đổi cơ chế này** — bài multi-select vẫn
đăng đúng lên N kênh, nhưng mọi click/đơn hàng phát sinh từ bất kỳ kênh nào
trong N kênh đều bị quy hết về kênh chính trong báo cáo EPC-theo-kênh. Đây
là giới hạn đã biết, được chấp nhận có chủ đích để D1 không phải sửa
`attribution.py` — module lõi nhạy cảm nhất hệ thống (theo docstring của
chính nó: "lý do hệ thống tồn tại"). Quy kết chính xác theo từng kênh (N
tracking link riêng, `post_metrics` theo từng kênh, báo cáo JOIN qua
`publish_target` thay vì `post.channel_id`) để dành cho một sub-project
riêng sau D1, không phải D4 (D4 là polish UI, đây là thay đổi lõi báo cáo
doanh thu).

## 11. Testing plan

- Tạo post với N kênh → đúng N dòng `post_channel_selection`,
  `post.channel_id` = kênh đầu tiên trong danh sách.
- Tạo post với danh sách có 1 kênh bị disabled/không tồn tại → lỗi rõ ràng,
  không tạo post, không tạo dòng `post_channel_selection` nào.
- Tạo post không truyền `channel_ids` (gọi trực tiếp kiểu cũ) → hành vi y hệt
  trước D1 (regression, dùng lại test hiện có).
- `approve_post` với `channel_ids` đầy đủ / bớt lại 1 kênh so với lựa chọn
  gốc / rỗng (lỗi, không tạo target nào) / có 1 kênh bị disabled (lỗi, không
  tạo target nào) / không truyền (fallback 1 kênh, tương thích ngược).
- **Regression trực tiếp cho §9.1:** 2 target cùng post, target A publish
  trước (→ `post.status='PUBLISHED'`), target B (kênh khác) chạy sau → publish
  bình thường, **không** bị `_cancel_target_stale_post`.
- `_next_slot`/`_published_today` đúng theo từng kênh khi có nhiều target
  trên nhiều kênh khác nhau của cùng 1 post lẫn khác post — không rò rỉ giữa
  các kênh.
- `FETCH_INSIGHTS` cho 2 target cùng post không bị coi trùng idempotency;
  `fetch_insights()` fetch đúng `external_post_id` của từng target.
- Watermark ảnh: 1 kênh → có handle; ≥ 2 kênh → không có (`compose(handle=None)`
  không vẽ layer watermark).
- `/sanpham` cả 2 chế độ: không tick kênh nào → lỗi, không tạo post.
- `/duyet`: bỏ tick bớt 1 kênh trước khi Duyệt → chỉ N-1 kênh có
  `publish_target`, kênh bị bỏ tick không có target nào được tạo.
- Toàn bộ test suite hiện có (`test_pipeline.py`, `test_pilot.py`) xanh
  không đổi — mọi thay đổi đều tương thích ngược qua tham số optional.

## 12. Rủi ro/lưu ý khác

- `post.scheduled_at` đổi ý nghĩa từ "giờ đăng chính xác" sang "giờ sớm nhất
  trong N giờ, chỉ để sort/hiển thị" khi có ≥ 2 kênh — cần soát mọi nơi đang
  đọc `post.scheduled_at` với kỳ vọng "đây là giờ đăng thật" (hiện chỉ dùng
  để hiển thị ở `/duyet`/`/vanhanh` và sort — không có logic nghiệp vụ nào
  dựa vào giá trị chính xác của nó ngoài `_next_slot` cũ, đã thay bằng
  `publish_target` ở §9.2).
- `post.status='PUBLISHED'` sau D1 có nghĩa "ít nhất 1 trong N kênh đã đăng
  thành công", không phải "tất cả N kênh đã đăng". Đây là rollup thô, đủ
  dùng cho funnel/scoring hiện tại (chỉ cần biết "đã có bài ra chưa"); chi
  tiết từng kênh nằm ở `publish_target.status`, hiển thị breakdown là D4.
