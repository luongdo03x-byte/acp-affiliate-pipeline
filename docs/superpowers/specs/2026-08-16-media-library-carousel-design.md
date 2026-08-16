# ACP 2.0 — Thiết kế thư viện ảnh + carousel theo platform (Sub-project D3)

**Ngày:** 2026-08-16
**Trạng thái:** Thiết kế đã được chốt trong hội thoại; chờ review bản spec trước khi lập implementation plan.
**Thuộc:** D3 trong 4 phần (D1 → D2 → D3 → D4) chia nhỏ từ Sub-project D —
phần cuối của `PTYC_ACP_FACEBOOK_INSTAGRAM_MULTI_ACCOUNT.md`. D3 xây trên
nền D1 (chọn nhiều account + N `publish_target`) + D2 (caption theo platform
+ override account), cả hai đã merge vào `feat/shopee-affiliate-import` —
bắt buộc trước khi làm D4 (Account Group/preset, polish `/vanhanh`).

## 1. Mục tiêu

Hiện tại một `post` chỉ có đúng 1 ảnh (`post.image_url_composited`, ghép tự
động từ `imaging.compose()`), dùng chung cho mọi `publish_target` bất kể
platform. Facebook (1-10 ảnh/bài) và Instagram (1-10 ảnh, ≥2 thành carousel)
đã hỗ trợ nhiều ảnh từ sub-project C nhưng chưa có đường nào trong pipeline
tạo ra hơn 1 ảnh. D3 thêm khả năng đó: operator upload/dán URL ảnh vào 1 thư
viện dùng lại được giữa nhiều bài, chọn thêm tối đa 9 ảnh cho 1 bài lúc tạo
ở `/sanpham`, và `publish_post()` gửi đúng số ảnh mỗi platform cho phép.

**Ranh giới cứng đã chốt, không phải điểm mở:** `core/imaging.py` có
nguyên tắc rõ ràng "không dùng model sinh ảnh vì làm biến dạng sản phẩm
thật, gây hoàn đơn mất uy tín kênh". D3 **không** gọi bất kỳ API sinh ảnh
AI nào. Với ảnh AI, ACP chỉ **gợi ý 1 đoạn prompt** (tên/giá sản phẩm) để
operator tự dán vào công cụ AI bên ngoài (ChatGPT, DALL-E...), tự tải kết
quả về máy, rồi tự upload vào thư viện như bất kỳ ảnh nào khác — con người
quyết định dùng ảnh nào, ACP không tự động chèn ảnh AI chưa qua mắt người
duyệt.

## 2. Phạm vi

### Trong phạm vi
- 2 bảng mới: `media_asset` (thư viện, độc lập với post), `post_media`
  (join N-N post↔asset, có thứ tự).
- Trang mới `/thuvien-anh`: upload (file hoặc dán URL), xem grid, xoá (chặn
  nếu đang được post nào dùng).
- `/sanpham` (cả 2 chế độ): checklist chọn thêm tối đa 9 ảnh từ thư viện +
  khối gợi ý prompt AI (thuần HTML, không JS, không gọi API AI nào).
- `_create_post_from_raw_product()` (và 2 hàm gọi nó) nhận
  `media_asset_ids`, ghi `post_media` lúc tạo bài.
- `publish_post()` cắt đúng số ảnh theo trần từng platform
  (`MEDIA_MAX_COUNT`) trước khi gọi publisher — không dựa publisher tự chặn.
- Test cho mọi hành vi trên.

### Ngoài phạm vi (dành cho D4 hoặc không bao giờ)
- Gọi API sinh ảnh AI tự động (đã chốt cứng ở §1, không phải điểm mở).
- Sửa/thêm ảnh sau khi tạo bài (chọn ảnh chỉ ở `/sanpham` lúc tạo, không có
  ở `/duyệt` — quyết định có chủ đích, khác caption/kênh của D1/D2).
