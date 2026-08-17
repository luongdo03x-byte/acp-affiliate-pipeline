"""AI Judge + Hybrid Scoring + BEST selection + Anti-Repetition (Content
Engine v2, PTYC mục 28, 32-35).

Không đụng core/pipeline.py/core/content.py -- dormant như E1-E3, chưa
nối vào luồng tạo bài thật (việc của E6). Không sửa core/content_variant.py/
core/content_checker.py (E3, đã merge+review) -- chỉ import và gọi.
"""
import re
import unicodedata

from . import content_checker

_OPENING_WORDS = 5
_ANGLE_FREQUENCY_WINDOW = 5
_ANGLE_FREQUENCY_THRESHOLD = 0.6
_SIMILARITY_THRESHOLD = 0.6

_REPETITION_PENALTY = {
    "same_opening": 0.15,
    "same_hook_formula": 0.3,
    "same_angle_too_often": 0.1,
    "same_cta": 0.1,
    "high_text_similarity": 0.25,
}


def _variant_text(variant) -> str:
    """Trùng lặp nhỏ có chủ đích với content_checker._variant_text() --
    hàm private, không import chéo qua tên có gạch dưới đầu (đúng tiền lệ
    E3 chấp nhận trùng _GENERIC_OPENINGS với E2).
    """
    return " ".join([variant.hook, variant.main_message, " ".join(variant.body), variant.cta])


def _tokenize(text: str) -> set:
    return set(re.findall(r"\w+", unicodedata.normalize("NFC", text or "").lower()))


def _first_n_words(text: str, n: int) -> tuple:
    words = unicodedata.normalize("NFC", text or "").lower().split()
    return tuple(words[:n])


def check_repetition(variant, recent_variants: list) -> list:
    """list[dict] {"rule": ..., "message": ...}. [] nghĩa là không trùng
    bài gần đây nào. recent_variants nên sắp mới nhất trước (trách nhiệm
    của caller) -- chỉ dùng _ANGLE_FREQUENCY_WINDOW phần tử đầu cho rule
    same_angle_too_often. Mỗi rule tối đa 1 dict (boolean, không đếm số
    bài bị trùng).
    """
    violations = []
    if not recent_variants:
        return violations

    v_opening = _first_n_words(variant.hook, _OPENING_WORDS)
    if v_opening and any(_first_n_words(r.hook, _OPENING_WORDS) == v_opening for r in recent_variants):
        violations.append({"rule": "same_opening", "message": "Trùng mở đầu với bài gần đây"})

    v_hook = unicodedata.normalize("NFC", variant.hook or "").strip().lower()
    if v_hook and any(unicodedata.normalize("NFC", r.hook or "").strip().lower() == v_hook for r in recent_variants):
        violations.append({"rule": "same_hook_formula", "message": "Hook trùng y hệt bài gần đây"})

    window = recent_variants[:_ANGLE_FREQUENCY_WINDOW]
    if window:
        same_angle_count = sum(1 for r in window if r.angle == variant.angle)
        if same_angle_count / len(window) > _ANGLE_FREQUENCY_THRESHOLD:
            violations.append({"rule": "same_angle_too_often",
                                "message": f"Angle {variant.angle} lặp {same_angle_count}/{len(window)} bài gần đây"})

    v_cta = unicodedata.normalize("NFC", variant.cta or "").strip().lower()
    if v_cta and any(unicodedata.normalize("NFC", r.cta or "").strip().lower() == v_cta for r in recent_variants):
        violations.append({"rule": "same_cta", "message": "CTA trùng y hệt bài gần đây"})

    v_words = _tokenize(_variant_text(variant))
    for r in recent_variants:
        r_words = _tokenize(_variant_text(r))
        if v_words and r_words:
            jaccard = len(v_words & r_words) / len(v_words | r_words)
            if jaccard > _SIMILARITY_THRESHOLD:
                violations.append({"rule": "high_text_similarity",
                                    "message": f"Độ giống {jaccard:.0%} với bài gần đây"})
                break

    return violations


def repetition_penalty(variant, recent_variants: list) -> float:
    return sum(_REPETITION_PENALTY[v["rule"]] for v in check_repetition(variant, recent_variants))


_hybrid_judge_fn = None


def set_hybrid_judge(fn):
    """fn(prompt: str) -> str. Model trả JSON thô {"hook_strength": 0-1,
    "readability": 0-1, "relevance": 0-1, "originality": 0-1}.
    fn=None (mặc định) -- mỗi yếu tố mặc định = rule_score, không bịa
    điểm AI giả khi chưa có judge.
    """
    global _hybrid_judge_fn
    _hybrid_judge_fn = fn


def score_variant_hybrid(variant) -> dict:
    """{"rules": RuleScore, "judge": dict, "hybrid_score": float}.
    Task 2: chưa gọi LLM thật (_hybrid_judge_fn luôn None ở bước này) --
    Task 3 thêm nhánh LLM đầy đủ.
    """
    rules = content_checker.score_variant_rules(variant)
    if not rules.fact_safety_pass:
        return {"rules": rules, "judge": {}, "hybrid_score": 0.0}
    if _hybrid_judge_fn is None:
        judge = {k: rules.score for k in ("hook_strength", "readability", "relevance", "originality")}
    else:
        judge = {k: rules.score for k in ("hook_strength", "readability", "relevance", "originality")}
    hybrid_score = round((rules.score + sum(judge.values()) / 4) / 2, 4)
    return {"rules": rules, "judge": judge, "hybrid_score": hybrid_score}
