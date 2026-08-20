# ACP 2.0 — Thiết kế AI Judge + Hybrid Scoring + BEST selection + Anti-Repetition (Content Engine v2, phần E4)

**Ngày:** 2026-08-17
**Trạng thái:** Thiết kế đã được chốt trong hội thoại; chờ review bản spec trước khi lập implementation plan.
**Thuộc:** E4 trong 6 phần (E1 → E2 → E3 → E4 → E5 → E6) chia nhỏ từ
`PTYC_ACP_CONTENT_ENGINE_V2.md`. E4 xây trên nền E3 (`core/content_variant.py`
— `ContentVariant`; `core/content_checker.py` — `score_variant_rules()`) —
cả hai đã merge vào `feat/content-engine-v2`, qua final review, **không
sửa lại**. E5 (Platform Adaptation) và E6 (tích hợp `/duyet`) dùng
`select_best_variant()` của E4 làm bước cuối trước khi ra `PENDING_REVIEW`.

## 1. Mục tiêu

PTYC §28, §32-35: kết hợp Rule-based Scoring (đã có ở E3) với AI Judge cho
các yếu tố mềm còn lại của §30, chọn **BEST** trong 3 variant, và chống
lặp nội dung so với bài gần đây cùng account/platform.

**Ranh giới cứng đã chốt:** giống E1-E3 — không đụng `core/pipeline.py`,
không đụng `core/content.py`. Dormant hoàn toàn, chưa nối vào luồng tạo
bài thật (việc của E6). **Không sửa `core/content_variant.py`/
`core/content_checker.py` (E3)** — cả hai đã merge và qua final review;
sửa lại sẽ phá vỡ kỷ luật "module đã merge không bị đụng lại" đã giữ
xuyên suốt E1→E3, và buộc phải review lại toàn bộ E3.

**Quyết định kiến trúc đã chốt:**
1. AI Judge cho 4 yếu tố còn lại của §30 (`hook_strength`, `readability`,
   `relevance`, `originality` — E3 đã chấm `naturalness`/`salesy_level`
   qua `score_variant_soft()`) nằm trong module **mới**
   `core/content_scoring.py`, tách hẳn khỏi E3's `score_variant_soft()`.
   `select_best_variant()` (§4 dưới) chỉ gọi `content_checker.
   score_variant_rules()` (phần rule-based deterministic của E3), **không
   gọi `score_variant_soft()`** — tránh 2 lần gọi LLM/variant (1 lần cho
   soft score cũ, 1 lần cho hybrid judge mới) khi chỉ cần đúng 1 lần đánh
   giá tổng hợp cho BEST selection.
2. Anti-Repetition nhận **`list[ContentVariant]`** làm đại diện "bài gần
   đây" — tái dùng đúng dataclass đã có ở E3, không tạo dataclass mới.
   E6 (nơi nối DB thật) sẽ tự chuyển `post`/`publish_target` row thành
   `ContentVariant`-like object khi gọi các hàm này.

## 2. Phạm vi

### Trong phạm vi
- Module mới `core/content_scoring.py`:
  - Anti-Repetition: `check_repetition()`, `repetition_penalty()`.
  - Hybrid Judge: `set_hybrid_judge(fn)`, `score_variant_hybrid()`.
  - BEST selection: `select_best_variant()`.
- Test cho toàn bộ hàm trên.

### Ngoài phạm vi (dành cho E5+/P1)
- `same_sentence_structure` (§29/§33) — cần hiểu cấu trúc câu ngữ nghĩa,
  không dò chính xác bằng regex/đếm, cùng lý do E3 đã bỏ
  `excessive_adjectives`/`feature_dump`/`too_many_benefits`.
- Platform Adaptation (E5), nối vào `core/pipeline.py`/UI `/duyet`/Content
  State machine/audit events (E6).
- Winning Pattern Library, Performance Feedback (P2) — chưa có dữ liệu
  hiệu suất thật để học.
- Query `post`/`publish_target` thật để lấy "bài gần đây" — E4 chỉ định
  nghĩa hàm nhận `list[ContentVariant]` làm tham số, không tự query DB.

## 3. Anti-Repetition

