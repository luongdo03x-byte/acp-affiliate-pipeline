"""Bài không bán hàng -- "phương pháp 3 bài" (bước 7 của BANGIAO.md).

Xen bài giá trị giữa các bài bán hàng để kênh không toàn quảng cáo: người theo
dõi ở lại vì thấy thông tin hữu ích, không chỉ vì link mua hàng. Ba loại bài,
không loại nào gắn với MỘT sản phẩm cụ thể nên post.product_id để trống:

  1. price_level    -- mặt bằng giá một nhóm hàng trong 30 ngày qua
  2. real_discount   -- điểm qua vài món đang giảm giá thật, không phải giá ảo
  3. checklist       -- những điều nên xem trước khi mua một nhóm hàng

Không có affiliate link, không có CTA -- content.validate(..., post_type='VALUE')
bỏ qua ba yêu cầu chỉ áp dụng cho bài bán hàng. Vẫn chịu mọi rào chắn nội dung
khác (không tuyệt đối hoá, không bịa trải nghiệm, không cam kết công dụng).
"""
import random

DISCLOSURE_VALUE = "Bài tổng hợp thông tin, không phải nội dung được tài trợ."

KINDS = ("price_level", "real_discount", "checklist")

# Mỗi nhóm 4-5 tiêu chí tham khảo khi mua -- viết theo tiêu chí khách quan
# (thông số, chính sách, cách đọc đánh giá), không phải cam kết công dụng.
CHECKLISTS = {
    "thoi-trang-nu": ["chất liệu vải ghi trong mô tả", "bảng size của đúng shop đó, không suy từ shop khác",
                       "chính sách đổi trả nếu sai size", "ảnh đánh giá thật từ người mua"],
    "thoi-trang-nam": ["chất liệu và form dáng ghi trong mô tả", "bảng size của đúng shop đó",
                        "chính sách đổi trả nếu sai size", "ảnh đánh giá thật từ người mua"],
    "my-pham": ["bảng thành phần đầy đủ", "hạn sử dụng còn lại khi nhận hàng", "dung tích thật ghi trên bao bì",
                "nguồn gốc/tem nhập khẩu nếu có", "đánh giá có ảnh thật, không chỉ đánh giá chữ"],
    "me-va-be": ["chứng nhận an toàn nếu là đồ dùng trực tiếp cho bé", "kích thước/độ tuổi phù hợp",
                 "chất liệu không mùi lạ", "chính sách đổi trả"],
    "thu-cung": ["kích cỡ phù hợp với vật nuôi nhà mình", "chất liệu an toàn khi cắn/liếm phải",
                 "hạn sử dụng nếu là thức ăn/hạt", "đánh giá từ người đã mua cùng loại thú cưng"],
    "gia-dung": ["công suất tiêu thụ điện", "dung tích/kích thước phù hợp số người dùng",
                 "thời gian và điều kiện bảo hành", "đánh giá thực tế trên trang bán"],
    "cong-nghe": ["thông số kỹ thuật khớp với thiết bị đang dùng", "thời gian bảo hành",
                  "chuẩn kết nối/cổng cắm", "đánh giá thực tế trên trang bán"],
    "the-thao": ["chất liệu và độ bền khi dùng ngoài trời/tiếp xúc mồ hôi", "kích thước/trọng lượng phù hợp",
                 "chính sách đổi trả", "đánh giá thực tế trên trang bán"],
}
GENERIC_CHECKLIST = ["thông số ghi trong mô tả có khớp nhu cầu không", "chính sách đổi trả",
                      "thời gian bảo hành nếu có", "đánh giá thực tế trên trang bán"]


def _fmt_vnd(v) -> str:
    return f"{int(v):,}đ".replace(",", ".")


def price_level_text(niche_name: str, median_price) -> str:
    """Bài mặt bằng giá. median_price=None nghĩa là chưa đủ dữ liệu 30 ngày -- gọi
    nơi khác phải tự kiểm tra trước khi gọi hàm này."""
    return (
        f"Mặt bằng giá nhóm {niche_name} trong 30 ngày qua đang quanh mức "
        f"{_fmt_vnd(median_price)}. Ai đang so sánh giá thì lưu lại làm mốc tham khảo, "
        f"tránh bị hét giá cao hơn mặt bằng chung.\n\n{DISCLOSURE_VALUE}"
    )


def real_discount_text(niche_name: str, products: list) -> str:
    """products: danh sách dict có 'name', 'current_price', 'discount_pct' -- tối đa 3 món,
    đã lọc sẵn ở tầng gọi (chỉ đưa vào món có giảm giá thật đo được, không phải giá ảo)."""
    lines = "\n".join(
        f"- {p['name'][:70]} — {_fmt_vnd(p['current_price'])} "
        f"(giảm khoảng {max(1, round(p['discount_pct'] * 100))}% so với mặt bằng 30 ngày)"
        for p in products[:3]
    )
    return (
        f"Vài món trong nhóm {niche_name} đang giảm giá thật so với mặt bằng 30 ngày qua, "
        f"tổng hợp lại cho ai đang cần:\n\n{lines}\n\n{DISCLOSURE_VALUE}"
    )


def checklist_text(niche_code: str, niche_name: str) -> str:
    items = CHECKLISTS.get(niche_code) or GENERIC_CHECKLIST
    body = "\n".join(f"{i + 1}. {it}" for i, it in enumerate(items))
    return (
        f"Trước khi mua đồ nhóm {niche_name}, để ý mấy điều này cho đỡ mua nhầm:\n\n"
        f"{body}\n\n{DISCLOSURE_VALUE}"
    )


def build(kind: str, niche_code: str = None, niche_name: str = "sản phẩm đang theo dõi",
          median_price=None, discounted_products: list = None) -> str:
    """Điều phối theo kind. Trả về None nếu thiếu dữ liệu cần thiết cho loại đó --
    người gọi (pipeline.create_value_post) phải xử lý None bằng cách báo lỗi rõ
    thay vì đăng một bài rỗng."""
    if kind == "price_level":
        if median_price is None:
            return None
        return price_level_text(niche_name, median_price)
    if kind == "real_discount":
        if not discounted_products:
            return None
        return real_discount_text(niche_name, discounted_products)
    if kind == "checklist":
        return checklist_text(niche_code, niche_name)
    raise ValueError(f"Loại bài giá trị không hợp lệ: {kind!r} (chọn: {', '.join(KINDS)})")


def pick_kind(rng: random.Random = None) -> str:
    rng = rng or random.Random()
    return rng.choice(KINDS)
