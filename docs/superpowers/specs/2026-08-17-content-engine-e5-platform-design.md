# ACP 2.0 — Thiết kế Platform Adaptation (Content Engine v2, phần E5)

**Ngày:** 2026-08-17
**Trạng thái:** Thiết kế đã được chốt trong hội thoại; chờ review bản spec trước khi lập implementation plan.
**Thuộc:** E5 trong 6 phần (E1 → E2 → E3 → E4 → E5 → E6) chia nhỏ từ
`PTYC_ACP_CONTENT_ENGINE_V2.md`. E5 xây trên nền E3 (`core/content_variant.py`
— `ContentVariant`) — đã merge, qua final review, **không sửa lại**. E6
(tích hợp `/duyet`) dùng `adapt_for_platforms()` của E5 để tạo caption
cuối cùng cho từng platform trước khi ra `PENDING_REVIEW`.

## 1. Mục tiêu

PTYC §23-27: ghép `ContentVariant` (Content Core — angle/hook/
main_message/body/cta, chưa thành chuỗi) thành **caption hoàn chỉnh riêng
cho từng platform** (Threads/Facebook/Instagram), không generate lại nội
dung độc lập 3 lần — chỉ khác cách ghép/định dạng.

**Ranh giới cứng đã chốt:** giống E1-E4 — không đụng `core/pipeline.py`,
không đụng `core/content.py`'s `generate()`/`validate()` (chỉ import 2
hằng số read-only: `PLATFORM_MAX_LEN`, `DISCLOSURE_DEFAULT` — không sửa
file, không gọi hàm nào của nó). Không sửa `core/content_variant.py` (E3).
Dormant hoàn toàn, chưa nối vào luồng tạo bài thật (việc của E6).

