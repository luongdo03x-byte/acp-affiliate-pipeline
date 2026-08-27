"""Reviewer-style Threads captions for Shopee Affiliate products.

Turns product facts already owned by ACP into short conversational copy. It
never schedules/publishes and never invents first-hand product experience.
"""
from __future__ import annotations

import os
import re
import random
import unicodedata
from dataclasses import dataclass

MAX_DRAFT_LEN = 380
HOOK_WORD_TARGET = 12

_SALESY_OR_ROBOTIC_PHRASES = (
    "sự lựa chọn lý tưởng", "không thể bỏ lỡ", "hoàn hảo", "tuyệt vời",
    "nâng tầm", "mua ngay", "chốt đơn ngay", "listing", "detail",
    "mình chú ý", "mình note lại", "đáng chú ý", "khoan lướt",
    "đúng kiểu này", "siêu phẩm", "số lượng có hạn", "nhanh tay",
    "kẻo hết", "cháy hàng", "giá sốc", "deal hời", "bỏ lỡ",
)
_FABRICATED_EXPERIENCE = (
    "mình đã dùng", "mình dùng thử", "mình xài", "mình đã thử",
    "mình mua về dùng", "sau khi dùng", "trải nghiệm của mình",
    "cho đến khi thử", "đến khi thử", "sau khi thử", "mình cũng từng nghĩ",
)
_SOLD_SIGNAL_PHRASES = (
    "lượt mua", "đã bán", "sold count", "sold_count", " sold ",
)
_FEATURES = (
    "chống nắng toàn thân", "lưng nhún chun", "nhún eo chun", "chân váy lụa",
    "chân váy ngắn", "đũi vân mây", "dáng đuôi tôm", "hở lưng", "form rộng",
    "ống rộng", "ống suông", "cạp chun", "cạp cao", "lưng cao", "thun tăm",
    "mỏng nhẹ", "co giãn", "dễ phối đồ", "dễ phối", "ren bèo", "cổ vuông",
    "tay bồng", "sạc nhanh", "không dây", "chống nắng", "chống thấm",
    "gấp gọn", "chấm bi", "2 dây", "dáng suông", "dáng dài", "oversize",
    "lụa", "ren", "pijama",
)
_USE_CASES = (
    "mặc nhà", "đi biển", "du lịch", "dạo phố", "đi tiệc", "hẹn hò",
    "đi học", "đi làm", "đi cafe",
)
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
    """Extract caption signals without consulting ``sold_count`` at all."""
    title = str(_row_get(product, "name", "") or "")
    size_range = _extract_size_range(title)
    feature = _extract_feature(title)
    use_case = _extract_use_case(title)
    if size_range:
        angle = "AUDIENCE"
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
    )


def _feature_point(signals: ReviewerSignals) -> str:
    return signals.feature or signals.use_case or signals.kind


def _hook_set(signals: ReviewerSignals) -> dict[str, str]:
    """Human-sounding hooks aligned with the existing stable H1..H9 IDs.

    The IDs remain stable for attribution history; they are identifiers, not a
    promise that H5 must expose a social-proof number. ``sold_count`` is never
    used by this engine.
    """
    if signals.angle == "AUDIENCE":
        s, k, price = signals.size_range, signals.kind, signals.price_short
        return {
            "H1_GIAGIAM": f"{price} mà size tới {s}, cái này được nè",
            "H2_SOSANH": f"size tới {s} mà form nhìn khá xinh á",
            "H3_KHANHIEM": f"ê mẫu {k} này có size tới {s} nè",
            "H4_CAUHOI": f"mấy bà {s} có mê {k} kiểu này không?",
            "H5_XAHOI": f"{k} có size tới {s}, nhìn ổn áp phết",
            "H6_HANGMOI": f"lướt thấy {k} có size tới {s} là phải xem",
            "H7_TIETKIEM": f"{price} mà size tới {s}, cũng dễ thử á",
            "H8_CANHBAO": f"ai hay khó kiếm size {s} thì xem cái này",
            "H9_TRUCTIEP": f"{k} size tới {s}, giá {price}",
        }

    if signals.angle == "FEATURE":
        point, price, kind = _feature_point(signals), signals.price_short, signals.kind
        return {
            "H1_GIAGIAM": f"{price} cho cái form này cũng được đó",
            "H2_SOSANH": f"{point} nhìn khá dễ mặc ghê",
            "H3_KHANHIEM": f"ê cái {kind} này nhìn ổn phết =))",
            "H4_CAUHOI": f"mấy bà có mê kiểu {point} không?",
            "H5_XAHOI": f"{point} nhìn xinh nha",
            "H6_HANGMOI": f"lướt thấy {point} là phải bấm xem =))",
            "H7_TIETKIEM": f"{price} mà có {point}, cũng dễ thử á",
            "H8_CANHBAO": f"{point} kiểu này nhìn cuốn ghê",
            "H9_TRUCTIEP": f"{point}, {price}",
        }

    price = signals.price_short
    return {
        "H1_GIAGIAM": f"{price} thôi á? mình tưởng cao hơn =))",
        "H2_SOSANH": f"tầm {price} mà nhìn vậy thì cũng ổn đó",
        "H3_KHANHIEM": f"ê cái này {price} nè",
        "H4_CAUHOI": f"{price} cho kiểu này, mấy bà thấy sao?",
        "H5_XAHOI": f"giá {price} nhìn cũng dễ chịu phết",
        "H6_HANGMOI": f"lướt thấy giá {price} là phải bấm vào xem",
        "H7_TIETKIEM": f"{price} thì cũng dễ thử á",
        "H8_CANHBAO": f"ai đang canh tầm {price} thì xem cái này",
        "H9_TRUCTIEP": f"{price} — link ở dưới nè",
    }


