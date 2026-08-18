# Dọn kiến trúc: chuyển logic regenerate ra khỏi route (G2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chuyển logic nghiệp vụ của 3 nhánh `doi-hook`/`lam-lai`/`doi-angle` từ `web/server.py::review_action()` vào `core/content_engine.py`, không đổi hành vi bên ngoài của route.

**Architecture:** `core/content_engine.py` thêm 4 hàm (`_load_regen_context()` private + `regenerate_hook()`/`regenerate_variant()`/`switch_angle()` public, mỗi hàm tự làm trọn vẹn lookup+validate+gọi E1-E3+ghi DB+audit). `web/server.py::review_action()` rút gọn còn dispatch 3 dòng.

**Tech Stack:** Python 3, SQLite.

**Spec:** `docs/superpowers/specs/2026-08-18-content-engine-g2-refactor-design.md`

## Global Constraints

- TUYỆT ĐỐI không sửa `core/content_facts.py`, `core/content_angle.py`, `core/content_hook.py`, `core/content_variant.py`, `core/content_checker.py`, `core/content_scoring.py`, `core/content_platform.py` (E1-E5, đã merge + review) — chỉ GỌI hàm public của chúng.
- KHÔNG đổi hành vi bên ngoài của `POST /duyet/<post_id>/<action>` — refactor thuần, không phải đổi tính năng.
- 5 test Flask hiện có trong `tests/test_pilot.py` (`test_review_action_doi_hook_changes_only_hook`, `test_review_action_lam_lai_regenerates_same_angle`, `test_review_action_doi_angle_changes_angle`, `test_review_action_doi_hook_missing_variant_id_errors_gracefully`, `test_review_action_doi_hook_rejects_variant_from_other_post`) PHẢI tiếp tục pass, KHÔNG được sửa nội dung.
- Logic bên trong 3 hàm mới là COPY NGUYÊN VĂN từ `review_action()` hiện có — không viết lại, không cải tiến gì thêm.
- Test dùng bộ harness sẵn có của repo (`check(name, cond, detail)`, list `PASS`/`FAIL` toàn cục, đăng ký tường minh trong `if __name__ == "__main__":`) — không dùng pytest.
- Chạy test bằng: `cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g2 && acp/.venv/bin/python3 -m acp.tests.<module>`.
- Baseline trước G2: `test_pipeline.py` 595 PASS/0 FAIL, `test_pilot.py` 515 PASS/0 FAIL.
- Commit message tiếng Việt có dấu đầy đủ.

---

### Task 1: `core/content_engine.py` — thêm 4 hàm regenerate

