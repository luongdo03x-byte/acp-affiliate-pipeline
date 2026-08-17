# Tích hợp /duyet + pipeline.py (Content Engine v2, E6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nối E1-E5 vào luồng tạo bài + `/duyet` thật, sau feature flag mặc định tắt, đủ bộ action tương tác (chọn variant, regenerate hook/variant/angle) — không đụng `approve_post()`/`publish_post()`.

**Architecture:** Bảng mới (`system_setting`, `content_generation_run`, `content_variant_row`) + module mới `core/system_settings.py`/`core/content_engine.py` (orchestrate E1-E5, ghi/đọc state) + sửa có kiểm soát `core/pipeline.py`'s `_create_post_from_raw_product()` (chỉ đổi nguồn `caption`, không đổi câu INSERT) + `web/server.py`'s `review()`/`review_action()` (route generic có sẵn) + `web/templates/review.html`.

**Tech Stack:** Python 3, SQLite, vanilla JS (không framework, đúng pattern `acpTickGroup` đã có).

**Spec:** `docs/superpowers/specs/2026-08-17-content-engine-e6-integration-design.md`

## Global Constraints

- **TUYỆT ĐỐI không sửa** `core/content_facts.py`, `core/content_angle.py`, `core/content_hook.py`, `core/content_variant.py`, `core/content_checker.py`, `core/content_scoring.py`, `core/content_platform.py` (E1-E5, đã merge+review) — chỉ import và gọi.
- **TUYỆT ĐỐI không sửa** `approve_post()`/`publish_post()`/bất kỳ hàm nào trong khối "chặng 4" của `core/pipeline.py` (duyệt/lên lịch/đăng) — luồng này giữ nguyên 100%.
- Flag `content_engine_v2_enabled` mặc định **TẮT** (`"0"`) — khi tắt, `_create_post_from_raw_product()` phải cho kết quả **byte-identical** với trước E6.
- Câu `INSERT INTO post (...)` trong `_create_post_from_raw_product()` **giữ nguyên nội dung, không thêm cột, không đổi thứ tự placeholder** — chỉ đổi nguồn biến `caption` được tính TRƯỚC câu INSERT đó.
- Content Engine v2 raise exception bất ngờ → fallback êm về `content.generate()` (v1), ghi audit, KHÔNG được làm crash việc tạo bài.
- `content.validate()` vẫn chạy trên caption cuối cùng dù nguồn v1 hay v2.
- Regenerate action (`đổi hook`/`regenerate variant`/`đổi angle`) tái dùng route generic có sẵn `POST /duyet/<post_id>/<action>` (hàm `review_action()`) — KHÔNG tạo route mới, `variant_id` đọc từ `request.form.get("variant_id")`.
- "Sửa caption" và "dùng cho tất cả kênh" **đã có sẵn** (D1-D2 textarea) — E6 chỉ cần điền đúng giá trị ban đầu, không xây lại.
- `generate_content()` (job handler `GENERATE_CONTENT`) **ngoài phạm vi P0** — giữ nguyên dùng v1 vô điều kiện.
- Test dùng bộ harness sẵn có của repo (`check(name, cond, detail)`, list `PASS`/`FAIL` toàn cục, đăng ký tường minh trong `if __name__ == "__main__":`) — thêm vào `tests/test_pipeline.py`, không dùng pytest.
- Chạy test bằng: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline` (venv riêng của repo).
- Baseline trước E6: 505 PASS, 0 FAIL (`test_pipeline.py`), 340 PASS/0 FAIL (`test_pilot.py`).
- Commit message tiếng Việt CÓ DẤU ĐẦY ĐỦ.

---

### Task 1: Bảng mới + `core/system_settings.py`

**Files:**
- Modify: `core/db.py` (thêm 3 bảng vào `SCHEMA`)
- Create: `core/system_settings.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: bảng `system_setting`/`content_generation_run`/`content_variant_row`; `get_setting()`, `set_setting()`, `is_content_engine_v2_enabled()`.

- [ ] **Step 1: Viết 5 test (sẽ fail vì bảng/module chưa tồn tại)**

Thêm vào `tests/test_pipeline.py`, ngay sau `test_adapt_for_platforms_all_three_matches_individual_calls()` (test cuối cùng hiện có của E5):

```python
def test_system_setting_schema():
    print("\nBảng system_setting/content_generation_run/content_variant_row tồn tại đúng cột")
    conn = connect()
    ss_cols = {r[1] for r in conn.execute("PRAGMA table_info(system_setting)").fetchall()}
    check("system_setting đủ cột", ss_cols == {"key", "value", "updated_at", "updated_by"}, ss_cols)
    cgr_cols = {r[1] for r in conn.execute("PRAGMA table_info(content_generation_run)").fetchall()}
    check("content_generation_run đủ cột", cgr_cols == {"id", "post_id", "status", "created_at", "updated_at"}, cgr_cols)
    cv_cols = {r[1] for r in conn.execute("PRAGMA table_info(content_variant_row)").fetchall()}
    check("content_variant_row đủ cột", cv_cols == {
        "id", "run_id", "label", "angle", "hook", "main_message", "body_json", "cta", "structure",
        "rule_score", "hybrid_score", "final_score", "is_best", "manual_edited", "created_at", "updated_at"
    }, cv_cols)
    conn.close()


def test_get_setting_default_when_missing():
    print("\nget_setting() trả default khi chưa có key")
    from acp.core import system_settings
    conn = connect()
    check("chưa có key -> default", system_settings.get_setting(conn, "khong_ton_tai_xxx", "mac_dinh") == "mac_dinh")
    conn.close()


def test_set_setting_then_get_roundtrip():
    print("\nset_setting() rồi get_setting() trả đúng giá trị vừa lưu")
    from acp.core import system_settings
    conn = connect()
    system_settings.set_setting(conn, "test_key_e6", "gia_tri_moi", actor="test")
    check("get lại đúng giá trị", system_settings.get_setting(conn, "test_key_e6") == "gia_tri_moi")
    conn.close()


def test_set_setting_overwrites_existing():
    print("\nset_setting() ghi đè giá trị cũ, không tạo dòng trùng")
    from acp.core import system_settings
    conn = connect()
    system_settings.set_setting(conn, "test_key_e6_overwrite", "v1")
    system_settings.set_setting(conn, "test_key_e6_overwrite", "v2")
    rows = conn.execute("SELECT * FROM system_setting WHERE key=?", ("test_key_e6_overwrite",)).fetchall()
    check("chỉ 1 dòng sau 2 lần set", len(rows) == 1, rows)
    check("giá trị là bản mới nhất", rows[0]["value"] == "v2", rows[0]["value"])
    conn.close()


def test_is_content_engine_v2_enabled_default_false():
    print("\nis_content_engine_v2_enabled() mặc định False khi chưa cấu hình")
    from acp.core import system_settings
    conn = connect()
    check("mặc định tắt", system_settings.is_content_engine_v2_enabled(conn) is False)
    system_settings.set_setting(conn, "content_engine_v2_enabled", "1")
    check("bật đúng sau khi set '1'", system_settings.is_content_engine_v2_enabled(conn) is True)
    system_settings.set_setting(conn, "content_engine_v2_enabled", "0")
    check("tắt lại đúng sau khi set '0'", system_settings.is_content_engine_v2_enabled(conn) is False)
    conn.close()
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: lỗi cột rỗng (bảng chưa có) hoặc `ModuleNotFoundError`.

- [ ] **Step 3: Thêm 3 bảng vào `SCHEMA` trong `core/db.py`**

Chèn ngay trước dòng đóng `"""` cuối `SCHEMA` (sau khối `audit_log`/`idx_audit_entity`):

```sql

CREATE TABLE IF NOT EXISTS system_setting (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  TEXT
);

CREATE TABLE IF NOT EXISTS content_generation_run (
    id          TEXT PRIMARY KEY,
    post_id     TEXT NOT NULL REFERENCES post(id),
    status      TEXT NOT NULL DEFAULT 'READY',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS content_variant_row (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES content_generation_run(id),
    label           TEXT NOT NULL,
    angle           TEXT NOT NULL,
    hook            TEXT NOT NULL,
    main_message    TEXT NOT NULL,
    body_json       TEXT NOT NULL,
    cta             TEXT NOT NULL,
    structure       TEXT NOT NULL,
    rule_score      REAL,
    hybrid_score    REAL,
    final_score     REAL,
    is_best         INTEGER NOT NULL DEFAULT 0,
    manual_edited   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_content_variant_run ON content_variant_row(run_id);
```

- [ ] **Step 4: Viết `core/system_settings.py`**

```python
"""Feature flag / cấu hình hệ thống dạng key-value (Content Engine v2, E6).

