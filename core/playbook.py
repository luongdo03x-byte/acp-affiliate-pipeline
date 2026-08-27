"""9 hook mở đầu + thư viện CTA (bước 6 của BANGIAO.md).

Mỗi hook là một góc tiếp cận tâm lý khác nhau cho CÙNG một sản phẩm. Hook được
gán làm variant_code khi tạo bài (xem pipeline.plan_content), nên hiệu quả của
từng hook đo được trực tiếp qua sub3 -- attribution.encode_sub_ids() gắn
variant_code vào sub3, reconcile_transactions() đọc lại từ đó. Không cần đoán
hook nào tốt hơn, cứ nhìn EPC theo sub3.

CTA (kêu gọi hành động) tách khỏi hook: một bài CHỈ được dùng ĐÚNG MỘT CTA.
Trộn nhiều CTA trong một bài làm loãng hành động mong muốn -- content.validate()
chặn vi phạm này bằng contains_multiple_cta().
"""
import random


def _fmt_vnd(v: int) -> str:
    return f"{v:,}đ".replace(",", ".")


def _get(product, key, default=""):
    try:
        v = product[key]
    except (KeyError, TypeError, IndexError):
        v = getattr(product, key, default)
    return v if v is not None else default


def _social_bits(product) -> str:
    # Lượt mua bị bỏ khỏi social proof (quyết định vận hành 2026-08): đếm số
    # "50.000 người mua rồi" là khuôn công nghiệp dễ bị lặp trên cả feed.
    # Giữ duy nhất điểm đánh giá khi đủ mẫu làm tín hiệu chất lượng.
    rating = _get(product, "rating", 0) or 0
    reviews = _get(product, "review_count", 0) or 0
    if rating and reviews >= 20:
        return f"đánh giá {rating:g}/5"
    return ""


def _h_gia_giam(product, discount_pct):
    pct = max(1, round((discount_pct or 0) * 100))
    return (f"Giá đang treo {_fmt_vnd(_get(product, 'current_price', 0))}, mềm hơn tầm {pct}% "
            "so với bình thường -- thấy hời nên để lại đây.")


def _h_so_sanh(product, discount_pct):
    return "So mấy món cùng tầm giá thì cái này có vẻ đáng tiền hơn hẳn."


def _h_khan_hiem(product, discount_pct):
    # Không được bịa khan hiếm khi không có dữ liệu tồn kho -- giọng giữ ở mức
    # "đáng cân nhắc sớm" thay vì "sắp hết".
    return "Món này nhìn đi nhìn lại vẫn thấy đáng để ý, ai đang cần thì xem sớm nhé."


def _h_cau_hoi(product, discount_pct):
    return "Có ai đang tìm món kiểu này không?"


def _h_xa_hoi(product, discount_pct):
    bits = _social_bits(product)
    if bits:
        return f"Thấy {bits}, để lại đây cho ai đang cần."
    return "Lướt thấy món này trông ổn, để lại đây cho ai quan tâm."


def _h_hang_moi(product, discount_pct):
    return "Mới thấy món này, để lại thông tin cơ bản cho ai đang cần."


def _h_tiet_kiem(product, discount_pct):
    return "Mua đúng lúc này thì đỡ được một khoản, để lại thông tin cho ai cần."


def _h_canh_bao(product, discount_pct):
    return "Giá đang thấp hơn mức thường thấy, sợ lên lại nên chia sẻ luôn."


def _h_truc_tiep(product, discount_pct):
    return f"{_get(product, 'name', '')[:100]} — đang có giá {_fmt_vnd(_get(product, 'current_price', 0))}."


# Mã hook giữ nguyên vĩnh viễn một khi đã dùng để đo -- đổi mã là mất lịch sử
# so sánh theo sub3. Thêm hook mới thì thêm mã mới, không sửa mã cũ.
HOOKS = {
    "H1_GIAGIAM":  {"name": "Giảm giá",      "render": _h_gia_giam},
    "H2_SOSANH":   {"name": "So sánh",       "render": _h_so_sanh},
    "H3_KHANHIEM": {"name": "Khan hiếm",     "render": _h_khan_hiem},
    "H4_CAUHOI":   {"name": "Câu hỏi",       "render": _h_cau_hoi},
    "H5_XAHOI":    {"name": "Bằng chứng xã hội", "render": _h_xa_hoi},
    "H6_HANGMOI":  {"name": "Hàng mới để ý", "render": _h_hang_moi},
    "H7_TIETKIEM": {"name": "Tiết kiệm",     "render": _h_tiet_kiem},
    "H8_CANHBAO":  {"name": "Cảnh báo giá",  "render": _h_canh_bao},
    "H9_TRUCTIEP": {"name": "Trực tiếp",     "render": _h_truc_tiep},
}

CTA_LIBRARY = [
    "Xem giá và đặt mua tại đây:",
    "Ai cần thì bấm vào link bên dưới:",
    "Thông tin đầy đủ mình để ở link:",
    "Quan tâm thì xem thêm ở đây:",
    "Ai đang tìm món này thì lưu link nhé:",
]


def hook_codes() -> list:
    """Danh sách mã hook theo đúng thứ tự khai báo -- dùng để xoay vòng làm biến thể."""
    return list(HOOKS)


def pick_hook(code: str = None, rng: random.Random = None) -> str:
    if code and code in HOOKS:
        return code
    rng = rng or random.Random()
    return rng.choice(hook_codes())


def render_hook(code: str, product, discount_pct: float = 0.0) -> str:
    hook = HOOKS.get(code) or HOOKS[hook_codes()[0]]
    return hook["render"](product, discount_pct or 0.0)


def pick_cta(rng: random.Random = None) -> str:
    rng = rng or random.Random()
    return rng.choice(CTA_LIBRARY)


def contains_multiple_cta(caption: str) -> bool:
    """True nếu caption chứa từ hai cụm CTA trong thư viện trở lên."""
    flat = (caption or "").lower()
    count = sum(1 for c in CTA_LIBRARY if c.lower() in flat)
    return count > 1