**Files:**
- Modify: `core/content_engine.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `regenerate_hook(conn, post_id, variant_id) -> dict`, `regenerate_variant(conn, post_id, variant_id) -> dict`, `switch_angle(conn, post_id, variant_id) -> dict` (mỗi hàm trả `{"ok": True}` hoặc `{"ok": False, "error": "..."}`).

- [ ] **Step 1: Viết fixture + 4 test (sẽ fail vì hàm chưa tồn tại)**

Thêm vào `tests/test_pipeline.py`, ngay sau `test_recent_variants_scoped_by_channel_and_ordered()` (dùng lại `_mk_content_engine_fixture()` đã có ở đó):

```python
def _mk_regen_fixture():
    """Trả (conn, post_id, variant_row_dict, channel_id) -- 1 bài đã
    persist qua Content Engine v2 thật (compute_variants+persist_run),
    variant_row đầu tiên (label A) dùng để test 3 hàm regenerate_*()/
    switch_angle(). Caller tự dọn dẹp bằng _cleanup_regen_fixture()."""
    from acp.core import content_engine
    conn, product, ch_id = _mk_content_engine_fixture()
    computed = content_engine.compute_variants(conn, product, ch_id, ["threads"], "https://link.test")
    post_id = ulid()
    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, variant_code, caption_body,
        disclosure_text, caption_final, affiliate_link, status, created_at, updated_at)
        VALUES (?,?,?,(SELECT id FROM campaign LIMIT 1),'A','x',?,'x','https://link.test','PENDING_REVIEW',?,?)""",
        (post_id, product["id"], ch_id, content.DISCLOSURE_DEFAULT, now(), now()))
    persisted = content_engine.persist_run(conn, post_id, computed)
    variant_row = conn.execute(
        "SELECT * FROM content_variant_row WHERE run_id=? ORDER BY label LIMIT 1", (persisted["run_id"],)).fetchone()
    return conn, post_id, dict(variant_row), ch_id


def _cleanup_regen_fixture(conn, post_id, ch_id):
    run_row = conn.execute("SELECT id FROM content_generation_run WHERE post_id=?", (post_id,)).fetchone()
    if run_row:
        conn.execute("DELETE FROM content_variant_row WHERE run_id=?", (run_row["id"],))
        conn.execute("DELETE FROM content_generation_run WHERE id=?", (run_row["id"],))
    conn.execute("DELETE FROM post WHERE id=?", (post_id,))
    conn.execute("DELETE FROM channel WHERE id=?", (ch_id,))
    conn.close()


def test_regenerate_hook_changes_only_hook():
    print("\nregenerate_hook() chỉ đổi hook, giữ nguyên angle/main_message/cta")
    from acp.core import content_engine
    conn, post_id, variant, ch_id = _mk_regen_fixture()
    res = content_engine.regenerate_hook(conn, post_id, variant["id"])
    check("trả ok=True", res.get("ok") is True, res)
    after = conn.execute("SELECT * FROM content_variant_row WHERE id=?", (variant["id"],)).fetchone()
    check("angle không đổi", after["angle"] == variant["angle"], (after["angle"], variant["angle"]))
    check("main_message không đổi", after["main_message"] == variant["main_message"])
    check("cta không đổi", after["cta"] == variant["cta"])
    _cleanup_regen_fixture(conn, post_id, ch_id)


def test_regenerate_variant_keeps_angle_changes_content():
    print("\nregenerate_variant() giữ nguyên angle, nội dung thực sự đổi")
    from acp.core import content_engine, content_variant as _cv
    conn, post_id, variant, ch_id = _mk_regen_fixture()
    call_count = [0]
    original = _cv._body_generator_fn

    def fake_gen(prompt):
        call_count[0] += 1
        return json.dumps({"main_message": f"Thông điệp mới lần {call_count[0]}",
                            "body": ["Điểm mới A", "Điểm mới B"]})

    _cv.set_body_generator(fake_gen)
    try:
        res = content_engine.regenerate_variant(conn, post_id, variant["id"])
        check("trả ok=True", res.get("ok") is True, res)
        after = conn.execute("SELECT * FROM content_variant_row WHERE id=?", (variant["id"],)).fetchone()
        check("angle giữ nguyên", after["angle"] == variant["angle"], (after["angle"], variant["angle"]))
        check("main_message thực sự đổi", after["main_message"] != variant["main_message"],
              (after["main_message"], variant["main_message"]))
        check("gọi đúng 1 lần body_generator", call_count[0] == 1, call_count[0])
    finally:
        _cv.set_body_generator(original)
        _cleanup_regen_fixture(conn, post_id, ch_id)


def test_switch_angle_moves_to_unused_angle():
    print("\nswitch_angle() đổi sang angle chưa dùng trong cùng run")
    from acp.core import content_engine, content_angle as _ca
    conn, post_id, variant, ch_id = _mk_regen_fixture()
    res = content_engine.switch_angle(conn, post_id, variant["id"])
    check("trả ok=True", res.get("ok") is True, res)
    after = conn.execute("SELECT * FROM content_variant_row WHERE id=?", (variant["id"],)).fetchone()
    check("angle thực sự đổi", after["angle"] != variant["angle"], (after["angle"], variant["angle"]))
    check("angle mới nằm trong content_angle.ANGLES", after["angle"] in _ca.ANGLES, after["angle"])
    _cleanup_regen_fixture(conn, post_id, ch_id)


def test_regenerate_hook_rejects_missing_or_wrong_post_variant():
    print("\nregenerate_hook() trả lỗi rõ khi thiếu variant_id hoặc variant thuộc post khác, không crash")
    from acp.core import content_engine
    conn, post_id, variant, ch_id = _mk_regen_fixture()
    conn2, post_id2, variant2, ch_id2 = _mk_regen_fixture()
    res_missing = content_engine.regenerate_hook(conn, post_id, None)
    check("thiếu variant_id -> ok=False, không crash", res_missing.get("ok") is False, res_missing)
    res_wrong_post = content_engine.regenerate_hook(conn, post_id, variant2["id"])
    check("variant thuộc post khác -> ok=False, không crash", res_wrong_post.get("ok") is False, res_wrong_post)
    _cleanup_regen_fixture(conn2, post_id2, ch_id2)
    _cleanup_regen_fixture(conn, post_id, ch_id)
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g2 && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: `AttributeError: module 'acp.core.content_engine' has no attribute 'regenerate_hook'`

- [ ] **Step 3: Viết 4 hàm trong `core/content_engine.py`**

Thêm `content_hook, content_angle` vào dòng import có sẵn (hiện là `from . import content_facts, content_variant, content_scoring, content_platform`), thêm `audit` vào dòng `from .db import now, ulid`:

```python
from . import content_facts, content_variant, content_scoring, content_platform, content_hook, content_angle
from .db import audit, now, ulid
```

Thêm vào cuối file:

```python
def _load_regen_context(conn, post_id: str, variant_id: str):
    """Trả (variant_row, run, product, None) nếu hợp lệ, hoặc
    (None, None, None, "<lý do>") nếu không -- dùng chung cho cả 3 hàm
    regenerate_*()/switch_angle() bên dưới. variant phải thuộc ĐÚNG post_id
    (chặn trộn nội dung giữa 2 bài, bài học từ Task 6's fix E6)."""
    variant_row = conn.execute(
        "SELECT * FROM content_variant_row WHERE id=?", (variant_id,)).fetchone() if variant_id else None
    if not variant_row:
        return None, None, None, "Thiếu hoặc không tìm thấy variant"
    run = conn.execute(
        "SELECT * FROM content_generation_run WHERE id=?", (variant_row["run_id"],)).fetchone()
    post = conn.execute("SELECT * FROM post WHERE id=?", (post_id,)).fetchone()
    product = conn.execute(
        "SELECT * FROM product WHERE id=?", (post["product_id"],)).fetchone() if post else None
    if not (run and run["status"] == "READY" and run["post_id"] == post_id and product):
        return None, None, None, "Variant không thuộc về bài này"
    return variant_row, run, product, None


def regenerate_hook(conn, post_id: str, variant_id: str) -> dict:
    """Đổi riêng hook, giữ nguyên angle/main_message/cta/structure của
    variant. Trả {"ok": True} hoặc {"ok": False, "error": "..."}."""
    variant_row, run, product, error = _load_regen_context(conn, post_id, variant_id)
    if error:
        return {"ok": False, "error": error}
    facts = content_facts.build_product_facts(conn, product)
    hook_result = content_hook.select_best_hook(variant_row["angle"], facts)
    conn.execute("UPDATE content_variant_row SET hook=?, updated_at=? WHERE id=?",
                 (hook_result["hook"], now(), variant_id))
    res = {"ok": True}
    audit(conn, "content_variant_row", variant_id, "doi-hook", actor="operator", detail=res)
    return res


def regenerate_variant(conn, post_id: str, variant_id: str) -> dict:
    """Sinh lại toàn bộ hook/main_message/body/cta, GIỮ NGUYÊN angle. Trả
    {"ok": True} hoặc {"ok": False, "error": "..."}."""
    variant_row, run, product, error = _load_regen_context(conn, post_id, variant_id)
    if error:
        return {"ok": False, "error": error}
    facts = content_facts.build_product_facts(conn, product)
    new_variant = content_variant.generate_variant(variant_row["angle"], facts)
    conn.execute("""UPDATE content_variant_row SET hook=?, main_message=?, body_json=?, cta=?,
                    updated_at=? WHERE id=?""",
                 (new_variant.hook, new_variant.main_message,
                  json.dumps(new_variant.body, ensure_ascii=False), new_variant.cta, now(), variant_id))
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
    variant_row, run, product, error = _load_regen_context(conn, post_id, variant_id)
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
        res = {"ok": True}
    audit(conn, "content_variant_row", variant_id, "doi-angle", actor="operator", detail=res)
    return res
```

- [ ] **Step 4: Đăng ký 4 test (helper `_mk_regen_fixture`/`_cleanup_regen_fixture` KHÔNG đăng ký), chạy lại**

Thêm 4 hàm `test_*` vào danh sách lời gọi cuối `tests/test_pipeline.py`, sau các test của `_mk_content_engine_fixture()`.

Run: `cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g2 && acp/.venv/bin/python3 -m acp.tests.test_pipeline`
Expected: toàn bộ PASS, 0 FAIL. Baseline 595 + check mới (3+3+2+2=10) = 605.

- [ ] **Step 5: Commit**

```bash
git add core/content_engine.py tests/test_pipeline.py
git commit -m "feat: regenerate_hook()/regenerate_variant()/switch_angle() -- chuyển logic regenerate vào core/content_engine.py (Content Engine v2, G2)"
```

---

### Task 2: `web/server.py::review_action()` — rút gọn thành dispatch

**Files:**
- Modify: `web/server.py`
- Test: `tests/test_pilot.py` (không sửa nội dung 5 test hiện có, chỉ chạy lại xác nhận)

**Interfaces:**
- Consumes: `content_engine.regenerate_hook()`/`regenerate_variant()`/`switch_angle()` (Task 1).

- [ ] **Step 1: Xác nhận baseline trước khi sửa**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g2 && acp/.venv/bin/python3 -m acp.tests.test_pilot`
Expected: 515 PASS, 0 FAIL (bao gồm 5 test `test_review_action_*` — ghi lại kết quả để so sánh sau khi sửa).

- [ ] **Step 2: Thêm import `content_engine`, rút gọn `review_action()`**

Thêm `content_engine` vào dòng import có sẵn (dòng 32), chèn đúng vị trí alphabet giữa `content_checker` và `content_facts`:
```python
from ..core import content_angle, content_checker, content_engine, content_facts, content_hook, content_platform, content_scoring, content_variant
```

Thay TOÀN BỘ khối `elif action in ("doi-hook", "lam-lai", "doi-angle"):` hiện có (từ dòng đó tới ngay trước `else:` / `conn.close(); abort(404)`) bằng:

```python
        elif action in ("doi-hook", "lam-lai", "doi-angle"):
            variant_id = request.form.get("variant_id")
            if action == "doi-hook":
                res = content_engine.regenerate_hook(conn, post_id, variant_id)
            elif action == "lam-lai":
                res = content_engine.regenerate_variant(conn, post_id, variant_id)
            else:
                res = content_engine.switch_angle(conn, post_id, variant_id)
```

- [ ] **Step 3: Xoá `content_angle` khỏi import nếu không còn dùng chỗ nào khác**

Run: `grep -n "content_angle\." web/server.py`
Nếu KHÔNG còn dòng nào (ngoài chính khối vừa xoá), bỏ `content_angle` khỏi dòng import dòng 32. Nếu vẫn còn dùng ở chỗ khác, giữ nguyên. `content_facts`/`content_hook` GIỮ NGUYÊN trong import list bất kể (G1's `create_app()` vẫn dùng `set_extractor`/`set_hook_generator`/`set_hook_judge`).

- [ ] **Step 4: Chạy lại 5 test hiện có + toàn bộ `test_pilot.py`**

Run: `cd /home/dluowng/Downloads/ACP/worktrees/content-engine-g2 && acp/.venv/bin/python3 -m acp.tests.test_pilot`
Expected: 515 PASS, 0 FAIL — y hệt Step 1 (route hoạt động y hệt bên ngoài, KHÔNG hàm test nào bị sửa nội dung). Cũng chạy lại `acp/.venv/bin/python3 -m acp.tests.test_pipeline` xác nhận vẫn 605 PASS/0 FAIL (Task 2 không đụng file đó).

- [ ] **Step 5: Commit**

```bash
git add web/server.py
git commit -m "refactor: review_action() chỉ còn dispatch tới content_engine.regenerate_*()/switch_angle() (Content Engine v2, G2)"
```