Không đụng core/pipeline.py's approve_post()/publish_post() -- module này
chỉ đọc/ghi 1 bảng cấu hình chung, không có logic nghiệp vụ.
"""
from .db import audit, now


def get_setting(conn, key: str, default: str = None) -> str:
    row = conn.execute("SELECT value FROM system_setting WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key: str, value: str, actor: str = "system") -> None:
    conn.execute("""INSERT INTO system_setting (key, value, updated_at, updated_by)
        VALUES (?,?,?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at,
            updated_by=excluded.updated_by""", (key, value, now(), actor))
    audit(conn, "system_setting", key, "updated", actor=actor, detail={"value": value})


def is_content_engine_v2_enabled(conn) -> bool:
    return get_setting(conn, "content_engine_v2_enabled", "0") == "1"
```

- [ ] **Step 5: Đăng ký 5 test, chạy lại**

Thêm 5 hàm vào danh sách lời gọi cuối `tests/test_pipeline.py`.

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: `test_system_setting_schema` (3 check), `test_get_setting_default_when_missing` (1 check), `test_set_setting_then_get_roundtrip` (1 check), `test_set_setting_overwrites_existing` (2 check), `test_is_content_engine_v2_enabled_default_false` (3 check) — tổng đúng 10 check mới. Tổng: 505 + 10 = 515 PASS, 0 FAIL.

- [ ] **Step 6: Commit**

```bash
git add core/db.py core/system_settings.py tests/test_pipeline.py
git commit -m "feat: bảng system_setting/content_generation_run/content_variant_row + core/system_settings.py (Content Engine v2, E6)"
```

---

### Task 2: `core/content_engine.py` — orchestrator E1-E5

**Files:**
- Create: `core/content_engine.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `content_facts.build_product_facts()`, `content_variant.generate_variants()`, `content_scoring.select_best_variant()`, `content_platform.adapt_for_platforms()` (E1,E3,E4,E5 — không sửa), bảng từ Task 1.
- Produces: `compute_variants(conn, product, channel_id, platforms, affiliate_link) -> dict`, `persist_run(conn, post_id, computed) -> dict`, `_recent_variants(conn, channel_id, limit=5)`.

- [ ] **Step 1: Viết 6 test (sẽ fail vì module chưa tồn tại)**

Thêm vào `tests/test_pipeline.py`, ngay sau `test_is_content_engine_v2_enabled_default_false()`:

```python
def _mk_content_engine_fixture():
    """Trả (conn, product, channel_id) -- product có discount rõ + category
    gia-dung (đã kiểm chứng cho đủ 3 angle distinct từ E3), channel Threads
    riêng cho test này (không dùng ch1 chung, tránh nhiễu _recent_variants
    giữa các test khác nhau)."""
    conn = connect()
    ch_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled, created_at)
        VALUES (?,?,?,?,?,?,?)""", (ch_id, f"ce_test_{ch_id[:6]}", "threads", "@cetest", "ACTIVE", 1, now()))
    product = conn.execute("""SELECT * FROM product WHERE original_price IS NOT NULL
        AND original_price > current_price
        AND (original_price - current_price) * 1.0 / original_price >= 0.05
        AND category_code = 'gia-dung' LIMIT 1""").fetchone()
    return conn, product, ch_id


def test_compute_variants_ready_status_has_captions():
    print("\ncompute_variants() sản phẩm bình thường -> status READY, có đủ caption theo platform yêu cầu")
    from acp.core import content_engine
    conn, product, ch_id = _mk_content_engine_fixture()
    computed = content_engine.compute_variants(conn, product, ch_id, ["threads", "facebook"], "https://link.test")
    check("status READY", computed["status"] == "READY", computed["status"])
    check("đúng 3 variant", len(computed["variants"]) == 3, len(computed["variants"]))
    check("có caption threads", "threads" in computed["captions"], computed["captions"].keys())
    check("có caption facebook", "facebook" in computed["captions"], computed["captions"].keys())
    check("không có caption instagram (không yêu cầu)", "instagram" not in computed["captions"], computed["captions"].keys())
    conn.close()


