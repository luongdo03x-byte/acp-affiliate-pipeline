"""Angle Selector -- chọn content angle theo product (Content Engine v2, PTYC mục 10-11).

Không đụng core/pipeline.py/core/content.py -- dormant như E1, chưa nối vào
luồng tạo bài thật (việc của E6). P0 chỉ cài rule có tín hiệu khách quan từ
product (giá, category) -- xem spec E2 mục 2, 3 cho lý do 8/11 angle chưa
tự chọn được (thiếu AudienceContext).
"""

ANGLES = [
    "DEAL_PRICE", "PAIN_POINT", "CURIOSITY", "PERSONAL_RECOMMENDATION",
    "PROBLEM_SOLUTION", "USE_CASE", "COMPARISON", "SOCIAL_PROOF",
    "MISTAKE_LESSON", "EDUCATIONAL", "BOLD_OPINION",
]

MIN_DISCOUNT_PCT = 0.05
_USE_CASE_CATEGORIES = {"gia-dung", "phu-kien-cong-nghe"}
_PERSONAL_REC_CATEGORIES = {"thoi-trang", "cham-soc-ca-nhan"}


def select_angle_candidates(product) -> list:
    """Trả angle theo thứ tự ưu tiên (tốt nhất trước). Luôn có ít nhất 1
    phần tử, luôn kết thúc bằng PERSONAL_RECOMMENDATION (fallback trung tính).

    Không nhận ProductFacts: cả 3 rule chỉ cần original_price/current_price/
    category_code của product, không dùng gì từ ProductFacts -- thêm tham
    số không dùng là dead param (bài học từ check_fact_safety() ở E1).
    """
    candidates = []
    original = product["original_price"]
    current = product["current_price"]
    if original and current and original > current:
        discount_pct = (original - current) / original
        if discount_pct >= MIN_DISCOUNT_PCT:
            candidates.append("DEAL_PRICE")

    category = product["category_code"]
    if category in _USE_CASE_CATEGORIES:
        candidates.append("USE_CASE")
    elif category in _PERSONAL_REC_CATEGORIES:
        if "PERSONAL_RECOMMENDATION" not in candidates:
            candidates.append("PERSONAL_RECOMMENDATION")

    if "PERSONAL_RECOMMENDATION" not in candidates:
        candidates.append("PERSONAL_RECOMMENDATION")

    return candidates
