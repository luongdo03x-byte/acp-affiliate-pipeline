# ACP 2.0 — Thiết kế AccountGroup/preset chọn nhanh (Sub-project D4, phần A)

**Ngày:** 2026-08-16
**Trạng thái:** Thiết kế đã được chốt trong hội thoại; chờ review bản spec trước khi lập implementation plan.
**Thuộc:** D4 trong 4 phần (D1 → D2 → D3 → D4) chia nhỏ từ Sub-project D —
phần cuối của `PTYC_ACP_FACEBOOK_INSTAGRAM_MULTI_ACCOUNT.md`. D4 tự chia
thêm 2 phần độc lập: **phần A** (spec này) — AccountGroup/preset chọn
nhanh; **phần B** — polish `/vanhanh` hiển thị breakdown theo
`publish_target` thay vì rollup `post.status` thô. Phần A xây trên nền D1
(chọn nhiều account + N `publish_target`), D2 (caption theo platform +
override account), D3 (thư viện ảnh + carousel) — cả ba đã merge vào
`feat/shopee-affiliate-import`. Phần A không phụ thuộc phần B, làm trước
theo lựa chọn của người dùng.

## 1. Mục tiêu

Từ D1, mỗi lần tạo bài ở `/sanpham` operator phải tích từng channel một
trong 1 checklist phẳng (`niche-grid` liệt kê toàn bộ channel ACTIVE). Khi
số channel tăng lên (nhiều Facebook Page + Instagram + Threads), việc này
lặp lại tốn thời gian nếu operator hay đăng cùng 1 bộ channel cố định (vd
"tất cả FB Page chính", "bộ 3 tài khoản test"). D4 phần A thêm
**AccountGroup**: 1 nhóm channel đặt tên trước, hiển thị thành nút bấm ở
`/sanpham`, bấm 1 cái tick nhanh cả nhóm thay vì tích từng ô.

**Ranh giới cứng đã chốt:** AccountGroup thuần là **tiện ích UI**, không
phải khái niệm nghiệp vụ mới. Không lưu vết "bài này tạo từ nhóm nào", không
gắn quyền hạn/role, không đổi logic `core/pipeline.py`'s luồng tạo bài hiện
có. Chọn nhóm chỉ tick sẵn checkbox `channel_codes` đã có từ D1 — sau khi
submit, `post`/`publish_target` không biết gì về nhóm đã dùng, y hệt như
operator tự tay tích từng ô.

## 2. Phạm vi

### Trong phạm vi
- 2 bảng mới: `account_group` (nhóm, độc lập với post), `account_group_channel`
  (join N-N nhóm↔channel).
- Quản lý nhóm (tạo/sửa/xoá) ngay tại `/kenh` — không thêm trang mới.
- `core/pipeline.py`: 4 hàm CRUD mới — `create_account_group()`,
  `update_account_group_channels()`, `delete_account_group()`,
  `list_account_groups()` — hoàn toàn tách biệt khỏi luồng tạo bài.
- `/sanpham` (cả 2 chế độ): hàng nút "Chọn nhanh theo nhóm", bấm 1 nhóm
  → JS tick sẵn checkbox `channel_codes` khớp nhóm đó (cộng dồn, không
  thay thế lựa chọn đang có).
- Test cho mọi hành vi trên.

### Ngoài phạm vi (dành cho D4 phần B hoặc sau)
- Polish `/vanhanh` hiển thị breakdown theo `publish_target` (D4 phần B —
  brainstorm/spec riêng sau khi phần A merge).
- Lưu vết nhóm đã dùng vào `post`/`publish_target` — quyết định có chủ
  đích ở §1, không phải giới hạn kỹ thuật.
- Nút "Chọn nhanh theo nhóm" ở `/duyệt` — checklist `/duyệt` chỉ là tập con
  cố định từ lúc tạo bài (chỉ được bỏ tích, không thêm mới), khác hẳn
  `/sanpham` nơi checklist là toàn bộ channel ACTIVE để chọn từ đầu. Thêm
  nút nhóm ở `/duyệt` không có giá trị tương xứng độ phức tạp nên bỏ qua.
- Phân quyền/role theo nhóm — nhóm không gắn với ai được thao tác channel
  nào.
- Giới hạn nhóm theo platform — 1 nhóm được trộn Threads/Facebook/
  Instagram thoải mái.
- Nút "bỏ tích cả nhóm" (ngược lại với tick) — cơ chế chỉ cộng dồn.
- Chặn trùng tên nhóm — `name` không unique, chỉ `code` tự sinh unique.

## 3. Data model

```sql
CREATE TABLE IF NOT EXISTS account_group (
    id          TEXT PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_group_channel (
    group_id    TEXT NOT NULL REFERENCES account_group(id),
    channel_id  TEXT NOT NULL REFERENCES channel(id),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (group_id, channel_id)
);
```

