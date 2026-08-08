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
