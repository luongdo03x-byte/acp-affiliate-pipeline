"""Gọi OpenAI làm lớp viết lại caption/nội dung.

GPT-5.6 dùng Responses API stateless; model cũ giữ Chat Completions để tương
thích. Cả hai đường giữ nguyên chữ ký rewrite()/rewrite_json() cho callers.
song song và thay thế được cho Gemini -- cùng hai chữ ký:

- ACP_CAPTION_LLM=openai -- rewrite() (v1), core/content.py dùng.
- ACP_CONTENT_ENGINE_LLM=openai -- rewrite_json() (G1), Content Engine v2.
- ACP_CAPTION_LLM=openai -- rewrite_json() cho Seeding multi-account planner.

Cần OPENAI_API_KEY đặt vào shared/.env.local dưới tên ACP_OPENAI_API_KEY;
tắt cờ nào thì phần đó rơi về template/logic tĩnh như khi chưa có LLM.

Hàm ở đây ĐƯỢC PHÉP raise -- nơi gọi (content.generate(), E1-E4, seeding)
là nơi bắt exception và fallback; callers đã tự retry tối đa 3 lần nên đây
KHÔNG tự retry. Không log key hay nguyên câu request -- chỉ log type lỗi.
"""
import os

import requests

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
_TIMEOUT_SECONDS = 30


def _config():
    from . import openai_settings

    api_key = openai_settings.get_api_key()
    if not api_key:
        return None, None, None
    model = openai_settings.get_model()
    base_url = (os.environ.get("ACP_OPENAI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    return api_key, model, base_url


def _raise_http_error(response):
    if response.status_code == 200:
        return
    # Không đưa nguyên body vào exception -- có thể chứa echo của key hoặc
    # payload nhạy cảm. Chỉ giữ error.code/type chuẩn của OpenAI để operator
    # phân biệt rate limit với hết quota.
    detail = ""
    try:
        error = response.json().get("error") or {}
        code = str(error.get("code") or error.get("type") or "")
        if code.replace("_", "").replace("-", "").isalnum():
            detail = f" ({code})"
    except (AttributeError, TypeError, ValueError):
        pass
    raise RuntimeError(f"OpenAI HTTP {response.status_code}{detail}")


def _chat(api_key, model, base_url, prompt, json_mode):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": float(os.environ.get("ACP_OPENAI_TEMPERATURE", "0.9")),
    }
    if json_mode:
        # json_object buộc model trả JSON hợp lệ -- tương đương Gemini JSON
        # mode, giảm rủi ro code-fence làm vỡ json.loads() phía gọi.
        body["response_format"] = {"type": "json_object"}
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json=body,
        timeout=_TIMEOUT_SECONDS,
    )
    _raise_http_error(response)
    data = response.json()
    try:
        text = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("OpenAI trả về cấu trúc không mong muốn")
    if not text:
        raise RuntimeError("OpenAI trả về rỗng")
    return text


def _responses(api_key, model, base_url, prompt, json_mode):
    body = {
        "model": model,
        "input": prompt,
        "store": False,
        "reasoning": {"effort": "none"},
    }
    if json_mode:
        body["text"] = {"format": {"type": "json_object"}}
    response = requests.post(
        f"{base_url}/responses",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json=body,
        timeout=_TIMEOUT_SECONDS,
    )
    _raise_http_error(response)
    try:
        parts = [
            content["text"]
            for item in response.json()["output"]
            if item.get("type") == "message"
            for content in item.get("content", [])
            if content.get("type") == "output_text" and content.get("text")
        ]
    except (AttributeError, KeyError, TypeError):
        raise RuntimeError("OpenAI trả về cấu trúc không mong muốn")
    text = "\n".join(parts).strip()
    if not text:
        raise RuntimeError("OpenAI trả về rỗng")
    return text


def _generate(api_key, model, base_url, prompt, json_mode):
    if model.startswith("gpt-5.6"):
        return _responses(api_key, model, base_url, prompt, json_mode)
    return _chat(api_key, model, base_url, prompt, json_mode)


def rewrite(prompt: str) -> str:
    """fn(prompt) -> str theo đúng chữ ký content.set_llm() yêu cầu."""
    api_key, model, base_url = _config()
    if not api_key:
        raise RuntimeError("ACP_OPENAI_API_KEY chưa được đặt")
    return _generate(api_key, model, base_url, prompt, json_mode=False)


def rewrite_json(prompt: str) -> str:
    """fn(prompt) -> str JSON hợp lệ cho các set_*() của Content Engine v2
    và Seeding -- cùng hợp đồng với llm_gemini.rewrite_json()."""
    api_key, model, base_url = _config()
    if not api_key:
        print("  ! Content Engine v2 LLM lỗi: ACP_OPENAI_API_KEY chưa được đặt")
        raise RuntimeError("ACP_OPENAI_API_KEY chưa được đặt")
    return _generate(api_key, model, base_url, prompt, json_mode=True)
