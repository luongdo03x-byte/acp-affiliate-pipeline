# ACP 2.0 — Thiết kế 3 Variants + Anti-Industrial Checker + Rule-based Scoring (Content Engine v2, phần E3)

**Ngày:** 2026-08-17
**Trạng thái:** Thiết kế đã được chốt trong hội thoại; chờ review bản spec trước khi lập implementation plan.
**Thuộc:** E3 trong 6 phần (E1 → E2 → E3 → E4 → E5 → E6) chia nhỏ từ
`PTYC_ACP_CONTENT_ENGINE_V2.md`. E3 xây trên nền E1 (`core/content_facts.py`)
và E2 (`core/content_angle.py`, `core/content_hook.py`) — cả hai đã merge
vào `feat/content-engine-v2`. E4 (AI Judge toàn-caption + BEST selection +
Anti-Repetition) sẽ dùng `ContentVariant`/`score_variant()` của E3 làm input.

## 1. Mục tiêu

PTYC §12, §16-22, §29: sinh **3 variant** khác angle/hook/cấu trúc cho 1
sản phẩm (không phải paraphrase nhau), chặn giọng văn "công nghiệp"
(§16-17), và chấm điểm rule-based deterministic (§29) làm nền cho Hybrid
Scoring của E4.

**Ranh giới cứng đã chốt:** giống E1/E2 — không đụng `core/pipeline.py`,
không đụng `core/content.py`'s `generate()`/`validate()`. Dormant hoàn
toàn, chưa nối vào luồng tạo bài thật (việc của E6).

**Quyết định kiến trúc đã chốt trong hội thoại:**
1. Output là **Content Core** — `ContentVariant` dataclass với field riêng
   (`angle`, `hook`, `main_message`, `body`, `cta`, `structure`), **chưa**
   ghép thành 1 chuỗi caption. Đúng PTYC §23 "Content Core → Platform
   Adapter" — E5 tự ghép + điều chỉnh riêng theo Threads/Facebook/
   Instagram, không phải parse ngược 1 chuỗi đã ghép sẵn.
2. Body sinh qua LLM + fallback template (pattern giống `generate_hooks()`
   ở E2), không phải template thuần.
3. Scoring tách 2 lớp riêng biệt, không trộn: `score_variant_rules()`
   thuần rule/regex, **deterministic 100%** (đúng PTYC §29 "Rule-based
   phải deterministic và test được"); `score_variant_soft()` là AI Judge
   **tuỳ chọn** chấm 2 yếu tố mềm không dò được bằng regex
   (`naturalness`, `salesy_level` — trong danh sách §30), tách tên hàm rõ
   ràng để không mang nợ kỹ thuật đặt sai tên "rule-based" cho thứ có gọi
   AI. Không có judge đăng ký → `score_variant_soft()` trả lại chính
   `score_variant_rules().score` (không bịa số AI giả).

## 2. Phạm vi

### Trong phạm vi
- Module mới `core/content_variant.py`: `ContentVariant` dataclass,
  `generate_variant()`, `generate_variants()`, sinh CTA (pool cố định,
  không LLM), sinh body (LLM + template fallback).
- Module mới `core/content_checker.py`: `INDUSTRIAL_PHRASES`,
  `CTA_SPAM_PHRASES`, `check_industrial_phrases()`, `check_variant_rules()`,
  `score_variant_rules()`, `score_variant_soft()`, `score_variant()`.
- Test cho toàn bộ hàm trên.

### Ngoài phạm vi (dành cho E4+/P1)
- `excessive_adjectives`, `feature_dump`, `too_many_benefits` (§17) — cần
  hiểu ngữ nghĩa câu, không dò chính xác bằng regex/đếm. Cố mã hoá
  heuristic yếu (vd đếm tính từ theo danh sách cố định) sẽ báo sai nhiều
  hơn giá trị mang lại — bỏ qua P0, ghi rõ đây là giới hạn có chủ đích,
  không phải thiếu sót.
- `recent_similarity` (§29) — cần dữ liệu bài đăng gần đây, thuộc phạm vi
  Anti-Repetition của E4, không phải E3.
- Hybrid Scoring toàn-caption kết hợp `score_variant()` của cả 3 variant
  để chọn BEST, Fact Safety hard-gate loại variant khỏi BEST selection —
  việc của E4. E3 chỉ tính điểm cho **1 variant riêng lẻ**, không so sánh
  giữa các variant.
- Nối `generate_variants()` vào `core/pipeline.py`, UI `/duyet`, Content
  State machine — việc của E6.
- **Số lượng angle distinct trong 3 variant**: `select_angle_candidates()`
  (E2) chỉ có tối đa 3 giá trị khả dĩ trong toàn bộ universe P0
  (`DEAL_PRICE`, `USE_CASE`, `PERSONAL_RECOMMENDATION` — 8 angle còn lại
  chưa tự chọn được, xem spec E2 §2). Nhiều sản phẩm thực tế chỉ có 1-2
  angle khách quan (vd không giảm giá + category không khớp nhóm nào →
  chỉ `PERSONAL_RECOMMENDATION`). `generate_variants()` **không ép** luôn
  đủ 3 angle khác nhau bằng cách lặp lại angle — làm vậy trong P0 (chưa
  có LLM tạo khác biệt thật giữa 2 variant cùng angle ở nhánh template
  fallback deterministic) sẽ tạo 2 variant gần giống hệt nhau, vi phạm
  trực tiếp PTYC §12 "không tạo 3 bản gần như giống nhau" — nặng hơn việc
  trả về ít hơn 3 variant. `generate_variants()` trả **đúng số lượng
  angle distinct sẵn có** (1-3 variant), đúng tinh thần "3 angle khác
  nhau **khi dữ liệu cho phép**" (PTYC §12). Ràng buộc "luôn đúng 3
  variant" của §12 được đáp ứng ở tầng LLM thật (khi có `set_body_generator`
  đăng ký, LLM có thể tạo khác biệt thật giữa 2 variant cùng angle) —
  E6 (nơi nối vào luồng tạo bài thật với LLM thật) sẽ đảm bảo luôn đủ 3.

