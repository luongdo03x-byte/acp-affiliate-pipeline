# ACP 2.0 — Thiết kế ProductFacts Gate + Fact Safety (Content Engine v2, phần E1)

**Ngày:** 2026-08-17
**Trạng thái:** Thiết kế đã được chốt trong hội thoại; chờ review bản spec trước khi lập implementation plan.
**Thuộc:** E1 trong 6 phần (E1 → E2 → E3 → E4 → E5 → E6) chia nhỏ từ
`PTYC_ACP_CONTENT_ENGINE_V2.md`. E1 là nền tảng — mọi phần sau (E2 Angle/
Hook, E3 Variant generation, E4 Scoring/BEST, E5 Platform Adaptation, E6
`/duyet` integration) đều dựa trên `ProductFacts` và cổng `Fact Safety` xây
ở đây. E1 không phụ thuộc phần nào khác trong Content Engine v2.

## 1. Mục tiêu

Content hiện tại (`core/content.py`) sinh caption trực tiếp từ
`product.description` (text tự do) và chỉ chặn được câu chữ đã biết trước
qua vài blacklist cố định (`FABRICATED_EXPERIENCE`, `EFFICACY_CLAIMS`,
`BANNED_SUPERLATIVES`). Không có khái niệm "danh sách fact được phép dùng"
— generator có thể vô tình paraphrase description thành câu khẳng định sai
lệch mà không blacklist nào bắt được, vì blacklist chỉ khớp cụm từ chính
xác, không biết caption đang bịa gì so với dữ liệu gốc.

E1 xây `ProductFacts`: 1 object chuẩn hoá tách `description` thành
`facts` (được phép dùng) và `unknown` (không được bịa), cache theo
product; và `Fact Safety` checker mở rộng khỏi 3 blacklist hiện có để phủ
đúng 3 nhóm bịa đặt nêu ở PTYC mục 8 (trải nghiệm cá nhân, social proof,
urgency).

**Ranh giới cứng đã chốt:** E1 chỉ là nền tảng thuần — không đụng
`core/pipeline.py`, không đụng `core/content.py`'s `generate()`/
`validate()` hiện có, không tạo `post` mới theo cách nào khác. Engine cũ
(Threads, D1-D4 multi-account) tiếp tục chạy y nguyên. Việc nối
`ProductFacts`/Fact Safety vào luồng tạo variant thật là việc của E3/E6.

## 2. Phạm vi

### Trong phạm vi
- Bảng mới `product_facts` — cache `ProductFacts` theo `product_id`.
- Module mới `core/content_facts.py`:
  - `ProductFacts` dataclass.
  - `set_extractor(fn)` — hook LLM trích xuất facts, theo đúng pattern
    `content.set_llm(fn)` đã có.
  - `build_product_facts(conn, product, rng=None) -> ProductFacts` —
    cache hit/miss, gọi extractor khi cần, parse+validate JSON, retry giới
    hạn khi model trả sai schema, fallback an toàn khi retry hết.
  - `check_fact_safety(caption, facts) -> list[str]` — hard gate, tái sử
    2 blacklist có sẵn từ `content.py` + 2 blacklist mới
    (`FABRICATED_SOCIAL_PROOF`, `FABRICATED_URGENCY`).
- Bộ trích xuất mặc định (không cần LLM) dùng cho test/`ACP_ADAPTER=mock`
  — tách câu heuristic từ `description`.
- Test cho toàn bộ hàm trên.

### Ngoài phạm vi (dành cho E2+)
- Angle Selector, Hook Generator (E2).
- Sinh 3 variant, Rule-based Scoring, Anti-Industrial checker (E3).
- AI Judge, Hybrid Scoring, BEST selection, Anti-Repetition (E4) — E1 chỉ
  cung cấp *khả năng* trả `FACT_SAFETY = FAIL`, việc dùng kết quả đó để
  loại variant khỏi BEST selection là việc của E4.
- Platform Adaptation (E5).
- Nối vào `core/pipeline.py`, UI `/duyet`, Content State machine, audit
  events, feature flag rollout (E6).
- Semantic/NLP đối chiếu từng câu caption với từng fact cụ thể — P0 dừng ở
  mức blacklist phrase-detection đúng như ví dụ cụ thể tại PTYC mục
  8.1-8.3 (bản thân spec cũng chỉ liệt kê ví dụ cụm từ cố định, không đòi
  hỏi semantic matching).

