"""ProductFacts Gate -- chuẩn hoá fact được phép dùng từ product.description,
cache theo product (Content Engine v2, PTYC mục 6-8).

Không đụng core/content.py's generate()/validate() -- engine caption hiện có
(Threads, D1-D4 multi-account) tiếp tục dùng nguyên trạng. Module này chỉ là
nền tảng cho E2+ (Angle/Hook/Variant/Scoring), chưa nối vào pipeline.
"""
import hashlib
import json
import re
from dataclasses import dataclass

from .db import now

PROMPT_VERSION = "e1-v1"

_extractor_fn = None


def set_extractor(fn):
    """fn(prompt: str) -> str. Model trả JSON thô, build_product_facts() tự parse.

    fn=None (mặc định) -- dùng bộ trích xuất heuristic, không cần model.
    """
    global _extractor_fn
    _extractor_fn = fn


@dataclass(frozen=True)
class ProductFacts:
    name: str
    price: int
    original_price: object
    category: str
    facts: list
    unknown: list


def _source_hash(description: str) -> str:
    return hashlib.sha256((description or "").encode("utf-8")).hexdigest()


def _heuristic_facts(description: str):
    """Tách câu từ description làm facts. Không cần model, deterministic."""
    if not description:
        return [], []
    parts = re.split(r"[.\n;]", description)
    facts = [p.strip() for p in parts if p.strip() and len(p.strip()) <= 200]
    return facts, []


def _row_to_facts(product, row) -> ProductFacts:
    return ProductFacts(
        name=product["name"],
        price=product["current_price"],
        original_price=product["original_price"],
        category=row["category"],
        facts=json.loads(row["facts_json"]),
        unknown=json.loads(row["unknown_json"]),
    )


def build_product_facts(conn, product, rng=None) -> ProductFacts:
    """Trả ProductFacts cho product, dùng cache trong product_facts khi còn hợp lệ.

    rng nhận vào nhưng chưa dùng trong E1 -- giữ chữ ký nhất quán với
    content.generate(..., rng=...), tránh phải đổi chữ ký lần nữa ở E2+.
    """
    product_id = product["id"]
    description = product["description"] or ""
    src_hash = _source_hash(description)

    row = conn.execute("SELECT * FROM product_facts WHERE product_id = ?", (product_id,)).fetchone()
    if row and row["source_hash"] == src_hash:
        return _row_to_facts(product, row)

    if _extractor_fn is None:
        facts, unknown = _heuristic_facts(description)
    else:
        facts, unknown = _extract_via_llm(description)

    category = product["category_code"]
    conn.execute("""
        INSERT INTO product_facts (product_id, facts_json, unknown_json, category,
                                    source_hash, prompt_version, extracted_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(product_id) DO UPDATE SET
            facts_json = excluded.facts_json, unknown_json = excluded.unknown_json,
            category = excluded.category, source_hash = excluded.source_hash,
            prompt_version = excluded.prompt_version, extracted_at = excluded.extracted_at
    """, (product_id, json.dumps(facts, ensure_ascii=False), json.dumps(unknown, ensure_ascii=False),
          category, src_hash, PROMPT_VERSION, now()))

    return ProductFacts(name=product["name"], price=product["current_price"],
                         original_price=product["original_price"], category=category,
                         facts=facts, unknown=unknown)


def _build_extract_prompt(description: str) -> str:
    return (
        "Trích xuất fact từ mô tả sản phẩm dưới đây. Trả về đúng JSON, "
        "không thêm chữ nào khác:\n"
        '{"facts": ["câu fact 1", "câu fact 2"], "unknown": ["điều không rõ 1"]}\n\n'
        "RÀNG BUỘC:\n"
        "- facts chỉ chứa thông tin có trong mô tả gốc, không suy luận thêm.\n"
        "- unknown liệt kê những khía cạnh người mua có thể quan tâm nhưng mô tả "
        "không nói tới (vd độ bền, phù hợp dáng người...).\n"
        "- Không thêm nhận định, đánh giá, hay câu không có trong dữ liệu.\n\n"
        f"Mô tả gốc:\n{description}"
    )


def _extract_via_llm(description: str):
    prompt = _build_extract_prompt(description)
    for _ in range(3):
        raw = _extractor_fn(prompt)
        try:
            data = json.loads(raw)
            facts = [str(x) for x in data.get("facts", [])]
            unknown = [str(x) for x in data.get("unknown", [])]
            return facts, unknown
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue
    return [], [description]
