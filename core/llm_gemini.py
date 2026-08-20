"""Gọi Gemini free tier cho caption và structured Seeding generation.

Bật bằng ACP_CAPTION_LLM=gemini + ACP_GEMINI_API_KEY trong shared/.env.local.
``rewrite()`` trả text tự do cho caption; ``rewrite_json()`` dùng Gemini JSON
mode cho các caller cần JSON hợp lệ như Seeding multi-account planner.
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
    """Return Gemini output in JSON response mode for structured callers."""
    from google.genai import types

    client = _client()
    if client is None:
        raise RuntimeError("ACP_GEMINI_API_KEY chưa được đặt")
    model = os.environ.get("ACP_GEMINI_MODEL", "gemini-flash-latest")
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
        ),
    )
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Gemini trả về JSON rỗng")
    return text
