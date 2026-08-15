# ACP 2.0 — Thiết kế caption theo platform + override theo account (Sub-project D2)

**Ngày:** 2026-08-15
**Trạng thái:** Thiết kế đã được chốt trong hội thoại; chờ review bản spec trước khi lập implementation plan.
**Thuộc:** D2 trong 4 phần (D1 → D2 → D3 → D4) chia nhỏ từ Sub-project D —
phần cuối của `PTYC_ACP_FACEBOOK_INSTAGRAM_MULTI_ACCOUNT.md`. D2 xây trên
nền D1 (chọn nhiều account + sinh N `publish_target`, đã merge vào
`feat/shopee-affiliate-import`) — bắt buộc trước khi làm D3 (media library
+ carousel), D4 (Account Group/preset, polish `/vanhanh`).

## 1. Mục tiêu

D1 cho một post nhắm tới N account, nhưng cả N `publish_target` vẫn dùng
chung đúng 1 `post.caption_final` — không phân biệt platform, không có cách
nào viết khác nội dung cho Facebook so với Threads, càng không có cách viết
riêng cho 1 account cụ thể. D2 thêm 2 tầng ghi đè, theo đúng thứ tự ưu
tiên: **override theo account cụ thể > caption riêng theo platform >
caption gốc**. Không tự động sinh nội dung khác nhau theo platform —
`content.generate()` giữ nguyên, D2 chỉ cho operator **sửa tay** ở
`/duyệt`.

Kết thúc D2: operator có thể để mọi account dùng chung 1 caption (mặc định,
không đổi hành vi D1), hoặc viết riêng cho Facebook/Instagram, hoặc ghi đè
riêng 1 account cụ thể — validate đúng giới hạn ký tự và luật nội dung của
đúng platform sẽ nhận caption đó.

## 2. Phạm vi

### Trong phạm vi
- 3 cột mới: `post.caption_facebook`, `post.caption_instagram`,
  `publish_target.caption_override` (đều nullable, additive).
- `content.validate()` nhận thêm `max_len: int = MAX_LEN` (mặc định giữ
  nguyên 500); hằng số `content.PLATFORM_MAX_LEN` cho 3 platform.
- Hàm mới `_resolve_caption(post, target, channel) -> str` trong
  `core/pipeline.py`, dùng ở cả `approve_post()` (validate) lẫn
  `publish_post()` (đăng bài).
- `approve_post()` nhận thêm `caption_facebook`, `caption_instagram`,
  `caption_overrides: dict`; validate theo nhóm (gom kênh theo đúng chuỗi
  caption sẽ dùng, union niches trong từng nhóm).
- `/duyệt`: thêm field caption theo platform (chỉ hiện khi platform đó có
  mặt trong lựa chọn của bài), thêm `<details>` ẩn cho override theo từng
  account trong checklist.
- Test cho mọi hành vi trên.

### Ngoài phạm vi (dành cho D3/D4 hoặc không bao giờ)
- Tự động sinh nội dung khác nhau theo platform (`content.generate()`
  không đổi).
- Đọc lại `publish_target.caption_override` cũ để tiền điền lại khi render
  `/duyệt` — ô override-theo-account luôn rỗng lúc render, đơn giản hoá có
  chủ đích (xem §5).
- Media/carousel theo platform (D3).
- Account Group/preset (D4).
- Sửa caption ở `/sanpham` lúc tạo bài — giữ nguyên như D1, caption chỉ
  sửa được ở `/duyệt`.

## 3. Data model

3 migration mới, theo đúng pattern `MIGRATIONS` hiện có trong `core/db.py`
(ALTER TABLE, idempotent, chạy được cho cả cài mới lẫn nâng cấp CSDL live):

```python
("post", "caption_facebook", "ALTER TABLE post ADD COLUMN caption_facebook TEXT"),
("post", "caption_instagram", "ALTER TABLE post ADD COLUMN caption_instagram TEXT"),
("publish_target", "caption_override", "ALTER TABLE publish_target ADD COLUMN caption_override TEXT"),
```