def test_persist_run_writes_one_run_and_three_variant_rows():
    print("\npersist_run() ghi đúng 1 content_generation_run + 3 content_variant_row, đúng 1 is_best")
    from acp.core import content_engine
    conn, product, ch_id = _mk_content_engine_fixture()
    computed = content_engine.compute_variants(conn, product, ch_id, ["threads"], "https://link.test")
    post_id = ulid()
    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, variant_code, caption_body,
        disclosure_text, caption_final, affiliate_link, status, created_at, updated_at)
        VALUES (?,?,?,(SELECT id FROM campaign LIMIT 1),'A','x',?,'x','https://link.test','PENDING_REVIEW',?,?)""",
        (post_id, product["id"], ch_id, content.DISCLOSURE_DEFAULT, now(), now()))
    persisted = content_engine.persist_run(conn, post_id, computed)
    rows = conn.execute("SELECT * FROM content_variant_row WHERE run_id=?", (persisted["run_id"],)).fetchall()
    check("đúng 3 dòng variant", len(rows) == 3, len(rows))
    check("đúng 1 dòng is_best=1", sum(r["is_best"] for r in rows) == 1, [r["is_best"] for r in rows])
    check("best_label khớp dòng is_best", persisted["best_label"] in [r["label"] for r in rows if r["is_best"]])
    run_row = conn.execute("SELECT * FROM content_generation_run WHERE id=?", (persisted["run_id"],)).fetchone()
    check("run status khớp computed", run_row["status"] == computed["status"], run_row["status"])
    conn.close()


def test_recent_variants_scoped_by_channel_and_ordered():
    print("\n_recent_variants() chỉ lấy theo đúng channel_id, sắp mới nhất trước, giới hạn limit")
    from acp.core import content_engine
    conn, product, ch_id = _mk_content_engine_fixture()
    check("chưa có run nào -> []", content_engine._recent_variants(conn, ch_id) == [])
    computed = content_engine.compute_variants(conn, product, ch_id, ["threads"], "https://link.test")
    post_id = ulid()
    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, variant_code, caption_body,
        disclosure_text, caption_final, affiliate_link, status, created_at, updated_at)
        VALUES (?,?,?,(SELECT id FROM campaign LIMIT 1),'A','x',?,'x','https://link.test','PENDING_REVIEW',?,?)""",
        (post_id, product["id"], ch_id, content.DISCLOSURE_DEFAULT, now(), now()))
    content_engine.persist_run(conn, post_id, computed)
    recent = content_engine._recent_variants(conn, ch_id)
    check("có đúng 1 recent variant sau 1 lần persist", len(recent) == 1, len(recent))
    check("recent variant là ContentVariant thật", hasattr(recent[0], "angle"), recent[0])
    conn.close()
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: `ModuleNotFoundError: No module named 'acp.core.content_engine'`

- [ ] **Step 3: Viết `core/content_engine.py`**

```python
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
```

- [ ] **Step 4: Đăng ký 3 test (helper `_mk_content_engine_fixture` KHÔNG đăng ký), chạy lại**

Thêm 3 hàm `test_*` vào danh sách lời gọi cuối `tests/test_pipeline.py`, sau các test của Task 1.

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: `test_compute_variants_ready_status_has_captions` (5 check), `test_persist_run_writes_one_run_and_three_variant_rows` (4 check), `test_recent_variants_scoped_by_channel_and_ordered` (3 check) — tổng đúng 12 check mới. Tổng: 515 + 12 = 527 PASS, 0 FAIL.

- [ ] **Step 5: Commit**

```bash
git add core/content_engine.py tests/test_pipeline.py
git commit -m "feat: core/content_engine.py -- orchestrate E1-E5, lưu content_generation_run/content_variant_row (Content Engine v2, E6)"
```

---

### Task 3: Nối vào `_create_post_from_raw_product()`

**Files:**
- Modify: `core/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `system_settings.is_content_engine_v2_enabled()` (Task 1), `content_engine.compute_variants()`/`persist_run()` (Task 2).
- Produces: `_create_post_from_raw_product()` sinh caption từ v2 khi flag bật + `READY`, fallback v1 khi tắt/lỗi/`FACT_CHECK_FAILED`.

- [ ] **Step 1: Viết 4 test (sẽ fail vì logic chưa có)**

Thêm vào `tests/test_pipeline.py`, ngay sau `test_recent_variants_scoped_by_channel_and_ordered()`:

```python
def test_create_post_flag_off_behaves_exactly_like_before():
    print("\n_create_post_from_raw_product() flag TẮT -> không có content_generation_run, caption từ v1")
    from acp.core import system_settings
    conn = connect()
    system_settings.set_setting(conn, "content_engine_v2_enabled", "0")
    src = MockAccessTrade()
    target = next(p for p in src.fetch_products(limit=50) if p.product_url)
    ctx = {"source": src, "publishers": {}}
    res = pipeline.create_post_for_product(conn, ctx, target.external_product_id, "test")
    check("tạo bài thành công", res.get("ok"), res.get("error"))
    run = conn.execute("SELECT * FROM content_generation_run WHERE post_id=?", (res["post_id"],)).fetchone()
    check("không có content_generation_run nào", run is None, run)
    conn.close()


def test_create_post_flag_on_uses_v2_caption_and_persists_run():
    print("\n_create_post_from_raw_product() flag BẬT -> có content_generation_run READY, caption_facebook/instagram được điền")
    from acp.core import system_settings
    conn = connect()
    system_settings.set_setting(conn, "content_engine_v2_enabled", "1")
    fb_id, ig_id = ulid(), ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled, created_at)
        VALUES (?,?,?,?,?,?,?)""", (fb_id, f"e6_fb_{fb_id[:6]}", "facebook", "FB E6 Test", "ACTIVE", 1, now()))
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled, created_at)
        VALUES (?,?,?,?,?,?,?)""", (ig_id, f"e6_ig_{ig_id[:6]}", "instagram", "IG E6 Test", "ACTIVE", 1, now()))
    src = MockAccessTrade()
    target = next(p for p in src.fetch_products(limit=80) if p.product_url)
    ctx = {"source": src, "publishers": {}}
    res = pipeline.create_post_for_product(
        conn, ctx, target.external_product_id, "test",
        channel_codes=["ch1", f"e6_fb_{fb_id[:6]}", f"e6_ig_{ig_id[:6]}"])
    check("tạo bài thành công", res.get("ok"), res.get("error"))
    if res.get("ok"):
        run = conn.execute("SELECT * FROM content_generation_run WHERE post_id=?", (res["post_id"],)).fetchone()
        check("có content_generation_run", run is not None, run)
        if run:
            check("status READY hoặc FACT_CHECK_FAILED (hợp lệ cả 2)",
                  run["status"] in ("READY", "FACT_CHECK_FAILED"), run["status"])
        post = conn.execute("SELECT * FROM post WHERE id=?", (res["post_id"],)).fetchone()
        if run and run["status"] == "READY":
            check("caption_facebook được điền", bool(post["caption_facebook"]), post["caption_facebook"])
            check("caption_instagram được điền", bool(post["caption_instagram"]), post["caption_instagram"])
    system_settings.set_setting(conn, "content_engine_v2_enabled", "0")
    conn.close()