## 3. `core/content_variant.py`

```python
@dataclass(frozen=True)
class ContentVariant:
    angle: str
    hook: str
    main_message: str
    body: list          # str, tối đa 2 supporting points (PTYC §20)
    cta: str
    structure: str       # 1 trong 6 giá trị STRUCTURES (PTYC §18)

STRUCTURES = [
    "HOOK_VALUE_CTA", "PROBLEM_SOLUTION_RESULT", "STORY_LESSON_MESSAGE",
    "MISTAKE_INSIGHT", "DEAL_BENEFIT_CTA", "USE_CASE_VALUE_CTA",
]

ANGLE_TO_STRUCTURE = {
    "DEAL_PRICE": "DEAL_BENEFIT_CTA",
    "USE_CASE": "USE_CASE_VALUE_CTA",
    "PERSONAL_RECOMMENDATION": "HOOK_VALUE_CTA",
}

CTA_TYPES = ["VIEW_PRODUCT", "CHECK_PRICE", "COMMENT", "SAVE", "SHARE", "ASK_OPINION"]

ANGLE_TO_CTA_TYPE = {
    "DEAL_PRICE": "CHECK_PRICE",
    "USE_CASE": "VIEW_PRODUCT",
    "PERSONAL_RECOMMENDATION": "ASK_OPINION",
}

CTA_POOL = {
    "CHECK_PRICE": [
        "Giá hiện tại mình để ở link.",
        "Ai đang tìm mẫu này thì xem giá ở link.",
    ],
    "VIEW_PRODUCT": [
        "Mình để link để bạn xem thêm.",
        "Xem chi tiết ở link nhé.",
    ],
    "ASK_OPINION": [
        "Bạn nghĩ sao về món này?",
        "Ai đã dùng rồi cho mình xin ý kiến với.",
    ],
}
```

`ANGLE_TO_STRUCTURE`/`ANGLE_TO_CTA_TYPE` chỉ map 3 angle E2 thực sự chọn
được (đúng giới hạn đã chốt ở E2 §2) — không map 8 angle còn lại vì
chưa có nơi nào tạo ra chúng để cần map tới.

### 3.1. Sinh body

`set_body_generator(fn)` — `fn(prompt: str) -> str`, model trả JSON thô
`{"main_message": "...", "body": ["...", "..."]}`, pattern y hệt
`content_hook.set_hook_generator()`.

