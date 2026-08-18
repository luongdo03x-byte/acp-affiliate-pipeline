"""Content Engine v2 -- orchestrate E1-E5 thành 1 lần chạy, lưu kết quả để
sống qua nhiều request (chọn/regenerate variant), trả BEST variant + caption
theo platform (Content Engine v2, E6 tích hợp).

Khác E1-E5 (dormant, pure function) -- module này CÓ ghi DB
(content_generation_run/content_variant_row). Vẫn KHÔNG gọi
approve_post()/publisher/publish -- chỉ sinh + lưu (PTYC mục 55).
"""
import json

from . import content_facts, content_variant, content_scoring, content_platform
from .db import now, ulid


def _row_to_variant(row) -> content_variant.ContentVariant:
    return content_variant.ContentVariant(
        angle=row["angle"], hook=row["hook"], main_message=row["main_message"],
        body=json.loads(row["body_json"]), cta=row["cta"], structure=row["structure"])


def _recent_variants(conn, channel_id: str, limit: int = 5) -> list:
    """N variant BEST gần nhất đã dùng cho cùng channel_id -- input cho
    Anti-Repetition (E4). post.channel_id là kênh chính (D1)."""
    rows = conn.execute("""
        SELECT cv.* FROM content_variant_row cv
        JOIN content_generation_run cgr ON cv.run_id = cgr.id
        JOIN post p ON cgr.post_id = p.id
        WHERE p.channel_id = ? AND cv.is_best = 1
        ORDER BY cv.created_at DESC LIMIT ?
    """, (channel_id, limit)).fetchall()
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
