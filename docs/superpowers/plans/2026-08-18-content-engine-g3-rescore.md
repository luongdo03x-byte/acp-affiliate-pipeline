# Re-score + re-fact-check sau regenerate (G3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sau `regenerate_hook()`/`regenerate_variant()`/`switch_angle()`, chấm lại điểm dựa trên nội dung MỚI (thay vì để điểm cũ), ẩn khỏi `/duyet` nếu nội dung mới fact-unsafe, bọc try/except quanh regenerate dispatch trong route, và vá 2 lỗ hổng test còn thiếu từ G2.

**Architecture:** `core/content_engine.py` thêm `_rescore_variant()`, mở rộng `_recent_variants()` (thêm `exclude_variant_id`) và `_load_regen_context()` (thêm `post` trong tuple trả về), gọi từ cả 3 hàm regenerate. `web/server.py::review_action()` bọc try/except quanh dispatch.

**Tech Stack:** Python 3, SQLite.

**Spec:** `docs/superpowers/specs/2026-08-18-content-engine-g3-rescore-design.md`

## Global Constraints

- TUYỆT ĐỐI không sửa `core/content_facts.py`, `core/content_angle.py`, `core/content_hook.py`, `core/content_variant.py`, `core/content_checker.py`, `core/content_scoring.py`, `core/content_platform.py` (E1-E5, đã merge + review) — chỉ GỌI hàm public của chúng.
- KHÔNG đụng `approve_post()`/`publish_post()`/chặng 4 của `core/pipeline.py`.
- KHÔNG dùng LLM thật trong test — `fn=None` (rule-based fallback) cho mọi test, đúng nguyên tắc mock-first.
- 5 test Flask hiện có (E6/G2) + 4 test core-level hiện có (G2) PHẢI tiếp tục pass KHÔNG SỬA — đã kiểm tra (grep), không test nào đọc `rule_score`/`hybrid_score`/`final_score`/`is_best`.
- `_recent_variants()`'s tham số mới `exclude_variant_id` mặc định `None` — caller cũ `compute_variants()` KHÔNG được đổi cách gọi, hành vi phải y hệt trước G3.
- Test dùng bộ harness sẵn có của repo (`check(name, cond, detail)`, list `PASS`/`FAIL` toàn cục, đăng ký tường minh trong `if __name__ == "__main__":`) — không dùng pytest.
- Chạy test bằng: `cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g3 && acp/.venv/bin/python3 -m acp.tests.<module>`.
- Baseline trước G3: `test_pipeline.py` 608 PASS/0 FAIL, `test_pilot.py` 515 PASS/0 FAIL.
- Commit message tiếng Việt có dấu đầy đủ.

---

### Task 1: `core/content_engine.py` — `_rescore_variant()` + tích hợp

**Files:**
- Modify: `core/content_engine.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `_rescore_variant(conn, variant_id, channel_id) -> None`; `_recent_variants()` mở rộng thêm `exclude_variant_id: str = None`; `_load_regen_context()` trả 5-tuple thay vì 4 (`variant_row, run, post, product, error`).
- Consumes: `content_scoring.score_variant_hybrid()`, `content_scoring.repetition_penalty()` (E4, không sửa).

- [ ] **Step 1: Viết 3 test mới (sẽ fail vì hàm/tham số chưa tồn tại)**

Thêm vào `tests/test_pipeline.py`, ngay sau `test_regenerate_hook_rejects_missing_or_wrong_post_variant()`:

```python
def test_rescore_variant_after_regenerate_produces_real_scores():
    print("\n_rescore_variant() sau regenerate_hook() -> điểm thực, không phải NULL/điểm cũ để nguyên")
    from acp.core import content_engine
    conn, post_id, variant, ch_id = _mk_regen_fixture()
    check("có điểm ban đầu (persist_run() đã chấm)", variant["final_score"] is not None, variant)
    res = content_engine.regenerate_hook(conn, post_id, variant["id"])
    check("regenerate_hook thành công", res.get("ok") is True, res)
    after = conn.execute("SELECT * FROM content_variant_row WHERE id=?", (variant["id"],)).fetchone()
    check("rule_score vẫn có giá trị thực (không NULL)", after["rule_score"] is not None, after["rule_score"])
    check("hybrid_score vẫn có giá trị thực (không NULL)", after["hybrid_score"] is not None, after["hybrid_score"])
    check("final_score vẫn có giá trị thực (không NULL)", after["final_score"] is not None, after["final_score"])
    _cleanup_regen_fixture(conn, post_id, ch_id)


