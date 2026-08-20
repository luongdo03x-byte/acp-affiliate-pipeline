# ACP 2.0 — Thiết kế Angle Selector + Hook Generator (Content Engine v2, phần E2)

**Ngày:** 2026-08-17
**Trạng thái:** Thiết kế đã được chốt trong hội thoại; chờ review bản spec trước khi lập implementation plan.
**Thuộc:** E2 trong 6 phần (E1 → E2 → E3 → E4 → E5 → E6) chia nhỏ từ
`PTYC_ACP_CONTENT_ENGINE_V2.md`. E2 xây trên nền E1 (`core/content_facts.py`
— `ProductFacts`, `check_fact_safety()` — đã merge vào
`feat/content-engine-v2`). E3 (3 variant + Rule-based Scoring + Anti-
Industrial checker) sẽ dùng `select_angle_candidates()` và
`select_best_hook()` của E2 làm input.

## 1. Mục tiêu

PTYC mục 10-15: ACP không được dùng cùng 1 công thức mở đầu cho mọi sản
phẩm. E2 xây 2 bước đầu của pipeline sinh nội dung (§77): **Angle
Selector** (chọn góc tiếp cận phù hợp sản phẩm, trong 11 angle) và **Hook
Generator** (sinh 5 câu mở đầu ứng viên theo angle, lọc theo rule, chấm
điểm, chọn câu tốt nhất).

**Ranh giới cứng đã chốt:** giống E1 — không đụng `core/pipeline.py`,
không đụng `core/content.py`'s `generate()`/`validate()`. Engine caption cũ
tiếp tục chạy y nguyên. E2 hoàn toàn dormant, chưa nối vào luồng tạo bài
thật (việc của E6). Không có bảng DB mới — cả 2 module đều là pure
function, không cache gì (khác E1, vì việc chọn angle/hook không tốn kém
để tính lại mỗi lần, không cần cache theo product).

## 2. Phạm vi

### Trong phạm vi
- Module mới `core/content_angle.py`: `select_angle_candidates(product) ->
  list[str]`.
- Module mới `core/content_hook.py`: `generate_hooks()`,
  `check_hook_rules()`, `score_hooks()`, `select_best_hook()`,
  `set_hook_generator(fn)`, `set_hook_judge(fn)`.
- Bộ template hook cố định (không cần LLM) cho test/`ACP_ADAPTER=mock`.
- Test cho toàn bộ hàm trên.

### Ngoài phạm vi (dành cho P1/E3+)
- Content Objective enum (§9) — PTYC chỉ định 1 giá trị mặc định
  (`CONVERSION`) cho affiliate post, không có logic chọn động nào cần cài
  ở P0; không tạo module riêng cho 1 hằng số, để E3 khai báo tại chỗ dùng
  nếu cần.
- 8 angle còn lại trong enum 11 angle (PAIN_POINT, CURIOSITY,
  PROBLEM_SOLUTION, COMPARISON, SOCIAL_PROOF, MISTAKE_LESSON, EDUCATIONAL,
  BOLD_OPINION) — ACP **chưa có nguồn dữ liệu AudienceContext** (§36:
  niche/pain_points/desires) để suy ra các angle này một cách khách quan,
  và "feature lạ hoặc khác biệt" (§11) quá chủ quan để mã hoá thành rule
  không cần model. `select_angle_candidates()` P0 chỉ trả về tối đa 2 phần
  tử (`DEAL_PRICE` khi có, luôn kết thúc bằng `PERSONAL_RECOMMENDATION`).
  8 angle kia vẫn khai báo trong `ANGLES` enum (để E3+ dùng khi gán ngẫu
  nhiên/thủ công hoặc khi có AudienceContext ở P1) nhưng selector không tự
  chọn ra được — đúng PTYC mục 11 dòng cuối: *"Không chọn angle đòi hỏi dữ
  kiện mà ProductFacts không có."*