Không có `caption_threads` — `post.caption_final` (đã có sẵn từ trước D1)
tiếp tục đóng vai trò vừa là "bản gốc" vừa là caption hiệu lực cho Threads.
`NULL` ở cả 3 cột mới nghĩa là "dùng tầng phía trên" trong thứ tự ưu tiên
— không đổi ý nghĩa bất kỳ cột nào đã có.

## 4. Thứ tự ưu tiên & resolve caption

Hàm mới trong `core/pipeline.py`, đặt cạnh `_resolve_channels_by_id`:

```python
def _resolve_caption(post, target, channel) -> str:
    """Thứ tự ưu tiên: override riêng account > caption riêng theo platform
    > caption gốc. Thuần tính toán, không query. `post`/`target` chỉ cần hỗ
    trợ post["col"]/target["col"] -- dict thường (lúc validate, chưa có
    publish_target thật) hay sqlite3.Row (lúc publish, target là row CSDL
    thật) đều dùng được, không cần phân biệt loại."""
    if target["caption_override"]:
        return target["caption_override"]
    platform_col = {"facebook": "caption_facebook", "instagram": "caption_instagram"}.get(channel["platform"])
    if platform_col and post[platform_col]:
        return post[platform_col]
    return post["caption_final"]
```

`channel["platform"]` không phải `"facebook"`/`"instagram"` (tức
`"threads"` hoặc platform lạ trong tương lai) → `platform_col` là `None`,
rơi thẳng xuống `post["caption_final"]` — đúng ý "Threads không có cột
riêng". Lúc gọi từ vòng lặp validate (§5), `target` truyền vào là dict
literal `{"caption_override": overrides.get(ch["id"])}`; lúc gọi từ
`publish_post()` (§7), `target` là row CSDL thật — cả hai đều thoả
`target["caption_override"]`, không cần nhánh riêng.

## 5. Validate theo nhóm

D1 validate 1 lần duy nhất bằng `_union_niches()` qua *toàn bộ* kênh được
chọn, vì lúc đó mọi kênh chắc chắn dùng chung 1 caption. D2 phá vỡ giả định
đó — cách đúng: **gom các kênh theo đúng chuỗi caption chúng sẽ dùng**, rồi
validate mỗi nhóm đúng 1 lần bằng union niches *trong nhóm đó*:

Validate phải dùng đúng giá trị **sắp được lưu** trong lần duyệt này, không
phải giá trị cũ đang có trong CSDL (operator có thể đang set/sửa
`caption_facebook`/`caption_instagram` ngay trong request này) — dựng 1
dict `post_effective` gộp row CSDL hiện tại với 2 tham số mới trước khi
đưa vào vòng lặp, trước khi UPDATE CSDL thật:

```python
post_effective = dict(post)
if caption_facebook is not None:
    post_effective["caption_facebook"] = caption_facebook.strip() or None
if caption_instagram is not None:
    post_effective["caption_instagram"] = caption_instagram.strip() or None
caption_text = caption_override or post["caption_final"]
post_effective["caption_final"] = caption_text

groups = {}  # caption_text -> [channel dict, ...]
for ch in channels:
    text = _resolve_caption(post_effective, {"caption_override": (caption_overrides or {}).get(ch["id"])}, ch)
    groups.setdefault(text, []).append(ch)

for text, chs in groups.items():
    ids = [c["id"] for c in chs]
    platforms_in_group = {c["platform"] for c in chs}
    max_len = min(content.PLATFORM_MAX_LEN.get(p, content.MAX_LEN) for p in platforms_in_group)
    problems = content.validate(text, niches=_union_niches(conn, ids), max_len=max_len)
    if problems:
        return {"ok": False, "error": "; ".join(problems)}
```