## 3. Data model

```sql
CREATE TABLE IF NOT EXISTS product_facts (
    product_id      TEXT PRIMARY KEY REFERENCES product(id),
    facts_json      TEXT NOT NULL,   -- JSON list[str]: fact được phép dùng
    unknown_json    TEXT NOT NULL,   -- JSON list[str]: điều không biết, cấm bịa
    category        TEXT,
    source_hash     TEXT NOT NULL,   -- sha256(product.description) hiện tại lúc extract
    prompt_version  TEXT NOT NULL,
    extracted_at    TEXT NOT NULL
);
```

Bảng mới hoàn toàn, thêm vào `SCHEMA` (không phải `MIGRATIONS`) — đúng
pattern đã dùng cho `account_group`/`post_channel_selection`/
`media_asset`. Không bảng nào khác tham chiếu `product_facts.product_id`
làm khoá ngoại từ phía nó; xoá `product` (nếu từng cần) chỉ cần xoá kèm
dòng `product_facts` tương ứng.

`source_hash` là `hashlib.sha256(description.encode()).hexdigest()` —
nếu `product.description` đổi (source re-crawl cập nhật mô tả), hash lệch
với dòng cache hiện có → coi là stale, extract lại. Không dùng
`product.updated_at` vì cột đó đổi bất kỳ khi nào product được ghi lại
(kể cả đổi giá), trong khi facts chỉ phụ thuộc `description`.

## 4. `ProductFacts` và `build_product_facts()`

```python
@dataclass(frozen=True)
class ProductFacts:
    name: str
    price: int
    original_price: int | None
    category: str
    facts: list       # str, chỉ những gì generator được phép dùng
    unknown: list      # str, những gì KHÔNG được bịa
```

`build_product_facts(conn, product, rng=None)`:
1. Tính `source_hash` từ `product["description"]`.
2. Query `product_facts` theo `product_id`. Nếu tồn tại và `source_hash`
   khớp → dựng `ProductFacts` từ `facts_json`/`unknown_json` đã cache,
   trả về ngay, **không** gọi extractor.
3. Cache miss/stale:
   - Không có extractor đăng ký (`_extractor_fn is None`, đúng trạng thái
     mặc định khi test/`ACP_ADAPTER=mock`) → dùng
     `_heuristic_facts(description)`: tách câu theo `[.\n;]` (tái dùng ý
     tưởng `_highlight()` ở `content.py`), mỗi câu non-empty ≤ 200 ký tự
     là 1 phần tử `facts`, `unknown = []`. Deterministic, không network.
   - Có extractor → gọi `_extractor_fn(_build_extract_prompt(product))`,
     parse JSON theo schema mục 47 PTYC (`{"facts": [...], "unknown":
     [...]}`). Parse fail → retry tối đa 2 lần (tổng 3 lần gọi, đúng mục
     48 "retry có giới hạn"). Hết retry vẫn fail → fallback
     `facts=[], unknown=[description]` (an toàn tuyệt đối: không có fact
     nào được phép dùng, coi như mọi thứ trong description là "chưa rõ").
4. Upsert `product_facts` (ghi đè dòng cũ nếu có) với `prompt_version`
   hiện tại (hằng số `PROMPT_VERSION = "e1-v1"` trong module, theo đúng
   tinh thần Prompt Versioning mục 44 — dù E1 chưa cần so sánh version,
   khai báo sẵn để E2+ không phải sửa schema).
5. Trả `ProductFacts`.

`rng` nhận vào nhưng không dùng trong E1 (giữ chữ ký nhất quán với
`content.generate(..., rng=...)` — các bản heuristic/extractor tương lai ở
E2+ có thể cần rng, không đổi chữ ký lần nữa).

## 5. Fact Safety checker

```python
def check_fact_safety(caption: str) -> list:
    """[] nghĩa là FACT_SAFETY = PASS. Non-empty là FAIL (PTYC mục 8.4).

    Không nhận ProductFacts làm tham số: E1 không đối chiếu semantic giữa
    caption và facts.facts/unknown (xem §2 "Ngoài phạm vi") -- toàn bộ cơ
    chế là blacklist/regex cố định, không phụ thuộc dữ liệu sản phẩm cụ
    thể nào. Chữ ký chỉ nhận caption để tránh tham số chết; nếu E4+ cần
    semantic check thật, đó là lúc thêm tham số facts, không phải bây giờ.
    """
```