- Hybrid Scoring toàn-caption, BEST Variant selection giữa 3 variant, Anti-
  Repetition (E4) — `score_hooks()` của E2 chỉ chấm **hook** (câu ngắn,
  trong phạm vi 1 angle), không phải Hybrid Scoring của cả caption hoàn
  chỉnh. Không trùng phạm vi với E4, dù cả 2 đều gọi AI Judge.
- Nối `select_angle_candidates()`/`select_best_hook()` vào
  `core/pipeline.py`, sinh full caption (body/CTA), 3-variant orchestration
  — việc của E3/E6.

## 3. `core/content_angle.py` — Angle Selector

```python
ANGLES = [
    "DEAL_PRICE", "PAIN_POINT", "CURIOSITY", "PERSONAL_RECOMMENDATION",
    "PROBLEM_SOLUTION", "USE_CASE", "COMPARISON", "SOCIAL_PROOF",
    "MISTAKE_LESSON", "EDUCATIONAL", "BOLD_OPINION",
]

MIN_DISCOUNT_PCT = 0.05
_USE_CASE_CATEGORIES = {"gia-dung", "phu-kien-cong-nghe"}
_PERSONAL_REC_CATEGORIES = {"thoi-trang", "cham-soc-ca-nhan"}


def select_angle_candidates(product) -> list:
    """Trả angle theo thứ tự ưu tiên (tốt nhất trước). Luôn có ít nhất 1
    phần tử, luôn kết thúc bằng PERSONAL_RECOMMENDATION (fallback trung tính).
    """
```

Logic:
1. `original_price` và `current_price` đều có, `original_price >
   current_price`, và `(original_price - current_price) / original_price
   >= MIN_DISCOUNT_PCT` (5%, ngưỡng chọn để lọc giảm giá vặt vãnh làm tròn
   giá không có ý nghĩa marketing) → thêm `DEAL_PRICE` vào đầu danh sách.
2. `category_code ∈ _USE_CASE_CATEGORIES` (`gia-dung`, `phu-kien-cong-nghe`
   — nhóm sản phẩm dùng để giải quyết việc cụ thể) → thêm `USE_CASE`.
3. Ngược lại nếu `category_code ∈ _PERSONAL_REC_CATEGORIES` (`thoi-trang`,
   `cham-soc-ca-nhan` — nhóm phong cách/cá nhân) → thêm
   `PERSONAL_RECOMMENDATION` (nếu chưa có trong list).
4. Nếu `PERSONAL_RECOMMENDATION` chưa có trong list sau bước 2-3 (category
   không khớp nhóm nào, vd `thu-cung`/`the-thao`/`my-pham` trong seed data
   thật) → thêm vào cuối làm fallback.

Không nhận tham số `facts: ProductFacts` — cả 3 rule chỉ cần
`product["original_price"]`/`current_price`/`category_code`, không dùng gì
từ `ProductFacts`; thêm tham số không dùng là dead param (bài học từ E1's
`check_fact_safety()`).

## 4. `core/content_hook.py` — Hook Generator

```python
HOOK_TYPES = [
    "CURIOSITY", "PAIN", "PRICE", "BOLD_STATEMENT", "QUESTION",
    "CONTRAST", "CONFESSION_STYLE", "SURPRISING_FACT",
]

THREADS_HOOK_WORD_TARGET = 12
_GENERIC_OPENINGS = ["sản phẩm này", "đây là"]

_hook_generator_fn = None
_hook_judge_fn = None


def set_hook_generator(fn):
    """fn(prompt: str) -> str. Model trả JSON thô (list[str], 5 phần tử).
    fn=None (mặc định) -- dùng 5 template cố định theo Hook Type.
    """

def set_hook_judge(fn):
    """fn(prompt: str) -> str. Model trả JSON thô (list[float], cùng thứ tự
    hooks đưa vào). fn=None (mặc định) -- dùng rule-based score.
    """
```

### 4.1. `generate_hooks(angle, facts) -> list[str]`