Bảng mới hoàn toàn, thêm vào `SCHEMA` (không phải `MIGRATIONS` — đúng
pattern đã dùng cho `post_channel_selection`/`media_asset`/`post_media`).
Không bảng nào khác tham chiếu `account_group.id` — xoá 1 nhóm chỉ cần xoá
`account_group` + mọi dòng `account_group_channel` liên quan, không cần
chặn kiểu `media_asset` phải chặn khi còn `post_media` tham chiếu.

`code` tự sinh từ `name` lúc tạo (operator không tự gõ code tay). Đây là
đường auto-slug **đầu tiên** trong codebase (campaign/channel code hiện
đều nhập tay/seed sẵn, không có hàm slug dùng chung để tái dùng) — dùng
lại `unicodedata.normalize` đã có sẵn ở `core/content.py`/`core/niche.py`
để bỏ dấu tiếng Việt, rồi lowercase + thay ký tự không phải chữ/số bằng
`-`. Vì `name` **không unique** (đã chốt ở §2), 2 nhóm trùng tên sẽ ra
slug gốc giống hệt nhau — tránh vỡ `code UNIQUE NOT NULL` bằng cách nối
thêm 6 ký tự đầu của chính `id` (`ulid()`) vào cuối slug, đảm bảo unique
tuyệt đối không cần query kiểm tra trùng trước khi insert: `code =
f"{slug}-{id[:6]}"`.

## 4. Quản lý nhóm ở `/kenh`

Thêm 1 khu vực mới trong `web/templates/channels.html`, đặt **sau** các
khối theo-platform hiện có (`{% for platform, chs in by_platform.items()
%}...{% endfor %}`), không xen giữa để tránh vỡ layout theo platform đang
có.

**Tạo nhóm:** 1 form nhỏ — nhập `name`, tick checklist channel dùng lại
đúng CSS `niche-grid`/`niche-tile` đã có, liệt kê **toàn bộ** channel đang
ACTIVE (không lọc theo platform, vì nhóm được phép trộn platform).

Route `channels()` hiện tại **chưa** truyền `platform_labels` vào
`channels.html` (chỉ có `by_platform` đã gộp sẵn theo platform, dùng cho
tiêu đề `{{ platform|upper }}` mỗi khối) — cần thêm `platform_labels=
PLATFORM_LABELS` (hằng số đã có ở `web/server.py:34`, đang dùng cho
`products.html`/`review.html`) vào lời gọi `render_template("channels.html",
...)`, và thêm 1 list phẳng `all_active_channels` (lọc từ `rows` route đã
tính sẵn, `status=='ACTIVE'` — đúng điều kiện lọc `_product_common_context()`
đang dùng cho checklist `/sanpham`, chỉ khác là **không** lọc thêm
`enabled=1` vì nhóm là preset lâu dài, channel tạm tắt vẫn nên giữ trong
nhóm để bật lại là dùng được ngay, không cần tạo lại nhóm).

```html
<form method="post" action="/kenh/nhom/tao">
  <input type="hidden" name="_csrf" value="{{ csrf_token }}">
  <div class="field"><label for="group-name">Tên nhóm</label>
    <input id="group-name" name="name" required placeholder="VD: FB Page chính"></div>
  <div class="niche-grid">
  {% for ch in all_active_channels %}
    <label class="niche-tile"><input type="checkbox" name="channel_ids" value="{{ ch.id }}">
      <span>[{{ platform_labels[ch.platform] }}] {{ ch.handle }}<small>{{ ch.code }}</small></span></label>
  {% endfor %}
  </div>
  <button class="btn btn--primary" type="submit">Tạo nhóm</button>
</form>
```

**Danh sách nhóm:** mỗi nhóm 1 card nhỏ — tên, số channel thành viên, tag
liệt kê handle từng channel (giống `channel_meta` đang hiển thị ở card
channel). Nút **Sửa** mở lại checklist tick sẵn theo thành viên hiện có,
submit **ghi đè toàn bộ** thành viên (đơn giản hơn thêm/bớt từng cái). Nút
**Xoá** xoá nhóm ngay, không cần xác nhận thêm (không có gì tham chiếu tới
nó nên không có rủi ro mất dữ liệu liên đới).

**Route mới trong `web/server.py`** (đặt cạnh các route `/kenh/*` hiện có):
- `POST /kenh/nhom/tao` — tạo nhóm mới.
- `POST /kenh/nhom/<group_id>/sua` — ghi đè danh sách thành viên.
- `POST /kenh/nhom/<group_id>/xoa` — xoá nhóm.

## 5. `/sanpham` — chọn nhanh theo nhóm

**Đây sẽ là `<script>` đầu tiên trong toàn bộ codebase** — đã kiểm tra,
hiện chưa có JS ở đâu cả trong `web/templates/*.html`, mọi tương tác đều
thuần server round-trip. Giữ tối thiểu: 1 khối `<script>` inline vanilla
JS trong `products.html`, không build step/npm/framework, khớp tinh thần
"không phát minh cơ chế mới thừa" xuyên suốt dự án.

