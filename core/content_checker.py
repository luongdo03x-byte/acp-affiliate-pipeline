"""Anti-Industrial Checker + Rule-based Scoring cho ContentVariant (Content
Engine v2, PTYC muc 16-17, 29-31).

Khong dung core/pipeline.py/core/content.py -- dormant nhu E1/E2/E3's
content_variant.py, chua noi vao luong tao bai that (viec cua E6).
"""
import re
import unicodedata

INDUSTRIAL_PHRASES = [
    "sản phẩm này mang lại", "lựa chọn hoàn hảo", "không thể bỏ qua",
    "trải nghiệm tuyệt vời", "chất lượng vượt trội", "đáp ứng mọi nhu cầu",
    "thiết kế hiện đại", "giải pháp tối ưu", "đáng để sở hữu",
    "mang đến sự tiện lợi",
]

CTA_SPAM_PHRASES = [
    "mua ngay", "comment ngay", "share ngay", "follow ngay", "đừng bỏ lỡ",
]

_LONG_SENTENCE_WORDS = 25
_LONG_PARAGRAPH_WORDS = 40
_EXCESS_EMOJI_THRESHOLD = 3
_GENERIC_OPENINGS = ["sản phẩm này", "đây là"]

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "]"
)


def _variant_text(variant) -> str:
    return " ".join([variant.hook, variant.main_message, " ".join(variant.body), variant.cta])


def check_industrial_phrases(text: str) -> list:
    flat = unicodedata.normalize("NFC", text or "").lower()
    return [p for p in INDUSTRIAL_PHRASES if p in flat]


def _ngrams(text: str, n: int = 4) -> set:
    words = unicodedata.normalize("NFC", text or "").lower().split()
    return set(tuple(words[i:i + n]) for i in range(len(words) - n + 1))


def check_variant_rules(variant) -> list:
    """Return list of dicts with rule and message keys."""
    violations = []
    text = _variant_text(variant)
    flat_main = unicodedata.normalize("NFC", variant.main_message or "").strip().lower()

    if any(flat_main.startswith(o) for o in _GENERIC_OPENINGS):
        violations.append({"rule": "generic_opening",
                            "message": "main_message mở đầu chung chung"})

    for phrase in check_industrial_phrases(text):
        violations.append({"rule": "marketing_cliche",
                            "message": f'Cụm công nghiệp: "{phrase}"'})

    flat_text = unicodedata.normalize("NFC", text).lower()
    cta_spam_hits = [p for p in CTA_SPAM_PHRASES if p in flat_text]
    if len(cta_spam_hits) > 1:
        violations.append({"rule": "too_many_ctas",
                            "message": f"Nhiều CTA spam cùng lúc: {cta_spam_hits}"})

    for item in variant.body:
        for sentence in re.split(r"[.!?]", item):
            if len(sentence.split()) > _LONG_SENTENCE_WORDS:
                violations.append({"rule": "long_sentence",
                                    "message": f'Câu quá dài (>{_LONG_SENTENCE_WORDS} từ): "{sentence.strip()}"'})
        if len(item.split()) > _LONG_PARAGRAPH_WORDS:
            violations.append({"rule": "long_paragraph",
                                "message": f'Đoạn quá dài (>{_LONG_PARAGRAPH_WORDS} từ): "{item}"'})

    hook_grams = _ngrams(variant.hook)
    if any(hook_grams & _ngrams(item) for item in variant.body):
        violations.append({"rule": "repeated_phrase",
                            "message": "Hook và body lặp cụm từ dài"})

    emoji_count = len(_EMOJI_RE.findall(text))
    for _ in range(max(0, emoji_count - _EXCESS_EMOJI_THRESHOLD)):
        violations.append({"rule": "excessive_emoji",
                            "message": f"Quá nhiều emoji ({emoji_count})"})

    return violations