Không có `_hook_generator_fn` → `_template_hooks(facts)`: 5 template cố
định ứng với 5 Hook Type **không phụ thuộc fact thật** (`CURIOSITY`,
`PAIN`, `PRICE`, `BOLD_STATEMENT`, `QUESTION`), **không đổi theo `angle`**
— đây là giới hạn cố ý của P0: bộ template chỉ tham số hoá bằng
`facts.name`/`facts.price`, không có đủ "văn phong" để tạo bản khớp riêng
từng angle một cách có ý nghĩa; viết 11 angle × 5 loại template sẽ phình
to mà chưa chắc caption hay hơn 1 bộ chung. `_template_hooks()` vì vậy
**không nhận tham số `angle`** (tránh dead param, khác `generate_hooks()`
bên ngoài nó — hàm ngoài vẫn giữ `angle` vì nhánh LLM thật có dùng, đưa
vào prompt để model biết viết hook theo góc nào). 3 Hook Type còn lại
(`CONTRAST`, `CONFESSION_STYLE`, `SURPRISING_FACT`) chỉ dùng được qua
đường LLM thật, vì §15 yêu cầu `SURPRISING_FACT` "chỉ dùng khi có fact
thật" và template cố định không có cách kiểm chứng fact nào ngoài
`facts.facts` (rủi ro chọn sai fact để "gây bất ngờ" nếu chỉ ghép chuỗi
máy móc) — an toàn hơn khi để LLM tự quyết định lúc nào dùng 3 loại này.

Có `_hook_generator_fn` → gọi với prompt đã rào delimiter (giống
`_build_extract_prompt()` ở E1, bài học từ prompt-injection fix wave —
`facts.name`/`facts.facts` đến từ dữ liệu crawl, không đáng tin), parse
JSON `list[str]`, retry tối đa 3 lần (bọc cả lỗi network/API của chính
lời gọi, không chỉ lỗi parse — áp dụng luôn bài học Finding 9 của E1 thay
vì đợi review phát hiện lại). Đúng 5 phần tử → dùng; sai số lượng/JSON
lỗi hết 3 lần → fallback `_template_hooks()` (an toàn, không bao giờ trả
danh sách rỗng).

### 4.2. `check_hook_rules(hook, facts) -> list[str]`

```python
def check_hook_rules(hook: str, facts) -> list:
    problems = list(content_facts.check_fact_safety(hook))
    flat = (hook or "").strip().lower()
    if not flat:
        problems.append("Hook rỗng")
        return problems
    for opening in _GENERIC_OPENINGS:
        if flat.startswith(opening):
            problems.append(f"Mở đầu chung chung: “{opening}”")
    if facts.name and flat == facts.name.strip().lower():
        problems.append("Hook trùng y hệt tên sản phẩm, không có điểm nhấn")
    return problems
```

Tái dùng `content_facts.check_fact_safety(hook)` cho đúng ý "không
clickbait sai sự thật" (§14) — hook cũng là 1 đoạn text có thể bịa y hệt
caption, không cần viết lại logic riêng. `[]` = hợp lệ, non-empty = loại
khỏi candidate ở bước `select_best_hook()`.

### 4.3. `score_hooks(hooks, angle, facts) -> list[float]`

Không có `_hook_judge_fn` → rule-based, mỗi hook chấm độc lập:
```python
def _rule_score(hook: str, facts) -> float:
    """0-1, cao hơn = tốt hơn. Không hard-fail theo độ dài (PTYC mục 14,
    "không dùng giới hạn từ như hard-fail cho mọi trường hợp"), chỉ trừ điểm.
    """
    if not hook.strip():
        return 0.0
    score = 1.0
    word_count = len(hook.split())
    if word_count > THREADS_HOOK_WORD_TARGET:
        score -= 0.05 * (word_count - THREADS_HOOK_WORD_TARGET)
    if facts.name and facts.name.lower() in hook.lower():
        score -= 0.2
    return max(0.0, score)
```