- UI "gỡ ảnh khỏi 1 bài cụ thể" để xoá được asset đang dùng — xoá bị chặn
  hoàn toàn khi còn tham chiếu, không có luồng gỡ riêng trong D3.
- Account Group/preset (D4).

## 3. Data model

```sql
CREATE TABLE IF NOT EXISTS media_asset (
    id          TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    source      TEXT NOT NULL,   -- 'upload' | 'url'
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS post_media (
    post_id         TEXT NOT NULL REFERENCES post(id),
    media_asset_id  TEXT NOT NULL REFERENCES media_asset(id),
    position        INTEGER NOT NULL,
    PRIMARY KEY (post_id, media_asset_id)
);
CREATE INDEX IF NOT EXISTS idx_post_media_post ON post_media(post_id, position);
```

Bảng mới hoàn toàn, thêm vào `SCHEMA` (không phải `MIGRATIONS` — đúng
pattern đã dùng cho `post_channel_selection` ở D1, không phải pattern
`ALTER TABLE` dùng cho việc thêm cột vào bảng đã có). `post.
image_url_composited` **giữ nguyên không đụng** — luôn là ảnh đầu
tiên/bắt buộc, không lưu vào `post_media`. `media_asset` độc lập với
`post` — 1 ảnh dùng lại được ở nhiều `post_media` của nhiều bài khác nhau,
đúng ý "thư viện".

## 4. `/thuvien-anh` — trang quản lý thư viện