def _score_hook(hook: str, signals: ReviewerSignals) -> float:
    words = len(hook.split())
    score = 1.0
    if words > HOOK_WORD_TARGET:
        score -= 0.15 * (words - HOOK_WORD_TARGET)
    if words < 4:
        score -= 0.1
    primary = signals.size_range or signals.feature or signals.use_case or signals.price_short
    if primary and _fold(primary) in _fold(hook):
        score += 0.2
    return score


def select_hook(signals: ReviewerSignals, hook_code: str = None) -> str:
    hooks = _hook_set(signals)
    requested = hooks.get(str(hook_code or ""))
    if requested and len(requested.split()) <= HOOK_WORD_TARGET:
        return requested
    # Không có mã hợp lệ: lấy top-3 theo điểm rồi chọn ổn-định theo dấu hiệu
    # sản phẩm -- feed không lặp một khuôn hook giá duy nhất khi pipeline
    # gọi mà không truyền hook_code.
    ranked = sorted(hooks.values(), key=lambda h: (-_score_hook(h, signals), h))
    eligible = [h for h in ranked if len(h.split()) <= HOOK_WORD_TARGET] or ranked
    seed = "|".join((signals.angle, signals.kind, signals.feature,
                     signals.use_case, signals.price_short))
    return random.Random(f"hook:{seed}").choice(eligible[:3])


def _detail_line(signals: ReviewerSignals) -> str:
    if signals.angle == "AUDIENCE" and signals.feature:
        return f"{signals.feature} nhìn khá xinh"
    if signals.angle == "FEATURE" and signals.use_case and signals.use_case not in signals.feature:
        return f"mẫu này thiên về {signals.use_case}"
    return ""


def _price_line(signals: ReviewerSignals, hook: str) -> str:
    folded = _fold(hook)
    if _fold(signals.price_short) in folded or _fold(signals.price_full) in folded:
        return ""
    return signals.price_full


def _cta_line(hook_code: str = None) -> str:
    # Most personal Threads posts just drop the URL. Only a minority use a tiny
    # CTA so a feed generated across H1..H9 does not repeat one marketing line.
    return {
        "H1_GIAGIAM": "link đây nè ↓",
        "H4_CAUHOI": "mình để link dưới nha",
        "H7_TIETKIEM": "link mình để dưới nè ↓",
    }.get(str(hook_code or ""), "")


def _hook_contains_price(hook: str, signals: ReviewerSignals) -> bool:
    folded = _fold(hook)
    for value in (signals.price_full, signals.price_short):
        if value and _fold(value) in folded:
            return True
    return False


# Hook trung lập không chứa giá -- dự phòng khi mọi hook sinh theo tín hiệu
# đều dính con số tiền (thường gặp với đồ đắt, nơi giá là điểm duy nhất nổi
# bật trong tên sản phẩm).
_NEUTRAL_NO_PRICE_HOOKS = (
    "lướt thấy món này ổn nè",
    "món này nhìn đáng để ý ghê",
    "thấy món này cũng hay hay nè",
)