Có `_hook_judge_fn` → gọi 1 lần với toàn bộ `hooks` trong 1 prompt (đúng
tinh thần §46 "AI Judge có thể chấm cả 3 [ở đây là 5] trong một call"),
parse JSON `list[float]` cùng độ dài `hooks`, retry 3 lần bọc cả lỗi
network + lỗi parse (giống `generate_hooks()`), sai/hết retry → fallback
`_rule_score()` cho từng hook.

**Ranh giới rõ với E4:** đây là "hook-level AI Judge" — chấm 1 câu ngắn
trong phạm vi 1 lần gọi, khác hẳn Hybrid Scoring của E4 (chấm toàn bộ
caption đã hoàn chỉnh, gồm `hook_strength/naturalness/readability/
relevance/originality/salesy_level` theo §30, cho cả 3 variant để chọn
BEST). Không dùng chung 1 hàm, không phụ thuộc lẫn nhau.

### 4.4. `select_best_hook(angle, facts) -> dict`

```python
def select_best_hook(angle: str, facts) -> dict:
    hooks = generate_hooks(angle, facts)
    passing = [h for h in hooks if not check_hook_rules(h, facts)]
    if not passing:
        return {"hook": hooks[0], "score": 0.0, "all_rejected": True}
    scores = score_hooks(passing, angle, facts)
    best_i = max(range(len(passing)), key=lambda i: scores[i])
    return {"hook": passing[best_i], "score": scores[best_i], "all_rejected": False}
```

Nếu cả 5 hook đều fail rule check (vd extractor lỗi trả về rác) → trả hook
đầu tiên kèm `all_rejected=True`, không block cứng — không retry vô hạn
đúng tinh thần §48, để E3+ (nơi thực sự gọi hàm này trong pipeline) tự
quyết định regenerate hay dùng tạm.

## 5. Testing plan

- `select_angle_candidates()`: có discount rõ (>=5%) → `DEAL_PRICE` đầu
  list; category `gia-dung`/`phu-kien-cong-nghe` → có `USE_CASE`; category
  `thoi-trang`/`cham-soc-ca-nhan` → có `PERSONAL_RECOMMENDATION`; category
  lạ (`thu-cung`) không discount → chỉ có `PERSONAL_RECOMMENDATION`; luôn
  kết thúc bằng `PERSONAL_RECOMMENDATION`.
- `check_hook_rules()`: hook rỗng bị chặn; hook mở đầu "sản phẩm này"/"đây
  là" bị chặn; hook trùng y hệt tên sản phẩm bị chặn; hook chứa bịa trải
  nghiệm (tái dùng `check_fact_safety`) bị chặn; hook sạch pass.
- `_template_hooks()`/`generate_hooks()` (không extractor): luôn trả đúng
  5 phần tử, không phần tử nào rỗng.
- `generate_hooks()` (có generator mock): JSON hợp lệ 5 phần tử → dùng
  đúng; generator ném exception → fallback template, không propagate;
  JSON sai số lượng → fallback template.
- `score_hooks()` (rule-based, không judge): hook dài hơn 12 từ bị trừ
  điểm nhưng không về 0; hook chứa tên sản phẩm bị trừ điểm; hook rỗng =
  0.0.
- `score_hooks()` (có judge mock): JSON hợp lệ → dùng đúng thứ tự; judge
  ném exception → fallback rule-based.
- `select_best_hook()` end-to-end: mock generator + mock judge trả kết quả
  xác định trước → chọn đúng hook điểm cao nhất; toàn bộ 5 hook fail rule
  check (mock generator cố tình trả hook bịa) → trả `all_rejected=True`.
- Tương thích ngược: toàn bộ test `feat/content-engine-v2` hiện có (E1,
  358/0 + 340/0) phải giữ nguyên xanh — E2 không đụng file nào ngoài 2
  module mới + test mới.
