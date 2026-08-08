"""Sinh và kiểm duyệt caption (BRD FR4).

Hai nguyên tắc được cưỡng chế bằng mã, không bằng ý thức:
  1. Không bịa trải nghiệm sử dụng. Caption chỉ nói được điều có trong datafeed.
  2. Mọi bài đều có nhãn tiếp thị liên kết.

Bộ sinh mặc định dùng template -- deterministic, miễn phí, test được. Muốn dùng
LLM thì cài một hàm vào set_llm(); kết quả vẫn phải qua validate() y hệt.
"""
import random
import re
import unicodedata

from . import niche, playbook

MAX_LEN = 500

DISCLOSURE_DEFAULT = "#tiepthilienket — mình có nhận hoa hồng nếu bạn mua qua link này"

# Cụm từ tuyệt đối hoá bị Luật Quảng cáo cấm.
BANNED_SUPERLATIVES = [
    "tốt nhất", "số 1", "số một", "duy nhất", "hàng đầu", "đứng đầu",
    "rẻ nhất", "chất lượng nhất", "hoàn hảo", "tuyệt đối", "nhất thị trường",
]

# Dấu hiệu bịa trải nghiệm cá nhân -- thứ khiến nội dung thành review giả.
FABRICATED_EXPERIENCE = [
    "mình đã dùng", "mình dùng thử", "mình xài", "da mình", "tóc mình",
    "sau khi dùng", "dùng được mấy tuần", "dùng 2 tuần", "trải nghiệm của mình",
    "mình thấy hiệu quả", "mình đã thử", "nhà mình dùng", "mình mua về dùng",
]

# Cam kết công dụng -- cấm với mọi nhóm hàng, không riêng mỹ phẩm.
EFFICACY_CLAIMS = [
    "chữa khỏi", "trị dứt điểm", "đảm bảo hết", "cam kết hiệu quả",
    "giảm cân cấp tốc", "trắng da cấp tốc", "hết mụn sau",
]

TEMPLATES = {
    "price_drop": (
        "{name}\n\n"
        "Giá hiện {price}, mềm hơn khoảng {discount}% so với 30 ngày qua. "
        "{social}"
    ),
    "spec_highlight": (
        "{name}\n\n"
        "Giá {price}. {social} Bên bán mô tả: {highlight}."
    ),
    "deal_roundup": (
        "Lướt nhóm {category} hôm nay thấy món này giá khá hời:\n\n"
        "{name} — {price}. {social}"
    ),
    "comparison": (
        "Trong tầm giá {price_band}, {name} là món khá ổn — giá đang {price}. "
        "{social}"
    ),
}

_llm_fn = None


def set_llm(fn):
    """fn(prompt: str) -> str. Kết quả vẫn phải qua validate()."""
    global _llm_fn
    _llm_fn = fn


def _fmt_vnd(v: int) -> str:
    return f"{v:,}đ".replace(",", ".")


