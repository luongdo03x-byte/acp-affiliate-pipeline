"""Gọi Gemini free tier để viết lại caption/nội dung tự nhiên hơn từ bản
nháp deterministic, cho 2 nơi dùng độc lập:

- ACP_CAPTION_LLM=gemini -- rewrite() (v1), core/content.py dùng.
- ACP_CONTENT_ENGINE_LLM=gemini -- rewrite_json() (G1), 6 hook của Content
  Engine v2 (E1-E4: content_facts/content_angle/content_hook/
  content_variant/content_checker/content_scoring) dùng.

Cả 2 cờ đều cần ACP_GEMINI_API_KEY (lấy miễn phí ở aistudio.google.com,
không cần thẻ thanh toán) trong shared/.env.local; tắt cờ nào thì phần đó
chỉ dùng template/logic tĩnh -- không có gì đổi.

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
    # "gemini-flash-latest" là alias Google tự trỏ vào bản flash hiện hành --
    # tránh phải sửa code mỗi khi Google khoá một model cụ thể cho tài khoản
    # mới (đã gặp với gemini-2.5-flash lúc build tính năng này).
    model = os.environ.get("ACP_GEMINI_MODEL", "gemini-flash-latest")
    response = client.models.generate_content(model=model, contents=prompt)
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Gemini trả về rỗng")
    return text


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
        print("  ! Content Engine v2 LLM lỗi: ACP_GEMINI_API_KEY chưa được đặt")
        raise RuntimeError("ACP_GEMINI_API_KEY chưa được đặt")
    model = os.environ.get("ACP_GEMINI_MODEL", "gemini-flash-latest")
    # timeout=30000ms -- SDK cài trong .venv mặc định KHÔNG timeout; không
    # chặn thì 1 request tạo bài có thể xếp hàng tới ~13 lần gọi hook x tối
    # đa 3 lần retry mỗi hook, treo cả tiến trình Flask đơn luồng nếu Gemini
    # bị đứng.
    response = client.models.generate_content(
        model=model, contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            http_options=types.HttpOptions(timeout=30000),
        ),
    )
    text = (response.text or "").strip()
    if not text:
        print("  ! Content Engine v2 LLM lỗi: Gemini trả về rỗng")
        raise RuntimeError("Gemini trả về rỗng")
    return text