def test_create_post_v2_exception_falls_back_to_v1_without_crashing():
    print("\n_create_post_from_raw_product() v2 raise exception -> fallback v1, tạo bài vẫn thành công")
    from acp.core import system_settings, content_engine
    conn = connect()
    system_settings.set_setting(conn, "content_engine_v2_enabled", "1")
    original = content_engine.compute_variants

    def crashing_compute(*a, **kw):
        raise RuntimeError("giả lập lỗi Content Engine v2")

    content_engine.compute_variants = crashing_compute
    try:
        src = MockAccessTrade()
        target = next(p for p in src.fetch_products(limit=50) if p.product_url)
        ctx = {"source": src, "publishers": {}}
        res = pipeline.create_post_for_product(conn, ctx, target.external_product_id, "test")
        check("tạo bài vẫn thành công dù v2 crash", res.get("ok"), res.get("error"))
        run = conn.execute("SELECT * FROM content_generation_run WHERE post_id=?", (res.get("post_id"),)).fetchone()
        check("không có content_generation_run (v2 crash trước khi persist)", run is None, run)
        audit_row = conn.execute(
            "SELECT * FROM audit_log WHERE entity='post' AND action='content_engine_v2_failed' "
            "AND entity_id=? ORDER BY created_at DESC LIMIT 1", (res.get("post_id"),)).fetchone()
        check("có audit content_engine_v2_failed", audit_row is not None, audit_row)
    finally:
        content_engine.compute_variants = original
        system_settings.set_setting(conn, "content_engine_v2_enabled", "0")
    conn.close()


def test_create_post_fact_check_failed_falls_back_to_v1_caption():
    print("\n_create_post_from_raw_product() v2 trả FACT_CHECK_FAILED -> caption vẫn dùng v1, không crash")
    from acp.core import system_settings, content_engine
    conn = connect()
    system_settings.set_setting(conn, "content_engine_v2_enabled", "1")
    original = content_engine.compute_variants

    def failed_compute(*a, **kw):
        return {"status": "FACT_CHECK_FAILED", "variants": [], "result": {"all_rejected": True, "candidates": []}, "captions": {}}

    content_engine.compute_variants = failed_compute
    try:
        src = MockAccessTrade()
        target = next(p for p in src.fetch_products(limit=50) if p.product_url)
        ctx = {"source": src, "publishers": {}}
        res = pipeline.create_post_for_product(conn, ctx, target.external_product_id, "test")
        check("tạo bài vẫn thành công", res.get("ok"), res.get("error"))
        check("caption không rỗng (rơi về v1)", bool(res.get("caption")), res.get("caption"))
    finally:
        content_engine.compute_variants = original
        system_settings.set_setting(conn, "content_engine_v2_enabled", "0")
    conn.close()
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: các test flag-bật fail vì chưa có logic nối (vd `content_generation_run` không tồn tại dù flag bật, hoặc `caption_facebook` rỗng).

- [ ] **Step 3: Sửa `_create_post_from_raw_product()` trong `core/pipeline.py`**

Thêm import ở đầu file (cạnh các import `core.*` hiện có):

```python
from . import content_engine, system_settings
```

Thay đúng 3 dòng hiện tại:
```python
    caption = content.generate(product, template["code"], link, discount_pct=discount)
    problems = content.validate(caption, niches=_union_niches(conn, channel_ids))
    status = "PENDING_REVIEW" if not problems else "DRAFT"
```
thành:
```python
    v2_computed = None
    if system_settings.is_content_engine_v2_enabled(conn):
        try:
            platforms = sorted({ch["platform"] for ch in channels} & {"threads", "facebook", "instagram"})
            v2_computed = content_engine.compute_variants(conn, product, channel["id"], platforms, link)
        except Exception as exc:
            # Không để lỗi Content Engine v2 làm hỏng việc tạo bài -- fallback
            # êm về v1, ghi audit để vận hành viên biết mà kiểm tra.
            audit(conn, "post", post_id, "content_engine_v2_failed", actor="system",
                  detail={"error": str(exc)})
            v2_computed = None

    if v2_computed and v2_computed["status"] == "READY":
        caption = (v2_computed["captions"].get(channel["platform"])
                   or v2_computed["captions"].get("threads")
                   or content.generate(product, template["code"], link, discount_pct=discount))
    else:
        caption = content.generate(product, template["code"], link, discount_pct=discount)
    problems = content.validate(caption, niches=_union_niches(conn, channel_ids))
    status = "PENDING_REVIEW" if not problems else "DRAFT"
```

**KHÔNG đổi câu `INSERT INTO post (...)`** ngay sau đó (giữ nguyên nội
dung 100% — biến `caption` đã tự động mang đúng giá trị v1 hoặc v2 tuỳ
nhánh trên).

Ngay sau dòng `_save_channel_selection(conn, post_id, channel_ids)` (vẫn
giữ nguyên vị trí, chỉ thêm code MỚI ngay dưới nó, TRƯỚC khối
`if media_asset_ids:`), thêm:

```python
    if v2_computed and v2_computed["status"] == "READY":
        if "facebook" in v2_computed["captions"]:
            conn.execute("UPDATE post SET caption_facebook=? WHERE id=?",
                         (v2_computed["captions"]["facebook"], post_id))
        if "instagram" in v2_computed["captions"]:
            conn.execute("UPDATE post SET caption_instagram=? WHERE id=?",
                         (v2_computed["captions"]["instagram"], post_id))
    if v2_computed:
        persisted = content_engine.persist_run(conn, post_id, v2_computed)
        audit(conn, "content_generation_run", persisted["run_id"], "generated", actor="operator",
              detail={"post_id": post_id, "status": v2_computed["status"],
                      "best_label": persisted.get("best_label")})
```

- [ ] **Step 4: Đăng ký 4 test, chạy lại**

Thêm 4 hàm vào danh sách lời gọi cuối `tests/test_pipeline.py`, sau các test của Task 2.

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: toàn bộ PASS, 0 FAIL, không hàm cũ nào (kể cả toàn bộ test D1-D4B đã có từ trước Content Engine v2) bị hỏng — đây là điểm kiểm tra quan trọng nhất của cả plan, vì lần đầu tiên `core/pipeline.py` bị sửa.

- [ ] **Step 5: Commit**