```python
def _build_body_prompt(angle: str, hook: str, structure: str, facts) -> str:
    facts_text = "\n".join(f"- {f}" for f in facts.facts) or "(không có fact cụ thể nào)"
```

Rào `facts.name`/`facts_text` trong delimiter `<<<FACT>>>`/`<<<HẾT_FACT>>>`
**bên trong khối fence** (đúng bài học Finding I2 của E2's final review —
áp dụng ngay từ đầu, không đợi review phát hiện lại), nhắc lại ràng buộc
sau khối. Yêu cầu model: 1 `main_message` ngắn + tối đa 2 `body` item
(PTYC §20), theo cấu trúc `structure`, không lặp nguyên văn `hook`.

`generate_body(angle, hook, structure, facts) -> (main_message: str, body: list)`:
không có generator → `_template_body(angle, facts)` (deterministic, dựng
từ `facts.price`/`facts.facts[0]` theo angle); có generator → gọi tối đa
3 lần (bọc lỗi network + lỗi parse, đúng pattern E2), JSON hợp lệ với
đúng 2 key + `body` là list ≤2 phần tử thì dùng, sai/hết retry → fallback
template.

`_template_body(angle, facts) -> (str, list)`: 3 nhánh ứng 3 angle P0
(`DEAL_PRICE`/`USE_CASE`/else-PERSONAL_RECOMMENDATION), dùng
`facts.price` (định dạng `f"{v:,}đ".replace(",", ".")`, giống
`content.py._fmt_vnd()`) và `facts.facts[0]` nếu có.

### 3.2. `generate_variant()` / `generate_variants()`

```python
def generate_variant(angle: str, facts, rng=None) -> ContentVariant:
    rng = rng or random.Random()
    hook = content_hook.select_best_hook(angle, facts)["hook"]
    structure = ANGLE_TO_STRUCTURE.get(angle, "HOOK_VALUE_CTA")
    main_message, body = generate_body(angle, hook, structure, facts)
    cta_type = ANGLE_TO_CTA_TYPE.get(angle, "VIEW_PRODUCT")
    cta = rng.choice(CTA_POOL[cta_type])
    return ContentVariant(angle=angle, hook=hook, main_message=main_message,
                           body=body, cta=cta, structure=structure)


def generate_variants(facts, product, rng=None) -> list:
    """Trả list ContentVariant, 1 phần tử/angle distinct từ
    select_angle_candidates() (E2) -- 1-3 phần tử tuỳ dữ liệu sản phẩm,
    xem spec §2 "Ngoài phạm vi" cho lý do không ép đủ 3.
    """
    rng = rng or random.Random()
    angles = content_angle.select_angle_candidates(product)
    return [generate_variant(a, facts, rng) for a in angles]
```

`select_best_hook()` không nhận `rng` (chữ ký E2 đã chốt) nên hook trong
mỗi variant deterministic theo template fallback khi chưa đăng ký
generator/judge — đúng tinh thần mock-first (PTYC §70).

## 4. `core/content_checker.py`

```python
INDUSTRIAL_PHRASES = [
    "sản phẩm này mang lại", "lựa chọn hoàn hảo", "không thể bỏ qua",
    "trải nghiệm tuyệt vời", "chất lượng vượt trội", "đáp ứng mọi nhu cầu",
    "thiết kế hiện đại", "giải pháp tối ưu", "đáng để sở hữu",
    "mang đến sự tiện lợi",
]

CTA_SPAM_PHRASES = [
    "mua ngay", "comment ngay", "share ngay", "follow ngay", "đừng bỏ lỡ",
]

_LONG_SENTENCE_WORDS = 25
_LONG_PARAGRAPH_WORDS = 40
_EXCESS_EMOJI_THRESHOLD = 3
_GENERIC_OPENINGS = ["sản phẩm này", "đây là"]  # trùng content_hook, xem ghi chú
```

`_GENERIC_OPENINGS` trùng list ở `content_hook.py` — **không import chéo**
(2 module cùng cấp E3/E2, import chéo tạo phụ thuộc vòng nghĩa không cần
thiết cho 1 list 2 phần tử) — chấp nhận trùng lặp nhỏ này, khác hẳn quy
mô của `check_fact_safety()` (hàng chục dòng logic) mà E2 đã đúng khi
import thay vì copy.