```python
_OPENING_WORDS = 5
_ANGLE_FREQUENCY_WINDOW = 5
_ANGLE_FREQUENCY_THRESHOLD = 0.6
_SIMILARITY_THRESHOLD = 0.6

_REPETITION_PENALTY = {
    "same_opening": 0.15,
    "same_hook_formula": 0.3,
    "same_angle_too_often": 0.1,
    "same_cta": 0.1,
    "high_text_similarity": 0.25,
}
```

`_variant_text(variant) -> str`: hàm nội bộ, ghép `hook + main_message +
" ".join(body) + cta` — **trùng lặp nhỏ có chủ đích** với
`content_checker._variant_text()` (hàm private, không import chéo qua
tên có gạch dưới đầu — đúng tinh thần "không import private function
xuyên module" và đúng tiền lệ E3 đã chấp nhận trùng `_GENERIC_OPENINGS`
với E2 vì quy mô nhỏ, không đáng tạo phụ thuộc).

`check_repetition(variant, recent_variants: list) -> list[dict]` —
`{"rule": ..., "message": ...}`, cùng dạng `check_variant_rules()` của
E3:
- `recent_variants` rỗng → `[]` ngay (không có gì để so sánh).
- `same_opening`: 5 từ đầu (`hook.split()[:5]`, NFC-normalize, lowercase)
  trùng bất kỳ `recent_variants[i].hook`'s 5 từ đầu.
- `same_hook_formula`: `hook` (NFC-normalize, lowercase, strip) trùng y
  hệt bất kỳ `recent_variants[i].hook`.
- `same_angle_too_often`: xét `_ANGLE_FREQUENCY_WINDOW` (5) phần tử đầu
  của `recent_variants` (giả định đã sắp theo mới nhất trước — trách
  nhiệm của caller, giống cách `post` thường query `ORDER BY created_at
  DESC`), tỷ lệ cùng `angle` với `variant` > `_ANGLE_FREQUENCY_THRESHOLD`
  (0.6) → vi phạm.
- `same_cta`: `cta` (NFC-normalize, lowercase, strip) trùng y hệt bất kỳ
  `recent_variants[i].cta`.
- `high_text_similarity`: Jaccard similarity (word-set overlap) giữa
  `_variant_text(variant)` và `_variant_text(recent_variants[i])` >
  `_SIMILARITY_THRESHOLD` (0.6) với bất kỳ phần tử nào. Tokenize bằng
  `re.findall(r"\w+", ...)` (NFC-normalize + lowercase trước), **không**
  dùng `.split()` thô — đã tự kiểm chứng `.split()` giữ nguyên dấu câu
  dính vào từ cuối câu (`"vậy?"` vs `"đấy?"`) khiến 2 câu ý nghĩa gần như
  giống hệt nhau nhưng chỉ khác dấu câu bị tính là "khác từ", làm giảm
  giả tạo độ tương đồng đo được (case thật: 2 câu rõ ràng đạo ý nhau đo
  ra 0 với `.split()` do lệch dấu câu ở mọi từ cuối câu, nhưng ra đúng
  0.72 với `\w+`).

Mỗi rule chỉ phát **tối đa 1 dict** (boolean, không đếm số bài gần đây bị
trùng — khác với E3's rule "đếm theo đơn vị" như `marketing_cliche`, vì ở
đây "trùng với 1 bài cũ" đã đủ nghiêm trọng, trùng với N bài không nặng
hơn N lần).

`repetition_penalty(variant, recent_variants) -> float` —
`sum(_REPETITION_PENALTY[v["rule"]] for v in check_repetition(...))`.

## 4. Hybrid Judge

```python
_hybrid_judge_fn = None

def set_hybrid_judge(fn):
    """fn(prompt: str) -> str. Model trả JSON thô {"hook_strength": 0-1,
    "readability": 0-1, "relevance": 0-1, "originality": 0-1}.
    fn=None (mặc định) -- mỗi yếu tố mặc định = rule_score, không bịa
    điểm AI giả khi chưa có judge (đúng nguyên tắc E1-E3).
    """
```

`_build_hybrid_judge_prompt(variant, rule_score) -> str`: rào toàn bộ
`_variant_text(variant)` trong delimiter `<<<CAPTION>>>`/`<<<HẾT_CAPTION>>>`
+ nhắc lại ràng buộc SAU khối đó (đúng bài học Finding I2 của E2, áp dụng
chủ động từ đầu, giống cách E3 đã làm với `_build_soft_judge_prompt()`).

