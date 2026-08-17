"""Platform Adaptation -- ghép ContentVariant (E3) thành caption hoàn chỉnh riêng cho Threads/Facebook/Instagram (Content Engine v2, PTYC mục 23-27).

Không đụng core/pipeline.py/core/content.py (chỉ đọc 2 hằng số read-only) -- dormant như E1-E4, chưa nối vào luồng tạo bài thật (việc của E6).
"""
from . import content


def _fit_to_length(body: str, affiliate_link: str, disclosure: str, max_len: int) -> str:
    """Nối affiliate_link + disclosure vào cuối, cắt body nếu vượt max_len."""
    tail = f"\n\n{affiliate_link}\n\n{disclosure}"
    budget = max_len - len(tail)
    body = body.strip()
    if len(body) <= budget:
        return body + tail
    head = body[:max(0, budget)].rsplit(" ", 1)[0].rstrip(" ,.—-") + "…"
    return head + tail


def adapt_for_threads(variant, affiliate_link: str, disclosure: str = None) -> str:
    """PTYC mục 24: hook cực nhanh, conversational, dòng ngắn, không paragraph dài, CTA nhẹ, không hashtag."""
    disclosure = disclosure if disclosure is not None else content.DISCLOSURE_DEFAULT
    lines = [variant.hook, "", variant.main_message, *variant.body, variant.cta]
    body = "\n".join(l for l in lines if l)
    return _fit_to_length(body, affiliate_link, disclosure, content.PLATFORM_MAX_LEN["threads"])


def adapt_for_facebook(variant, affiliate_link: str, disclosure: str = None) -> str:
    """PTYC mục 25: dòng đầu mạnh (hook), có thể giải thích hơn Threads -- gộp main_message + body thành 1 đoạn văn liền mạch."""
    disclosure = disclosure if disclosure is not None else content.DISCLOSURE_DEFAULT
    paragraph = " ".join([variant.main_message, *variant.body])
    lines = [variant.hook, "", paragraph, "", variant.cta]
    body = "\n".join(l for l in lines if l)
    return _fit_to_length(body, affiliate_link, disclosure, content.PLATFORM_MAX_LEN["facebook"])


def adapt_for_instagram(variant, affiliate_link: str, disclosure: str = None) -> str:
    """PTYC mục 26: hook đầu, ngắn rõ, CTA rõ -- cùng kiểu ghép xuống dòng như Threads, khác biệt chính là giới hạn ký tự (2200 vs 500)."""
    disclosure = disclosure if disclosure is not None else content.DISCLOSURE_DEFAULT
    lines = [variant.hook, "", variant.main_message, *variant.body, variant.cta]
    body = "\n".join(l for l in lines if l)
    return _fit_to_length(body, affiliate_link, disclosure, content.PLATFORM_MAX_LEN["instagram"])
