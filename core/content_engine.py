"""Content Engine v2 -- orchestrate E1-E5 thành 1 lần chạy, lưu kết quả để
sống qua nhiều request (chọn/regenerate variant), trả BEST variant + caption
theo platform (Content Engine v2, E6 tích hợp).

Khác E1-E5 (dormant, pure function) -- module này CÓ ghi DB
(content_generation_run/content_variant_row). Vẫn KHÔNG gọi
approve_post()/publisher/publish -- chỉ sinh + lưu (PTYC mục 55).
"""
import json

from . import content_facts, content_variant, content_scoring, content_platform, content_hook, content_angle
from .db import audit, now, ulid


def _row_to_variant(row) -> content_variant.ContentVariant:
    return content_variant.ContentVariant(
        angle=row["angle"], hook=row["hook"], main_message=row["main_message"],
        body=json.loads(row["body_json"]), cta=row["cta"], structure=row["structure"])


def _recent_variants(conn, channel_id: str, limit: int = 5, exclude_variant_id: str = None) -> list:
    """N variant BEST gần nhất đã dùng cho cùng channel_id -- input cho
    Anti-Repetition (E4). post.channel_id là kênh chính (D1).
    exclude_variant_id: loại chính variant đang được chấm lại ra khỏi tập
    so sánh (G3) -- tránh so 1 variant với chính nó sau regenerate, gây
    repetition_penalty giả tạo (variant đã từng is_best=1 sẽ tự khớp
    chính nó nếu không loại trừ)."""
    rows = conn.execute("""
        SELECT cv.* FROM content_variant_row cv
        JOIN content_generation_run cgr ON cv.run_id = cgr.id
        JOIN post p ON cgr.post_id = p.id
        WHERE p.channel_id = ? AND cv.is_best = 1
              AND (? IS NULL OR cv.id != ?)
        ORDER BY cv.created_at DESC LIMIT ?
    """, (channel_id, exclude_variant_id, exclude_variant_id, limit)).fetchall()
    return [_row_to_variant(r) for r in rows]


def compute_variants(conn, product, channel_id: str, platforms: list, affiliate_link: str) -> dict:
    """Thuần -- không ghi DB (trừ product_facts cache của E1, không tính
    là 'ghi DB của E6'). Trả {"status":..., "variants": [...], "result":
    <select_best_variant() output>, "captions": {platform: caption}}.
    """
    facts = content_facts.build_product_facts(conn, product)
    variants = content_variant.generate_variants(facts, product)
    recent = _recent_variants(conn, channel_id)
    result = content_scoring.select_best_variant(variants, recent_variants=recent)
    status = "FACT_CHECK_FAILED" if result["all_rejected"] else "READY"
    captions = {}
    if status == "READY":
        captions = content_platform.adapt_for_platforms(result["best"], platforms, affiliate_link)
    return {"status": status, "variants": variants, "result": result, "captions": captions}


