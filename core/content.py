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
from .system_settings import content_guards_disabled as _guards_disabled

MAX_LEN = 500

# Trùng đúng Publisher.max_caption_length ở adapters/base.py (mặc định),
# adapters/mock.py, adapters/live.py cho từng platform -- 2 nguồn cùng giá
# trị, sửa 1 chỗ nhớ sửa chỗ kia (không lấy động từ ctx["publishers"] vì
# approve_post() không nhận ctx).
PLATFORM_MAX_LEN = {"threads": 500, "facebook": 63206, "instagram": 2200}

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

# Biến thể KHÔNG nhắc giá -- chọn xen kẽ (~50%) theo rng của caller (pipeline
# xoay seed theo bài nên phân bổ đều); khi không có rng thì băm từ định danh
# sản phẩm để vừa cố định giữa các lần chạy, vừa khác nhau giữa các bài --
# tránh cả feed cùng một khuôn "giá X đ".
NO_PRICE_TEMPLATES = {
    "price_drop": (
        "{name}\n\n"
        "Giá mấy nay mềm hơn trước, ai đang cần thì xem ở link dưới. "
        "{social}"
    ),
    "spec_highlight": (
        "{name}\n\n"
        "{social} Bên bán mô tả: {highlight}."
    ),
    "deal_roundup": (
        "Lướt nhóm {category} hôm nay thấy món này đáng để ý:\n\n"
        "{name}. {social}"
    ),
    "comparison": (
        "{name} là món khá ổn trong nhóm đồ cùng loại hiện đang bán. "
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
    # Lượt mua bị bỏ theo quyết định vận hành 2026-08: social proof bằng số
    # dễ khiến caption thành "đếm số" kiểu công nghiệp; giữ lại duy nhất
    # điểm đánh giá khi đủ mẫu (>=20 review) làm tín hiệu chất lượng.
    rating, reviews = product["rating"] or 0, product["review_count"] or 0
    if rating and reviews >= 20:
        return f"Cũng có đánh giá {rating:g}/5."
    return ""


_SHOP_SUFFIX_RE = re.compile(r"[ \t]*[_|]\s*([A-Za-z0-9][A-Za-z0-9]*(?:\.[A-Za-z0-9]+)+)\s*$")


def _strip_shop_suffix(name, shop: str = None):
    """Nhiều seller Shopee nhét tên shop vào cuối tên sản phẩm (vd. '..._Linhchi.studio'),
    đọc như tên file chứ không phải lời người thật -- cắt bỏ trước khi dùng trong caption.

    Ưu tiên cắt đúng theo `shop` đã biết (chính xác nhất). Không có/không khớp thì
    suy đoán bằng heuristic: hậu tố sau dấu '_' hoặc '|', không khoảng trắng, có
    dạng tên miền (chứa dấu chấm) -- để không nhầm với đơn vị đo ("500ml", "5L")
    hay các đoạn tên sản phẩm có khoảng trắng bình thường."""
    if not name:
        return name
    text = name.rstrip()
    if shop:
        shop = shop.strip()
        for sep in ("_", "-", "|"):
            suffix = f"{sep}{shop}"
            if text.lower().endswith(suffix.lower()):
                return text[: -len(suffix)].rstrip(" _-|.,")
    m = _SHOP_SUFFIX_RE.search(text)
    if m:
        return text[: m.start()].rstrip(" _-|.,")
    return text


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


# Danh mục thiên về "bán bằng đặc điểm": người mua quan tâm chất liệu/an toàn/
# tương thích trước, giá chỉ là điều kiện kèm theo -- nhắc giá sớm dễ khiến bài
# thành bảng giá. Ngược lại các nhóm tiêu dùng nhanh thì giá/deal là lý do chốt.
_FEATURE_LED_CATEGORIES = {"me-va-be", "thoi-trang", "phu-kien-cong-nghe",
                           "the-thao", "my-pham"}
_PRICE_LED_CATEGORIES = {"gia-dung", "cham-soc-ca-nhan", "thu-cung"}


def price_lead_weight(product, discount_pct: float = 0.0) -> float:
    """Xác suất NÊN nhắc giá trong caption (0..1), dựa trên SẢN PHẨM chứ không
    tung xu mù:

    - deal thật sâu (>=15%)          -> +0.25 : mức giảm chính là câu chuyện;
                                        dùng giảm THẬT theo original_price,
                                        fallback tham số khi không có giá gốc.
    - giá rẻ impulse (<=100k)        -> +0.20 : "chỉ 29k" là hook tự nhiên nhất.
    - giá cao (>=1 triệu)            -> -0.25 : đừng dọa người đọc bằng giá
                                        ngay dòng đầu, hãy bán bằng đặc điểm.
    - danh mục feature-led           -> -0.12 ; price-led -> +0.08.
    Kết quả kẹp trong [0.2, 0.85] -- không bao giờ cấm hẳn cũng không bao giờ
    bắt buộc tuyệt đối, để feed vẫn có biến thể.
    """
    price = product.get("current_price") or 0
    orig = product.get("original_price") or 0
    real_discount = ((orig - price) / orig if orig > price > 0
                     else (discount_pct or 0))
    weight = 0.55
    if real_discount >= 0.15:
        weight += 0.25
    if 0 < price <= 100_000:
        weight += 0.20
    elif price >= 1_000_000:
        weight -= 0.25
    code = str(product.get("category_code") or "")
    if code in _FEATURE_LED_CATEGORIES:
        weight -= 0.12
    elif code in _PRICE_LED_CATEGORIES:
        weight += 0.08
    return max(0.2, min(0.85, weight))


def _include_price(template_code: str, product, rng, discount_pct: float = 0.0) -> bool:
    """Giá KHÔNG bắt buộc phải xuất hiện trong caption (quyết định vận hành
    2026-08): xác suất theo price_lead_weight() của CHÍNH sản phẩm này. Có rng
    thì xoay theo caller (pipeline seed theo lượt plan); không có rng thì băm
    từ định danh sản phẩm -- cố định giữa các lần chạy nhưng khác nhau giữa
    các sản phẩm."""
    weight = price_lead_weight(product, discount_pct)
    if rng is not None:
        return rng.random() < weight
    key = str(product.get("id") or product.get("external_product_id")
              or product.get("name") or "")
    # Roll đều 0..1 cố định theo sản phẩm: P(roll < weight) đúng bằng weight.
    return random.Random(f"price:{key}:{template_code}").random() < weight


def generate(product, template_code: str, affiliate_link: str,
             discount_pct: float = 0.0, disclosure: str = '',
             hook_code: str = None, rng: random.Random = None) -> str:
    """Sinh caption hoàn chỉnh theo cấu trúc HOOK -> THÂN -> MỘT CTA -> DISCLOSURE.

    hook_code chọn dòng mở đầu (core/playbook.py). Bỏ trống thì bốc ngẫu nhiên --
    nhưng pipeline.plan_content() luôn truyền vào một mã cụ thể để xoay vòng hook
    làm biến thể đo bằng sub3. Caption trả về CHƯA qua validate().
    """
    # dict(product) chuẩn hoá cả dict thường lẫn sqlite3.Row về cùng kiểu, để
    # gán lại "name" đã cắt hậu tố shop -- áp dụng cho MỌI nơi đọc product["name"]
    # phía sau, kể cả playbook.render_hook().
    product = dict(product)
    product["name"] = _strip_shop_suffix(product.get("name"), product.get("shop"))

    caller_rng = rng
    rng = rng or random.Random()
    hook_code = playbook.pick_hook(hook_code, rng=rng)
    hook_line = playbook.render_hook(hook_code, product, discount_pct)
    cta_line = playbook.pick_cta(rng=rng)
    social = _social_proof(product)
    template_source = (TEMPLATES[template_code]
                       if _include_price(template_code, product, caller_rng, discount_pct)
                       else NO_PRICE_TEMPLATES[template_code])
    body = template_source.format(
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
        # LLM là lớp làm mượt tuỳ chọn, không phải nguồn tin cậy duy nhất -- lỗi
        # mạng/hết quota không được làm hỏng việc tạo bài, và output phải còn
        # nguyên affiliate link mới được chấp nhận (không chỉ dựa vào chỉ dẫn
        # trong prompt). Không log nguyên exception vì có thể lộ chi tiết key.
        try:
            # Prompt chung của operator (nếu đặt) thắng prompt mặc định --
            # xem core/prompt_template.py cho token và nguồn ưu tiên.
            from . import prompt_template
            custom = prompt_template.get_custom_template_with_file()
            prompt = (prompt_template.render(custom, product, full, affiliate_link)
                      if custom else _build_prompt(product, full))
            rewritten = _llm_fn(prompt)
        except Exception as e:
            rewritten = None
            print(f"  ! caption LLM lỗi ({type(e).__name__}), dùng bản nháp deterministic")
        if rewritten and affiliate_link in rewritten:
            full = rewritten
    return _fit(full, disclosure)


def _build_prompt(product, draft: str) -> str:
    # Danh sách cấm cập nhật theo lỗi thật gặp phải ở output LLM -- caption
    # "công nghiệp" gần như luôn rơi vào vài mẫu câu này. Kỹ thuật giọng văn
    # lấy từ phương pháp viết Threads tự nhiên: viết cho MỘT người, câu ngắn
    # xuống dòng, dòng đầu chạm tò mò/cảm xúc từ DỮ LIỆU THẬT (giá giảm, số
    # người mua) -- tuyệt đối không mượn trải nghiệm cá nhân không có thật.
    orig = product.get("original_price") or 0
    cur = product.get("current_price") or 0
    real_discount = round((orig - cur) / orig * 100) if orig > cur > 0 else 0
    lead = ("Món này RẺ hoặc đang deal sâu: được phép dẫn bằng giá -- con số "
            "tiền/giảm giá là điểm mở đầu tự nhiên."
            if price_lead_weight(product) >= 0.6 else
            "Món này NÊN dẫn bằng đặc điểm thật trong tên sản phẩm (chất liệu, "
            "đối tượng dùng, tính năng); nếu nhắc giá thì để nhẹ ở sau, đừng mở đầu bằng tiền.")
    return (
        "Viết lại đoạn giới thiệu sản phẩm dưới đây thành status Threads như "
        "bạn đang NHẮN TIN CHO MỘT NGƯỜI BẠN, không phải quảng cáo cho đám đông.\n"
        f"SỰ THẬT GIÁ bắt buộc tuân theo: mức giảm thật của sản phẩm là "
        f"{real_discount}%. Nếu con số này dưới 5 thì TUYỆT ĐỐI KHÔNG được dùng "
        "từ 'giảm giá', 'sale', 'ưu đãi', 'hời' -- chỉ được nhắc đến giá hiện tại.\n"
        f"HƯỚNG GIỌNG THEO LOẠI SẢN PHẨM: {lead}\n"
        "RÀNG BUỘC BẮT BUỘC:\n"
        "- Chỉ dùng thông tin có trong đoạn gốc. Không thêm chi tiết, số liệu, "
        "hay đánh giá nào khác. KHÔNG bịa tình trạng khan hiếm/hết hàng.\n"
        "- KHÔNG viết như đã từng dùng sản phẩm. Không nói 'mình đã dùng', "
        "'da mình', 'nhà mình đang dùng', không kể chuyện cá nhân bịa ra.\n"
        "- Không dùng từ tuyệt đối hoá: tốt nhất, số 1, duy nhất.\n"
        "- Không cam kết công dụng (chữa khỏi, trị dứt điểm...).\n"
        "- CẤM các mẫu câu bán hàng sáo rỗng: 'chốt đơn ngay', 'siêu phẩm', "
        "'đừng bỏ lỡ', 'số lượng có hạn', 'cơ hội vàng', 'deal hời lịch sử', "
        "'freeship toàn quốc', 'nhanh tay kẻo hết', 'cháy hàng', 'giá sốc'.\n"
        "- Câu ngắn dài xen kẽ, xuống dòng tự nhiên như status; không đoạn văn "
        "dài liền khối.\n"
        "- Nếu đoạn gốc không nhắc giá tiền thì đừng tự thêm con số tiền vào; "
        "nhắc lượt mua cũng không cần.\n"
        "- Dòng đầu phải khiến người lướt DỪNG lại: chọn điều đáng chú ý nhất "
        "TRONG dữ liệu gốc (mức giảm giá thật nếu có, giá rẻ hơn mặt bằng, "
        "số người đã mua...) và nói thẳng nó ra.\n"
        "- Tối đa 1 emoji, không chuỗi emoji, không hashtag ngoài nhãn tiếp thị "
        "liên kết sẵn có.\n"
        "- Không markdown (không **, không #, không gạch đầu dòng).\n"
        "- Giữ NGUYÊN URL ở cuối. Tối đa 380 ký tự.\n\n"
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
             post_type: str = "SALES", max_len: int = MAX_LEN) -> list:
    """Trả về danh sách vi phạm. Rỗng nghĩa là được phép đưa vào hàng đợi duyệt.

    niches: danh sách mã chủ đề đang bật. Mỗi chủ đề có thể thêm cụm cấm riêng --
    mỹ phẩm là nhóm hàng quảng cáo có điều kiện nên cấm mọi khẳng định điều trị.

    post_type: 'SALES' (mặc định) bắt buộc có nhãn tiếp thị liên kết + link + đúng
    một CTA. 'VALUE' (bài không bán hàng, xem core/valuepost.py) không quảng cáo
    sản phẩm cụ thể nên bỏ qua ba yêu cầu đó -- nhưng vẫn chịu mọi rào chắn nội
    dung khác (không tuyệt đối hoá, không bịa trải nghiệm, không cam kết công dụng).

    max_len: giới hạn ký tự theo platform sẽ nhận caption này -- mặc định 500
    (Threads). Facebook/Instagram có giới hạn khác, xem PLATFORM_MAX_LEN.
    """
    problems = []
    flat = unicodedata.normalize("NFC", caption).lower()

    if len(caption) > max_len:
        problems.append(f"Dài {len(caption)} ký tự, giới hạn {max_len}")

    # Công tắc vận hành: BỎ RÀO CHẾN NỘI DUNG -- operator tự chịu trách nhiệm
    # về nội dung. Giữ lại duy nhất rào kỹ thuật độ dài ở trên (nền tảng từ
    # chối caption vượt giới hạn, đăng sẽ hỏng). Xem system_settings
    # .content_guards_disabled() và /chamdiem để bật/tắt.
    if not _guards_disabled():
        for phrase in EFFICACY_CLAIMS:
            if phrase in flat:
                problems.append(f"Cam kết công dụng: “{phrase}”")

        # Cụm cấm theo chủ đề. Dò trên văn bản đã bỏ dấu để bắt cả biến thể viết
        # không dấu -- "tri mun" và "trị mụn" đều phải chặn.
        for phrase in niche.contains_banned(caption, niches):
            problems.append(f"Khẳng định điều trị, cấm với nhóm hàng có điều kiện: “{phrase}”")

    return problems
