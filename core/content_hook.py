"""Hook Generator -- sinh + chấm + chọn hook tốt nhất theo angle (Content Engine v2, PTYC mục 13-15).

Không đụng core/pipeline.py/core/content.py -- dormant như E1, chưa nối vào
luồng tạo bài thật (việc của E6).
"""
from . import content_facts

HOOK_TYPES = [
    "CURIOSITY", "PAIN", "PRICE", "BOLD_STATEMENT", "QUESTION",
    "CONTRAST", "CONFESSION_STYLE", "SURPRISING_FACT",
]

THREADS_HOOK_WORD_TARGET = 12
_GENERIC_OPENINGS = ["sản phẩm này", "đây là"]

_hook_generator_fn = None
_hook_judge_fn = None


def set_hook_generator(fn):
    """fn(prompt: str) -> str. Model trả JSON thô (list[str], 5 phần tử).
    fn=None (mặc định) -- dùng 5 template cố định theo Hook Type.
    """
    global _hook_generator_fn
    _hook_generator_fn = fn


def set_hook_judge(fn):
    """fn(prompt: str) -> str. Model trả JSON thô (list[float], cùng thứ tự
    hooks đưa vào). fn=None (mặc định) -- dùng rule-based score.
    """
    global _hook_judge_fn
    _hook_judge_fn = fn


def _template_hooks(facts) -> list:
    """5 template cố định, KHÔNG đổi theo angle (giới hạn cố ý P0, xem spec
    E2 mục 4.1) -- deterministic, không cần LLM, dùng khi chưa đăng ký
    hook generator.
    """
    price = f"{facts.price:,}đ".replace(",", ".")
    name = facts.name
    return [
        f"{name} có gì mà nhiều người để ý vậy?",
        f"Đang tìm {name.lower()} mà chưa ưng cái nào?",
        f"{price} cho {name} — đáng để xem không?",
        f"{name} không phải lựa chọn cho tất cả mọi người.",
        f"Bạn đã thử {name} chưa?",
    ]


def check_hook_rules(hook: str, facts) -> list:
    """[] nghĩa là hook hợp lệ. Non-empty là vi phạm -- loại khỏi candidate
    ở select_best_hook(). Tái dùng content_facts.check_fact_safety() cho
    đúng ý "không clickbait sai sự thật" (PTYC mục 14) -- hook cũng là 1
    đoạn text có thể bịa y hệt caption.
    """
    problems = list(content_facts.check_fact_safety(hook))
    flat = (hook or "").strip().lower()
    if not flat:
        problems.append("Hook rỗng")
        return problems
    for opening in _GENERIC_OPENINGS:
        if flat.startswith(opening):
            problems.append(f'Mở đầu chung chung: “{opening}”')
    if facts.name and flat == facts.name.strip().lower():
        problems.append("Hook trùng y hệt tên sản phẩm, không có điểm nhấn")
    return problems