### 4.1. `check_industrial_phrases(text) -> list`

Blacklist match, NFC-normalize trước khi so khớp (đúng bài học Finding I1
của E2, áp dụng từ đầu).

### 4.2. `check_variant_rules(variant) -> list[dict]`

Ghép `hook + main_message + " ".join(body) + cta` thành `text` (hàm nội bộ
`_variant_text(variant)`), trả **list[dict]** — mỗi dict
`{"rule": <tên rule>, "message": <mô tả tiếng Việt>}` — chứ không phải
`list[str]` phẳng, vì `score_variant_rules()` (§4.3) cần biết **loại** vi
phạm để áp đúng mức trừ điểm; suy ngược loại từ 1 chuỗi message bằng
string-matching sẽ giòn và dễ vỡ khi đổi câu chữ thông báo. `[]` nghĩa là
sạch. Rule nào **đếm được theo đơn vị** (số câu dài, số đoạn dài, số
emoji vượt ngưỡng, số cụm cliché khớp) phát ra **1 dict/đơn vị vi phạm**
— để `score_variant_rules()` trừ điểm lặp lại đúng số lần; rule dạng
boolean (có/không) phát ra **tối đa 1 dict**:

- `generic_opening` (boolean): `main_message` mở đầu 1 trong
  `_GENERIC_OPENINGS`.
- `marketing_cliche` (đếm theo đơn vị): 1 dict / cụm trong
  `INDUSTRIAL_PHRASES` khớp trong `text` — `check_industrial_phrases(text)`.
- `too_many_ctas` (boolean): đếm phần tử `CTA_SPAM_PHRASES` xuất hiện
  trong `text` (NFC-normalize), `>1` phát 1 dict duy nhất (đúng PTYC §22
  "cta_count > 1 primary CTA phải bị cảnh báo" — CTA field của variant
  không tính vào đây, vì đó không phải cụm spam, chỉ đếm cụm nằm trong
  danh sách cấm).
- `long_sentence` (đếm theo đơn vị): 1 dict / câu `>25` từ, tách từng
  phần tử `body` theo `[.!?]`.
- `long_paragraph` (đếm theo đơn vị): 1 dict / phần tử `body` `>40` từ
  (nguyên cả phần tử, chưa tách câu).
- `repeated_phrase` (boolean): n-gram 4 từ liên tiếp xuất hiện cả trong
  `hook` lẫn 1 phần tử `body` (lowercase, NFC-normalize).
- `excessive_emoji` (đếm theo đơn vị): đếm emoji trong `text` bằng regex
  Unicode range (`\U0001F300-\U0001FAFF`, `\U00002600-\U000027BF`,
  `\U0001F1E6-\U0001F1FF`); phát `max(0, count - 3)` dict (1 dict/emoji
  vượt ngưỡng 3).

`message` của mỗi dict dùng dấu ngoặc kép cong `"..."` khi trích dẫn cụm
từ (bài học Finding 10 của E1).

### 4.3. `score_variant_rules(variant) -> RuleScore`

```python
@dataclass(frozen=True)
class RuleScore:
    score: float          # 0-1
    violations: list       # str -- chỉ lấy field "message" từ check_variant_rules()
    fact_safety_pass: bool

_RULE_PENALTY = {
    "generic_opening": 0.15,
    "marketing_cliche": 0.15,
    "too_many_ctas": 0.2,
    "long_sentence": 0.05,
    "long_paragraph": 0.05,
    "repeated_phrase": 0.1,
    "excessive_emoji": 0.05,
}
```

1. `check_fact_safety(_variant_text(variant))` (E1) — FAIL (non-empty) →
   `RuleScore(score=0.0, violations=<fact problems>, fact_safety_pass=False)`
   ngay, **không gọi `check_variant_rules()`** (đúng PTYC §8.4 "variant bị
   loại, không được chọn BEST" — điểm 0 đảm bảo E4's `max()` tự động bỏ
   qua variant này mà không cần logic loại trừ riêng, dù E4 vẫn nên tự
   kiểm `fact_safety_pass` tường minh thay vì suy luận ngầm từ
   `score==0.0`).