Nhóm gồm nhiều platform khác nhau (hiếm — chỉ xảy ra khi 2 kênh khác
platform tình cờ cùng rơi về đúng `caption_final`, ví dụ Threads + 1
Facebook account chưa từng có caption riêng) thì lấy `min()` giới hạn ký
tự trong nhóm — an toàn, không cho lọt caption vượt giới hạn của kênh nào.
Khi mọi kênh vẫn dùng chung 1 caption (hành vi D1 cũ, chưa ai dùng D2) thì
công thức này tự nhiên rút gọn về đúng y hệt cách D1 làm — không phải hai
code path riêng biệt cho "có D2" và "không có D2".

`content.validate()` đổi chữ ký:
```python
def validate(caption: str, disclosure: str = DISCLOSURE_DEFAULT, niches=None,
             max_len: int = MAX_LEN) -> list:
```
Thông báo lỗi độ dài đổi từ hard-code `"Threads chỉ cho {MAX_LEN}"` sang
dùng đúng `max_len` được truyền vào (không còn giả định luôn là Threads).

`content.PLATFORM_MAX_LEN` — hằng số mới:
```python
PLATFORM_MAX_LEN = {"threads": 500, "facebook": 63206, "instagram": 2200}
```
Trùng đúng giá trị `max_caption_length` đã có sẵn trên từng `Publisher`
(`adapters/base.py`, `adapters/mock.py`, `adapters/live.py`) — **cố ý không
lấy từ `ctx["publishers"][platform].max_caption_length`** vì `approve_post()`
hiện không nhận `ctx`, thêm vào sẽ lan chữ ký ra `web/server.py`/`run.py`,
không cần thiết cho D2. Đánh đổi: 2 nguồn số cùng giá trị, có nguy cơ lệch
nhau nếu sau này sửa 1 chỗ quên chỗ kia — ghi comment cross-reference ở cả
`content.py` và mỗi `Publisher` để giảm rủi ro.

## 6. `approve_post()` mở rộng chữ ký

```python
def approve_post(conn, post_id, actor="operator", caption_override=None,
                  channel_ids=None, caption_facebook=None, caption_instagram=None,
                  caption_overrides: dict = None) -> dict:
```

- `caption_facebook`/`caption_instagram`: `None` → giữ nguyên giá trị cũ
  trong `post` (không UPDATE cột đó); chuỗi rỗng `""` → UPDATE về `NULL`
  (xoá override, quay lại dùng caption gốc); chuỗi có nội dung → UPDATE
  bằng chuỗi đó. Quy ước này khớp với kịch bản "bài bị bounce về
  `PENDING_REVIEW` (vd content violation ở 1 kênh khác — xem D1 fix
  Important) rồi duyệt lại": form `/duyệt` luôn gửi lại đúng giá trị đang
  hiện trên trang, nên hành vi tự nhiên đúng mà không cần logic đặc biệt.
- `caption_overrides`: dict `channel_id -> text`, chỉ áp dụng cho các
  `channel_id` nằm trong `channel_ids` đang được duyệt lần này. Ghi thẳng
  vào `publish_target.caption_override` lúc insert target đó trong vòng
  lặp per-channel đã có từ D1 — không lưu vào đâu khác, không cần "nhớ"
  giữa các lần duyệt (mỗi lần duyệt tạo `publish_target` mới).
- Không truyền 3 tham số mới (mặc định `None`) → hành vi y hệt D1, tương
  thích ngược 100% cho mọi lời gọi cũ (`run.py`, ~20 test hiện có).

## 7. `publish_post()` dùng caption đã resolve

Chỗ gọi publisher hiện tại:
```python
result = publisher.publish(channel, post["caption_final"], media=media)
```
đổi thành:
```python
caption = _resolve_caption(post, target, channel)
result = publisher.publish(channel, caption, media=media)
```
`target` ở đây là row CSDL thật (có `caption_override` từ migration §3).

## 8. UI `/duyệt`