def deterministic_draft(product, affiliate_link: str, hook_code: str = None,
                        allow_price: bool = True) -> str:
    """allow_price=False: chọn hook PHI-GIÁ và bỏ hẳn dòng giá khỏi nháp --
    dùng khi price_lead_weight thấp (đồ đắt/hàng feature-led): LLM không còn
    bản nháp dẫn bằng giá để mà bám theo."""
    signals = extract_signals(product)
    hook = select_hook(signals, hook_code)
    if not allow_price:
        candidates = [h for h in _hook_set(signals).values()
                      if len(h.split()) <= HOOK_WORD_TARGET
                      and not _hook_contains_price(h, signals)]
        if candidates:
            ranked = sorted(candidates, key=lambda h: (-_score_hook(h, signals), h))
            seed = "|".join((signals.angle, signals.kind, signals.feature,
                             signals.use_case, signals.price_short))
            hook = random.Random(f"noprice:{seed}").choice(ranked[:3])
        else:
            seed = "|".join((signals.angle, signals.kind, signals.feature,
                             signals.use_case, signals.price_short))
            hook = random.Random(f"neutral:{seed}").choice(_NEUTRAL_NO_PRICE_HOOKS)
    lines = [
        hook,
        _detail_line(signals),
        _price_line(signals, hook) if allow_price else "",
        _cta_line(hook_code),
        str(affiliate_link or "").strip(),
    ]
    return "\n".join(line for line in lines if line).strip()


def _allowed_number_tokens(text: str) -> set[str]:
    return {token.lower() for token in _NUMBER_TOKEN_RE.findall(text or "")}


def _allowed_numbers(product, draft: str) -> set[str]:
    allowed = _allowed_number_tokens(draft)
    allowed |= _allowed_number_tokens(_fmt_vnd(_row_get(product, "current_price", 0)))
    allowed |= _allowed_number_tokens(_fmt_price_short(_row_get(product, "current_price", 0)))
    return allowed


def _safe_rewrite(candidate: str, draft: str, affiliate_link: str, product,
                  structural: bool = True, enforce_numbers: bool = True,
                  guards: bool = True) -> bool:
    """guards=False khi operator bật "bỏ rào chắn nội dung" (/chamdiem):
    chỉ còn hai điều kiện kỹ thuật -- caption không rỗng và CÒN LINK affiliate
    (mất link là mất hoa hồng). Ngược lại giữ toàn bộ rào như cũ."""
    candidate = str(candidate or "").strip()
    if not candidate:
        return False
    if str(affiliate_link or "").strip() not in candidate:
        return False
    if not guards:
        return True
    if structural and len(candidate) > MAX_DRAFT_LEN:
        return False
    nonempty = [line.strip() for line in candidate.splitlines() if line.strip()]
    if structural and not 2 <= len(nonempty) <= 5:
        return False
    if structural and len(nonempty[0].split()) > HOOK_WORD_TARGET:
        return False
    flat = _fold(candidate)
    blocked = _SALESY_OR_ROBOTIC_PHRASES + _FABRICATED_EXPERIENCE + _SOLD_SIGNAL_PHRASES
    if any(_fold(phrase) in flat for phrase in blocked):
        return False
    if "#" in candidate:
        return False
    if enforce_numbers and not _allowed_number_tokens(candidate).issubset(
            _allowed_numbers(product, draft)):
        return False
    return True