2. PASS → gọi `check_variant_rules(variant)`, `score = 1.0 -
   sum(_RULE_PENALTY[v["rule"]] for v in rule_violations)`, kẹp về
   `max(0.0, score)`. `violations` (field trả ra ngoài) là
   `[v["message"] for v in rule_violations]` — người gọi bên ngoài
   (test, E4, tương lai UI `/duyet` ở E6) chỉ cần đọc message, không cần
   biết cấu trúc dict nội bộ.

### 4.4. `score_variant_soft(variant, rule_score, rng=None) -> float`

```python
_variant_judge_fn = None

def set_variant_judge(fn):
    """fn(prompt: str) -> str. Model trả JSON thô
    {"naturalness": 0-1, "salesy_level": 0-1}. fn=None (mặc định) -- trả
    lại rule_score, không bịa điểm AI giả khi chưa có judge.
    """
```

Có judge → gọi tối đa 3 lần (bọc lỗi network + lỗi parse), parse đúng 2
key, kẹp mỗi giá trị về `[0,1]`, trả
`round((naturalness + (1 - salesy_level)) / 2, 4)` (salesy càng cao càng
xấu, đúng PTYC §31 "Salesy score càng cao càng xấu" — đảo dấu để cùng
chiều "cao = tốt" với `naturalness`). Hết retry vẫn fail → trả `rule_score`
(fallback an toàn, không phải 0.0 — 1 lỗi tạm thời của judge không nên
làm variant tốt bị chấm như variant tệ).

### 4.5. `score_variant(variant, rng=None) -> dict`

```python
def score_variant(variant, rng=None) -> dict:
    rules = score_variant_rules(variant)
    soft = score_variant_soft(variant, rules.score, rng)
    return {"rules": rules, "soft": soft, "overall": round((rules.score + soft) / 2, 4)}
```

`overall` chỉ có ý nghĩa so sánh **trong nội bộ E3** (test/demo) — E4's
Hybrid Scoring thật sẽ có công thức riêng kết hợp `rules`/`soft` với các
yếu tố khác (`hook_strength`, `readability`, `relevance`, `originality`
theo §30 mà E3 chưa chấm), không nhất thiết dùng nguyên `overall` này.

## 5. Testing plan

- `generate_variants()`: sản phẩm có discount + category `gia-dung` → 3
  variant distinct angle (`DEAL_PRICE`/`USE_CASE`/`PERSONAL_RECOMMENDATION`);
  sản phẩm category `thiet-bi-y-te` không giảm giá → đúng 1 variant
  (`PERSONAL_RECOMMENDATION`). Mỗi variant có `hook`/`main_message` non-empty,
  `body` ≤2 phần tử, `cta` thuộc đúng pool theo `ANGLE_TO_CTA_TYPE`.
- `_template_body()`: mỗi angle P0 cho `main_message` khác nhau, `body`
  dùng đúng `facts.price`/`facts.facts[0]`.
- `generate_body()`: JSON hợp lệ từ mock generator → dùng đúng; generator
  ném exception → fallback template; JSON thiếu key/sai kiểu `body` →
  fallback template; prompt fencing test giống pattern E2 (facts nằm
  trong khối fence).
- `check_industrial_phrases()`: mỗi cụm trong `INDUSTRIAL_PHRASES` có 1
  case chặn; caption sạch pass. NFD input cũng bị chặn đúng (bài học I1).
- `check_variant_rules()`: mỗi rule ở §4.2 có 1 case vi phạm + 1 case
  sạch tương ứng (7 rule × 2 case tối thiểu).
- `score_variant_rules()`: fact-unsafe variant → `score=0.0`,
  `fact_safety_pass=False`, không tính violation khác. Variant sạch →
  `score` gần 1.0. Variant nhiều vi phạm → `score` thấp hơn nhưng
  `>=0.0` (không âm).
- `score_variant_soft()`: không judge → trả lại `rule_score` nguyên vẹn;
  judge hợp lệ → parse đúng công thức đảo dấu `salesy`; judge ném
  exception/JSON sai → fallback `rule_score`.
- Tương thích ngược: toàn bộ test hiện có của `feat/content-engine-v2`
  (E1+E2, 404/0 + 340/0) phải giữ nguyên xanh.
