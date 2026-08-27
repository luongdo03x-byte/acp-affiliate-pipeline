"""Nơi duy nhất quyết định dùng adapter nào.

Trước đây CLI đọc ACP_ADAPTER còn route /vanhanh/work khởi tạo thẳng MockThreads,
nên bấm nút trên web vẫn chạy giả lập dù đã bật live -- sai lệch âm thầm và rất
khó phát hiện. Giờ cả hai đường đều đi qua đây.

ACP_SOURCE chọn nguồn sản phẩm độc lập với ACP_ADAPTER, để chạy TikTok Shop live
trong khi Shopee còn chờ duyệt campaign.
"""
import os

_CACHE = {}


def is_live() -> bool:
    return os.environ.get("ACP_ADAPTER", "mock").lower() == "live"


def get_source(name: str = None):
    """name: 'mock' | 'shopee' | 'tiktokshop'. Bỏ trống thì đọc từ môi trường."""
    name = (name or os.environ.get("ACP_SOURCE") or ("shopee" if is_live() else "mock")).lower()
    if name in _CACHE:
        return _CACHE[name]

    if name == "mock":
        from .mock import MockAccessTrade
        src = MockAccessTrade()
    elif name in ("shopee", "accesstrade", "accesstrade_shopee"):
        from .live import AccessTradeSource
        src = AccessTradeSource()
    elif name in ("tiktokshop", "tiktok", "tiktok_shop"):
        from .tiktokshop import AccessTradeTikTokShopSource
        src = AccessTradeTikTokShopSource()
    else:
        raise ValueError(f"Nguồn sản phẩm không hợp lệ: {name!r} "
                         f"(chọn: mock, shopee, tiktokshop)")
    _CACHE[name] = src
    return src


def get_channel():
    if is_live():
        from .live import ThreadsChannel
        return ThreadsChannel()
    from .mock import MockThreads
    return MockThreads(fail_rate=0.08, seed=7)


def get_caption_llm():
    """Trả về fn(prompt)->str cho content.set_llm(), hoặc None nếu tắt.
    ACP_CAPTION_LLM=gemini | openai -- chọn nhà cung cấp viết lại caption."""
    choice = (os.environ.get("ACP_CAPTION_LLM") or "").lower()
    if not choice:
        from ..core import openai_settings
        if openai_settings.has_saved_key():
            choice = "openai"
    if choice == "gemini":
        from ..core import llm_gemini
        return llm_gemini.rewrite
    if choice in ("openai", "chatgpt"):
        from ..core import llm_openai
        return llm_openai.rewrite
    return None


def get_seeding_llm():
    """Structured JSON LLM used by the Seeding multi-account planner.

    Reuse ACP_CAPTION_LLM as the operator switch, but use JSON mode rather
    than the free-form caption rewrite callback because seeding_tasks expects an
    ``accounts[]`` JSON document.
    """
    choice = (os.environ.get("ACP_CAPTION_LLM") or "").lower()
    if choice == "gemini":
        from ..core import llm_gemini
        return llm_gemini.rewrite_json
    if choice in ("openai", "chatgpt"):
        from ..core import llm_openai
        return llm_openai.rewrite_json
    return None


def get_content_engine_llm():
    """Trả về fn(prompt)->str cho 6 set_*() của Content Engine v2
    (core/content_facts.py, content_hook.py, content_variant.py,
    content_checker.py, content_scoring.py), hoặc None nếu tắt.
    ACP_CONTENT_ENGINE_LLM=gemini|openai -- CỜ RIÊNG, độc lập với
    ACP_CAPTION_LLM (v1) vì khối lượng gọi khác hẳn nhau (~13 lần/bài
    so với 1 lần/bài của v1)."""
    choice = (os.environ.get("ACP_CONTENT_ENGINE_LLM") or "").lower()
    if choice == "gemini":
        from ..core import llm_gemini
        return llm_gemini.rewrite_json
    if choice in ("openai", "chatgpt"):
        from ..core import llm_openai
        return llm_openai.rewrite_json
    return None


def get_publishers() -> dict:
    """platform -> Publisher. Đủ 3 platform kể từ sub-project C."""
    publishers = {"threads": get_channel()}
    if is_live():
        from .live import FacebookPublisher, InstagramPublisher
        publishers["facebook"] = FacebookPublisher()
        publishers["instagram"] = InstagramPublisher()
    else:
        from .mock import MockFacebookPublisher, MockInstagramPublisher
        publishers["facebook"] = MockFacebookPublisher()
        publishers["instagram"] = MockInstagramPublisher()
    return publishers


def get_meta_connection_service():
    if is_live():
        from .live import LiveMetaConnectionService
        return LiveMetaConnectionService()
    from .mock import MockMetaConnectionService
    return MockMetaConnectionService()


def build_context(source_name: str = None) -> dict:
    """Ngữ cảnh truyền vào các job handler."""
    from ..core import content, storage
    from .accesstrade_client import AccessTradeClient
    content.set_llm(get_caption_llm())
    return {
        "source": get_source(source_name),
        "product_client": AccessTradeClient.from_env(),
        "channel": get_channel(),
        "publishers": get_publishers(),
        "storage": storage.get_storage(),
    }


def reset_cache():
    """Dùng trong test khi đổi biến môi trường giữa chừng."""
    _CACHE.clear()
