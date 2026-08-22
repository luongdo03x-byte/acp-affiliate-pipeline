"""Reviewer-style Threads captions for Shopee Affiliate products.

Turns product facts already owned by ACP into short conversational copy. It
never schedules/publishes and never invents first-hand product experience.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

MAX_DRAFT_LEN = 380
HOOK_WORD_TARGET = 12

_SALESY_OR_ROBOTIC_PHRASES = (
    "sự lựa chọn lý tưởng", "không thể bỏ lỡ", "hoàn hảo", "tuyệt vời",
    "nâng tầm", "mua ngay", "chốt đơn ngay", "listing", "detail",
)
_FABRICATED_EXPERIENCE = (
    "mình đã dùng", "mình dùng thử", "mình xài", "mình đã thử",
    "mình mua về dùng", "sau khi dùng", "trải nghiệm của mình",
)
_FEATURES = (
    "chống nắng toàn thân", "lưng nhún chun", "nhún eo chun", "chân váy lụa",
    "chân váy ngắn", "đũi vân mây", "dáng đuôi tôm", "hở lưng", "form rộng",
    "ống rộng", "ống suông", "cạp chun", "thun tăm", "ren bèo", "cổ vuông",
    "tay bồng", "sạc nhanh", "không dây", "chống nắng", "chống thấm",
    "gấp gọn", "chấm bi", "2 dây", "dáng suông", "dáng dài", "lụa", "ren",
    "pijama",
)
_USE_CASES = ("mặc nhà", "đi biển", "du lịch", "dạo phố", "đi tiệc", "hẹn hò")
_PRODUCT_KINDS = (
    "chân váy", "áo yếm", "áo khoác", "áo thun", "áo sơ mi", "quần bom",
    "quần jean", "quần", "đầm maxi", "đầm", "váy", "set đồ", "set bộ",
    "bộ ngủ", "pijama", "túi xách", "túi", "giày", "sandal", "tai nghe",
    "sạc dự phòng", "cáp sạc", "ốp lưng", "serum", "kem chống nắng",
    "sữa rửa mặt",
)

_SIZE_RANGE_RE = re.compile(r"(?<!\d)(\d{2,3})\s*[-–~]\s*(\d{2,3})\s*kg\b", re.I)
_NUMBER_TOKEN_RE = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)?(?:k\+?|kg|%)?", re.I)
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ReviewerSignals:
    angle: str
    kind: str
    feature: str
    use_case: str
    size_range: str
    price_full: str
    price_short: str
    sold_label: str


def _row_get(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key] if key in row.keys() else default
    except (AttributeError, KeyError, IndexError, TypeError):
        return default


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFD", str(text or ""))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return _SPACE_RE.sub(" ", text.replace("đ", "d").replace("Đ", "D").lower()).strip()


def _fmt_vnd(value) -> str:
    try:
        amount = max(0, int(value or 0))
    except (TypeError, ValueError):
        amount = 0
    return f"{amount:,}đ".replace(",", ".")


def _fmt_price_short(value) -> str:
    try:
        amount = max(0, int(value or 0))
    except (TypeError, ValueError):
        amount = 0
    if 10_000 <= amount < 1_000_000:
        thousands = amount / 1000.0
        if thousands.is_integer():
            return f"{int(thousands)}k"
        return f"{thousands:.1f}".replace(".", ",").rstrip("0").rstrip(",") + "k"
    return _fmt_vnd(amount)


def _fmt_sold(value) -> str:
    try:
        sold = max(0, int(value or 0))
    except (TypeError, ValueError):
        sold = 0
    if sold >= 1000:
        return f"{sold // 1000}k+"
    if sold >= 100:
        return f"{sold}+"
    return ""


def _extract_size_range(title: str) -> str:
    match = _SIZE_RANGE_RE.search(title or "")
    return f"{match.group(1)}–{match.group(2)}kg" if match else ""


def _extract_kind(title: str) -> str:
    folded = _fold(title)
    return next((kind for kind in _PRODUCT_KINDS if _fold(kind) in folded), "món này")


def _distinct_hits(title: str, phrases) -> list[str]:
    folded = _fold(title)
    selected = []
    for phrase in phrases:
        token = _fold(phrase)
        if token not in folded:
            continue
        if any(token in _fold(existing) for existing in selected):
            continue
        selected.append(phrase)
    return selected


def _extract_feature(title: str) -> str:
    hits = _distinct_hits(title, _FEATURES)
    return " + ".join(hits[:2]) if hits else ""


def _extract_use_case(title: str) -> str:
    hits = _distinct_hits(title, _USE_CASES)
    return " / ".join(hits[:2]) if hits else ""


def extract_signals(product) -> ReviewerSignals:
    title = str(_row_get(product, "name", "") or "")
    size_range = _extract_size_range(title)
    feature = _extract_feature(title)
    use_case = _extract_use_case(title)
    sold_label = _fmt_sold(_row_get(product, "sold_count", 0))
    if size_range:
        angle = "AUDIENCE"
    elif sold_label and int(_row_get(product, "sold_count", 0) or 0) >= 1000:
        angle = "SOCIAL_PROOF"
    elif feature or use_case:
        angle = "FEATURE"
    else:
        angle = "PRICE"
    return ReviewerSignals(
        angle=angle,
        kind=_extract_kind(title),
        feature=feature,
        use_case=use_case,
        size_range=size_range,
        price_full=_fmt_vnd(_row_get(product, "current_price", 0)),
        price_short=_fmt_price_short(_row_get(product, "current_price", 0)),
        sold_label=sold_label,
    )


def _hook_set(signals: ReviewerSignals) -> dict[str, str]:
    """Safe reviewer hooks aligned with existing H1..H9 analytics codes."""
    if signals.angle == "AUDIENCE":
        s, k, price = signals.size_range, signals.kind, signals.price_short
        return {
            "H1_GIAGIAM": f"{price}, mà range tới {s} — mình note lại.",
            "H2_SOSANH": f"Không cần tên dài, range {s} đã đủ đáng chú ý.",
            "H3_KHANHIEM": f"Khoan lướt, mẫu {k} này ghi range tới {s}.",
            "H4_CAUHOI": f"Ai {s} đang tìm {k} kiểu này không?",
            "H5_XAHOI": f"Range {s} là điểm mình chú ý nhất ở mẫu này.",
            "H6_HANGMOI": f"Lướt thấy {k} có range {s}, mình dừng lại.",
            "H7_TIETKIEM": f"{price} cho range {s}, mình để lại để xem kỹ.",
            "H8_CANHBAO": f"Khoan lướt nếu bạn đang cần range {s}.",
            "H9_TRUCTIEP": f"Mẫu {k} này ghi size tới {s}.",
        }

    if signals.angle == "SOCIAL_PROOF":
        sold, price = signals.sold_label, signals.price_short
        return {
            "H1_GIAGIAM": f"{price} mà {sold} lượt mua, mình dừng lại xem.",
            "H2_SOSANH": f"Giá {price}, lượt mua {sold}: con số khá đáng chú ý.",
            "H3_KHANHIEM": f"Khoan lướt, con số {sold} lượt mua khá đáng nhìn.",
            "H4_CAUHOI": f"{sold} lượt mua ở mức {price}, có đáng xem không?",
            "H5_XAHOI": f"{sold} lượt mua ở mức {price} — mình chú ý.",
            "H6_HANGMOI": f"Lướt tới {sold} lượt mua là mình dừng lại.",
            "H7_TIETKIEM": f"Mức {price} đi cùng {sold} lượt mua, mình note lại.",
            "H8_CANHBAO": f"Khoan lướt qua con số {sold} lượt mua này.",
            "H9_TRUCTIEP": f"{sold} lượt mua, giá hiện tại {price}.",
        }

    if signals.angle == "FEATURE":
        point, price = signals.feature or signals.use_case, signals.price_short
        return {
            "H1_GIAGIAM": f"{price}, còn điểm mình để ý là {point}.",
            "H2_SOSANH": f"Không cần tên dài, {point} mới là điểm chính.",
            "H3_KHANHIEM": f"Khoan lướt, phần {point} khá đáng nhìn.",
            "H4_CAUHOI": f"Ai thích kiểu {point} không?",
            "H5_XAHOI": f"{point.capitalize()} là điểm mình để ý nhất.",
            "H6_HANGMOI": f"Lướt thấy {point}, mình dừng lại xem.",
            "H7_TIETKIEM": f"Mức {price} với phần {point}, mình để lại đây.",
            "H8_CANHBAO": f"Khoan lướt nếu bạn đang tìm kiểu {point}.",
            "H9_TRUCTIEP": f"Điểm mình để ý: {point}.",
        }

    price = signals.price_short
    return {
        "H1_GIAGIAM": f"Mức {price} là lý do mình dừng ở món này.",
        "H2_SOSANH": f"Không cần tên dài, mức {price} đã đủ để xem tiếp.",
        "H3_KHANHIEM": f"Khoan lướt, món này đang ở mức {price}.",
        "H4_CAUHOI": f"{price} cho kiểu này, có đáng xem không?",
        "H5_XAHOI": f"{price} là con số làm mình dừng lại xem.",
        "H6_HANGMOI": f"Lướt tới mức {price} là mình dừng lại xem.",
        "H7_TIETKIEM": f"Ai đang canh tầm {price}, mình để lại mẫu này.",
        "H8_CANHBAO": f"Khoan lướt nếu bạn đang canh tầm {price}.",
        "H9_TRUCTIEP": f"Giá mình thấy ở đây là {price}.",
    }


def _score_hook(hook: str, signals: ReviewerSignals) -> float:
    words = len(hook.split())
    score = 1.0
    if words > HOOK_WORD_TARGET:
        score -= 0.15 * (words - HOOK_WORD_TARGET)
    if words < 4:
        score -= 0.1
    primary = signals.size_range or signals.sold_label or signals.feature or signals.use_case or signals.price_short
    if primary and _fold(primary) in _fold(hook):
        score += 0.2
    return score


def select_hook(signals: ReviewerSignals, hook_code: str = None) -> str:
    hooks = _hook_set(signals)
    requested = hooks.get(str(hook_code or ""))
    if requested and len(requested.split()) <= HOOK_WORD_TARGET:
        return requested
    return max(hooks.values(), key=lambda hook: _score_hook(hook, signals))


def _detail_line(signals: ReviewerSignals) -> str:
    if signals.angle == "FEATURE":
        return ""
    if signals.feature:
        return f"Mình để ý thêm phần {signals.feature}."
    return ""


def _support_line(signals: ReviewerSignals) -> str:
    if signals.angle in ("SOCIAL_PROOF", "PRICE"):
        return ""
    bits = [signals.price_full]
    if signals.sold_label:
        bits.append(f"{signals.sold_label} lượt mua")
    return " · ".join(bits) + "."


def deterministic_draft(product, affiliate_link: str, hook_code: str = None) -> str:
    signals = extract_signals(product)
    lines = [
        select_hook(signals, hook_code),
        _detail_line(signals),
        _support_line(signals),
        "Link mình để đây cho ai đang tìm đúng kiểu này ↓",
        str(affiliate_link or "").strip(),
    ]
    return "\n".join(line for line in lines if line).strip()


def _allowed_number_tokens(draft: str) -> set[str]:
    return {token.lower() for token in _NUMBER_TOKEN_RE.findall(draft or "")}


def _safe_rewrite(candidate: str, draft: str, affiliate_link: str) -> bool:
    candidate = str(candidate or "").strip()
    if not candidate or len(candidate) > MAX_DRAFT_LEN:
        return False
    if str(affiliate_link or "").strip() not in candidate:
        return False
    nonempty = [line.strip() for line in candidate.splitlines() if line.strip()]
    if not 3 <= len(nonempty) <= 6:
        return False
    if len(nonempty[0].split()) > HOOK_WORD_TARGET:
        return False
    flat = _fold(candidate)
    blocked = _SALESY_OR_ROBOTIC_PHRASES + _FABRICATED_EXPERIENCE
    if any(_fold(phrase) in flat for phrase in blocked):
        return False
    if "#" in candidate:
        return False
    if not _allowed_number_tokens(candidate).issubset(_allowed_number_tokens(draft)):
        return False
    return True


def _rewrite_prompt(product, draft: str) -> str:
    title = str(_row_get(product, "name", "") or "")
    sold = int(_row_get(product, "sold_count", 0) or 0)
    price = _fmt_vnd(_row_get(product, "current_price", 0))
    return (
        "Viết lại caption Shopee dưới đây theo giọng tài khoản review Threads chân thật.\n"
        "MỤC TIÊU: dòng đầu níu người đọc nhưng không có cảm giác đang đọc quảng cáo.\n\n"
        "RÀNG BUỘC:\n"
        "- 3-5 dòng ngắn trước URL; toàn bộ tối đa 380 ký tự.\n"
        "- Dòng đầu tối đa 12 từ.\n"
        "- Một angle chính; không liệt kê hàng loạt lợi ích.\n"
        "- Viết như nhắn cho một người bạn, không phải catalogue/brand.\n"
        "- Không chép nguyên tên sản phẩm dài.\n"
        "- Không bịa đã mua/đã mặc/đã dùng/đã thử.\n"
        "- Không thêm công dụng, số liệu, giảm giá, urgency hay social proof ngoài dữ liệu.\n"
        "- Không dùng: hoàn hảo, tuyệt vời, nâng tầm, không thể bỏ lỡ, sự lựa chọn lý tưởng, mua ngay.\n"
        "- Tránh từ máy móc như listing/detail trong caption.\n"
        "- 0-2 emoji; không markdown; không hashtag.\n"
        "- Một CTA mềm và giữ nguyên URL.\n\n"
        "<<<FACT>>>\n"
        f"Tên sản phẩm: {title}\nGiá: {price}\nSold count: {sold}\nDraft an toàn:\n{draft}\n"
        "<<<HẾT_FACT>>>\n\nChỉ trả caption, không giải thích."
    )


def generate(product, affiliate_link: str, *, discount_pct: float = 0.0,
             hook_code: str = None, llm_fn=None) -> str:
    """Return a safe reviewer-style draft including the exact affiliate URL."""
    draft = deterministic_draft(product, affiliate_link, hook_code=hook_code)
    if llm_fn is None:
        return draft
    try:
        candidate = llm_fn(_rewrite_prompt(product, draft))
    except Exception:
        return draft
    return str(candidate).strip() if _safe_rewrite(candidate, draft, affiliate_link) else draft