Logic: chuẩn hoá `caption` giống `content.validate()` (NFC + lowercase),
rồi quét lần lượt:
- `content.FABRICATED_EXPERIENCE` (import trực tiếp, không copy) — mục
  8.1.
- `content.EFFICACY_CLAIMS` (import trực tiếp) — cam kết công dụng, liên
  quan tới việc bịa lợi ích không có trong `facts`.
- `FABRICATED_SOCIAL_PROOF` (mới, trong `content_facts.py`) — mục 8.2:
  `["đã bán hết", "ai dùng cũng khen", "best seller", "bán chạy nhất",
  "được nhiều người tin dùng"]`. Con số cụ thể kiểu "10.000 người đã mua"
  không đưa vào blacklist cố định (vô hạn biến thể số) — thay vào đó quét
  regex `\d[\d.,]*\s*(người|khách|đơn)\s*(đã\s*)?(mua|đặt)` để bắt mọi biến
  thể số.
- `FABRICATED_URGENCY` (mới) — mục 8.3: `["sắp hết hàng", "chỉ còn hôm
  nay", "số lượng có hạn", "nhanh tay kẻo lỡ"]`.

Không đối chiếu semantic với `facts.facts`/`facts.unknown` trong E1 (xem
lý do ở §2 "Ngoài phạm vi") — 4 blacklist + 1 regex là toàn bộ cơ chế
detect. Vi phạm nào cũng append đúng dạng thông báo tiếng Việt hiện có
trong `content.validate()` (vd `f"Bịa trải nghiệm cá nhân chưa từng có:
“{phrase}”"`) để nhất quán UI hiển thị lỗi sau này (E6).

## 6. `set_extractor()` và prompt

```python
def set_extractor(fn):
    """fn(prompt: str) -> str. Model trả JSON thô, build_product_facts() tự parse."""
```

Prompt dựng trong `_build_extract_prompt(product)`:
```
Trích xuất fact từ mô tả sản phẩm dưới đây. Trả về đúng JSON, không thêm
chữ nào khác:
{"facts": ["câu fact 1", "câu fact 2"], "unknown": ["điều không rõ 1"]}

RÀNG BUỘC:
- facts chỉ chứa thông tin có trong mô tả gốc, không suy luận thêm.
- unknown liệt kê những khía cạnh người mua có thể quan tâm nhưng mô tả
  không nói tới (vd độ bền, phù hợp dáng người...).
- Không thêm nhận định, đánh giá, hay câu không có trong dữ liệu.

Mô tả gốc:
{description}
```

## 7. Testing plan

- `product_facts` schema: tồn tại đúng cột, `product_id` là PK.
- `build_product_facts()`:
  - Cache miss, không extractor → heuristic facts đúng (tách câu, bỏ câu
    rỗng).
  - Cache hit (`source_hash` khớp) → không gọi extractor (đếm số lần gọi
    mock extractor = 0).
  - Cache stale (description đổi) → gọi lại extractor, ghi đè dòng cache
    cũ.
  - Extractor trả JSON hợp lệ → parse đúng `facts`/`unknown`.
  - Extractor trả JSON sai schema 3 lần liên tiếp → fallback
    `facts=[], unknown=[description]`, không raise exception ra ngoài.
  - Extractor trả sai lần đầu, đúng lần 2 → dùng kết quả lần 2, tổng số
    lần gọi = 2 (đúng cơ chế retry).
- `check_fact_safety()`: mỗi blacklist/regex có ít nhất 1 case chặn đúng
  (`FAIL`, danh sách non-empty) và 1 case caption sạch tương ứng
  (`PASS`, `[]`). Case riêng cho regex số lượng người mua (`"1.234 người
  đã mua"` bị chặn, "đã bán 1.234 lượt" từ `_social_proof()` thật — dùng
  cụm khác "đã bán" không phải "người đã mua" — không bị chặn nhầm).
- Tương thích ngược: toàn bộ test hiện có của `feat/shopee-affiliate-import`
  (`test_pipeline.py`, `test_pilot.py`) phải giữ nguyên xanh — E1 không
  đụng file nào ngoài `core/db.py` (thêm bảng) và file mới
  `core/content_facts.py`/test mới.
