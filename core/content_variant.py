"""Content Core -- sinh 3 variant (angle/hook/body/cta) cho 1 sản phẩm
(Content Engine v2, PTYC mục 12, 18-22).

Không đụng core/pipeline.py/core/content.py -- dormant như E1/E2, chưa nối
vào luồng tạo bài thật (việc của E6). Trả ContentVariant (field riêng,
CHƯA ghép thành chuỗi) -- E5 (Platform Adaptation) tự ghép theo platform.
"""
import random
from dataclasses import dataclass

from . import content_angle, content_hook


@dataclass(frozen=True)
class ContentVariant:
    angle: str
    hook: str
    main_message: str
    body: list
    cta: str
    structure: str


STRUCTURES = [
    "HOOK_VALUE_CTA", "PROBLEM_SOLUTION_RESULT", "STORY_LESSON_MESSAGE",
    "MISTAKE_INSIGHT", "DEAL_BENEFIT_CTA", "USE_CASE_VALUE_CTA",
]

ANGLE_TO_STRUCTURE = {
    "DEAL_PRICE": "DEAL_BENEFIT_CTA",
    "USE_CASE": "USE_CASE_VALUE_CTA",
    "PERSONAL_RECOMMENDATION": "HOOK_VALUE_CTA",
}

CTA_TYPES = ["VIEW_PRODUCT", "CHECK_PRICE", "COMMENT", "SAVE", "SHARE", "ASK_OPINION"]

ANGLE_TO_CTA_TYPE = {
    "DEAL_PRICE": "CHECK_PRICE",
    "USE_CASE": "VIEW_PRODUCT",
    "PERSONAL_RECOMMENDATION": "ASK_OPINION",
}

CTA_POOL = {
    "CHECK_PRICE": [
        "Giá hiện tại mình để ở link.",
        "Ai đang tìm mẫu này thì xem giá ở link.",
    ],
    "VIEW_PRODUCT": [
        "Mình để link để bạn xem thêm.",
        "Xem chi tiết ở link nhé.",
    ],
    "ASK_OPINION": [
        "Bạn nghĩ sao về món này?",
        "Ai đã dùng rồi cho mình xin ý kiến với.",
    ],
}


def _template_body(angle: str, facts) -> tuple:
    """Dựng main_message/body deterministic theo angle -- không cần LLM.
    Task 2 thêm nhánh LLM (generate_body() gọi hàm này làm fallback).
    """
    price = f"{facts.price:,}đ".replace(",", ".")
    fact_line = facts.facts[0] if facts.facts else ""
    if angle == "DEAL_PRICE":
        main_message = "Giá hiện tại đáng chú ý"
        body = [f"Đang bán {price}."] + ([fact_line] if fact_line else [])
    elif angle == "USE_CASE":
        main_message = fact_line or f"{facts.name} dùng được ngay"
        body = [f"Giá {price}."]
    else:
        main_message = f"{facts.name} đáng để cân nhắc"
        body = [f"Giá {price}."] + ([fact_line] if fact_line else [])
    return main_message, body[:2]


def generate_body(angle: str, hook: str, structure: str, facts) -> tuple:
    """(main_message, body). Task 1: luôn dùng template. Task 2 thêm nhánh
    LLM (set_body_generator) gọi trước khi fallback về đây.
    """
    return _template_body(angle, facts)


def generate_variant(angle: str, facts, rng=None) -> ContentVariant:
    rng = rng or random.Random()
    hook = content_hook.select_best_hook(angle, facts)["hook"]
    structure = ANGLE_TO_STRUCTURE.get(angle, "HOOK_VALUE_CTA")
    main_message, body = generate_body(angle, hook, structure, facts)
    cta_type = ANGLE_TO_CTA_TYPE.get(angle, "VIEW_PRODUCT")
    cta = rng.choice(CTA_POOL[cta_type])
    return ContentVariant(angle=angle, hook=hook, main_message=main_message,
                           body=body, cta=cta, structure=structure)


def generate_variants(facts, product, rng=None) -> list:
    """1 ContentVariant / angle distinct từ select_angle_candidates() (E2)
    -- 1-3 phần tử tuỳ dữ liệu sản phẩm, xem spec E3 §2 (không ép đủ 3).
    """
    rng = rng or random.Random()
    angles = content_angle.select_angle_candidates(product)
    return [generate_variant(a, facts, rng) for a in angles]