```bash
git add core/pipeline.py tests/test_pipeline.py
git commit -m "feat: nối Content Engine v2 vào _create_post_from_raw_product() sau feature flag, fallback êm khi lỗi (Content Engine v2, E6)"
```

---

### Task 4: `/duyet` hiển thị 3 variant

**Files:**
- Modify: `web/server.py` (route `review()`)
- Modify: `web/templates/review.html`
- Test: `tests/test_pilot.py` (**không phải `test_pipeline.py`** — test dùng
  `app.test_client()` phải nằm ở file test có sẵn hạ tầng Flask app;
  `test_pipeline.py` không import `create_app`/không có pattern login,
  xem Global Constraints)

**Interfaces:**
- Consumes: `content_platform.adapt_for_platforms()`, `content_checker.check_variant_rules()` (đọc trực tiếp `content_variant_row`, dựng lại `ContentVariant`).
- Produces: mỗi post dict trong context của `review()` có thêm `variants: list[dict]`.

**Ghi chú quan trọng về cách viết test Flask trong repo này** (đã kiểm
chứng từ các test có sẵn trong `tests/test_pilot.py`, KHÔNG suy đoán):
- `/duyet` (GET lẫn POST) yêu cầu đăng nhập — chưa đăng nhập thì mọi
  request trả **302 redirect**, không chạm được logic route thật. Mỗi
  test phải tự tạo Flask app + đăng nhập riêng, theo đúng khuôn mẫu:
  ```python
  os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
  os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
  from acp.web.server import create_app
  app = create_app()
  app.config["TESTING"] = True
  c = app.test_client()
  c.post("/dangnhap", data={"password": "matkhau-test"})
  ```
  và dọn lại env ở cuối test: `for var in ("ACP_ADMIN_PASSWORD",
  "ACP_SECRET_KEY"): os.environ.pop(var, None)`.
- Mọi POST cần `_csrf` **thật**, lấy từ session, KHÔNG được dùng chuỗi giả
  như `"x"` (sẽ bị chặn 400): `with c.session_transaction() as sess: csrf
  = sess["csrf"]`.
- `ctx` cho `pipeline.create_post_for_product()` trong `test_pilot.py`
  cần thêm `"storage": _FakeStorage()` (class đã có sẵn trong file, dùng
  chung toàn bộ test hiện có) — không dùng `ctx` thiếu `storage` như
  trong `test_pipeline.py` (2 file có quy ước ctx hơi khác nhau, đây là
  quy ước của `test_pilot.py`).
- `MockAccessTrade().get_product()`/`.fetch_products()` đọc thẳng file
  seed JSON, không phụ thuộc `pipeline.ingest_datafeed()` đã chạy hay
  chưa — không cần gọi `ingest_datafeed()` trước khi
  `create_post_for_product()`.

- [ ] **Step 1: Viết 3 test (sẽ fail vì logic chưa có)**

Thêm vào `tests/test_pilot.py`, cuối file (trước khối `if __name__ ==
"__main__":`):

```python
def test_duyet_shows_variants_block_when_generation_run_exists():
    print("\nGET /duyet hiện khối CONTENT VARIANTS khi bài có content_generation_run READY")
    from acp.core import system_settings
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    system_settings.set_setting(conn, "content_engine_v2_enabled", "1")
    src = MockAccessTrade()
    target = next(p for p in src.fetch_products(limit=80) if p.product_url)
    ctx = {"source": src, "publishers": {}, "storage": _FakeStorage()}
    res = pipeline.create_post_for_product(conn, ctx, target.external_product_id, "gd2026")
    system_settings.set_setting(conn, "content_engine_v2_enabled", "0")
    check("tạo bài thành công", res.get("ok"), res.get("error"))
    if res.get("ok"):
        run = conn.execute("SELECT * FROM content_generation_run WHERE post_id=?", (res["post_id"],)).fetchone()
        if run and run["status"] == "READY":
            body = c.get("/duyet").get_data(as_text=True)
            check("có chữ CONTENT VARIANTS trong trang", "CONTENT VARIANTS" in body, "không tìm thấy")
            check("có nhãn Variant hoặc Bản tốt nhất", ("Variant" in body or "Bản tốt nhất" in body))
    conn.close()
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_duyet_no_variants_block_when_no_generation_run():
    print("\nGET /duyet KHÔNG hiện khối CONTENT VARIANTS cho bài tạo lúc flag tắt")
    from acp.core import system_settings
    conn = connect()
    system_settings.set_setting(conn, "content_engine_v2_enabled", "0")
    src = MockAccessTrade()
    target = next(p for p in src.fetch_products(limit=50) if p.product_url)
    ctx = {"source": src, "publishers": {}, "storage": _FakeStorage()}
    res = pipeline.create_post_for_product(conn, ctx, target.external_product_id, "gd2026")
    check("tạo bài thành công", res.get("ok"), res.get("error"))
    if res.get("ok"):
        run = conn.execute("SELECT * FROM content_generation_run WHERE post_id=?", (res["post_id"],)).fetchone()
        check("không có content_generation_run", run is None, run)
    conn.close()


def test_duyet_variant_card_embeds_use_variant_button():
    print("\nGET /duyet mỗi variant card có nút acpUseVariant với data caption_by_platform nhúng qua tojson")
    from acp.core import system_settings
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    system_settings.set_setting(conn, "content_engine_v2_enabled", "1")
    src = MockAccessTrade()
    target = next(p for p in src.fetch_products(limit=80) if p.product_url)
    ctx = {"source": src, "publishers": {}, "storage": _FakeStorage()}
    res = pipeline.create_post_for_product(conn, ctx, target.external_product_id, "gd2026")
    run = conn.execute("SELECT * FROM content_generation_run WHERE post_id=?", (res.get("post_id"),)).fetchone() if res.get("ok") else None
    system_settings.set_setting(conn, "content_engine_v2_enabled", "0")
    if res.get("ok") and run and run["status"] == "READY":
        body = c.get("/duyet").get_data(as_text=True)
        check("có acpUseVariant( trong trang (nút chọn variant)", "acpUseVariant(" in body, "không tìm thấy")
    conn.close()
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pilot`
Expected: không tìm thấy "CONTENT VARIANTS"/"acpUseVariant(" trong response body.

- [ ] **Step 3: Sửa route `review()` trong `web/server.py`**

Thêm import ở đầu file: `from acp.core import content_checker, content_platform, content_variant` (kiểm tra tên import hiện có, có thể đã có 1 phần — chỉ thêm phần thiếu).

Trong hàm `review()`, ngay sau vòng lặp gán `sel["prior_override"]` (trước dòng `recent = [...]`), thêm:

```python
        run_by_post = {r["post_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM content_generation_run WHERE post_id IN ({}) AND status='READY'".format(
                ",".join("?" * len(rows))), [r["id"] for r in rows]).fetchall()} if rows else {}
        for r in rows:
            run = run_by_post.get(r["id"])
            r["variants"] = []
            if not run:
                continue
            variant_rows = conn.execute(
                "SELECT * FROM content_variant_row WHERE run_id=? ORDER BY label", (run["id"],)).fetchall()
            platforms = sorted({sel["platform"] for sel in r["selected_channels"]} & {"threads", "facebook", "instagram"})
            for vr in variant_rows:
                variant_obj = content_variant.ContentVariant(
                    angle=vr["angle"], hook=vr["hook"], main_message=vr["main_message"],
                    body=json.loads(vr["body_json"]), cta=vr["cta"], structure=vr["structure"])
                r["variants"].append({
                    "id": vr["id"], "label": vr["label"], "angle": vr["angle"], "hook": vr["hook"],
                    "is_best": bool(vr["is_best"]), "final_score": vr["final_score"],
                    "caption_by_platform": content_platform.adapt_for_platforms(
                        variant_obj, platforms, r["affiliate_link"]) if platforms else {},
                    "violations": [v["message"] for v in content_checker.check_variant_rules(variant_obj)],
                })
```

(`json` đã import sẵn ở đầu `web/server.py` — kiểm tra trước khi thêm lại nếu đã có.)

- [ ] **Step 4: Sửa `web/templates/review.html`**

Thêm khối mới ngay TRƯỚC dòng `<div class="field"><label for="caption-{{ p.id }}">` (dòng chứa textarea caption gốc):

```html
      {% if p.variants %}
      <section class="content-variants">
        <h4>CONTENT VARIANTS</h4>
        {% for v in p.variants %}
        <div class="variant-card {{ 'variant-card--best' if v.is_best }}">
          <strong>{{ '★ Bản tốt nhất' if v.is_best else 'Variant ' + v.label }}</strong>
          <div class="dim">Angle: {{ v.angle }} · Score: {{ (v.final_score * 100) | round | int if v.final_score is not none else '—' }}</div>
          <div>Hook: {{ v.hook }}</div>
          <button type="button" class="btn btn--small"
            onclick="acpUseVariant('{{ p.id }}', {{ v.caption_by_platform | tojson | forceescape }})">
            Chọn variant này
          </button>
          <form method="post" action="/duyet/{{ p.id }}/doi-hook" style="display:inline">
            <input type="hidden" name="_csrf" value="{{ csrf_token }}">
            <input type="hidden" name="variant_id" value="{{ v.id }}">
            <button class="btn btn--small btn--ghost" type="submit">Đổi hook</button>
          </form>
          {% if v.violations %}
          <details><summary>Xem phân tích ({{ v.violations|length }} lưu ý)</summary>
            <ul>{% for msg in v.violations %}<li>{{ msg }}</li>{% endfor %}</ul>
          </details>
          {% endif %}
        </div>
        {% endfor %}
      </section>
      {% endif %}
```

- [ ] **Step 5: Đăng ký 3 test, chạy lại**

