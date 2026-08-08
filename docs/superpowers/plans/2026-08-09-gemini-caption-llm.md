# Gemini Caption LLM Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire an optional Gemini-free-tier rewrite pass into
`core/content.py::generate()` so captions can be rewritten by a real LLM,
with zero behavior change when the feature is off and zero risk of a
network/quota failure breaking post creation.

**Architecture:** New module `core/llm_gemini.py` wraps the Google GenAI SDK
behind one function matching `content.set_llm()`'s expected signature
(`fn(prompt: str) -> str`). `adapters/factory.py` gains `get_caption_llm()`
following the exact pattern of its existing `get_source()`/`get_channel()`
env-var-driven provider selection, wired into `build_context()`.
`content.generate()` gets a safety wrapper: any exception from the LLM call,
or an LLM response missing the affiliate link, discards the LLM output and
keeps the existing deterministic draft.

**Tech Stack:** Python 3, `google-genai` SDK (pip), existing test harness.

## Global Constraints

- No network calls during `./manage.sh test` or `python3 -m acp.tests.*` —
  `ACP_CAPTION_LLM` must be unset in that environment (it already is; do not
  add it to `manage.sh`'s test invocation).
- `content.validate()` and every banned-phrase list stay untouched.
- `_fit()` still appends the disclosure line **after** any LLM rewrite — the
  LLM must never see or touch the disclosure text.
- Never print a raw exception message from the Gemini call — log only
  `type(e).__name__`, per AGENTS.md "never log secrets or full access
  tokens" (a raised SDK exception can embed the API key in its message).
- Do not read, print, or commit `ACP_GEMINI_API_KEY` — the user adds it to
  `shared/.env.local` themselves.

---

### Task 1: `core/llm_gemini.py` + `requirements.txt`

**Files:**
- Create: `core/llm_gemini.py`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: `google.genai.Client` (from the `google-genai` pip package).
- Produces: `llm_gemini.rewrite(prompt: str) -> str` — raises on any failure
  (missing key, empty response, SDK error). Task 3's `content.generate()`
  is the only caller and the only place that catches these exceptions.

- [ ] **Step 1: Add the dependency**

Read `requirements.txt` first, then append `google-genai` on its own line
(alphabetical position doesn't matter — match the file's existing style).

- [ ] **Step 2: Install it into the release venv**

```bash
cd ~/Downloads/ACP/releases/2.0/acp && .venv/bin/pip install google-genai 2>&1 | tail -5
```
Expected: `Successfully installed google-genai-...` (plus its transitive deps).

- [ ] **Step 3: Write `core/llm_gemini.py`**

```python
"""Gọi Gemini free tier để viết lại caption tự nhiên hơn từ bản nháp
deterministic của core/content.py.

Bật bằng ACP_CAPTION_LLM=gemini + ACP_GEMINI_API_KEY (lấy miễn phí ở
aistudio.google.com, không cần thẻ thanh toán) trong shared/.env.local.
Không bật thì content.py chỉ dùng template tĩnh -- không có gì đổi.

Hàm rewrite() ở đây ĐƯỢC PHÉP raise -- core/content.py::generate() là nơi
bắt exception và rơi về bản nháp deterministic (xem docstring generate()),
không phải hàm này, để lỗi không bị nuốt câm khi gọi trực tiếp lúc debug.
"""
import os


def _client():
    from google import genai
    api_key = os.environ.get("ACP_GEMINI_API_KEY")
    if not api_key:
        return None
    return genai.Client(api_key=api_key)


def rewrite(prompt: str) -> str:
    """fn(prompt) -> str theo đúng chữ ký content.set_llm() yêu cầu."""
    client = _client()
    if client is None:
        raise RuntimeError("ACP_GEMINI_API_KEY chưa được đặt")
    model = os.environ.get("ACP_GEMINI_MODEL", "gemini-2.5-flash")
    response = client.models.generate_content(model=model, contents=prompt)
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Gemini trả về rỗng")
    return text
```

- [ ] **Step 4: Manual smoke check (only if `ACP_GEMINI_API_KEY` is already set)**

```bash
cd ~/Downloads/ACP/releases/2.0/acp && .venv/bin/python -c "
import sys; sys.path.insert(0, '..')
from acp.core import llm_gemini
print(llm_gemini.rewrite('Nói \"OK\" bằng một từ.'))
"
```
Expected: prints text from Gemini, or a clear `RuntimeError` if the key is
not set yet (that error is expected at this point in the plan — Task 4
covers getting a real key from the user).

- [ ] **Step 5: Commit**

```bash
cd ~/Downloads/ACP/releases/2.0/acp
git add core/llm_gemini.py requirements.txt
git commit -m "feat: thêm core/llm_gemini.py để gọi Gemini free tier viết lại caption"
```

---

### Task 2: Wire it through `adapters/factory.py`

**Files:**
- Modify: `adapters/factory.py`

**Interfaces:**
- Consumes: `llm_gemini.rewrite` (Task 1), `core.content.set_llm()`
  (existing, signature `set_llm(fn)` where `fn(prompt: str) -> str`).
- Produces: `factory.get_caption_llm() -> callable | None` — used by
  `factory.build_context()` and directly testable.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pilot.py`, inside `test_factory()` (find it with
`grep -n "def test_factory" tests/test_pilot.py`) — append at the end of
that function, before its closing blank line:

```python
    os.environ.pop("ACP_CAPTION_LLM", None)
    check("get_caption_llm() tắt mặc định (không set ACP_CAPTION_LLM)",
          factory.get_caption_llm() is None)
    os.environ["ACP_CAPTION_LLM"] = "gemini"
    llm = factory.get_caption_llm()
    check("get_caption_llm() trả về llm_gemini.rewrite khi bật",
          llm is not None and llm.__name__ == "rewrite")
    os.environ.pop("ACP_CAPTION_LLM", None)
```

`test_pilot.py` already imports `os` and `factory` at the top (`from
acp.adapters import factory`) — no new import needed.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ~/Downloads/ACP/releases/2.0 && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot 2>&1 | grep -A1 "get_caption_llm"
```
Expected: FAIL — `AttributeError: module 'acp.adapters.factory' has no
attribute 'get_caption_llm'`.

- [ ] **Step 3: Add `get_caption_llm()` and wire it into `build_context()`**

In `adapters/factory.py`, add this function (place it near `get_channel()`,
before `build_context()`):

```python
def get_caption_llm():
    """Trả về fn(prompt)->str cho content.set_llm(), hoặc None nếu tắt.
    ACP_CAPTION_LLM=gemini bật viết lại caption bằng Gemini free tier."""
    choice = (os.environ.get("ACP_CAPTION_LLM") or "").lower()
    if choice == "gemini":
        from ..core import llm_gemini
        return llm_gemini.rewrite
    return None
```

Then modify `build_context()` — replace:

```python
def build_context(source_name: str = None) -> dict:
    """Ngữ cảnh truyền vào các job handler."""
    from ..core import storage
    return {
        "source": get_source(source_name),
        "channel": get_channel(),
        "storage": storage.get_storage(),
    }
```

with:

```python
def build_context(source_name: str = None) -> dict:
    """Ngữ cảnh truyền vào các job handler."""
    from ..core import content, storage
    content.set_llm(get_caption_llm())
    return {
        "source": get_source(source_name),
        "channel": get_channel(),
        "storage": storage.get_storage(),
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd ~/Downloads/ACP/releases/2.0 && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot 2>&1 | tail -6
```
Expected: `NNN đạt, 0 hỏng`.

- [ ] **Step 5: Commit**

```bash
cd ~/Downloads/ACP/releases/2.0/acp
git add adapters/factory.py tests/test_pilot.py
git commit -m "feat: factory.get_caption_llm() chọn provider LLM caption theo ACP_CAPTION_LLM"
```

---

### Task 3: Safety wrapper in `core/content.py::generate()`

**Files:**
- Modify: `core/content.py` (function `generate()`, and `_build_prompt()`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `_llm_fn` (module-global set via `content.set_llm()`, already
  exists).
- Produces: `content.generate(...)` keeps its exact existing signature and
  return type (str) — no caller elsewhere in the codebase needs to change.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pipeline.py`, right after `test_caption_tone()` (before
`def test_scoring():`):

```python
def test_caption_llm_safety():
    print("\nAn toàn khi bật LLM viết lại caption")
    product = {"name": "Quần linen giả váy", "current_price": 100250,
               "original_price": None, "sold_count": 0, "rating": None,
               "review_count": 0, "category_code": "thoi-trang",
               "description": ""}
    link = "https://go.isclix.com/x?sub1=abc"

    def _boom(prompt):
        raise RuntimeError("giả lập Gemini lỗi mạng")

    content.set_llm(_boom)
    try:
        caption = content.generate(product, "comparison", link, discount_pct=0.1)
    finally:
        content.set_llm(None)
    check("LLM lỗi không làm hỏng generate()", link in caption)
    check("LLM lỗi thì caption vẫn qua validate()", content.validate(caption) == [])

    def _drop_link(prompt):
        return "Caption không còn link gốc luôn, viết linh tinh."

    content.set_llm(_drop_link)
    try:
        caption2 = content.generate(product, "comparison", link, discount_pct=0.1)
    finally:
        content.set_llm(None)
    check("LLM làm mất link thì bị bỏ qua, dùng bản nháp deterministic",
          link in caption2, caption2)
```

- [ ] **Step 2: Add the call to `__main__`**

In `tests/test_pipeline.py`, find `test_caption_tone()` in the
`if __name__ == "__main__":` block and add `test_caption_llm_safety()`
right after it, before `test_scoring()`.

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd ~/Downloads/ACP/releases/2.0 && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline 2>&1 | grep -B1 -A2 "An toàn khi bật"
```
Expected: FAIL — `content.generate()` currently calls `_llm_fn(...)`
unguarded, so `_boom` raises straight through the test.

- [ ] **Step 4: Implement the safety wrapper**

In `core/content.py`, replace:

```python
    full = f"{hook_line}\n\n{body}\n\n{cta_line}\n{affiliate_link}"
    if _llm_fn:
        full = _llm_fn(_build_prompt(product, full))
    return _fit(full, disclosure)
```

with:

```python
    full = f"{hook_line}\n\n{body}\n\n{cta_line}\n{affiliate_link}"
    if _llm_fn:
        try:
            rewritten = _llm_fn(_build_prompt(product, full))
        except Exception as e:
            rewritten = None
            print(f"  ! caption LLM lỗi ({type(e).__name__}), dùng bản nháp deterministic")
        if rewritten and affiliate_link in rewritten:
            full = rewritten
    return _fit(full, disclosure)
```

- [ ] **Step 5: Strengthen `_build_prompt()`**

In `core/content.py`, in `_build_prompt()`, replace:

```python
def _build_prompt(product, draft: str) -> str:
    return (
        "Viết lại đoạn giới thiệu sản phẩm dưới đây cho tự nhiên hơn.\n"
        "RÀNG BUỘC BẮT BUỘC:\n"
        "- Chỉ dùng thông tin có trong đoạn gốc. Không thêm chi tiết nào khác.\n"
        "- KHÔNG viết như đã từng dùng sản phẩm. Không nói 'mình đã dùng', 'mình thấy'.\n"
        "- Không dùng từ tuyệt đối hoá: tốt nhất, số 1, duy nhất.\n"
        "- Không cam kết công dụng.\n"
        "- Giữ nguyên URL. Tối đa 380 ký tự.\n\n"
        f"Đoạn gốc:\n{draft}"
    )
```

with:

```python
def _build_prompt(product, draft: str) -> str:
    return (
        "Viết lại đoạn giới thiệu sản phẩm dưới đây cho tự nhiên hơn.\n"
        "RÀNG BUỘC BẮT BUỘC:\n"
        "- Chỉ dùng thông tin có trong đoạn gốc. Không thêm chi tiết nào khác.\n"
        "- KHÔNG viết như đã từng dùng sản phẩm. Không nói 'mình đã dùng', 'mình thấy'.\n"
        "- Không dùng từ tuyệt đối hoá: tốt nhất, số 1, duy nhất.\n"
        "- Không cam kết công dụng.\n"
        "- Giọng người bình thường tình cờ thấy hay nên chia sẻ lại, không phải giọng quảng cáo trang trọng.\n"
        "- Không dùng markdown (không **, không #, không gạch đầu dòng).\n"
        "- Giữ nguyên URL. Tối đa 380 ký tự.\n\n"
        f"Đoạn gốc:\n{draft}"
    )
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd ~/Downloads/ACP/releases/2.0 && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline 2>&1 | tail -6
```
Expected: `NNN đạt, 0 hỏng`.

- [ ] **Step 7: Commit**

```bash
cd ~/Downloads/ACP/releases/2.0/acp
git add core/content.py tests/test_pipeline.py
git commit -m "feat: bọc an toàn quanh _llm_fn trong content.generate() + siết prompt"
```

---

### Task 4: Full verification, get the user's API key, live smoke test

**Files:** none modified — verification only.

- [ ] **Step 1: Run the full release test suite**

```bash
cd ~/Downloads/ACP && ./manage.sh test 2>&1 | tail -10
```
Expected: `TEST_OK` — no network calls made (no `ACP_CAPTION_LLM` in the
test environment).

- [ ] **Step 2: Ask the user for their Gemini API key**

Tell the user: get a free key at https://aistudio.google.com/apikey (no
credit card), then add these two lines to `~/Downloads/ACP/shared/.env.local`
themselves (do not do this step for them — `AGENTS.md` forbids touching that
file):

```bash
export ACP_GEMINI_API_KEY=<key>
export ACP_CAPTION_LLM=gemini
```

- [ ] **Step 3: Once the user confirms the key is added, smoke-test the live call**

```bash
cd ~/Downloads/ACP && set -a && source shared/.env.local && set +a
cd ~/Downloads/ACP/releases/2.0/acp && .venv/bin/python -c "
from core import llm_gemini
print(llm_gemini.rewrite('Nói \"OK\" bằng một từ.'))
"
```
Expected: a real text response from Gemini (not a `RuntimeError`).

- [ ] **Step 4: Preview 2-3 rewritten captions before enabling live**

```bash
cd ~/Downloads/ACP && set -a && source shared/.env.local && set +a
cd ~/Downloads/ACP/releases/2.0/acp && .venv/bin/python -c "
from core import content, llm_gemini
content.set_llm(llm_gemini.rewrite)
product = {'name': 'Quần linen giả váy chất đũi tơ thiết kế cạp nhúm hàng 2 lớp_Linhchi.studio',
           'current_price': 100250, 'original_price': None, 'sold_count': 0,
           'rating': None, 'review_count': 0, 'category_code': 'thoi-trang', 'description': ''}
for code in ('comparison', 'price_drop'):
    cap = content.generate(product, code, 'https://go.isclix.com/x?sub1=abc',
                            discount_pct=0.1, hook_code='H4_CAUHOI')
    print(f'--- {code} ---'); print(cap); print()
    print('validate():', content.validate(cap))
    print()
"
```

Read the output. Confirm: affiliate link intact, `validate()` returns `[]`
(or explain to the user which rule tripped if not), tone reads like a
person sharing something, not a template.

- [ ] **Step 5: Restart the release with the new dependency and config live**

```bash
cd ~/Downloads/ACP && ./manage.sh restart 2>&1
```
Expected: `ACP_STARTED ...` then `NGROK_STARTED ...`.

- [ ] **Step 6: Report to the user**

Tell the user Gemini rewriting is live, and ask them to create one fresh
post from `/sanpham` to see it end-to-end on `/duyet`. Do not create that
post automatically (same reasoning as the caption-tone plan's Task 3).

- [ ] **Step 7: Final commit (if Step 5 produced no file changes, skip)**

No files change in this task beyond what Tasks 1-3 already committed.