**Caption theo platform** — chỉ hiện field cho platform thực sự có mặt
trong `p.selected_channels` của bài đó:
```html
{% set platforms = p.selected_channels | map(attribute='platform') | unique | list %}
{% if 'facebook' in platforms %}
<div class="field"><label>Caption riêng cho Facebook (để trống = dùng caption gốc)</label>
  <textarea name="caption_facebook">{{ p.caption_facebook or '' }}</textarea></div>
{% endif %}
{% if 'instagram' in platforms %}
<div class="field"><label>Caption riêng cho Instagram (để trống = dùng caption gốc)</label>
  <textarea name="caption_instagram">{{ p.caption_instagram or '' }}</textarea></div>
{% endif %}
```
Giá trị luôn đọc thẳng từ `post.caption_facebook`/`caption_instagram` —
không cần query thêm, `review()` đã fetch `p.*`.

**Caption riêng theo account** — mỗi dòng checklist thêm 1 khối
`<details>` thuần HTML (không JS), ẩn mặc định:
```html
{% for sel in p.selected_channels %}
<div class="channel-caption-row">
  <label class="niche-tile"><input type="checkbox" name="channel_ids" value="{{ sel.id }}" checked>
    <span>[{{ platform_labels[sel.platform] }}] {{ sel.handle }}<small>{{ sel.code }}</small></span></label>
  <details><summary>✎ caption riêng cho account này</summary>
    <textarea name="caption_override_{{ sel.id }}" placeholder="Để trống = dùng caption theo platform/gốc"></textarea>
  </details>
</div>
{% endfor %}
```
Ô này **luôn rỗng lúc render** — không đọc lại `publish_target.caption_override`
cũ để tiền điền. Đơn giản hoá có chủ đích: đây là tầng hiếm dùng (chỉ cần
khi ≥2 account cùng platform muốn nói khác nhau), không đáng công dựng
lookup ngược lại target cũ của lần duyệt trước.

**Route `review_action()`** (nhánh approve): đọc `caption_facebook`/
`caption_instagram` bằng `request.form.get(...)` (không kèm default —
`None` nếu field không có trong form, khác với chuỗi rỗng nếu có mặt
nhưng để trống), và dựng `caption_overrides` bằng cách duyệt qua
`channel_ids` đọc `caption_override_{channel_id}`:
```python
caption_overrides = {}
for cid in channel_ids:
    val = request.form.get(f"caption_override_{cid}", "").strip()
    if val:
        caption_overrides[cid] = val
```

## 9. Testing plan
- `content.validate(caption, max_len=63206)` PASS cho caption 3000 ký tự;
  mặc định (`max_len=500`) FAIL cùng caption đó.
- `_resolve_caption`: đúng thứ tự override > platform > gốc, cả 3 trạng
  thái, cả trường hợp `channel["platform"]` không có cột riêng (threads).
- `approve_post`: lưu đúng `caption_facebook`/`caption_instagram` vào
  `post`; `""` xoá về `NULL`; `None` giữ nguyên giá trị cũ;
  `caption_overrides` ghi đúng vào từng `publish_target.caption_override`
  tương ứng, không rò rỉ sang target khác.
- Validate theo nhóm: 2 kênh cùng caption (gộp union niches, kết quả giống
  hệt D1) vs 2 kênh khác caption (validate riêng, không lẫn niches của
  nhau) vs 1 caption dùng chung bởi 2 platform khác nhau (lấy `min()`
  giới hạn ký tự).
- `publish_post()`: target có override dùng đúng override; target không
  override nhưng platform có caption riêng dùng đúng caption đó; còn lại
  rơi về `caption_final` — cả 3 nhánh, verify bằng nội dung thực sự
  publisher nhận được (không chỉ verify `publish_target.status`).
- Tương thích ngược: `approve_post()` không truyền 3 tham số mới → hành vi
  và kết quả y hệt trước D2 (dùng lại test D1 hiện có, không sửa).
- `/duyệt`: render đúng field theo platform có mặt trong lựa chọn của
  từng bài cụ thể, không hiện field cho platform bài đó không nhắm tới;
  submit end-to-end cả 2 tầng, xác nhận `publish_target.caption_override`
  và `post.caption_facebook`/`caption_instagram` sau khi duyệt đúng như
  form đã gửi.