**Phát hiện quan trọng lúc thiết kế:** `ContentVariant`/`ProductFacts`
(E1-E4) không mang theo affiliate link — `cta` chỉ là câu văn ("Giá hiện
tại mình để ở link.") không có URL thật. E5 là bước ghép chuỗi cuối cùng
nên là nơi hợp lý nhất để nhận `affiliate_link` làm tham số riêng, nối
vào cuối caption — đúng pattern `content.generate(product, template_code,
affiliate_link, ...)` đã có, không đụng gì tới `CTA_POOL` của E3.

**E5 không cần LLM**: nội dung (hook/main_message/body/cta) đã được E2/E3
sinh xong với chất lượng đã kiểm (rule check, fact safety). Khác biệt
giữa 3 platform theo §24-26 chủ yếu là **cách ghép** (xuống dòng theo câu
vs gộp đoạn văn) và **giới hạn ký tự**, không phải viết lại nội dung —
nên toàn bộ E5 là hàm thuần định dạng chuỗi, không có pluggable LLM nào.

## 2. Phạm vi

### Trong phạm vi
- Module mới `core/content_platform.py`: `adapt_for_threads()`,
  `adapt_for_facebook()`, `adapt_for_instagram()`, `adapt_for_platform()`
  (dispatcher), `adapt_for_platforms()` (batch, hỗ trợ §27 "dùng chung").
- Test cho toàn bộ hàm trên.

### Ngoài phạm vi (dành cho E6/P1)
- Nối `adapt_for_platforms()` vào `core/pipeline.py`/UI `/duyet` — việc
  của E6.
- Tự thêm hashtag (Threads §24 cấm "tự thêm hashtag hàng loạt" — E5 không
  có logic hashtag nào cả, đơn giản nhất là không cài gì, tự động thoả
  điều cấm).
- LLM rewrite riêng cho từng platform — nội dung đã cố định từ Content
  Core (E2/E3), không viết lại; nếu sau này cần "viết khác hẳn" cho từng
  platform (không chỉ đổi cách ghép) thì đó là mở rộng P1, không phải E5.
- Giới hạn Instagram "không nhồi toàn bộ thông số sản phẩm" (§26): Content
  Core đã giới hạn `body` tối đa 2 supporting point ngắn (không phải danh
  sách specs) từ E3, nên ràng buộc này đã được thoả từ tầng sinh nội
  dung — E5 không cắt bớt `body` thêm nữa (cắt tuỳ tiện sẽ mất nội dung có
  ý nghĩa mà không có cơ sở rõ ràng để chọn giữ ý nào).

## 3. `core/content_platform.py`

```python
from . import content  # chỉ đọc PLATFORM_MAX_LEN, DISCLOSURE_DEFAULT -- không gọi hàm, không sửa file
```

### 3.1. `_fit_to_length(body, affiliate_link, disclosure, max_len) -> str`

Hàm nội bộ dùng chung cho cả 3 adapter — nối `affiliate_link` + `disclosure`
vào cuối, cắt `body` nếu vượt `max_len`:

```python
def _fit_to_length(body: str, affiliate_link: str, disclosure: str, max_len: int) -> str:
    tail = f"\n\n{affiliate_link}\n\n{disclosure}"
    budget = max_len - len(tail)
    body = body.strip()
    if len(body) <= budget:
        return body + tail
    head = body[:max(0, budget)].rsplit(" ", 1)[0].rstrip(" ,.—-") + "…"
    return head + tail
```

**Không import `content._fit()`** (hàm private, cùng tinh thần "không
import private function xuyên module" đã giữ ở E2-E4) — viết bản riêng,
đơn giản hơn bản gốc vì E5 không cần tìm dòng `http` trong `body` (affiliate
link đã là tham số riêng, không lẫn trong `body`).

### 3.2. `adapt_for_threads(variant, affiliate_link, disclosure=None) -> str`

PTYC §24: hook cực nhanh, conversational, dòng ngắn, không paragraph dài,
CTA nhẹ. Ghép từng phần tử (`hook`, dòng trống, `main_message`, mỗi
`body` item 1 dòng riêng, `cta`) nối bằng `\n`, không gộp thành đoạn văn.

```python
def adapt_for_threads(variant, affiliate_link: str, disclosure: str = None) -> str:
    disclosure = disclosure if disclosure is not None else content.DISCLOSURE_DEFAULT
    lines = [variant.hook, "", variant.main_message, *variant.body, variant.cta]
    body = "\n".join(l for l in lines if l)
    return _fit_to_length(body, affiliate_link, disclosure, content.PLATFORM_MAX_LEN["threads"])
```

### 3.3. `adapt_for_facebook(variant, affiliate_link, disclosure=None) -> str`

PTYC §25: dòng đầu mạnh (hook), có thể giải thích hơn Threads — gộp
`main_message` + `body` thành 1 đoạn văn liền mạch (khác Threads).

```python
def adapt_for_facebook(variant, affiliate_link: str, disclosure: str = None) -> str:
    disclosure = disclosure if disclosure is not None else content.DISCLOSURE_DEFAULT
    paragraph = " ".join([variant.main_message, *variant.body])
    lines = [variant.hook, "", paragraph, "", variant.cta]
    body = "\n".join(l for l in lines if l)
    return _fit_to_length(body, affiliate_link, disclosure, content.PLATFORM_MAX_LEN["facebook"])
```

### 3.4. `adapt_for_instagram(variant, affiliate_link, disclosure=None) -> str`

PTYC §26: hook đầu, ngắn rõ, CTA rõ. Cùng kiểu ghép xuống dòng như
Threads (không có tiêu chí nào trong §26 đòi hỏi cách ghép khác Threads
ngoài giới hạn ký tự riêng — cả hai đều "ngắn, hội thoại", khác biệt
chính là ngưỡng ký tự `PLATFORM_MAX_LEN["instagram"]` = 2200 so với
Threads 500).

```python
def adapt_for_instagram(variant, affiliate_link: str, disclosure: str = None) -> str:
    disclosure = disclosure if disclosure is not None else content.DISCLOSURE_DEFAULT
    lines = [variant.hook, "", variant.main_message, *variant.body, variant.cta]
    body = "\n".join(l for l in lines if l)
    return _fit_to_length(body, affiliate_link, disclosure, content.PLATFORM_MAX_LEN["instagram"])
```

### 3.5. Dispatcher + batch

```python
_ADAPTERS = {
    "threads": adapt_for_threads,
    "facebook": adapt_for_facebook,
    "instagram": adapt_for_instagram,
}


def adapt_for_platform(variant, platform: str, affiliate_link: str, disclosure: str = None) -> str:
    return _ADAPTERS[platform](variant, affiliate_link, disclosure)


def adapt_for_platforms(variant, platforms: list, affiliate_link: str, disclosure: str = None) -> dict:
    """PTYC §27 "dùng nội dung này cho tất cả kênh": tính riêng từng
    platform trong danh sách được truyền vào -- không tự ý sinh cả 3 nếu
    caller chỉ cần 1-2 platform (khớp tinh thần "operator vẫn chỉnh riêng
    được, không tự khoá platform adaptation").
    """
    return {p: adapt_for_platform(variant, p, affiliate_link, disclosure) for p in platforms}
```

`platform` không hợp lệ (không có trong `_ADAPTERS`) → `KeyError` tự
nhiên từ dict lookup, không cần validate thêm — lỗi lập trình rõ ràng,
không phải input người dùng cuối (E6 sẽ luôn gọi với platform hợp lệ từ
`channel.platform` đã có trong DB).

## 4. Testing plan

- `_fit_to_length()`: body ngắn hơn budget → không cắt, có đủ link+disclosure
  ở cuối; body dài hơn → cắt đúng, vẫn giữ nguyên link+disclosure.
- `adapt_for_threads()`: có `affiliate_link` trong kết quả, có `disclosure`,
  độ dài `<= PLATFORM_MAX_LEN["threads"]` (500) kể cả với `body` dài; hook
  xuất hiện ở đầu chuỗi.
- `adapt_for_facebook()`: `main_message`+`body` gộp thành 1 dòng liền
  (không xuống dòng giữa chúng, khác Threads); có link+disclosure; giới
  hạn 63206.
- `adapt_for_instagram()`: có link+disclosure; giới hạn 2200; hook ở đầu.
- `adapt_for_platform()`: dispatch đúng theo tên platform, cả 3 giá trị
  hợp lệ (`threads`/`facebook`/`instagram`).
- `adapt_for_platforms()`: trả đúng dict với đúng các platform trong danh
  sách truyền vào (test với 1 platform, và với đủ 3 platform — không tự ý
  thêm platform ngoài danh sách yêu cầu).
- Không hashtag: verify không caption nào của cả 3 adapter chứa ký tự
  `#` ngoài chính `disclosure` (disclosure có `#tiepthilienket`, đây là
  ngoại lệ hợp lệ duy nhất — verify bằng cách bóc `disclosure` ra khỏi
  chuỗi trước khi kiểm `#`).
- Tương thích ngược: toàn bộ test `feat/content-engine-v2` hiện có (E1-E4,
  475/0 + 340/0) phải giữ nguyên xanh.
