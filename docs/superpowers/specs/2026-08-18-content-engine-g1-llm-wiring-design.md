# ACP 2.0 — Thiết kế Gắn LLM thật cho Content Engine v2 (G1)

**Ngày:** 2026-08-18
**Trạng thái:** Thiết kế đã được chốt trong hội thoại; chờ review bản spec trước khi lập implementation plan.
**Thuộc:** G1 trong 4 phần (G1 → G2 → G3 → G4) — bước tiếp theo sau khi
Content Engine v2 (E1-E6) đã merge vào `main` và đang chạy live, cờ
`content_engine_v2_enabled` mặc định tắt. G1 gắn LLM thật vào 6 hook
pluggable của E1-E4 (đang dùng fallback rule-based/template). G2 (dọn
kiến trúc regenerate ra khỏi route), G3 (re-score + re-fact-check sau
regenerate), G4 (polish CSS/N+1/audit) là các phần sau, không thuộc spec
này.

## 1. Mục tiêu

E1-E4 đã thiết kế 6 điểm mở (`set_extractor`, `set_hook_generator`,
`set_hook_judge`, `set_body_generator`, `set_variant_judge`,
`set_hybrid_judge`) — tất cả cùng chữ ký `fn(prompt: str) -> str`, model
trả JSON thô, `fn=None` (mặc định) dùng heuristic/template/rule-based,
không bịa điểm AI giả. Đến giờ chưa hàm nào được đăng ký — toàn bộ hệ
thống chạy deterministic. G1 đăng ký cả 6 bằng Gemini thật, tái dùng
đúng pattern đã chạy production cho v1 caption engine
(`core/llm_gemini.py` + `adapters/factory.py::get_caption_llm()` +
`web/server.py::create_app()`'s `content.set_llm(...)`).

**Ranh giới cứng đã chốt:**
- TUYỆT ĐỐI không sửa `core/content_facts.py`, `core/content_angle.py`,
  `core/content_hook.py`, `core/content_variant.py`,
  `core/content_checker.py`, `core/content_scoring.py`
  (E1-E4, đã merge + review) — chỉ GỌI các hàm `set_*` public của chúng
  từ `web/server.py`, không sửa file nào trong số này.
- KHÔNG sửa `core/content.py`/`core/llm_gemini.py::rewrite()` hiện có
  (v1 caption engine, đang chạy production thật) — chỉ THÊM hàm mới
  `rewrite_json()` cạnh `rewrite()`, không đổi hành vi cũ.
- KHÔNG đổi ý nghĩa cờ `ACP_CAPTION_LLM` hiện có (v1) — dùng cờ MỚI,
  riêng, `ACP_CONTENT_ENGINE_LLM=gemini` cho cả 6 hook của Content
  Engine v2, bật/tắt độc lập với v1.
- Vẫn giữ nguyên nguyên tắc "mock-first": `fn=None` vẫn phải là hành vi
  mặc định khi `ACP_CONTENT_ENGINE_LLM` không phải `"gemini"` — không
  đổi behavior test suite hiện có (E1-E6 test hiện tại 100% chạy với
  `fn=None`, không được phép silently đổi baseline).

## 2. Phạm vi

### Trong phạm vi
- `core/llm_gemini.py`: thêm `rewrite_json(prompt: str) -> str` — dùng
  Gemini JSON mode (`response_mime_type="application/json"`), tách
  riêng khỏi `rewrite()` (v1, text thô) vì 2 nhu cầu output khác nhau.
- `adapters/factory.py`: thêm `get_content_engine_llm()` — đọc
  `ACP_CONTENT_ENGINE_LLM=gemini`, trả `llm_gemini.rewrite_json` hoặc
  `None`.
- `web/server.py::create_app()`: đăng ký cả 6 hàm `set_*` bằng
  `get_content_engine_llm()`, đặt cạnh dòng `content.set_llm(...)` có
  sẵn — cùng lý do "đặt ở `create_app()`, không chỉ trong
  `build_context()`" đã có sẵn trong comment của dòng đó.
- Test cho `rewrite_json()` (mock `genai.Client`, không gọi API thật
  trong test) + test cho `get_content_engine_llm()` (giống khuôn test có
  sẵn của `get_caption_llm()` ở `tests/test_pilot.py`) + test xác nhận
  `create_app()` đăng ký đủ 6 hàm khi cờ bật, KHÔNG đăng ký gì khi cờ tắt
  (test qua state module-level, giống khuôn `test_pilot.py:520` có sẵn
  cho `content._llm_fn`).

### Ngoài phạm vi (G2-G4, hoặc mãi mãi ngoài phạm vi)
- Đổi bất kỳ logic bên trong E1-E4 (cách build prompt, cách parse JSON,
  retry logic) — đã đúng, đã review, không đụng.
- Rate-limit/backoff tinh vi hơn retry 3 lần đã có sẵn trong E1-E4 (mỗi
  hàm judge/generator tự retry tối đa 3 lần trước khi rơi về fallback) —
  đủ dùng cho quy mô thao tác thủ công của operator, không cần thêm
  hàng đợi/token bucket.
- Cache kết quả LLM giữa các lần gọi — YAGNI, chưa có nhu cầu rõ ràng.
- Đổi model Gemini theo từng hook riêng (vd model rẻ hơn cho judge, đắt
  hơn cho generator) — dùng chung 1 biến môi trường `ACP_GEMINI_MODEL`
  đã có sẵn cho cả v1 lẫn v2, đơn giản hoá vận hành.

## 3. `core/llm_gemini.py` — thêm `rewrite_json()`

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

`_client()` (đã có sẵn, đọc `ACP_GEMINI_API_KEY`) dùng chung cho cả
`rewrite()` và `rewrite_json()` — không nhân bản logic đọc API key.

## 4. `adapters/factory.py` — thêm `get_content_engine_llm()`

Đặt ngay dưới `get_caption_llm()` có sẵn, cùng khuôn:

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

## 5. `web/server.py::create_app()` — đăng ký 6 hàm

Đặt ngay sau dòng `content.set_llm(factory.get_caption_llm())` có sẵn
(dòng ~122), thêm import `content_facts, content_hook, content_variant,
content_checker, content_scoring` ở đầu file nếu chưa có đủ:

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

Dùng CHUNG 1 biến `content_engine_llm` (không gọi `get_content_engine_llm()`
6 lần) — tránh 6 lần đọc env var/tạo closure khác nhau không cần thiết,
và đảm bảo cả 6 hàm cùng bật/tắt đồng thời (không có trạng thái lệch
nhau giữa các hook).

## 6. Testing plan

- `rewrite_json()`: mock `google.genai.Client` (theo đúng khuôn mock đã
  dùng cho `rewrite()` nếu có, hoặc mock `core.llm_gemini._client`) —
  xác nhận gọi `generate_content()` với `config.response_mime_type ==
  "application/json"`, trả đúng `.text.strip()`; raise đúng khi thiếu
  API key hoặc response rỗng.
- `get_content_engine_llm()`: giống khuôn `test_pilot.py` có sẵn cho
  `get_caption_llm()` — tắt mặc định khi không set
  `ACP_CONTENT_ENGINE_LLM`, trả `llm_gemini.rewrite_json` khi set
  `"gemini"`.
- `create_app()` đăng ký đủ 6 hàm khi cờ bật: kiểm qua state
  module-level của từng module (`content_facts._extractor_fn`,
  `content_hook._hook_generator_fn`, v.v. — đọc trực tiếp biến private,
  giống khuôn `test_pilot.py:538` đã kiểm `content._llm_fn`), reset về
  `None` sau test (tránh rò rỉ sang test khác, đúng bài học rút ra từ
  E6's Task 2 fix round 1-2).
- `create_app()` KHÔNG đăng ký gì (cả 6 hàm vẫn `None`) khi
  `ACP_CONTENT_ENGINE_LLM` không set — xác nhận baseline không đổi.
- Tương thích ngược: toàn bộ test suite hiện có của `main` (4 file:
  `test_pipeline.py`, `test_pilot.py`, `test_product_automation.py`,
  `test_manage.py`) phải giữ nguyên PASS 100% sau khi thêm G1 — không
  test nào trong số đó set `ACP_CONTENT_ENGINE_LLM`, nên phải tiếp tục
  chạy ở nhánh `fn=None` y hệt trước G1.
