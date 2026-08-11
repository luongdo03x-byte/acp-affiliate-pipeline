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
    ACP_CAPTION_LLM=gemini bật viết lại caption bằng Gemini free tier."""
    choice = (os.environ.get("ACP_CAPTION_LLM") or "").lower()
    if choice == "gemini":
        from ..core import llm_gemini
        return llm_gemini.rewrite
    return None


def build_context(source_name: str = None) -> dict:
    """Ngữ cảnh truyền vào các job handler."""
    from ..core import content, storage
    from .accesstrade_client import AccessTradeClient
    content.set_llm(get_caption_llm())
    return {
        "source": get_source(source_name),
        "product_client": AccessTradeClient.from_env(),
        "channel": get_channel(),
        "storage": storage.get_storage(),
    }


def reset_cache():
    """Dùng trong test khi đổi biến môi trường giữa chừng."""
    _CACHE.clear()
