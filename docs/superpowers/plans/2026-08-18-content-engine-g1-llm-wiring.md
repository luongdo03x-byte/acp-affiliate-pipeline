# Gắn LLM Gemini thật cho Content Engine v2 (G1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đăng ký Gemini thật cho cả 6 hook pluggable của Content Engine v2 (E1-E4: extractor, hook_generator, hook_judge, body_generator, variant_judge, hybrid_judge), tái dùng đúng pattern đã chạy production của v1 caption engine, sau cờ riêng `ACP_CONTENT_ENGINE_LLM=gemini` — mặc định tắt, không đổi baseline hiện có.

**Architecture:** Thêm `rewrite_json()` cạnh `rewrite()` có sẵn trong `core/llm_gemini.py` (Gemini JSON mode) + `get_content_engine_llm()` cạnh `get_caption_llm()` có sẵn trong `adapters/factory.py` + 6 lời gọi `set_*()` trong `web/server.py::create_app()`, cạnh dòng `content.set_llm(...)` có sẵn.

**Tech Stack:** Python 3, `google-genai` SDK (đã cài trong `.venv`, đã dùng cho v1).

**Spec:** `docs/superpowers/specs/2026-08-18-content-engine-g1-llm-wiring-design.md`

## Global Constraints

- TUYỆT ĐỐI không sửa `core/content_facts.py`, `core/content_angle.py`, `core/content_hook.py`, `core/content_variant.py`, `core/content_checker.py`, `core/content_scoring.py` (E1-E4, đã merge + review) — chỉ GỌI các hàm `set_*` public của chúng từ `web/server.py`.
- KHÔNG sửa `core/llm_gemini.py::rewrite()`/`core/content.py`/`adapters/factory.py::get_caption_llm()` hiện có (v1, đang chạy production thật) — chỉ THÊM hàm mới cạnh chúng.
- Cờ MỚI, RIÊNG: `ACP_CONTENT_ENGINE_LLM=gemini` — không dùng chung/không đổi ý nghĩa `ACP_CAPTION_LLM` (v1).
- Mặc định (`ACP_CONTENT_ENGINE_LLM` không set) phải giữ đúng behavior rule-based/template hiện có của toàn bộ E1-E6 — không đổi baseline test.
- Test dùng bộ harness sẵn có của repo (`check(name, cond, detail)`, list `PASS`/`FAIL` toàn cục, đăng ký tường minh trong `if __name__ == "__main__":`) — không dùng pytest.
- Chạy test bằng: `cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g1 && acp/.venv/bin/python3 -m acp.tests.<module>`.
- Baseline trước G1: `test_pipeline.py` 589 PASS/0 FAIL, `test_pilot.py` 501 PASS/0 FAIL, `test_product_automation.py` (7 nhóm không tính `pipeline`) 79 PASS/0 FAIL, `test_manage.py` 4/4 OK.
- Commit message tiếng Việt có dấu đầy đủ.
- KHÔNG gọi Gemini thật trong test — mock `google.genai.Client`.

---

### Task 1: `core/llm_gemini.py` — thêm `rewrite_json()`

**Files:**
- Modify: `core/llm_gemini.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `rewrite_json(prompt: str) -> str` — cùng chữ ký `fn(prompt)->str` các `set_*()` của Content Engine v2 yêu cầu, dùng Gemini JSON mode.

- [ ] **Step 1: Viết 3 test (sẽ fail vì hàm chưa tồn tại)**

Thêm vào `tests/test_pipeline.py`, cuối file (trước khối `if __name__ == "__main__":`):

```python
class _FakeGeminiResponse:
    def __init__(self, text):
        self.text = text


class _FakeGeminiClient:
    """Giả genai.Client -- ghi lại đúng tham số generate_content() nhận
    được để test xác nhận rewrite_json() gọi đúng, không gọi API thật."""
    def __init__(self, *a, **kw):
        self.models = self
        self.last_call = None
        self._response_text = '{"ok": true}'

    def generate_content(self, model, contents, config=None):
        self.last_call = {"model": model, "contents": contents, "config": config}
        return _FakeGeminiResponse(self._response_text)