def persist_run(conn, post_id: str, computed: dict) -> dict:
    """Ghi content_generation_run + content_variant_row. BẮT BUỘC gọi SAU
    khi `post` đã tồn tại trong DB (post_id phải là FK hợp lệ tại thời
    điểm gọi hàm này -- xem spec E6 mục 3).
    """
    run_id = ulid()
    conn.execute("INSERT INTO content_generation_run (id, post_id, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                 (run_id, post_id, computed["status"], now(), now()))
    labels = ["A", "B", "C"]
    variant_rows = []
    for i, v in enumerate(computed["variants"]):
        candidate = next((c for c in computed["result"]["candidates"] if c["variant"] is v), None)
        is_best = 1 if computed["result"]["best"] is v else 0
        row_id = ulid()
        conn.execute("""INSERT INTO content_variant_row
            (id, run_id, label, angle, hook, main_message, body_json, cta, structure,
             rule_score, hybrid_score, final_score, is_best, manual_edited, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)""",
            (row_id, run_id, labels[i], v.angle, v.hook, v.main_message,
             json.dumps(v.body, ensure_ascii=False), v.cta, v.structure,
             candidate["hybrid"]["rules"].score if candidate else None,
             candidate["hybrid"]["hybrid_score"] if candidate else None,
             candidate["final_score"] if candidate else None,
             is_best, now(), now()))
        variant_rows.append({"id": row_id, "label": labels[i], "is_best": bool(is_best)})
    best_label = next((r["label"] for r in variant_rows if r["is_best"]), None)
    return {"run_id": run_id, "best_label": best_label, "variant_rows": variant_rows}


def _load_regen_context(conn, post_id: str, variant_id: str):
    """Trả (variant_row, run, post, product, None) nếu hợp lệ, hoặc
    (None, None, None, None, "<lý do>") nếu không -- dùng chung cho cả 3
    hàm regenerate_*()/switch_angle() bên dưới. variant phải thuộc ĐÚNG
    post_id (chặn trộn nội dung giữa 2 bài, bài học từ Task 6's fix E6)."""
    variant_row = conn.execute(
        "SELECT * FROM content_variant_row WHERE id=?", (variant_id,)).fetchone() if variant_id else None
    if not variant_row:
        return None, None, None, None, "Thiếu hoặc không tìm thấy variant"
    run = conn.execute(
        "SELECT * FROM content_generation_run WHERE id=?", (variant_row["run_id"],)).fetchone()
    post = conn.execute("SELECT * FROM post WHERE id=?", (post_id,)).fetchone()
    product = conn.execute(
        "SELECT * FROM product WHERE id=?", (post["product_id"],)).fetchone() if post else None
    if not (run and run["status"] == "READY" and run["post_id"] == post_id and product):
        return None, None, None, None, "Variant không thuộc về bài này"
    return variant_row, run, post, product, None


def _rescore_variant(conn, variant_id: str, channel_id: str) -> None:
    """Chấm lại rule_score/hybrid_score/final_score dựa trên nội dung MỚI
    của variant (đọc thẳng từ DB, không nhận tham số variant object --
    gọi SAU khi UPDATE nội dung đã commit, đảm bảo luôn chấm đúng bản mới
    nhất). Nếu nội dung mới KHÔNG an toàn (fact safety fail) -- set cả 3
    cột điểm về NULL + is_best=0, đúng tín hiệu "ẩn khỏi /duyet" đã có từ
    E6's final fix wave (web/server.py::review() bỏ qua variant có
    scores NULL). Không raise -- lỗi LLM đã được score_variant_hybrid()
    tự xử lý nội bộ (retry + fallback rule_score)."""
    row = conn.execute("SELECT * FROM content_variant_row WHERE id=?", (variant_id,)).fetchone()
    variant_obj = _row_to_variant(row)
    hybrid = content_scoring.score_variant_hybrid(variant_obj)
    if not hybrid["rules"].fact_safety_pass:
        conn.execute("""UPDATE content_variant_row SET rule_score=NULL, hybrid_score=NULL,
                        final_score=NULL, is_best=0, updated_at=? WHERE id=?""", (now(), variant_id))
        audit(conn, "content_variant_row", variant_id, "rescore_unsafe", actor="system",
              detail={"violations": hybrid["rules"].violations})
        return
    recent = _recent_variants(conn, channel_id, exclude_variant_id=variant_id)
    penalty = content_scoring.repetition_penalty(variant_obj, recent)
    final_score = max(0.0, round(hybrid["hybrid_score"] - penalty, 4))
    conn.execute("""UPDATE content_variant_row SET rule_score=?, hybrid_score=?, final_score=?,
                    updated_at=? WHERE id=?""",
                 (hybrid["rules"].score, hybrid["hybrid_score"], final_score, now(), variant_id))


def regenerate_hook(conn, post_id: str, variant_id: str) -> dict:
    """Đổi riêng hook, giữ nguyên angle/main_message/cta/structure của
    variant. Trả {"ok": True} hoặc {"ok": False, "error": "..."}."""
    variant_row, run, post, product, error = _load_regen_context(conn, post_id, variant_id)
    if error:
        return {"ok": False, "error": error}
    facts = content_facts.build_product_facts(conn, product)
    hook_result = content_hook.select_best_hook(variant_row["angle"], facts)
    conn.execute("UPDATE content_variant_row SET hook=?, updated_at=? WHERE id=?",
                 (hook_result["hook"], now(), variant_id))
    _rescore_variant(conn, variant_id, post["channel_id"])
    res = {"ok": True}
    audit(conn, "content_variant_row", variant_id, "doi-hook", actor="operator", detail=res)
    return res


def regenerate_variant(conn, post_id: str, variant_id: str) -> dict:
    """Sinh lại toàn bộ hook/main_message/body/cta, GIỮ NGUYÊN angle. Trả
    {"ok": True} hoặc {"ok": False, "error": "..."}."""
    variant_row, run, post, product, error = _load_regen_context(conn, post_id, variant_id)
    if error:
        return {"ok": False, "error": error}
    facts = content_facts.build_product_facts(conn, product)
    new_variant = content_variant.generate_variant(variant_row["angle"], facts)
    conn.execute("""UPDATE content_variant_row SET hook=?, main_message=?, body_json=?, cta=?,
                    updated_at=? WHERE id=?""",
                 (new_variant.hook, new_variant.main_message,
                  json.dumps(new_variant.body, ensure_ascii=False), new_variant.cta, now(), variant_id))
    _rescore_variant(conn, variant_id, post["channel_id"])
    res = {"ok": True}
    audit(conn, "content_variant_row", variant_id, "lam-lai", actor="operator", detail=res)
    return res


def switch_angle(conn, post_id: str, variant_id: str) -> dict:
    """Đổi sang 1 angle CHƯA dùng trong cùng run (thủ công, khác
    select_angle_candidates() tự động của E2 -- lấy từ TOÀN BỘ
    content_angle.ANGLES, không phải select_angle_candidates(product):
    hàm đó chỉ tự động chọn 1-3 angle và generate_variants() đã dùng hết
    đúng danh sách đó cho 3 variant ban đầu -- lấy lại nó thì "đổi angle"
    không bao giờ còn candidate nào (chết cứng). select_angle_candidates()
    quản chọn angle TỰ ĐỘNG (E2), còn "đổi angle" là cửa thoát THỦ CÔNG của
    operator -- 2 mối quan tâm khác nhau. generate_variant() chạy an toàn
    với mọi angle trong ANGLES nhờ ANGLE_TO_STRUCTURE/ANGLE_TO_CTA_TYPE có
    default (.get(angle, ...)). Trả {"ok": False, "error": "Không còn
    angle nào khác để đổi"} nếu hết candidate."""
    variant_row, run, post, product, error = _load_regen_context(conn, post_id, variant_id)
    if error:
        return {"ok": False, "error": error}
    facts = content_facts.build_product_facts(conn, product)
    candidates = content_angle.ANGLES
    used_angles = {r["angle"] for r in conn.execute(
        "SELECT angle FROM content_variant_row WHERE run_id=?", (run["id"],)).fetchall()}
    next_angle = next((a for a in candidates if a not in used_angles), None)
    if not next_angle:
        res = {"ok": False, "error": "Không còn angle nào khác để đổi"}
    else:
        new_variant = content_variant.generate_variant(next_angle, facts)
        conn.execute("""UPDATE content_variant_row SET angle=?, hook=?, main_message=?,
                        body_json=?, cta=?, structure=?, updated_at=? WHERE id=?""",
                     (new_variant.angle, new_variant.hook, new_variant.main_message,
                      json.dumps(new_variant.body, ensure_ascii=False), new_variant.cta,
                      new_variant.structure, now(), variant_id))
        _rescore_variant(conn, variant_id, post["channel_id"])
        res = {"ok": True}
    audit(conn, "content_variant_row", variant_id, "doi-angle", actor="operator", detail=res)
    return res