def test_rescore_variant_excludes_self_from_repetition_check():
    print("\n_recent_variants(exclude_variant_id=...) không tự so variant với chính nó -- final_score = hybrid_score")
    from acp.core import content_engine
    conn, post_id, variant, ch_id = _mk_regen_fixture()
    res = content_engine.regenerate_hook(conn, post_id, variant["id"])
    check("regenerate_hook thành công", res.get("ok") is True, res)
    after = conn.execute("SELECT * FROM content_variant_row WHERE id=?", (variant["id"],)).fetchone()
    check("final_score bằng hybrid_score (không bị tự trừ penalty vì so với chính mình)",
          after["final_score"] == after["hybrid_score"], (after["final_score"], after["hybrid_score"]))
    _cleanup_regen_fixture(conn, post_id, ch_id)


def test_rescore_variant_unsafe_content_nulls_scores_and_audits():
    print("\n_rescore_variant() khi nội dung mới fact-unsafe -> 3 cột điểm NULL, is_best=0, có audit rescore_unsafe")
    from acp.core import content_engine, content_variant as _cv
    conn, post_id, variant, ch_id = _mk_regen_fixture()

    def unsafe_gen(prompt):
        return json.dumps({"main_message": "Mình đã dùng 2 tuần rồi, thấy rất ổn.", "body": []}, ensure_ascii=False)

    _cv.set_body_generator(unsafe_gen)
    try:
        res = content_engine.regenerate_variant(conn, post_id, variant["id"])
        check("regenerate_variant vẫn báo thành công (ghi được nội dung, chỉ điểm bị NULL)",
              res.get("ok") is True, res)
        after = conn.execute("SELECT * FROM content_variant_row WHERE id=?", (variant["id"],)).fetchone()
        check("rule_score NULL", after["rule_score"] is None, after["rule_score"])
        check("hybrid_score NULL", after["hybrid_score"] is None, after["hybrid_score"])
        check("final_score NULL", after["final_score"] is None, after["final_score"])
        check("is_best = 0", after["is_best"] == 0, after["is_best"])
        audit_row = conn.execute(
            "SELECT * FROM audit_log WHERE entity='content_variant_row' AND entity_id=? AND action='rescore_unsafe' "
            "ORDER BY created_at DESC LIMIT 1", (variant["id"],)).fetchone()
        check("có audit rescore_unsafe", audit_row is not None, audit_row)
    finally:
        _cv.set_body_generator(None)
        _cleanup_regen_fixture(conn, post_id, ch_id)
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g3 && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: fail vì `_rescore_variant` chưa tồn tại / điểm không đổi sau regenerate (test 1, 2 fail vì logic chưa nối; test 3 có thể fail khác — cứ chạy và xác nhận không PASS giả).

- [ ] **Step 3: Sửa `core/content_engine.py`**

Sửa `_recent_variants()` (thêm tham số, SQL thêm điều kiện loại trừ):

```python
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
```

Sửa `_load_regen_context()` (thêm `post` vào tuple trả về):

```python
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
```

Thêm hàm mới `_rescore_variant()`, đặt ngay sau `_load_regen_context()`:

```python
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
```

Cập nhật 3 hàm regenerate: đổi unpacking từ 4 thành 5 phần tử, gọi
`_rescore_variant()` ngay sau UPDATE nội dung của chính chúng, TRƯỚC dòng
`audit(...)` của hành động chính:

```python
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
```

Lưu ý: `_rescore_variant()` chỉ gọi trong nhánh THÀNH CÔNG của `switch_angle()`
(bên trong `else:`, sau UPDATE) — nhánh "hết candidate" (`if not next_angle:`)
không có nội dung mới để chấm lại, giữ nguyên không gọi.

- [ ] **Step 4: Đăng ký 3 test, chạy lại**

Thêm 3 hàm vào danh sách lời gọi cuối `tests/test_pipeline.py`, sau các test regenerate của G2.