def _rewrite_prompt(product, draft: str) -> str:
    title = str(_row_get(product, "name", "") or "")
    price = _fmt_vnd(_row_get(product, "current_price", 0))
    # Cùng chính sách với luồng v1 (content.price_lead_weight): món rẻ/deal sâu
    # thì dẫn bằng giá là tự nhiên; đồ đắt/hàng feature-led thì cấm mở đầu bằng
    # tiền -- bán bằng đặc điểm trong tên sản phẩm.
    try:
        from . import content as _content
        probe = dict(product) if not isinstance(product, dict) else product
        lead = ("món này rẻ hoặc đang deal sâu nên được PHÉP dẫn bằng giá"
                if _content.price_lead_weight(probe) >= 0.6 else
                "TUYỆT ĐỐI KHÔNG mở dòng đầu bằng số tiền -- bắt đầu bằng chất "
                "liệu/đối tượng dùng/chi tiết thật trong tên; có thể không cần "
                "nhắc giá luôn")
    except Exception:
        lead = ""
    return (
        "Viết lại caption Shopee dưới đây như một tài khoản Threads cá nhân Việt Nam đang lướt thấy món hợp gu rồi share lại.\n"
        "Đừng viết như reviewer chuyên nghiệp, brand hay quảng cáo. Câu cụt và lowercase đều được.\n\n"
        + (f"THEO LOẠI SẢN PHẨM NÀY: {lead}. Giá nằm trong dữ liệu dưới đây chỉ là "
           "tham khảo, KHÔNG phải nghĩa là phải nhắc tới.\n\n" if lead else "")
        + "RÀNG BUỘC:\n"
        "- 1-3 dòng nội dung ngắn trước URL; toàn bộ tối đa 380 ký tự.\n"
        "- Dòng đầu tối đa 12 từ và phải nghe như phản ứng tự nhiên.\n"
        "- Chỉ một điểm chính: giá, size hoặc một chi tiết thật trong tên sản phẩm.\n"
        "- KHÔNG nhắc số lượt mua, số đã bán hoặc bất kỳ social proof bằng số nào.\n"
        "- Không bịa đã mua, đã mặc, đã dùng hay đã thử sản phẩm.\n"
        "- Không thêm công dụng, thông số, giảm giá, urgency hay số liệu ngoài dữ liệu được đưa.\n"
        "- Draft có nhắc giá thì giữ mức giá đó; draft KHÔNG nhắc giá thì caption cũng không cần đưa giá vào.\n"
        "- Tránh giọng máy móc: mình chú ý, mình note lại, đáng chú ý, khoan lướt, listing, detail.\n"
        "- Không dùng: hoàn hảo, tuyệt vời, nâng tầm, không thể bỏ lỡ, sự lựa chọn lý tưởng, mua ngay.\n"
        "- Không bắt buộc CTA; nếu có thì chỉ một câu rất ngắn.\n"
        "- 0-1 emoji; =)) được phép; không markdown; không hashtag.\n"
        "- Giữ nguyên URL.\n\n"
        "<<<FACT>>>\n"
        f"Tên sản phẩm: {title}\n"
        f"Giá: {price}\n"
        f"Draft an toàn:\n{draft}\n"
        "<<<HẾT_FACT>>>\n\n"
        "Chỉ trả caption, không giải thích."
    )


class CaptionRewriteError(RuntimeError):
    """Custom prompt could not produce a caption; callers must not use the draft."""


def generate(product, affiliate_link: str, *, discount_pct: float = 0.0,
             hook_code: str = None, llm_fn=None) -> str:
    """Return a short reviewer-style draft including the exact affiliate URL."""
    # Chính sách theo sản phẩm: đồ đắt/hàng feature-led (weight < 0.4) thì
    # bản nháp KHÔNG chứa giá -- LLM không có gì dẫn bằng tiền để bám; món
    # rẻ/deal sâu giữ nguyên nháp có giá.
    try:
        from . import content as _content
        probe = dict(product) if not isinstance(product, dict) else product
        allow_price = _content.price_lead_weight(probe, discount_pct or 0.0) >= 0.4
    except Exception:
        allow_price = True
    draft = deterministic_draft(product, affiliate_link, hook_code=hook_code,
                                allow_price=allow_price)
    # Prompt chung của operator (nếu đặt) THAY prompt mặc định của luồng này,
    # đồng thời nới các rào cấu trúc (độ dài / số dòng / từ dòng đầu) --
    # operator sở hữu hình dạng caption. Rào NỘI DUNG tắt theo công tắc
    # "bỏ rào chắn nội dung" (/chamdiem); link affiliate luôn bắt buộc.
    from . import prompt_template
    from .system_settings import content_guards_disabled
    custom = prompt_template.get_custom_template_with_file()
    if llm_fn is None:
        if custom:
            raise CaptionRewriteError("Custom caption prompt requires an LLM")
        return draft
    guards_on = not content_guards_disabled()
    try:
        if custom:
            candidate = llm_fn(prompt_template.render(custom, product, draft, affiliate_link))
        else:
            candidate = llm_fn(_rewrite_prompt(product, draft))
    except Exception as error:
        if custom:
            raise CaptionRewriteError("Custom caption rewrite failed") from error
        return draft
    return str(candidate).strip() if _safe_rewrite(candidate, draft, affiliate_link,
                                                   product,
                                                   structural=not custom and guards_on,
                                                   enforce_numbers=not custom and guards_on,
                                                   guards=guards_on) else draft