`_product_common_context()` (đã trả `pending, channels, media_assets` từ
D3) nhận thêm phần tử thứ 4: `account_groups` — mỗi nhóm
`{id, name, channel_codes: [...]}` (danh sách **code**, không phải object
đầy đủ — đủ để JS so khớp `value` của checkbox `channel_codes`).

UI đặt ngay phía trên checklist "Kênh đăng bài" đã có, form-level ở **cả 2
chế độ** (channel checklist vốn đã form-level ở cả affiliate lẫn search
theo thiết kế D1):

```html
<div class="field field--full">
  <label>Chọn nhanh theo nhóm</label>
  <div class="quick-group-row">
  {% for g in account_groups %}
    <button type="button" class="btn btn--small" onclick="acpTickGroup(this, {{ g.channel_codes|tojson }})">{{ g.name }}</button>
  {% endfor %}
  </div>
</div>
```

Cơ chế **cộng dồn, không thay thế**: bấm 1 nhóm → tick thêm các checkbox
`channel_codes` khớp `value`, **không bỏ tick** cái đang tick sẵn —
operator bấm được nhiều nhóm liên tiếp để gộp, vẫn tự tay bỏ/thêm tick sau
đó trước khi submit:

```javascript
function acpTickGroup(btn, codes) {
  const form = btn.closest('form') || document;
  codes.forEach(code => {
    const box = form.querySelector('input[name="channel_codes"][value="' + code + '"]');
    if (box) box.checked = true;
  });
}
```

Ở chế độ Tìm kiếm, `btn.closest('form')` tìm đúng form chứa checklist
form-level (không phải form của từng dòng sản phẩm) vì nút nhóm nằm cùng
cấp với checklist kênh, ngoài vòng lặp `{% for p in items %}`.

Nếu nhóm chứa channel không còn `status='ACTIVE'` hoặc đã bị `enabled=0`
(checklist `/sanpham` chỉ liệt kê channel thoả cả 2 điều kiện, theo
`_product_common_context()` hiện có), `querySelector` không tìm thấy
checkbox tương ứng → im lặng bỏ qua channel đó, không lỗi JS, không cần xử
lý gì thêm ở CRUD nhóm (nhóm vẫn giữ nguyên thành viên đã lưu, chỉ là 1 vài
cái không tick được nữa cho tới khi channel đó bật lại) — nhất quán với
việc checklist tạo nhóm ở `/kenh` (§4) cố ý **không** lọc `enabled=1` để
nhóm là preset lâu dài, không mất thành viên khi channel bị tắt tạm thời.

## 6. Testing plan

Áp dụng đúng 2 kỷ luật đã đúc kết xuyên suốt D1–D3: **route + template + 1
test end-to-end thật trong cùng 1 task, GET trước khi POST**; và rút kinh
nghiệm từ lỗi trùng `media_asset_ids` ở final review D3 —
`update_account_group_channels()` phải bỏ trùng `channel_ids`
(order-preserving) **trước khi ghi**, tránh lặp lại đúng lớp bug đó với
`account_group_channel`'s PK `(group_id, channel_id)`.

- Schema: `account_group`/`account_group_channel` tồn tại đúng cột.
- `create_account_group()`: đúng tên, đúng N dòng `account_group_channel`.
  Có 1 `channel_id` không tồn tại → lỗi rõ, không tạo nhóm. `channel_ids`
  có phần tử trùng → tự bỏ trùng, không vỡ INSERT.
- `update_account_group_channels()`: ghi đè toàn bộ thành viên (nhóm có
  [A,B] → sửa thành [B,C] → còn đúng [B,C], không phải [A,B,C]). Cũng bỏ
  trùng như trên.
- `delete_account_group()`: xoá cả `account_group` lẫn mọi dòng
  `account_group_channel` liên quan.
- `list_account_groups()`: trả đúng nhóm + đúng `channel_codes` theo đúng
  nhóm.
- `/kenh`: GET render đúng field `name="name"` (form tạo nhóm) và
  checklist channel — TRƯỚC khi POST. POST `/kenh/nhom/tao` → nhóm mới
  xuất hiện trong danh sách. POST `/kenh/nhom/<id>/sua` → thành viên cập
  nhật đúng. POST `/kenh/nhom/<id>/xoa` → nhóm biến mất.
- `/sanpham` (cả 2 chế độ): GET render đúng tên nhóm + đúng `channel_codes`
  nhúng trong `onclick` (proof template/data đúng — **không** test được
  hành vi JS thật vì Flask test client không chạy JS, giống hệt giới hạn
  đã chấp nhận với `<textarea readonly>` ở D3).
- Tương thích ngược: toàn bộ test D1–D3 hiện có (`test_pipeline.py`
  297/0, `test_pilot.py` 314/0) phải giữ nguyên xanh — D4 phần A không đụng
  `core/pipeline.py`'s hàm tạo bài, không đụng schema `post`/
  `publish_target`.