def test_rewrite_json_uses_gemini_json_mode():
    print("\nrewrite_json() gọi Gemini với response_mime_type=application/json")
    from acp.core import llm_gemini
    import google.genai as genai_module
    os.environ["ACP_GEMINI_API_KEY"] = "fake-key-test"
    original_client_cls = genai_module.Client
    fake = _FakeGeminiClient()
    genai_module.Client = lambda api_key=None: fake
    try:
        result = llm_gemini.rewrite_json("prompt kiểm thử")
        check("trả đúng text từ response", result == '{"ok": true}', result)
        check("gọi đúng model mặc định", fake.last_call["model"] == "gemini-flash-latest", fake.last_call)
        check("dùng đúng prompt truyền vào", fake.last_call["contents"] == "prompt kiểm thử")
        check("dùng Gemini JSON mode",
              fake.last_call["config"].response_mime_type == "application/json",
              fake.last_call["config"])
    finally:
        genai_module.Client = original_client_cls
        os.environ.pop("ACP_GEMINI_API_KEY", None)


def test_rewrite_json_raises_when_api_key_missing():
    print("\nrewrite_json() raise rõ khi thiếu ACP_GEMINI_API_KEY, không nuốt câm")
    from acp.core import llm_gemini
    os.environ.pop("ACP_GEMINI_API_KEY", None)
    try:
        llm_gemini.rewrite_json("prompt")
        check("phải raise RuntimeError khi thiếu API key", False)
    except RuntimeError as e:
        check("raise đúng thông báo", "ACP_GEMINI_API_KEY" in str(e), str(e))


def test_rewrite_json_raises_when_response_empty():
    print("\nrewrite_json() raise rõ khi Gemini trả rỗng, không trả chuỗi rỗng câm lặng")
    from acp.core import llm_gemini
    import google.genai as genai_module
    os.environ["ACP_GEMINI_API_KEY"] = "fake-key-test"
    original_client_cls = genai_module.Client
    fake = _FakeGeminiClient()
    fake._response_text = ""
    genai_module.Client = lambda api_key=None: fake
    try:
        try:
            llm_gemini.rewrite_json("prompt")
            check("phải raise RuntimeError khi response rỗng", False)
        except RuntimeError as e:
            check("raise đúng thông báo rỗng", "rỗng" in str(e), str(e))
    finally:
        genai_module.Client = original_client_cls
        os.environ.pop("ACP_GEMINI_API_KEY", None)