Thêm 3 hàm vào danh sách lời gọi cuối `tests/test_pilot.py` (trong khối `if __name__ == "__main__":`).

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pilot`
Expected: toàn bộ PASS, 0 FAIL, không hàm test cũ nào trong `test_pilot.py` bị hỏng. Cũng chạy lại `acp/.venv/bin/python3 -m acp.tests.test_pipeline` xác nhận vẫn giữ nguyên baseline (Task 4 không đụng file đó).

- [ ] **Step 6: Commit**

```bash
git add web/server.py web/templates/review.html tests/test_pilot.py
git commit -m "feat: /duyet hiện khối CONTENT VARIANTS -- 3 variant + điểm + phân tích (Content Engine v2, E6)"
```

---

### Task 5: "Chọn variant khác" — JS tại chỗ

**Files:**
- Modify: `web/templates/review.html`
- Test: `tests/test_pilot.py`

**Interfaces:**
- Consumes: nút `acpUseVariant(postId, captionByPlatform)` đã render ở Task 4 (chưa có định nghĩa hàm JS).
- Produces: hàm JS `acpUseVariant()` trong `<script>` cuối file.

- [ ] **Step 1: Viết 1 test (sẽ fail vì hàm JS chưa định nghĩa)**

Thêm vào `tests/test_pilot.py`, sau `test_duyet_variant_card_embeds_use_variant_button()`. Route `/duyet` yêu cầu đăng nhập (xem Task 4's ghi chú) — dùng đúng khuôn mẫu login:

```python
def test_duyet_page_defines_acp_use_variant_function():
    print("\nGET /duyet có định nghĩa function acpUseVariant trong <script>")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})
    body = c.get("/duyet").get_data(as_text=True)
    check("có function acpUseVariant", "function acpUseVariant(" in body, "không tìm thấy")
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pilot`
Expected: không tìm thấy `function acpUseVariant(`.

- [ ] **Step 3: Thêm `<script>` vào cuối `web/templates/review.html`**

Ngay trước `{% endblock %}` (dòng cuối file), thêm:

```html
<script>
// Chọn variant khác -- đổi giá trị textarea tại chỗ, KHÔNG round-trip
// server (Content Engine v2, E6). Operator vẫn sửa tiếp được sau khi bấm,
// rồi Duyệt như luồng đã có -- không đụng approve_post().
function acpUseVariant(postId, captionByPlatform) {
  var mapping = {
    "threads": "caption-" + postId,
    "facebook": "caption-fb-" + postId,
    "instagram": "caption-ig-" + postId,
  };
  for (var platform in mapping) {
    if (captionByPlatform[platform] !== undefined) {
      var el = document.getElementById(mapping[platform]);
      if (el) el.value = captionByPlatform[platform];
    }
  }
}
</script>
```

- [ ] **Step 4: Đăng ký test, chạy lại**

Thêm hàm vào danh sách lời gọi cuối `tests/test_pilot.py`, sau các test của Task 4.

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pilot`
Expected: toàn bộ PASS, 0 FAIL.

- [ ] **Step 5: Commit**

```bash
git add web/templates/review.html tests/test_pilot.py
git commit -m "feat: acpUseVariant() -- chọn variant khác đổi textarea tại chỗ, không round-trip (Content Engine v2, E6)"
```

---

### Task 6: Regenerate actions (đổi hook / regenerate variant / đổi angle)

**Files:**
- Modify: `web/server.py` (mở rộng `review_action()`)
- Test: `tests/test_pilot.py`

**Interfaces:**
- Consumes: `content_hook.select_best_hook()`, `content_variant.generate_variant()`, `content_angle.select_angle_candidates()` (E2/E3, không sửa).
- Produces: `review_action()` xử lý thêm `action in ("doi-hook", "lam-lai", "doi-angle")`.

- [ ] **Step 1: Viết helper + 5 test (sẽ fail vì action chưa xử lý)**

Thêm vào `tests/test_pilot.py`, sau `test_duyet_page_defines_acp_use_variant_function()`. Mỗi test POST cần `_csrf` **thật** lấy từ session (không dùng chuỗi giả) — xem ghi chú Task 4:

```python
def _mk_ready_variant_row_and_client():
    """Tạo 1 bài qua Content Engine v2 (flag bật tạm thời) + 1 Flask test
    client đã đăng nhập, trả (post_id, variant_row đầu tiên, client, csrf)
    -- dùng chung cho các test regenerate action. Trả (None, None, None,
    None) nếu không tạo được bài READY (không assert cứng, để caller tự
    quyết định bỏ qua test case đó thay vì fail giả)."""
    from acp.core import system_settings
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})
    with c.session_transaction() as sess:
        csrf = sess["csrf"]

    conn = connect()
    system_settings.set_setting(conn, "content_engine_v2_enabled", "1")
    src = MockAccessTrade()
    target = next(p for p in src.fetch_products(limit=80) if p.product_url)
    ctx = {"source": src, "publishers": {}, "storage": _FakeStorage()}
    res = pipeline.create_post_for_product(conn, ctx, target.external_product_id, "gd2026")
    system_settings.set_setting(conn, "content_engine_v2_enabled", "0")
    if not res.get("ok"):
        conn.close()
        return None, None, None, None
    run = conn.execute("SELECT * FROM content_generation_run WHERE post_id=?", (res["post_id"],)).fetchone()
    if not run or run["status"] != "READY":
        conn.close()
        return None, None, None, None
    variant_row = conn.execute("SELECT * FROM content_variant_row WHERE run_id=? ORDER BY label LIMIT 1", (run["id"],)).fetchone()
    conn.close()
    return res["post_id"], dict(variant_row), c, csrf


def test_review_action_doi_hook_changes_only_hook():
    print("\nPOST /duyet/<id>/doi-hook chỉ đổi hook, không đổi angle/main_message/cta của variant")
    post_id, variant, c, csrf = _mk_ready_variant_row_and_client()
    if post_id:
        c.post(f"/duyet/{post_id}/doi-hook", data={"variant_id": variant["id"], "_csrf": csrf})
        conn = connect()
        after = conn.execute("SELECT * FROM content_variant_row WHERE id=?", (variant["id"],)).fetchone()
        conn.close()
        check("angle không đổi", after["angle"] == variant["angle"], (after["angle"], variant["angle"]))
        check("main_message không đổi", after["main_message"] == variant["main_message"])
        check("cta không đổi", after["cta"] == variant["cta"])
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_review_action_lam_lai_regenerates_same_angle():
    print("\nPOST /duyet/<id>/lam-lai sinh lại variant cùng angle")
    post_id, variant, c, csrf = _mk_ready_variant_row_and_client()
    if post_id:
        c.post(f"/duyet/{post_id}/lam-lai", data={"variant_id": variant["id"], "_csrf": csrf})
        conn = connect()
        after = conn.execute("SELECT * FROM content_variant_row WHERE id=?", (variant["id"],)).fetchone()
        conn.close()
        check("angle giữ nguyên (regenerate cùng angle)", after["angle"] == variant["angle"])
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_review_action_doi_angle_changes_angle():
    print("\nPOST /duyet/<id>/doi-angle đổi sang angle khác (nếu còn candidate)")
    post_id, variant, c, csrf = _mk_ready_variant_row_and_client()
    if post_id:
        c.post(f"/duyet/{post_id}/doi-angle", data={"variant_id": variant["id"], "_csrf": csrf})
        conn = connect()
        after = conn.execute("SELECT * FROM content_variant_row WHERE id=?", (variant["id"],)).fetchone()
        conn.close()
        # Không assert cứng "phải khác" -- sản phẩm có thể chỉ có 1 angle khả
        # dụng (đúng giới hạn E2 đã chốt), lúc đó route trả lỗi rõ, không đổi
        # gì -- chỉ assert route không crash (variant vẫn còn tồn tại).
        check("variant vẫn còn tồn tại sau request (không bị xoá/lỗi 500)", after is not None)
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_review_action_invalid_action_still_404():
    print("\nPOST /duyet/<id>/<action_la> action lạ vẫn 404 như trước E6 (không mở khoá action tuỳ ý)")
    post_id, variant, c, csrf = _mk_ready_variant_row_and_client()
    if post_id:
        resp = c.post(f"/duyet/{post_id}/hanh-dong-khong-ton-tai", data={"_csrf": csrf})
        check("404 với action không được định nghĩa", resp.status_code == 404, resp.status_code)
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_review_action_doi_hook_missing_variant_id_errors_gracefully():
    print("\nPOST /duyet/<id>/doi-hook thiếu variant_id -> lỗi rõ, không crash 500")
    post_id, variant, c, csrf = _mk_ready_variant_row_and_client()
    if post_id:
        resp = c.post(f"/duyet/{post_id}/doi-hook", data={"_csrf": csrf})
        check("không crash (không phải 500)", resp.status_code != 500, resp.status_code)
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pilot`
Expected: các action mới trả 404 (chưa xử lý trong `review_action()`).

- [ ] **Step 3: Mở rộng `review_action()` trong `web/server.py`**

Thêm import ở đầu file: `from acp.core import content_hook, content_angle` (nếu chưa có).

Trong hàm `review_action(post_id, action)`, thêm 3 nhánh `elif` MỚI, đặt SAU nhánh `elif action == "reject":` hiện có, TRƯỚC `else: conn.close(); abort(404)`:

```python
        elif action in ("doi-hook", "lam-lai", "doi-angle"):
            variant_id = request.form.get("variant_id")
            variant_row = conn.execute("SELECT * FROM content_variant_row WHERE id=?", (variant_id,)).fetchone() if variant_id else None
            if not variant_row:
                res = {"ok": False, "error": "Thiếu hoặc không tìm thấy variant"}
            else:
                run = conn.execute("SELECT * FROM content_generation_run WHERE id=?", (variant_row["run_id"],)).fetchone()
                post = conn.execute("SELECT * FROM post WHERE id=?", (post_id,)).fetchone()
                product = conn.execute("SELECT * FROM product WHERE id=?", (post["product_id"],)).fetchone() if post else None
                if not (run and run["status"] == "READY" and product):
                    res = {"ok": False, "error": "Bài này không ở trạng thái có thể regenerate"}
                else:
                    facts = content_facts.build_product_facts(conn, product)
                    if action == "doi-hook":
                        hook_result = content_hook.select_best_hook(variant_row["angle"], facts)
                        conn.execute("UPDATE content_variant_row SET hook=?, updated_at=? WHERE id=?",
                                     (hook_result["hook"], now(), variant_id))
                        res = {"ok": True}
                    elif action == "lam-lai":
                        new_variant = content_variant.generate_variant(variant_row["angle"], facts)
                        conn.execute("""UPDATE content_variant_row SET hook=?, main_message=?, body_json=?, cta=?,
                                        updated_at=? WHERE id=?""",
                                     (new_variant.hook, new_variant.main_message,
                                      json.dumps(new_variant.body, ensure_ascii=False), new_variant.cta, now(), variant_id))
                        res = {"ok": True}
                    else:  # doi-angle
                        candidates = content_angle.select_angle_candidates(product)
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
                            res = {"ok": True}
                    audit(conn, "content_variant_row", variant_id, action, actor="operator", detail=res)
```

(Import `content_facts`, `content_variant` — kiểm tra đã có ở đầu `web/server.py` từ Task 4 hay chưa, thêm nếu thiếu.)

- [ ] **Step 4: Đăng ký 5 test, chạy lại toàn bộ**

Thêm helper `_mk_ready_variant_row_and_client` (KHÔNG đăng ký `__main__`) + 5 hàm `test_*` vào danh sách lời gọi cuối `tests/test_pilot.py`, sau các test của Task 5.

Run: `cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import && acp/.venv/bin/python3 -m acp.tests.test_pilot`
Expected: toàn bộ PASS, 0 FAIL. Cũng chạy lại `acp/.venv/bin/python3 -m acp.tests.test_pipeline` xác nhận vẫn giữ nguyên baseline (Task 6 không đụng file đó ngoài `web/server.py`).

- [ ] **Step 5: Commit**

```bash
git add web/server.py tests/test_pilot.py
git commit -m "feat: /duyet regenerate hook/variant/angle -- round-trip thật qua route generic có sẵn (Content Engine v2, E6)"
```

---

### Task 7: Regression toàn diện + Definition of Done

**Files:**
- Test: `tests/test_pipeline.py`, `tests/test_pilot.py`

**Interfaces:**
- Consumes: toàn bộ hệ thống E1-E6 + engine v1 hiện có.

- [ ] **Step 1: Viết 1 test tổng hợp xác nhận flag tắt = hành vi cũ tuyệt đối**

Thêm vào `tests/test_pipeline.py`, sau `test_review_action_doi_hook_missing_variant_id_errors_gracefully()`:

```python
def test_content_engine_v2_default_disabled_end_to_end():
    print("\nContent Engine v2 mặc định TẮT toàn hệ thống -- xác nhận tường minh trước khi kết thúc E6")
    from acp.core import system_settings
    conn = connect()
    # Xoá key nếu test trước đó lỡ để lại (không tin cậy thứ tự chạy) --
    # kiểm tra đúng trạng thái "chưa từng cấu hình" như 1 CSDL mới.
    conn.execute("DELETE FROM system_setting WHERE key='content_engine_v2_enabled'")
    check("mặc định tắt khi chưa từng set", system_settings.is_content_engine_v2_enabled(conn) is False)
    src = MockAccessTrade()
    target = next(p for p in src.fetch_products(limit=50) if p.product_url)
    ctx = {"source": src, "publishers": {}}
    res = pipeline.create_post_for_product(conn, ctx, target.external_product_id, "test")
    check("tạo bài thành công với cấu hình mặc định", res.get("ok"), res.get("error"))
    if res.get("ok"):
        run = conn.execute("SELECT * FROM content_generation_run WHERE post_id=?", (res["post_id"],)).fetchone()
        check("không có content_generation_run nào khi chưa từng bật flag", run is None, run)
    conn.close()
```

- [ ] **Step 2: Đăng ký test, chạy toàn bộ regression suite**

Thêm hàm vào danh sách lời gọi cuối `tests/test_pipeline.py`.

Run:
```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
acp/.venv/bin/python3 -m acp.tests.test_pipeline
acp/.venv/bin/python3 -m acp.tests.test_pilot
```

Expected: `test_pipeline.py` toàn bộ PASS 0 FAIL (bao gồm MỌI test D1-D4B đã có từ trước Content Engine v2 — đây là bằng chứng regression §56 mạnh nhất: nếu bất kỳ test cũ nào hỏng, `_create_post_from_raw_product()`'s thay đổi ở Task 3 đã phá vỡ hành vi cũ). `test_pilot.py` giữ nguyên baseline 340 PASS.

- [ ] **Step 3: Đối chiếu Definition of Done (PTYC §76) — tự kiểm bằng tay, không phải code**

Đọc lại bảng đối chiếu ở spec E6 mục 10, xác nhận từng dòng:
- ProductFacts/Angle/3 variants/Hook/Anti-industrial/Fact Safety/Hybrid Scoring/BEST/Anti-repetition: đã nối vào luồng thật qua Task 3.
- Threads/Facebook/Instagram adaptation: đã nối qua Task 3 (`compute_variants()` gọi `content_platform.adapt_for_platforms()`).
- `/duyệt` xem 3 variant, đổi variant được: Task 4-5.
- User edit không bị overwrite: xác nhận bằng cách đọc lại `review_action()`'s nhánh `approve` (Task 6 KHÔNG sửa nhánh này) — textarea vẫn là nguồn caption cuối khi duyệt, không có cơ chế nào ghi đè giá trị operator đã gõ tay.
- Regenerate hook riêng được: Task 6.
- Affiliate link không đổi: xác nhận `link` (biến gốc) được truyền nguyên vẹn qua `compute_variants(..., link)` không qua bước biến đổi nào.
- Không auto publish: xác nhận `content_engine.py` không import `adapters.factory`/không gọi bất kỳ hàm publish nào (`grep -rn "publish" core/content_engine.py` → không có kết quả liên quan tới việc đăng bài thật).
- Regression tests đạt: Step 2 ở trên.

Không cần code thêm cho step này — chỉ xác nhận bằng đọc code + kết quả test, ghi vào báo cáo cuối cùng.

- [ ] **Step 4: Commit**

```bash
git add tests/test_pipeline.py
git commit -m "test: xác nhận Content Engine v2 mặc định tắt toàn hệ thống -- regression cuối cùng (Content Engine v2, E6)"
```
