# Gắn Gemini free tier vào content.generate() — thiết kế

## Bối cảnh

Sau khi viết lại giọng văn template tĩnh (xem
`2026-08-09-caption-tone-redesign-design.md`), người dùng vẫn thấy caption
"quá AI, không giống chia sẻ người dùng" và muốn một LLM thật viết lại câu
chữ mỗi lần, thay vì chỉ điền chỗ trống vào template cố định.

`core/content.py` đã có sẵn scaffold cho đúng việc này:
`set_llm(fn)` + `_build_prompt(product, draft)`, được gọi trong
`generate()` nếu `_llm_fn` đã được set. Chưa có provider nào được cắm vào.

Người dùng chọn **Gemini free tier** (Google AI Studio) sau khi so sánh với
Claude/ChatGPT (đều cần key trả phí) và Ollama local (miễn phí nhưng chất
lượng tiếng Việt kém hơn, cần cài đặt nặng hơn).

## Phạm vi

Trong phạm vi:
- `core/llm_gemini.py` (mới) — gọi Gemini, không raise ra ngoài luồng chính.
- `adapters/factory.py` — thêm `get_caption_llm()`, gọi trong `build_context()`.
- `core/content.py::generate()` — bọc an toàn quanh `_llm_fn`.
- `requirements.txt` — thêm `google-genai`.

Ngoài phạm vi (không đổi):
- `content.validate()` và mọi rào chắn nội dung.
- `_build_prompt()` — giữ nguyên các ràng buộc đã có, chỉ bổ sung nhỏ (xem dưới).
- Disclosure line vẫn được `_fit()` gắn vào **sau** khi LLM chạy xong — LLM
  không bao giờ chạm vào disclosure.

## Ràng buộc an toàn bắt buộc

1. **Không network call nào chạy trong test suite.** `ACP_CAPTION_LLM` không
   được set khi `manage.sh test` chạy (chỉ set `ACP_ADAPTER=mock
   ACP_SOURCE=mock`), nên `get_caption_llm()` phải trả về `None` mặc định.
2. **Mọi lỗi từ Gemini (thiếu key, hết quota, mất mạng, response rỗng) phải
   rơi về bản nháp deterministic**, không được raise ra pipeline và làm hỏng
   việc tạo bài.
3. **Affiliate link phải còn nguyên** trong output của LLM, kiểm tra bằng
   `affiliate_link in rewritten` trước khi chấp nhận — nếu không, bỏ qua kết
   quả LLM, dùng bản nháp deterministic. Đây là lớp bảo vệ thứ hai ngoài chỉ
   dựa vào chỉ dẫn trong prompt.
4. **Không log API key** — không print exception message thô nếu nó có thể
   chứa key; chỉ log `type(e).__name__`.
5. `content.validate()` chạy sau `generate()` y hệt hiện tại, bất kể caption
   đến từ template hay từ Gemini — không có đường tắt nào bỏ qua nó.

## Thiết kế cụ thể

### `core/llm_gemini.py`

```python
import os

def _client():
    from google import genai
    api_key = os.environ.get("ACP_GEMINI_API_KEY")
    if not api_key:
        return None
    return genai.Client(api_key=api_key)

def rewrite(prompt: str) -> str:
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

`rewrite()` được phép raise — nơi bắt exception là `content.generate()`
(ràng buộc 2), không phải hàm này, để lỗi không bị nuốt câm khi debug thủ
công (gọi `rewrite()` trực tiếp từ script vẫn thấy lỗi thật).

### `adapters/factory.py`

Thêm hàm mới theo đúng pattern `get_source()`/`get_channel()`:

```python
def get_caption_llm():
    choice = (os.environ.get("ACP_CAPTION_LLM") or "").lower()
    if choice == "gemini":
        from ..core import llm_gemini
        return llm_gemini.rewrite
    return None
```

`build_context()` thêm dòng `content.set_llm(get_caption_llm())` trước khi
dựng dict trả về (import `content` cùng chỗ với `storage`).

### `core/content.py::generate()`

Thay đoạn:

```python
    full = f"{hook_line}\n\n{body}\n\n{cta_line}\n{affiliate_link}"
    if _llm_fn:
        full = _llm_fn(_build_prompt(product, full))
    return _fit(full, disclosure)
```

bằng:

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

### `_build_prompt()` — bổ sung nhỏ

Thêm 2 dòng ràng buộc vào prompt hiện có (không xoá dòng nào cũ):

```python
        "- Giọng người bình thường tình cờ thấy hay nên chia sẻ lại, không phải giọng quảng cáo trang trọng.\n"
        "- Không dùng markdown (không **, không #, không gạch đầu dòng).\n"
```

### `requirements.txt`

Thêm dòng `google-genai`.

## Kiểm tra không hồi quy

- `tests/test_pilot.py::test_factory` đã kiểm `factory.build_context` trả
  đủ `source`/`channel`/`storage` — thêm việc `set_llm` không phá field nào
  trong dict đó, không cần test riêng vì hành vi không quan sát được qua
  dict trả về.
- Thêm test mới trong `tests/test_pipeline.py`: `_llm_fn` raise lỗi thì
  `content.generate()` vẫn trả về caption hợp lệ (không raise, `validate()`
  rỗng) — mô phỏng Gemini lỗi bằng một hàm giả ném exception.
- Thêm test: `_llm_fn` trả về chuỗi KHÔNG chứa affiliate link thì bị bỏ qua,
  caption vẫn chứa link gốc.
- `content._llm_fn` phải được set về `None` sau mỗi test đụng tới
  `content.set_llm()` để không rò sang test khác (bài học từ bug
  `test_web_security()` đã sửa hôm nay).

## Xác minh

1. `./manage.sh test` — không set `ACP_CAPTION_LLM`, không gọi mạng, vẫn
   `TEST_OK`.
2. Sau khi người dùng thêm `ACP_GEMINI_API_KEY` vào `shared/.env.local`:
   chạy script gọi `llm_gemini.rewrite()` trực tiếp với 1 câu test, xác nhận
   nhận được text thật từ Gemini (không phải lỗi thiếu key).
3. In thử 2-3 caption qua `content.generate()` với `content.set_llm(llm_gemini.rewrite)`
   bật thủ công, đọc bằng mắt trước khi bật `ACP_CAPTION_LLM=gemini` thật
   trong `.env.local`.