```

`_FakeGeminiResponse`/`_FakeGeminiClient` KHÔNG đăng ký trong `__main__` (không phải test, là helper dùng chung).

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g1 && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: `AttributeError: module 'acp.core.llm_gemini' has no attribute 'rewrite_json'`

- [ ] **Step 3: Viết `rewrite_json()` trong `core/llm_gemini.py`**

Thêm vào cuối file, sau `rewrite()` có sẵn (KHÔNG sửa `rewrite()`/`_client()` hiện có):

```python
def rewrite_json(prompt: str) -> str:
    """fn(prompt) -> str theo đúng chữ ký các set_*() của Content Engine
    v2 (E1-E4) yêu cầu -- model PHẢI trả JSON hợp lệ, dùng Gemini JSON
    mode thay vì text thô như rewrite() (v1) để giảm rủi ro model bọc
    markdown code-fence (```json ... ```) làm vỡ json.loads() ở phía gọi.
    Callers (E1-E4) đã tự retry tối đa 3 lần + fallback khi parse lỗi --
    hàm này KHÔNG tự retry, ĐƯỢC PHÉP raise, giống rewrite() ở trên.
    """
    from google.genai import types
    client = _client()
    if client is None:
        raise RuntimeError("ACP_GEMINI_API_KEY chưa được đặt")
    model = os.environ.get("ACP_GEMINI_MODEL", "gemini-flash-latest")
    response = client.models.generate_content(
        model=model, contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Gemini trả về rỗng")
    return text
```

- [ ] **Step 4: Đăng ký 3 test, chạy lại**

Thêm 3 hàm (không phải 2 helper class) vào danh sách lời gọi cuối `tests/test_pipeline.py`.

Run: `cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g1 && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: toàn bộ PASS, 0 FAIL. Baseline 589 + 8 check mới (4+1+2 -- đếm đúng theo số `check()` thực tế trong 3 test) = tổng mới, không hàm cũ nào hỏng.

- [ ] **Step 5: Commit**

```bash
git add core/llm_gemini.py tests/test_pipeline.py
git commit -m "feat: rewrite_json() -- gọi Gemini JSON mode cho 6 hook pluggable Content Engine v2 (G1)"
```

---

### Task 2: `adapters/factory.py` — thêm `get_content_engine_llm()`

**Files:**
- Modify: `adapters/factory.py`
- Test: `tests/test_pilot.py`

**Interfaces:**
- Consumes: `llm_gemini.rewrite_json` (Task 1).
- Produces: `get_content_engine_llm() -> callable | None`.

- [ ] **Step 1: Viết 2 check mới (sẽ fail vì hàm chưa tồn tại)**

Thêm vào `tests/test_pilot.py`, ngay sau dòng `os.environ.pop("ACP_CAPTION_LLM", None)` cuối cùng trong `test_factory()` (dòng chứa `check("get_caption_llm() trả về llm_gemini.rewrite khi bật", ...)` ngay phía trên):

```python
    os.environ.pop("ACP_CONTENT_ENGINE_LLM", None)
    check("get_content_engine_llm() tắt mặc định (không set ACP_CONTENT_ENGINE_LLM)",
          factory.get_content_engine_llm() is None)
    os.environ["ACP_CONTENT_ENGINE_LLM"] = "gemini"
    llm2 = factory.get_content_engine_llm()
    check("get_content_engine_llm() trả về llm_gemini.rewrite_json khi bật",
          llm2 is not None and llm2.__name__ == "rewrite_json")
    os.environ.pop("ACP_CONTENT_ENGINE_LLM", None)
```

(Thêm NGAY TRONG hàm `test_factory()` có sẵn, không tạo hàm test mới, không cần đăng ký thêm trong `__main__`.)

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g1 && acp/.venv/bin/python3 -m acp.tests.test_pilot`
Expected: `AttributeError: module 'acp.adapters.factory' has no attribute 'get_content_engine_llm'`

- [ ] **Step 3: Viết `get_content_engine_llm()` trong `adapters/factory.py`**

Thêm ngay dưới `get_caption_llm()` có sẵn (KHÔNG sửa `get_caption_llm()`):

```python
def get_content_engine_llm():
    """Trả về fn(prompt)->str cho 6 set_*() của Content Engine v2
    (core/content_facts.py, content_hook.py, content_variant.py,
    content_checker.py, content_scoring.py), hoặc None nếu tắt.
    ACP_CONTENT_ENGINE_LLM=gemini bật -- CỜ RIÊNG, độc lập với
    ACP_CAPTION_LLM (v1) vì khối lượng gọi khác hẳn nhau (~13 lần/bài
    so với 1 lần/bài của v1)."""
    choice = (os.environ.get("ACP_CONTENT_ENGINE_LLM") or "").lower()
    if choice == "gemini":
        from ..core import llm_gemini
        return llm_gemini.rewrite_json
    return None
```

- [ ] **Step 4: Chạy lại, xác nhận pass**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g1 && acp/.venv/bin/python3 -m acp.tests.test_pilot`
Expected: toàn bộ PASS, 0 FAIL. Baseline 501 + 2 check mới = 503, không hàm cũ nào hỏng.

- [ ] **Step 5: Commit**

```bash
git add adapters/factory.py tests/test_pilot.py
git commit -m "feat: get_content_engine_llm() -- cờ ACP_CONTENT_ENGINE_LLM riêng cho 6 hook Content Engine v2 (G1)"
```

---

### Task 3: `web/server.py::create_app()` — đăng ký 6 hook

**Files:**
- Modify: `web/server.py`
- Test: `tests/test_pilot.py`

**Interfaces:**
- Consumes: `factory.get_content_engine_llm()` (Task 2), `content_facts.set_extractor()`, `content_hook.set_hook_generator()`/`set_hook_judge()`, `content_variant.set_body_generator()`, `content_checker.set_variant_judge()`, `content_scoring.set_hybrid_judge()` (E1-E4, không sửa các file này).

- [ ] **Step 1: Viết 2 test (sẽ fail vì chưa đăng ký)**

Thêm import `content_scoring` vào dòng import `core.*` có sẵn ở đầu `tests/test_pilot.py` (dòng ~27, hiện là `from acp.core import content, playbook, valuepost`):

```python
from acp.core import content, content_checker, content_facts, content_hook, content_scoring, content_variant, playbook, valuepost  # noqa: E402
```

Thêm 2 hàm test vào `tests/test_pilot.py`, ngay sau `test_caption_llm_wired_regardless_of_manual_flow()` (tìm dòng cuối hàm đó, chứa khối `finally:` khôi phục `_saved`):

```python
def test_content_engine_llm_wired_at_create_app():
    """create_app() phải bật đủ 6 hook Content Engine v2 (extractor,
    hook_generator, hook_judge, body_generator, variant_judge,
    hybrid_judge) khi ACP_CONTENT_ENGINE_LLM=gemini -- cùng lý do đặt ở
    create_app() như content.set_llm() phía trên (G1)."""
    print("\nLLM Content Engine v2 được bật đủ 6 hook tại create_app()")
    _saved = {k: os.environ.get(k) for k in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY")}
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    os.environ["ACP_CONTENT_ENGINE_LLM"] = "gemini"
    content_facts.set_extractor(None)
    content_hook.set_hook_generator(None)
    content_hook.set_hook_judge(None)
    content_variant.set_body_generator(None)
    content_checker.set_variant_judge(None)
    content_scoring.set_hybrid_judge(None)
    try:
        from acp.web.server import create_app
        create_app()
        check("extractor được bật", content_facts._extractor_fn is not None
              and content_facts._extractor_fn.__name__ == "rewrite_json")
        check("hook_generator được bật", content_hook._hook_generator_fn is not None
              and content_hook._hook_generator_fn.__name__ == "rewrite_json")
        check("hook_judge được bật", content_hook._hook_judge_fn is not None
              and content_hook._hook_judge_fn.__name__ == "rewrite_json")
        check("body_generator được bật", content_variant._body_generator_fn is not None
              and content_variant._body_generator_fn.__name__ == "rewrite_json")
        check("variant_judge được bật", content_checker._variant_judge_fn is not None
              and content_checker._variant_judge_fn.__name__ == "rewrite_json")
        check("hybrid_judge được bật", content_scoring._hybrid_judge_fn is not None
              and content_scoring._hybrid_judge_fn.__name__ == "rewrite_json")
    finally:
        content_facts.set_extractor(None)
        content_hook.set_hook_generator(None)
        content_hook.set_hook_judge(None)
        content_variant.set_body_generator(None)
        content_checker.set_variant_judge(None)
        content_scoring.set_hybrid_judge(None)
        os.environ.pop("ACP_CONTENT_ENGINE_LLM", None)
        for k, v in _saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_content_engine_llm_not_wired_when_flag_off():
    """Không set ACP_CONTENT_ENGINE_LLM -- create_app() không đụng gì tới
    6 hook (giữ nguyên None), không đổi baseline rule-based/template
    của toàn bộ E1-E6."""
    print("\ncreate_app() KHÔNG bật hook nào khi ACP_CONTENT_ENGINE_LLM không set")
    _saved = {k: os.environ.get(k) for k in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY")}
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    os.environ.pop("ACP_CONTENT_ENGINE_LLM", None)
    content_facts.set_extractor(None)
    content_hook.set_hook_generator(None)
    content_hook.set_hook_judge(None)
    content_variant.set_body_generator(None)
    content_checker.set_variant_judge(None)
    content_scoring.set_hybrid_judge(None)
    try:
        from acp.web.server import create_app
        create_app()
        check("extractor vẫn None", content_facts._extractor_fn is None)
        check("hook_generator vẫn None", content_hook._hook_generator_fn is None)
        check("hook_judge vẫn None", content_hook._hook_judge_fn is None)
        check("body_generator vẫn None", content_variant._body_generator_fn is None)
        check("variant_judge vẫn None", content_checker._variant_judge_fn is None)
        check("hybrid_judge vẫn None", content_scoring._hybrid_judge_fn is None)
    finally:
        for k, v in _saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g1 && acp/.venv/bin/python3 -m acp.tests.test_pilot`
Expected: checks trong `test_content_engine_llm_wired_at_create_app()` fail (cả 6 vẫn `None` vì `create_app()` chưa gọi `set_*`).

- [ ] **Step 3: Sửa `web/server.py`**

Thêm `content_scoring` vào dòng import có sẵn (dòng 32):
```python
from ..core import content_angle, content_checker, content_facts, content_hook, content_platform, content_scoring, content_variant
```

Thêm đoạn đăng ký ngay sau dòng `content.set_llm(factory.get_caption_llm())` có sẵn trong `create_app()`:

```python

    # Gắn LLM thật cho Content Engine v2 (G1) -- cùng lý do đặt ở
    # create_app() như dòng content.set_llm() ở trên: luồng nhập Shopee
    # affiliate thủ công không gọi build_context(), đặt ở đây đảm bảo
    # mọi route đều thấy. ACP_CONTENT_ENGINE_LLM=gemini bật, mặc định
    # tắt (None) -- toàn bộ E1-E6 giữ nguyên hành vi rule-based/template
    # khi không bật, không đổi baseline test hiện có.
    content_engine_llm = factory.get_content_engine_llm()
    content_facts.set_extractor(content_engine_llm)
    content_hook.set_hook_generator(content_engine_llm)
    content_hook.set_hook_judge(content_engine_llm)
    content_variant.set_body_generator(content_engine_llm)
    content_checker.set_variant_judge(content_engine_llm)
    content_scoring.set_hybrid_judge(content_engine_llm)
```

- [ ] **Step 4: Đăng ký 2 test, chạy lại**

Thêm 2 hàm vào danh sách lời gọi cuối `tests/test_pilot.py`, ngay sau `test_caption_llm_wired_regardless_of_manual_flow()`.

Run: `cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g1 && acp/.venv/bin/python3 -m acp.tests.test_pilot`
Expected: toàn bộ PASS, 0 FAIL. Baseline 503 (sau Task 2) + 12 check mới (6+6) = 515, không hàm cũ nào hỏng (đặc biệt: `test_caption_llm_wired_regardless_of_manual_flow()` vẫn PASS y hệt, vì Task 3 không đụng dòng `content.set_llm(...)` có sẵn).

- [ ] **Step 5: Commit**

```bash
git add web/server.py tests/test_pilot.py
git commit -m "feat: create_app() đăng ký đủ 6 hook LLM Gemini cho Content Engine v2 (G1)"
```

---

### Task 4: Regression toàn diện + Definition of Done

**Files:**
- Test: không tạo file mới, chỉ chạy lại toàn bộ 4 file test hiện có của `main`.

**Interfaces:**
- Consumes: toàn bộ hệ thống Content Engine v2 (E1-E6) + G1 + v1 caption engine + publish worker fail-safe.

- [ ] **Step 1: Chạy toàn bộ 4 file test hiện có**

Run:
```bash
cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g1
acp/.venv/bin/python3 -m acp.tests.test_pipeline
acp/.venv/bin/python3 -m acp.tests.test_pilot
for g in docs migration client service cli web worker; do
  acp/.venv/bin/python3 -m acp.tests.test_product_automation "$g"
done
acp/.venv/bin/python3 -m acp.tests.test_manage
```

Expected: `test_pipeline.py` 589+8=597 PASS/0 FAIL, `test_pilot.py` 501+2+12=515 PASS/0 FAIL, `test_product_automation.py` (7 nhóm) 79 PASS/0 FAIL không đổi (G1 không đụng file/logic publish worker), `test_manage.py` 4/4 OK. KHÔNG chạy nhóm `pipeline` của `test_product_automation.py` -- lỗi `KeyError('publishers')` đã xác nhận có sẵn trên `main` từ trước, không thuộc phạm vi G1, không kỳ vọng sửa ở đây.

- [ ] **Step 2: Đối chiếu Definition of Done (spec G1 mục 1-2) -- tự kiểm bằng tay, không phải code**

Xác nhận từng dòng:
- Cả 6 hook (`extractor`, `hook_generator`, `hook_judge`, `body_generator`, `variant_judge`, `hybrid_judge`) đăng ký đúng bằng `rewrite_json`, KHÔNG hàm nào bị bỏ sót: đọc lại đoạn code Task 3 Step 3, đếm đủ 6 dòng `set_*`.
- `ACP_CONTENT_ENGINE_LLM` độc lập với `ACP_CAPTION_LLM`: grep `ACP_CONTENT_ENGINE_LLM` trong `adapters/factory.py`, xác nhận không xuất hiện trong `get_caption_llm()`.
- Không sửa file nào trong E1-E4: `git diff --stat <merge-base>..HEAD -- core/content_facts.py core/content_angle.py core/content_hook.py core/content_variant.py core/content_checker.py core/content_scoring.py` phải rỗng.
- Không sửa `rewrite()`/`get_caption_llm()`/dòng `content.set_llm(...)` có sẵn: diff 3 file `core/llm_gemini.py`/`adapters/factory.py`/`web/server.py` chỉ có phần THÊM, không có dòng `-` nào trong các hàm/dòng đó.
- Mặc định tắt: test Task 3's `test_content_engine_llm_not_wired_when_flag_off()` PASS.

Không cần code thêm cho step này — chỉ xác nhận bằng đọc code + kết quả test, ghi vào báo cáo cuối cùng.

- [ ] **Step 3: Không cần commit** (Task 4 không tạo thay đổi code/test mới, chỉ xác nhận)