Run: `cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g3 && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: toàn bộ PASS, 0 FAIL. Baseline 608 + 10 check mới (4+2+4=10) = 618.

- [ ] **Step 5: Commit**

```bash
git add core/content_engine.py tests/test_pipeline.py
git commit -m "feat: _rescore_variant() -- chấm lại điểm + ẩn nội dung fact-unsafe sau regenerate (Content Engine v2, G3)"
```

---

### Task 2: `web/server.py::review_action()` — bọc try/except quanh regenerate

**Files:**
- Modify: `web/server.py`
- Test: `tests/test_pilot.py`

**Interfaces:**
- Consumes: `content_engine.regenerate_hook/regenerate_variant/switch_angle()` (Task 1, không đổi chữ ký).

- [ ] **Step 1: Viết 1 test (sẽ fail vì chưa bọc)**

Thêm vào `tests/test_pilot.py`, ngay sau `test_review_action_doi_hook_rejects_variant_from_other_post()`:

```python
def test_review_action_regenerate_exception_redirects_gracefully_not_500():
    print("\nPOST /duyet/<id>/doi-hook khi content_engine raise -> redirect có lỗi, không phải 500, có audit")
    from acp.core import content_engine
    post_id, variant, c, csrf = _mk_ready_variant_row_and_client()
    if post_id:
        original = content_engine.regenerate_hook

        def crashing_regenerate(conn, post_id, variant_id):
            raise RuntimeError("giả lập lỗi LLM")

        content_engine.regenerate_hook = crashing_regenerate
        try:
            resp = c.post(f"/duyet/{post_id}/doi-hook", data={"variant_id": variant["id"], "_csrf": csrf})
            check("không phải 500", resp.status_code != 500, resp.status_code)
            check("redirect thường (302)", resp.status_code == 302, resp.status_code)
            conn = connect()
            audit_row = conn.execute(
                "SELECT * FROM audit_log WHERE entity='content_variant_row' AND entity_id=? "
                "AND action='doi-hook_failed' ORDER BY created_at DESC LIMIT 1", (variant["id"],)).fetchone()
            conn.close()
            check("có audit doi-hook_failed", audit_row is not None, audit_row)
        finally:
            content_engine.regenerate_hook = original
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g3 && acp/.venv/bin/python3 -m acp.tests.test_pilot`
Expected: fail vì exception hiện tại lan ra ngoài Flask (test client vẫn có thể bắt được lỗi 500 kiểu khác nhau tuỳ cấu hình — miễn xác nhận test KHÔNG pass giả trước khi sửa).

- [ ] **Step 3: Sửa `review_action()` trong `web/server.py`**

Thay khối `elif action in ("doi-hook", "lam-lai", "doi-angle"):` hiện có bằng:

```python
        elif action in ("doi-hook", "lam-lai", "doi-angle"):
            variant_id = request.form.get("variant_id")
            try:
                if action == "doi-hook":
                    res = content_engine.regenerate_hook(conn, post_id, variant_id)
                elif action == "lam-lai":
                    res = content_engine.regenerate_variant(conn, post_id, variant_id)
                else:
                    res = content_engine.switch_angle(conn, post_id, variant_id)
            except Exception as exc:
                res = {"ok": False, "error": "Không tạo được nội dung mới, thử lại sau"}
                pipeline.audit(conn, "content_variant_row", variant_id or post_id, f"{action}_failed",
                               actor="system", detail={"error": str(exc)})
```

Dùng `pipeline.audit(...)` (đúng quy ước sẵn có của `web/server.py`, KHÔNG
phải `audit(...)` trần trụi — file này không import `audit` trực tiếp từ
`core.db`).

- [ ] **Step 4: Đăng ký test, chạy lại**

Thêm hàm vào danh sách lời gọi cuối `tests/test_pilot.py`, sau các test `test_review_action_*` hiện có.

Run: `cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g3 && acp/.venv/bin/python3 -m acp.tests.test_pilot`
Expected: toàn bộ PASS, 0 FAIL. Baseline 515 + check mới (2) = 517, không hàm test cũ nào hỏng (đặc biệt 5 test `test_review_action_*` hiện có).

- [ ] **Step 5: Commit**

```bash
git add web/server.py tests/test_pilot.py
git commit -m "fix: bọc try/except quanh regenerate dispatch -- LLM lỗi không còn làm vỡ trang /duyet (Content Engine v2, G3)"
```

---

### Task 3: Vá 2 lỗ hổng test còn thiếu từ G2

**Files:**
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `content_engine.switch_angle()`, `content_engine.regenerate_hook()` (Task 1, không đổi).

- [ ] **Step 1: Viết 2 test**

Thêm vào `tests/test_pipeline.py`, ngay sau các test của Task 1 (`test_rescore_variant_unsafe_content_nulls_scores_and_audits`):

```python
def test_switch_angle_exhausted_candidates_returns_error_not_crash():
    print("\nswitch_angle() hết candidate -> trả lỗi rõ, không đổi angle, vẫn có audit (khác lỗi lookup)")
    from acp.core import content_engine, content_angle as _ca
    conn, post_id, variant, ch_id = _mk_regen_fixture()
    original_angles = _ca.ANGLES
    # Thu hẹp ANGLES tạm thời về đúng angle variant hiện có -- mọi angle
    # "khả dụng" coi như đã dùng hết, mô phỏng hết candidate mà không cần
    # tạo tay 11 dòng variant giả.
    _ca.ANGLES = [variant["angle"]]
    try:
        res = content_engine.switch_angle(conn, post_id, variant["id"])
        check("trả ok=False", res.get("ok") is False, res)
        check("thông báo đúng", res.get("error") == "Không còn angle nào khác để đổi", res)
        after = conn.execute("SELECT * FROM content_variant_row WHERE id=?", (variant["id"],)).fetchone()
        check("angle không đổi", after["angle"] == variant["angle"], (after["angle"], variant["angle"]))
        audit_row = conn.execute(
            "SELECT * FROM audit_log WHERE entity='content_variant_row' AND entity_id=? AND action='doi-angle' "
            "ORDER BY created_at DESC LIMIT 1", (variant["id"],)).fetchone()
        check("vẫn có audit dù thất bại (khác lỗi lookup của _load_regen_context())",
              audit_row is not None, audit_row)
    finally:
        _ca.ANGLES = original_angles
        _cleanup_regen_fixture(conn, post_id, ch_id)


