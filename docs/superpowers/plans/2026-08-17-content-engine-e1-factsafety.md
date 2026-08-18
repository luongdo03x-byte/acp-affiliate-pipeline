# ProductFacts Gate + Fact Safety (Content Engine v2, E1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Xây `ProductFacts` (chuẩn hoá fact được phép dùng từ `product.description`, cache theo product) và `check_fact_safety()` (hard gate chặn nội dung bịa trải nghiệm/social proof/urgency) làm nền tảng cho Content Engine v2, không đụng engine caption hiện có.

**Architecture:** 1 bảng mới `product_facts` (cache) + 1 module mới `core/content_facts.py` chứa toàn bộ logic thuần function, không phụ thuộc `core/pipeline.py`. Tái sử dụng 2 blacklist có sẵn từ `core/content.py` bằng import trực tiếp.

**Tech Stack:** Python 3, sqlite3 (qua `core/db.py`), không thêm dependency mới.

**Spec:** `docs/superpowers/specs/2026-08-17-content-engine-e1-factsafety-design.md`

## Global Constraints

- Không đụng `core/pipeline.py`, không đụng `core/content.py`'s `generate()`/`validate()` — engine Threads/multi-account hiện có phải tiếp tục chạy y nguyên.
- Bảng mới thêm vào `SCHEMA` trong `core/db.py` (không phải `MIGRATIONS`) — đúng pattern `account_group`.
- SQLite dùng `isolation_level=None` (autocommit) — không gọi `conn.commit()` sau `conn.execute()`, khớp toàn bộ codebase hiện có (`core/attribution.py` là ví dụ).
- Blacklist tái sử dụng (`FABRICATED_EXPERIENCE`, `EFFICACY_CLAIMS`) import trực tiếp từ `core/content.py`, không copy danh sách.
- `check_fact_safety(caption)` chỉ nhận `caption`, không nhận `ProductFacts` — không đối chiếu semantic trong E1 (xem spec §2, §5).
- Test dùng bộ harness sẵn có của repo (`check(name, cond, detail)`, list `PASS`/`FAIL`, đăng ký hàm test ở cuối file trong `if __name__ == "__main__":`) — thêm test vào `tests/test_pipeline.py`, không tạo file test mới.
- Mock-first: không có test nào được gọi network thật hay phụ thuộc `ACP_ADAPTER=live`.

---

### Task 1: Bảng `product_facts`