Route mới, thêm vào nav chính (`web/templates/base.html`, cạnh "Sản
phẩm"): `<a href="/thuvien-anh" class="nav-item ...">Thư viện ảnh</a>`.

- **`GET /thuvien-anh`** — grid ảnh (`media_asset` mới nhất trước), mỗi ô
  ảnh thumbnail + nút "Xoá" (form `POST` riêng).
- **`POST /thuvien-anh/upload`** — nhận **1 trong 2**, không cả hai:
  - File (`request.files["image"]`, `multipart/form-data`).
  - URL dán vào ô text (`request.form["image_url"]`).

  Cả 2 đường đều **tải/lưu file thật vào `MEDIA_DIR` rồi `storage.put()`**
  (giống hệt cách `imaging.compose()` đang làm) — không lưu thẳng URL bên
  ngoài vào `media_asset.url`, tránh carousel vỡ ảnh nếu link tạm (vd link
  ChatGPT sinh ra, hoặc file người dùng tải lên rồi xoá) hết hạn/mất trước
  khi Meta kịp tải lúc publish.

  Đường dán URL tái dùng nguyên `SafeHttpClient`
  (`adapters/safe_http.py`, đã có sẵn — đang phục vụ
  `ShopeeAffiliateSource.materialize_image()`), gọi
  `client.get(url, allowed_hosts=None, expected_content_prefix="image/")`
  (cùng tham số `allowed_hosts=None` đã dùng cho ảnh sản phẩm — không phải
  posture bảo mật mới), rồi xác thực bằng PIL `Image.verify()` giống hệt
  `materialize_image()` — cùng mức an toàn, không phát minh cơ chế mới, chỉ
  gọi lại logic đã có (tách thành hàm dùng chung hoặc import trực tiếp, xem
  Task ở plan).

  Đường upload file: lưu trực tiếp bằng `FileStorage.save(path)` của
  Flask/Werkzeug, sau đó **cùng bước xác thực PIL** như trên trước khi coi
  là hợp lệ (chặn file không phải ảnh thật dù đuôi file giả mạo).

  Cả 2 nhánh: `media_asset.source` ghi `'upload'` hoặc `'url'` để phân biệt
  nguồn (chỉ để hiển thị/audit, không ảnh hưởng logic).

- **`POST /thuvien-anh/<id>/xoa`** — xoá `media_asset`. **Chặn nếu còn
  `post_media` tham chiếu** (`SELECT COUNT(*) FROM post_media WHERE
  media_asset_id=?` > 0 → lỗi rõ "Ảnh đang được dùng ở N bài, không xoá
  được").

## 5. `/sanpham` — chọn ảnh thêm + gợi ý prompt AI

Checklist ảnh thư viện, thêm vào **cả 2 chế độ** (Tìm kiếm và Affiliate),
cùng vị trí/cùng pattern với checklist kênh của D1 (search mode: 1 form
chung bọc cả bảng kết quả; affiliate mode: trong form xác nhận):

```html
<div class="field field--full">
  <label>Ảnh thêm cho carousel (tối đa 9, ảnh ghép tự động luôn là ảnh đầu tiên)</label>
  <div class="niche-grid">
  {% for asset in media_assets %}
    <label class="niche-tile"><input type="checkbox" name="media_asset_ids" value="{{ asset.id }}">
      <img src="{{ asset.url }}" style="width:100%;border-radius:6px" loading="lazy"></label>
  {% endfor %}
  </div>
</div>
```
Server chặn nếu submit quá 9 `media_asset_ids` (lỗi rõ ràng, không âm thầm
cắt bớt).

Gợi ý prompt AI — khối `<details>` thuần HTML (cùng pattern override-theo-
account của D2, không JS), đặt ngay trên checklist ảnh, ở cả 2 chế độ (cả
hai đều có đủ tên/giá sản phẩm tại thời điểm này):

```html
<details><summary>💡 Gợi ý prompt tạo ảnh AI (dán vào ChatGPT/DALL-E ngoài, tự upload kết quả vào /thuvien-anh)</summary>
  <textarea readonly rows="4">Ảnh sản phẩm quảng cáo cho "{{ p.name }}", giá {{ p.current_price|vnd }}.
Phong cách: ảnh chụp sản phẩm studio chuyên nghiệp, nền sáng đơn sắc, ánh sáng tự nhiên, không có chữ/logo/watermark, tỷ lệ vuông 1:1. KHÔNG vẽ bao bì/nhãn hiệu cụ thể nào ngoài mô tả trên.</textarea>
</details>
```
`readonly` để operator bấm vào rồi tự chọn hết/copy tay — không cần JS
clipboard API. Câu prompt dặn "không vẽ bao bì/nhãn hiệu cụ thể" có chủ
đích — giảm rủi ro ảnh AI vẽ ra bao bì khác hàng thật, đúng tinh thần
nguyên tắc đã chốt ở `imaging.py` (§1), dù đây là ảnh AI sinh ngoài luồng
ACP không kiểm soát được kết quả cuối.

## 6. Ghi `post_media` lúc tạo bài

`_create_post_from_raw_product()` thêm tham số `media_asset_ids: list =
None`. Khi có giá trị: validate NGAY TRONG HÀM NÀY (không chỉ ở route web,
đúng khuôn `channel_codes`/`_resolve_channels_by_code` đã làm ở D1 — pipeline
là nguồn sự thật duy nhất, web chỉ là 1 trong nhiều caller có thể có):
(1) `len(media_asset_ids) > 9` → lỗi rõ "Tối đa 9 ảnh thêm, nhận N", không
tạo post; (2) asset nào không tồn tại trong `media_asset` → lỗi rõ, không
tạo post (tất-cả-hoặc-không-gì, giống hệt validate kênh). Qua cả 2 bước
trên mới ghi N dòng
`post_media` (`position` 1..N theo đúng thứ tự trong `media_asset_ids`)
ngay sau khi insert `post`, cùng transaction. `create_post_for_product()`
và `create_post_from_manual_affiliate_product()` nhận và truyền thẳng
xuống, y hệt cách `channel_codes` đã được thread qua ở D1.

## 7. `publish_post()` — cắt ảnh đúng trần từng platform

```python
# core/pipeline.py, gần publish_post(). Trùng đúng giới hạn đã hard-code
# trong publish() của từng Publisher (adapters/mock.py, adapters/live.py:
# Threads len(media)>1 báo lỗi, Facebook/Instagram 1-10 ảnh) -- 2 nguồn
# cùng giá trị, sửa 1 chỗ nhớ sửa chỗ kia, cùng rủi ro/cách xử lý đã chốt
# ở content.PLATFORM_MAX_LEN (D2).
MEDIA_MAX_COUNT = {"threads": 1, "facebook": 10, "instagram": 10}
```

Hàm mới `post_media_urls(conn, post_id) -> list[str]` — join `post_media`+
`media_asset`, `ORDER BY position`, trả list URL theo đúng thứ tự.

Trong `publish_post()`, chỗ dựng `media` hiện tại:
```python
media = [post["image_url_composited"]] if post["image_url_composited"] else []
```
đổi thành:
```python
media = [post["image_url_composited"]] if post["image_url_composited"] else []
media += post_media_urls(conn, post["id"])
media = media[:MEDIA_MAX_COUNT.get(channel["platform"], 1)]
```
Cắt **trước khi** gọi `publisher.publish()` — không dựa vào publisher tự
chặn. Lý do: publisher chặn bằng lỗi trần (`ValueError` ở Threads,
`ContentViolationError` ở FB/IG khi vượt 10 ảnh) — với payload media
không đổi qua các lần retry, lỗi này KHÔNG BAO GIỜ tự hết dù thử lại bao
nhiêu lần, nhưng `ValueError` (Threads) lại rơi vào nhánh `except
Exception` retryable chung của `core/jobs.py`, tốn hết `max_attempts` một
cách vô ích trước khi FAILED hẳn. Cắt trước khi gọi publisher tránh hoàn
toàn tình huống đó — Threads chỉ nhận đúng 1 ảnh (ảnh ghép), thừa bị cắt
âm thầm ở bước này, không phải lỗi cần báo operator.

## 8. Testing plan

- Schema: `media_asset`/`post_media` tồn tại đúng cột.
- `/thuvien-anh` upload file hợp lệ → tạo đúng 1 `media_asset`,
  `source='upload'`. Upload URL hợp lệ (mock `SafeHttpClient`, không gọi
  mạng thật) → tạo đúng 1 `media_asset`, `source='url'`, `url` là địa chỉ
  storage nội bộ chứ không phải URL ngoài gốc. Upload file/URL không phải
  ảnh thật (PIL verify thất bại) → lỗi rõ, không tạo asset.
- Xoá asset đang được `post_media` tham chiếu → bị chặn, đúng thông báo
  "N bài". Xoá asset không ai dùng → thành công.
- `post_media_urls()`: trả đúng thứ tự theo `position`.
- Tạo bài với `media_asset_ids` hợp lệ (2-3 ảnh) → đúng N dòng
  `post_media`, đúng `position`. Có 1 asset không tồn tại trong danh sách
  → lỗi rõ, không tạo post nào (tất-cả-hoặc-không-gì), không tạo dòng
  `post_media` nào. Truyền 10 `media_asset_ids` (vượt trần 9) trực tiếp vào
  `_create_post_from_raw_product()` (không qua route web) → lỗi rõ, không
  tạo post — xác nhận trần được chặn ở tầng pipeline, không chỉ ở route.
- `publish_post()`: post có ảnh ghép + 3 ảnh thêm (tổng 4) → target Threads
  chỉ publisher nhận đúng 1 ảnh (ảnh ghép, cắt bớt); target Facebook/
  Instagram nhận đủ cả 4, đúng thứ tự — verify bằng nội dung `media` thực
  sự publisher mock nhận được, không chỉ verify `publish_target.status`.
- `/sanpham` cả 2 chế độ: chọn quá 9 `media_asset_ids` → lỗi rõ ràng,
  không tạo bài, không tạo `post_media` nào.
- Tương thích ngược: không truyền `media_asset_ids` → hành vi y hệt trước
  D3 (post chỉ có ảnh ghép, dùng lại test D1/D2 hiện có, không sửa).
