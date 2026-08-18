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