**Files:**
- Modify: `core/db.py` (thêm bảng vào `SCHEMA`, ngay sau khối `product_price_history`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: bảng `product_facts(product_id, facts_json, unknown_json, category, source_hash, prompt_version, extracted_at)`, `product_id` là PK kiêm FK tới `product(id)`.

- [ ] **Step 1: Viết test schema (sẽ fail vì bảng chưa tồn tại)**

Thêm vào `tests/test_pipeline.py`, ngay sau `test_content_validate_platform_max_len()`:

```python
def test_product_facts_schema():
    print("\nBảng product_facts tồn tại đúng cột")
    conn = connect()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(product_facts)").fetchall()}
    check("có đủ cột product_facts",
          cols == {"product_id", "facts_json", "unknown_json", "category",
                   "source_hash", "prompt_version", "extracted_at"}, cols)
    conn.close()
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline` (cần thêm lời gọi hàm ở cuối file trước — xem Step 4)

Expected: lỗi `sqlite3.OperationalError: no such table: product_facts` hoặc cột rỗng vì bảng chưa có.

- [ ] **Step 3: Thêm bảng vào `SCHEMA`**

Trong `core/db.py`, chèn ngay sau khối `product_price_history` (trước `CREATE TABLE IF NOT EXISTS channel`):

```sql

CREATE TABLE IF NOT EXISTS product_facts (
    product_id      TEXT PRIMARY KEY REFERENCES product(id),
    facts_json      TEXT NOT NULL,
    unknown_json    TEXT NOT NULL,
    category        TEXT,
    source_hash     TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    extracted_at    TEXT NOT NULL
);
```

- [ ] **Step 4: Đăng ký test và chạy lại**

Thêm `test_product_facts_schema()` vào danh sách lời gọi cuối `tests/test_pipeline.py` (ngay sau `test_content_validate_platform_max_len()` trong khối `if __name__ == "__main__":`).

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: `test_product_facts_schema` PASS, tổng số PASS tăng, FAIL không tăng.

- [ ] **Step 5: Commit**

```bash
git add core/db.py tests/test_pipeline.py
git commit -m "feat: thêm bảng product_facts (Content Engine v2, E1)"
```

---

### Task 2: `ProductFacts` + `build_product_facts()` (heuristic, không extractor)

**Files:**
- Create: `core/content_facts.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: bảng `product_facts` (Task 1); `product` là `sqlite3.Row` từ bảng `product` (có `id`, `name`, `current_price`, `original_price`, `description`, `category_code`).
- Produces: `ProductFacts` dataclass (`name, price, original_price, category, facts: list, unknown: list`); `build_product_facts(conn, product, rng=None) -> ProductFacts`. Task 3 mở rộng hàm này thêm nhánh extractor.

- [ ] **Step 1: Viết test cho heuristic + cache (sẽ fail vì module chưa tồn tại)**

Thêm vào `tests/test_pipeline.py`, sau `test_product_facts_schema()`:

```python
def test_build_product_facts_heuristic_no_extractor():
    print("\nbuild_product_facts() dùng heuristic khi chưa đăng ký extractor")
    from acp.core import content_facts
    content_facts.set_extractor(None)
    conn = connect()
    p = conn.execute("SELECT * FROM product WHERE description != '' LIMIT 1").fetchone()
    facts = content_facts.build_product_facts(conn, p)
    check("facts là ProductFacts", isinstance(facts, content_facts.ProductFacts))
    check("facts.facts không rỗng khi description có nội dung", len(facts.facts) > 0, facts.facts)
    check("facts.unknown rỗng ở nhánh heuristic", facts.unknown == [])
    check("facts.name khớp product", facts.name == p["name"])
    check("facts.price khớp product", facts.price == p["current_price"])
    row = conn.execute("SELECT * FROM product_facts WHERE product_id = ?", (p["id"],)).fetchone()
    check("đã ghi cache vào product_facts", row is not None)
    check("prompt_version được ghi", row["prompt_version"] == content_facts.PROMPT_VERSION)
    conn.close()


def test_build_product_facts_cache_hit_skips_recompute():
    print("\nbuild_product_facts() dùng cache khi source_hash khớp, không ghi lại DB")
    from acp.core import content_facts
    content_facts.set_extractor(None)
    conn = connect()
    p = conn.execute("SELECT * FROM product WHERE description != '' LIMIT 1").fetchone()
    first = content_facts.build_product_facts(conn, p)
    # total_changes đếm tổng số dòng bị ghi (INSERT/UPDATE/DELETE) từ lúc mở
    # connection -- không đổi nghĩa là lần gọi thứ 2 không chạy câu INSERT ON
    # CONFLICT nào cả. Không dùng extracted_at để so sánh vì now() chỉ có độ
    # phân giải tới giây -- 2 lần ghi liên tiếp trong cùng 1 giây sẽ ra cùng
    # giá trị dù thực sự có ghi lại, khiến test không bắt được bug.
    changes_before = conn.total_changes
    second = content_facts.build_product_facts(conn, p)
    changes_after = conn.total_changes
    check("cache hit trả cùng facts", second.facts == first.facts)
    check("cache hit không ghi lại DB (total_changes không tăng)",
          changes_after == changes_before, (changes_before, changes_after))
    conn.close()


def test_build_product_facts_stale_cache_recomputes():
    print("\nbuild_product_facts() extract lại khi description đổi")
    from acp.core import content_facts
    content_facts.set_extractor(None)
    conn = connect()
    p = conn.execute("SELECT * FROM product WHERE description != '' LIMIT 1").fetchone()
    first = content_facts.build_product_facts(conn, p)
    conn.execute("UPDATE product SET description = ? WHERE id = ?",
                 ("Mô tả hoàn toàn khác để đổi hash", p["id"]))
    p2 = conn.execute("SELECT * FROM product WHERE id = ?", (p["id"],)).fetchone()
    second = content_facts.build_product_facts(conn, p2)
    check("description đổi làm facts đổi theo", second.facts != first.facts, (first.facts, second.facts))
    conn.close()
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: `ModuleNotFoundError: No module named 'acp.core.content_facts'`

- [ ] **Step 3: Viết `core/content_facts.py`**

```python
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


def _extract_via_llm(description: str):
    raise NotImplementedError  # Task 3 điền logic thật
```

- [ ] **Step 4: Đăng ký 3 test mới, chạy lại**

Thêm 3 hàm vào danh sách lời gọi cuối `tests/test_pipeline.py`, ngay sau `test_product_facts_schema()`.

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: cả 3 test PASS (nhánh `_extractor_fn is None` không đụng `_extract_via_llm`).

- [ ] **Step 5: Commit**

```bash
git add core/content_facts.py tests/test_pipeline.py
git commit -m "feat: ProductFacts + build_product_facts() nhánh heuristic (Content Engine v2, E1)"
```

---

### Task 3: `set_extractor()` — trích xuất qua LLM, retry, fallback

**Files:**
- Modify: `core/content_facts.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `set_extractor(fn)` đã khai báo ở Task 2 (chữ ký cố định, thân hàm `_extract_via_llm` hiện `raise NotImplementedError`).
- Produces: `_extract_via_llm(description) -> (facts: list, unknown: list)` hoạt động đầy đủ, dùng bởi `build_product_facts()` khi có extractor.

- [ ] **Step 1: Viết test retry/fallback (sẽ fail vì `_extract_via_llm` chưa cài)**

Thêm vào `tests/test_pipeline.py`, sau `test_build_product_facts_stale_cache_recomputes()`:

```python
def test_build_product_facts_extractor_valid_json():
    print("\nbuild_product_facts() dùng đúng JSON extractor trả về")
    from acp.core import content_facts
    calls = []

    def fake_extractor(prompt):
        calls.append(prompt)
        return '{"facts": ["chất liệu cotton"], "unknown": ["độ bền sau 1 năm"]}'

    content_facts.set_extractor(fake_extractor)
    try:
        conn = connect()
        p = conn.execute("SELECT * FROM product WHERE description != '' LIMIT 1").fetchone()
        conn.execute("DELETE FROM product_facts WHERE product_id = ?", (p["id"],))
        facts = content_facts.build_product_facts(conn, p)
        check("facts khớp JSON extractor trả về", facts.facts == ["chất liệu cotton"])
        check("unknown khớp JSON extractor trả về", facts.unknown == ["độ bền sau 1 năm"])
        check("chỉ gọi extractor đúng 1 lần khi JSON hợp lệ ngay", len(calls) == 1, len(calls))
        conn.close()
    finally:
        content_facts.set_extractor(None)


def test_build_product_facts_extractor_retries_then_succeeds():
    print("\nbuild_product_facts() retry khi extractor trả sai schema rồi đúng")
    from acp.core import content_facts
    calls = []

    def flaky_extractor(prompt):
        calls.append(prompt)
        if len(calls) < 2:
            return "không phải JSON"
        return '{"facts": ["form gọn"], "unknown": []}'

    content_facts.set_extractor(flaky_extractor)
    try:
        conn = connect()
        p = conn.execute("SELECT * FROM product WHERE description != '' LIMIT 1").fetchone()
        conn.execute("DELETE FROM product_facts WHERE product_id = ?", (p["id"],))
        facts = content_facts.build_product_facts(conn, p)
        check("dùng được kết quả lần retry thứ 2", facts.facts == ["form gọn"])
        check("gọi extractor đúng 2 lần (fail 1, thành công lần 2)", len(calls) == 2, len(calls))
        conn.close()
    finally:
        content_facts.set_extractor(None)


def test_build_product_facts_extractor_always_fails_falls_back():
    print("\nbuild_product_facts() fallback an toàn khi extractor luôn sai schema")
    from acp.core import content_facts
    calls = []

    def broken_extractor(prompt):
        calls.append(prompt)
        return "vẫn không phải JSON"

    content_facts.set_extractor(broken_extractor)
    try:
        conn = connect()
        p = conn.execute("SELECT * FROM product WHERE description != '' LIMIT 1").fetchone()
        conn.execute("DELETE FROM product_facts WHERE product_id = ?", (p["id"],))
        facts = content_facts.build_product_facts(conn, p)
        check("facts rỗng khi extractor luôn fail (an toàn tuyệt đối)", facts.facts == [])
        check("unknown chứa nguyên description khi fallback", facts.unknown == [p["description"]])
        check("retry giới hạn đúng 3 lần rồi dừng, không vô hạn", len(calls) == 3, len(calls))
        conn.close()
    finally:
        content_facts.set_extractor(None)
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: `NotImplementedError` từ `_extract_via_llm`.

- [ ] **Step 3: Cài `_extract_via_llm()` thật**

Trong `core/content_facts.py`, thay `_extract_via_llm` (đang `raise NotImplementedError`) bằng:

```python
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
```

- [ ] **Step 4: Đăng ký 3 test mới, chạy lại toàn bộ**

Thêm 3 hàm vào danh sách lời gọi cuối file, sau `test_build_product_facts_stale_cache_recomputes()`.

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: toàn bộ test Task 2 + Task 3 PASS, không hàm nào trong `tests/test_pipeline.py` từ trước bị FAIL mới.

- [ ] **Step 5: Commit**

```bash
git add core/content_facts.py tests/test_pipeline.py
git commit -m "feat: build_product_facts() gọi LLM extractor thật, retry giới hạn 3 lần (Content Engine v2, E1)"
```

---

### Task 4: `check_fact_safety()`

**Files:**
- Modify: `core/content_facts.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `content.FABRICATED_EXPERIENCE`, `content.EFFICACY_CLAIMS` (đã có sẵn trong `core/content.py`).
- Produces: `check_fact_safety(caption: str) -> list`. `[]` = PASS, non-empty = FAIL (PTYC mục 8.4). Dùng bởi E4 (BEST selection) sau này — E1 chỉ cung cấp hàm, không gọi nó ở đâu cả.

- [ ] **Step 1: Viết test cho từng nhóm vi phạm (sẽ fail vì hàm chưa tồn tại)**

Thêm vào `tests/test_pipeline.py`, sau `test_build_product_facts_extractor_always_fails_falls_back()`:

```python
def test_check_fact_safety_clean_caption_passes():
    print("\ncheck_fact_safety() PASS với caption sạch")
    from acp.core import content_facts
    clean = "Nồi chiên Bear 4L, đang bán 890.000đ. Trang bán ghi nhận đã bán 1.234 lượt."
    check("caption sạch trả []", content_facts.check_fact_safety(clean) == [], content_facts.check_fact_safety(clean))


def test_check_fact_safety_blocks_fabricated_experience():
    print("\ncheck_fact_safety() chặn bịa trải nghiệm cá nhân (mục 8.1)")
    from acp.core import content_facts
    bad = "Mình đã dùng 2 tuần rồi, thấy rất ổn."
    result = content_facts.check_fact_safety(bad)
    check("bịa trải nghiệm bị chặn", len(result) > 0, result)


def test_check_fact_safety_blocks_fabricated_social_proof_phrase():
    print("\ncheck_fact_safety() chặn social proof bịa dạng cụm cố định (mục 8.2)")
    from acp.core import content_facts
    bad = "Sản phẩm này là best seller, ai dùng cũng khen."
    result = content_facts.check_fact_safety(bad)
    check("cụm social proof cố định bị chặn", len(result) > 0, result)


def test_check_fact_safety_blocks_fabricated_social_proof_count():
    print("\ncheck_fact_safety() chặn social proof bịa dạng số lượng (mục 8.2)")
    from acp.core import content_facts
    bad = "Đã có 10.000 người đã mua sản phẩm này."
    result = content_facts.check_fact_safety(bad)
    check("số lượng người mua bịa bị chặn", len(result) > 0, result)


def test_check_fact_safety_does_not_block_real_sold_count_phrasing():
    print("\ncheck_fact_safety() không chặn nhầm cụm 'đã bán ... lượt' thật (khác 'người đã mua')")
    from acp.core import content_facts
    real = "Trang bán ghi nhận đã bán 1.234 lượt, 4.8/5 từ 200 đánh giá."
    check("cụm 'đã bán ... lượt' hợp lệ không bị chặn nhầm",
          content_facts.check_fact_safety(real) == [], content_facts.check_fact_safety(real))


def test_check_fact_safety_blocks_fabricated_urgency():
    print("\ncheck_fact_safety() chặn urgency bịa (mục 8.3)")
    from acp.core import content_facts
    bad = "Sắp hết hàng, mua ngay kẻo lỡ."
    result = content_facts.check_fact_safety(bad)
    check("urgency bịa bị chặn", len(result) > 0, result)


def test_check_fact_safety_blocks_efficacy_claim():
    print("\ncheck_fact_safety() chặn cam kết công dụng (tái sử content.EFFICACY_CLAIMS)")
    from acp.core import content_facts
    bad = "Dùng sản phẩm này cam kết hiệu quả, hết mụn sau 1 tuần."
    result = content_facts.check_fact_safety(bad)
    check("cam kết công dụng bị chặn", len(result) > 0, result)
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: `AttributeError: module 'acp.core.content_facts' has no attribute 'check_fact_safety'`

- [ ] **Step 3: Cài `check_fact_safety()`**

Trong `core/content_facts.py`, thêm import ở đầu file (ngay dưới `from .db import now`):

```python
import unicodedata

from .content import EFFICACY_CLAIMS, FABRICATED_EXPERIENCE
```

Thêm các hằng số và hàm (đặt sau `_source_hash()`, trước `_heuristic_facts()`):

```python
FABRICATED_SOCIAL_PROOF = [
    "đã bán hết", "ai dùng cũng khen", "best seller", "bán chạy nhất",
    "được nhiều người tin dùng",
]

FABRICATED_URGENCY = [
    "sắp hết hàng", "chỉ còn hôm nay", "số lượng có hạn", "nhanh tay kẻo lỡ",
]

_SOCIAL_PROOF_COUNT_RE = re.compile(r"\d[\d.,]*\s*(người|khách|đơn)\s*(đã\s*)?(mua|đặt)")


def check_fact_safety(caption: str) -> list:
    """[] nghĩa là FACT_SAFETY = PASS. Non-empty là FAIL (PTYC mục 8.4).

    Không nhận ProductFacts: E1 không đối chiếu semantic giữa caption và
    facts.facts/unknown (spec E1 §2, §5) -- toàn bộ cơ chế là blacklist/regex
    cố định. Nếu E4+ cần semantic check thật, đó là lúc thêm tham số facts.
    """
    problems = []
    flat = unicodedata.normalize("NFC", caption).lower()

    for phrase in FABRICATED_EXPERIENCE:
        if phrase in flat:
            problems.append(f"Bịa trải nghiệm cá nhân chưa từng có: “{phrase}”")
    for phrase in EFFICACY_CLAIMS:
        if phrase in flat:
            problems.append(f"Cam kết công dụng: “{phrase}”")
    for phrase in FABRICATED_SOCIAL_PROOF:
        if phrase in flat:
            problems.append(f"Bịa social proof: “{phrase}”")
    if _SOCIAL_PROOF_COUNT_RE.search(flat):
        problems.append("Bịa số lượng người mua/đặt không có nguồn")
    for phrase in FABRICATED_URGENCY:
        if phrase in flat:
            problems.append(f"Bịa cảm giác khan hiếm: “{phrase}”")

    return problems
```

- [ ] **Step 4: Đăng ký 7 test mới, chạy lại toàn bộ**

Thêm 7 hàm vào danh sách lời gọi cuối file, sau `test_build_product_facts_extractor_always_fails_falls_back()`.

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: toàn bộ PASS, tổng FAIL = 0.

- [ ] **Step 5: Chạy toàn bộ regression suite**

Run:
```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
acp/.venv/bin/python3 -m acp.tests.test_pipeline
acp/.venv/bin/python3 -m acp.tests.test_pilot
```

Expected: cả 2 file 0 FAIL — E1 không đụng file nào khác ngoài `core/db.py` (Task 1) và `core/content_facts.py` (mới), nên không có lý do `test_pilot.py` bị ảnh hưởng; chạy để xác nhận chắc chắn.

- [ ] **Step 6: Commit**

```bash
git add core/content_facts.py tests/test_pipeline.py
git commit -m "feat: check_fact_safety() hard gate, tái sử blacklist content.py (Content Engine v2, E1)"
```