def test_regenerate_hook_lookup_failure_does_not_write_audit():
    print("\nregenerate_hook() lỗi lookup (thiếu variant) -> KHÔNG ghi audit, khác lỗi 'hết angle' của switch_angle()")
    from acp.core import content_engine
    conn = connect()
    before_count = conn.execute("SELECT COUNT(*) FROM audit_log WHERE entity='content_variant_row'").fetchone()[0]
    res = content_engine.regenerate_hook(conn, "post-khong-ton-tai", None)
    check("trả ok=False", res.get("ok") is False, res)
    after_count = conn.execute("SELECT COUNT(*) FROM audit_log WHERE entity='content_variant_row'").fetchone()[0]
    check("không ghi thêm audit nào (đúng bất đối xứng đã xác nhận ở G2's final review)",
          after_count == before_count, (before_count, after_count))
    conn.close()
```

- [ ] **Step 2: Đăng ký 2 test, chạy lại**

Thêm 2 hàm vào danh sách lời gọi cuối `tests/test_pipeline.py`.

Run: `cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g3 && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: toàn bộ PASS, 0 FAIL. Baseline 618 (sau Task 1) + 5 check mới (4+1) = 623.

- [ ] **Step 3: Commit**

```bash
git add tests/test_pipeline.py
git commit -m "test: vá 2 case còn thiếu từ G2 -- switch_angle() hết candidate, bất đối xứng ghi audit (Content Engine v2, G3)"
```

---

### Task 4: Regression toàn diện + Definition of Done

**Files:**
- Test: không tạo file mới, chỉ chạy lại toàn bộ 4 file test hiện có của `main`.

**Interfaces:**
- Consumes: toàn bộ hệ thống Content Engine v2 (E1-G3) + v1 caption engine + publish worker fail-safe.

- [ ] **Step 1: Chạy toàn bộ 4 file test hiện có**

Run:
```bash
cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g3
acp/.venv/bin/python3 -m acp.tests.test_pipeline
acp/.venv/bin/python3 -m acp.tests.test_pilot
for g in docs migration client service cli web worker; do
  acp/.venv/bin/python3 -m acp.tests.test_product_automation "$g"
done
acp/.venv/bin/python3 -m acp.tests.test_manage
```

Expected: `test_pipeline.py` 623 PASS/0 FAIL, `test_pilot.py` 517 PASS/0 FAIL, `test_product_automation.py` (7 nhóm) 79 PASS/0 FAIL không đổi, `test_manage.py` 4/4 OK. KHÔNG chạy nhóm `pipeline` của `test_product_automation.py` (lỗi có sẵn trên `main` từ trước, ngoài phạm vi).

- [ ] **Step 2: Đối chiếu Definition of Done (spec G3 mục 1-2) -- tự kiểm bằng tay**

Xác nhận từng dòng:
- Sau regenerate, điểm không còn là điểm cũ: test Task 1's `test_rescore_variant_after_regenerate_produces_real_scores` PASS.
- Không tự so variant với chính nó: test Task 1's `test_rescore_variant_excludes_self_from_repetition_check` PASS.
- Nội dung fact-unsafe sau regenerate bị ẩn khỏi `/duyet`: test Task 1's `test_rescore_variant_unsafe_content_nulls_scores_and_audits` PASS + đọc lại `web/server.py::review()`'s điều kiện lọc NULL-score (từ E6's final fix wave, KHÔNG bị G3 đụng) vẫn nguyên vẹn.
- `review_action()` không còn crash 500 khi LLM lỗi: test Task 2 PASS.
- 2 lỗ hổng test từ G2 đã vá: test Task 3 PASS.
- Không sửa file nào trong E1-E5: `git diff --stat <merge-base>..HEAD -- core/content_facts.py core/content_angle.py core/content_hook.py core/content_variant.py core/content_checker.py core/content_scoring.py core/content_platform.py` rỗng.
- `_recent_variants()`'s tham số mới không đổi hành vi caller cũ: `compute_variants()`'s lời gọi `_recent_variants(conn, channel_id)` không đổi (đọc lại code, xác nhận không truyền `exclude_variant_id`).

Không cần code thêm cho step này — chỉ xác nhận bằng đọc code + kết quả test.

- [ ] **Step 3: Không cần commit** (Task 4 không tạo thay đổi code/test mới, chỉ xác nhận)