`score_variant_hybrid(variant) -> dict`:
1. `rules = content_checker.score_variant_rules(variant)` (E3, không sửa).
2. `rules.fact_safety_pass == False` → trả
   `{"rules": rules, "judge": {}, "hybrid_score": 0.0}` ngay, không gọi
   judge (đúng PTYC §32 "reject fact unsafe" — bước đầu tiên, trước cả
   rule penalty/AI judge).
3. PASS → không có `_hybrid_judge_fn` → `judge = {"hook_strength":
   rules.score, "readability": rules.score, "relevance": rules.score,
   "originality": rules.score}` (mặc định = rule score, không bịa).
   Có judge → gọi tối đa 3 lần (bọc lỗi network + lỗi parse, đúng pattern
   E2/E3), parse đúng 4 key, kẹp mỗi giá trị `[0,1]`; sai/hết retry →
   cùng fallback mặc định `= rules.score` cho cả 4 yếu tố.
4. `hybrid_score = round((rules.score + sum(judge.values()) / 4) / 2, 4)`.

## 5. BEST selection

```python
def select_best_variant(variants: list, recent_variants: list = None) -> dict:
    """PTYC §32: reject fact unsafe -> rule penalty (đã trong hybrid_score)
    -> AI judge (đã trong hybrid_score) -> anti-repetition -> overall ->
    BEST. Cả 3 variant fail fact safety -> all_rejected=True, không tự
    chọn (PTYC §32 "không auto chọn -> regenerate hoặc yêu cầu người
    dùng kiểm tra" -- E4 chỉ báo cáo trạng thái này, quyết định regenerate
    là việc của E6/người vận hành ở /duyet).
    """
```

1. `recent_variants = recent_variants or []`.
2. Với mỗi `v` trong `variants`: tính `h = score_variant_hybrid(v)`.
   `h["rules"].fact_safety_pass == False` → loại khỏi candidate (không
   tính `final_score`, không tính repetition — variant này không bao giờ
   được chọn).
3. Còn lại: `penalty = repetition_penalty(v, recent_variants)`,
   `final_score = max(0.0, round(h["hybrid_score"] - penalty, 4))`.
4. Không còn candidate nào (tất cả fail fact safety) →
   `{"best": None, "all_rejected": True, "candidates": []}`.
5. Còn candidate → chọn `final_score` cao nhất →
   `{"best": <ContentVariant>, "all_rejected": False, "final_score":
   <float>, "candidates": [{"variant":..., "hybrid":..., "repetition_penalty":..., "final_score":...}, ...]}`
   (`candidates` giữ lại toàn bộ, không chỉ BEST — để E6/UI `/duyet` có
   thể hiển thị điểm 3 variant như PTYC §49 yêu cầu, không phải tính lại).

## 6. Testing plan

- `check_repetition()`: mỗi rule có 1 case vi phạm + 1 case sạch;
  `recent_variants=[]` → `[]` ngay; `same_angle_too_often` test với
  window đủ 5 phần tử, tỷ lệ vượt/không vượt ngưỡng 0.6.
- `repetition_penalty()`: tổng đúng penalty khi nhiều rule cùng vi phạm.
- `score_variant_hybrid()`: fact-unsafe → `hybrid_score=0.0`, `judge={}`.
  Không judge → 4 yếu tố = `rules.score`, `hybrid_score` bằng chính
  `rules.score` (trung bình của chính nó với chính nó). Có judge hợp lệ
  → parse đúng, kẹp `[0,1]`. Judge exception/JSON sai → fallback đúng
  `rules.score` cho cả 4 yếu tố (không phải 0.0).
- `_build_hybrid_judge_prompt()`: rào delimiter đúng vị trí, nhắc lại
  ràng buộc sau khối.
- `select_best_variant()`: 3 variant sạch, không trùng bài gần đây →
  chọn đúng variant `hybrid_score` cao nhất. 1 variant fact-unsafe trong
  3 → loại khỏi candidate, chọn từ 2 còn lại. Cả 3 fact-unsafe →
  `all_rejected=True`, `best=None`. Variant trùng hook với bài gần đây →
  `final_score` thấp hơn (bị trừ penalty), có thể đổi kết quả BEST nếu đủ
  lớn.
- Tương thích ngược: toàn bộ test `feat/content-engine-v2` hiện có (E1-E3,
  448/0 + 340/0) phải giữ nguyên xanh — E4 không đụng file nào ngoài
  module mới + test mới.