def _price_band(v: int) -> str:
    step = 100_000 if v < 1_000_000 else 500_000
    lo = (v // step) * step
    return f"{_fmt_vnd(lo)}–{_fmt_vnd(lo + step)}"


def _social_proof(product) -> str:
    sold, rating, reviews = product["sold_count"] or 0, product["rating"] or 0, product["review_count"] or 0
    bits = []
    if sold >= 100:
        bits.append(f"{sold:,}".replace(",", ".") + " người mua rồi")
    if rating and reviews >= 20:
        bits.append(f"đánh giá {rating:g}/5")
    return ("Cũng " + ", ".join(bits) + ".") if bits else ""


def _highlight(product) -> str:
    desc = (product["description"] or "").strip()
    if not desc:
        return "chưa có mô tả chi tiết"
    first = re.split(r"[.\n;]", desc)[0].strip()
    return first[:120] if first else desc[:120]


CATEGORY_LABELS = {
    "gia-dung": "đồ gia dụng", "cham-soc-ca-nhan": "chăm sóc cá nhân",
    "phu-kien-cong-nghe": "phụ kiện công nghệ", "thoi-trang": "thời trang",
    "the-thao": "thể thao", "me-va-be": "mẹ và bé",
}


def generate(product, template_code: str, affiliate_link: str,
             discount_pct: float = 0.0, disclosure: str = DISCLOSURE_DEFAULT,
             hook_code: str = None, rng: random.Random = None) -> str:
    """Sinh caption hoàn chỉnh theo cấu trúc HOOK -> THÂN -> MỘT CTA -> DISCLOSURE.

    hook_code chọn dòng mở đầu (core/playbook.py). Bỏ trống thì bốc ngẫu nhiên --
    nhưng pipeline.plan_content() luôn truyền vào một mã cụ thể để xoay vòng hook
    làm biến thể đo bằng sub3. Caption trả về CHƯA qua validate().
    """
    rng = rng or random.Random()
    hook_code = playbook.pick_hook(hook_code, rng=rng)
    hook_line = playbook.render_hook(hook_code, product, discount_pct)
    cta_line = playbook.pick_cta(rng=rng)
    social = _social_proof(product)
    body = TEMPLATES[template_code].format(
        name=product["name"][:120],
        price=_fmt_vnd(product["current_price"]),
        price_band=_price_band(product["current_price"]),
        discount=max(1, round(discount_pct * 100)),
        social=social,
        highlight=_highlight(product),
        category=CATEGORY_LABELS.get(product["category_code"], product["category_code"]),
    )
    # {social} rỗng khi sản phẩm chưa đủ lượt bán/đánh giá -- để lại khoảng trắng
    # kép trong template. Gộp lại cho khỏi lộ chỗ điền-vào-chỗ-trống.
    body = re.sub(r" {2,}", " ", body).strip()
    full = f"{hook_line}\n\n{body}\n\n{cta_line}\n{affiliate_link}"
    if _llm_fn:
        full = _llm_fn(_build_prompt(product, full))
    return _fit(full, disclosure)


def _build_prompt(product, draft: str) -> str:
    return (
        "Viết lại đoạn giới thiệu sản phẩm dưới đây cho tự nhiên hơn.\n"
        "RÀNG BUỘC BẮT BUỘC:\n"
        "- Chỉ dùng thông tin có trong đoạn gốc. Không thêm chi tiết nào khác.\n"
        "- KHÔNG viết như đã từng dùng sản phẩm. Không nói 'mình đã dùng', 'mình thấy'.\n"
        "- Không dùng từ tuyệt đối hoá: tốt nhất, số 1, duy nhất.\n"
        "- Không cam kết công dụng.\n"
        "- Giữ nguyên URL. Tối đa 380 ký tự.\n\n"
        f"Đoạn gốc:\n{draft}"
    )


def _fit(body: str, disclosure: str) -> str:
    """Cắt phần thân sao cho tổng caption vừa 500 ký tự, ưu tiên giữ link và disclosure."""
    tail = "\n\n" + disclosure
    budget = MAX_LEN - len(tail)
    body = body.strip()
    if len(body) <= budget:
        return body + tail
    lines = body.split("\n")
    link_line = next((l for l in reversed(lines) if l.startswith("http")), "")
    keep = budget - len(link_line) - 4
    head = body[:max(0, keep)].rsplit(" ", 1)[0].rstrip(" ,.—-") + "…"
    return f"{head}\n\n{link_line}{tail}"


def validate(caption: str, disclosure: str = DISCLOSURE_DEFAULT, niches=None,
             post_type: str = "SALES") -> list:
    """Trả về danh sách vi phạm. Rỗng nghĩa là được phép đưa vào hàng đợi duyệt.

    niches: danh sách mã chủ đề đang bật. Mỗi chủ đề có thể thêm cụm cấm riêng --
    mỹ phẩm là nhóm hàng quảng cáo có điều kiện nên cấm mọi khẳng định điều trị.

    post_type: 'SALES' (mặc định) bắt buộc có nhãn tiếp thị liên kết + link + đúng
    một CTA. 'VALUE' (bài không bán hàng, xem core/valuepost.py) không quảng cáo
    sản phẩm cụ thể nên bỏ qua ba yêu cầu đó -- nhưng vẫn chịu mọi rào chắn nội
    dung khác (không tuyệt đối hoá, không bịa trải nghiệm, không cam kết công dụng).
    """
    problems = []
    flat = unicodedata.normalize("NFC", caption).lower()

    if len(caption) > MAX_LEN:
        problems.append(f"Dài {len(caption)} ký tự, Threads chỉ cho {MAX_LEN}")
    if post_type == "SALES":
        if disclosure.lower() not in flat:
            problems.append("Thiếu nhãn tiếp thị liên kết")
        if not re.search(r"https?://\S+", caption):
            problems.append("Thiếu link affiliate")
        if playbook.contains_multiple_cta(caption):
            problems.append("Chứa nhiều hơn một lời kêu gọi hành động (CTA)")

    for phrase in BANNED_SUPERLATIVES:
        if phrase in flat:
            problems.append(f"Chứa từ tuyệt đối hoá: “{phrase}”")
    for phrase in FABRICATED_EXPERIENCE:
        if phrase in flat:
            problems.append(f"Mô tả trải nghiệm cá nhân chưa từng có: “{phrase}”")
    for phrase in EFFICACY_CLAIMS:
        if phrase in flat:
            problems.append(f"Cam kết công dụng: “{phrase}”")

    # Cụm cấm theo chủ đề. Dò trên văn bản đã bỏ dấu để bắt cả biến thể viết
    # không dấu -- "tri mun" và "trị mụn" đều phải chặn.
    for phrase in niche.contains_banned(caption, niches):
        problems.append(f"Khẳng định điều trị, cấm với nhóm hàng có điều kiện: “{phrase}”")

    return problems
