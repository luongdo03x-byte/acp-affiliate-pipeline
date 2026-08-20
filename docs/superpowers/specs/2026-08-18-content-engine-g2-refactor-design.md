# ACP 2.0 — Thiết kế Dọn kiến trúc: chuyển logic regenerate ra khỏi route (G2)

**Ngày:** 2026-08-18
**Trạng thái:** Thiết kế đã được chốt trong hội thoại (phần của scope G1-G4 đã duyệt tổng thể); chờ review bản spec trước khi lập implementation plan.
**Thuộc:** G2 trong 4 phần (G1 → G2 → G3 → G4). G1 (gắn LLM Gemini thật cho 6 hook) đã merge vào `main`. G2 dọn nợ kiến trúc mà final review của cả E6 lẫn G1 đều nêu: logic nghiệp vụ "regenerate" (đổi hook/làm lại/đổi angle) đang nằm trực tiếp trong route Flask `review_action()`, trái với nguyên tắc chính codebase đã tự nêu ("pipeline là nguồn sự thật duy nhất, web chỉ là 1 trong nhiều caller").

## 1. Mục tiêu

3 nhánh `doi-hook`/`lam-lai`/`doi-angle` trong `web/server.py::review_action()`
(dòng ~893-943) hiện chứa: lookup + validate ownership (variant thuộc đúng
post), gọi E1-E3 (`content_facts`, `content_hook`, `content_variant`,
`content_angle`), ghi `UPDATE content_variant_row`, ghi audit — toàn bộ
logic nghiệp vụ, không phải việc của tầng web. Hệ quả cụ thể:
- Không test được phần lõi mà không dựng cả Flask test client (đã là lý do
  5 test E6 hiện có phải đi qua `app.test_client()` dù bản chất chỉ là 3
  hàm thuần).
- Không gọi lại được từ CLI/job sau này mà không copy nguyên khối code.

G2 chuyển toàn bộ khối đó vào `core/content_engine.py` (module E6 đã tạo,
CÓ ghi DB, đúng vị trí — không phải E1-E4 bị khoá cứng), route chỉ còn đọc
`variant_id`, gọi 1 hàm, không còn logic nghiệp vụ nào.

**Ranh giới cứng đã chốt:**
- TUYỆT ĐỐI không sửa `core/content_facts.py`, `core/content_angle.py`,
  `core/content_hook.py`, `core/content_variant.py`, `core/content_checker.py`,
  `core/content_scoring.py`, `core/content_platform.py` (E1-E5, đã merge +
  review) — chỉ GỌI các hàm public của chúng từ `core/content_engine.py`
  (thay vì từ `web/server.py` như hiện tại).
- KHÔNG đổi hành vi bên ngoài của route `POST /duyet/<post_id>/<action>` —
  cùng input, cùng output (redirect + query string lỗi), cùng side-effect
  trên DB. Đây là refactor thuần (di chuyển code), không phải thay đổi tính
  năng — 5 test Flask hiện có của E6 (`test_review_action_doi_hook_changes_only_hook`,
  `test_review_action_lam_lai_regenerates_same_angle`,
  `test_review_action_doi_angle_changes_angle`,
  `test_review_action_doi_hook_missing_variant_id_errors_gracefully`,
  `test_review_action_doi_hook_rejects_variant_from_other_post`) PHẢI tiếp
  tục pass y hệt, không sửa nội dung test đó.
- KHÔNG đụng `approve`/`reject` (2 nhánh khác của cùng `review_action()`) —
  chỉ tách nhánh `doi-hook`/`lam-lai`/`doi-angle`.
- KHÔNG đụng `web/server.py::review()` (route GET hiển thị variant, việc
  của Task 4 E6) — G2 chỉ đụng `review_action()` (route POST).

## 2. Phạm vi

### Trong phạm vi
- `core/content_engine.py` thêm 4 hàm: `_load_regen_context()` (private,
  helper dùng chung), `regenerate_hook()`, `regenerate_variant()`,
  `switch_angle()` (public, mỗi hàm tự làm TRỌN VẸN: lookup + validate +
  gọi E1-E3 + ghi DB + audit — gọi được độc lập, không cần Flask).
- `web/server.py::review_action()`'s nhánh `doi-hook`/`lam-lai`/`doi-angle`
  rút gọn còn 3 dòng dispatch.
- Xoá import `content_angle` khỏi `web/server.py` nếu không còn chỗ nào
  khác dùng (kiểm tra lại trước khi xoá — `content_facts`/`content_hook`
  vẫn cần giữ vì G1 dùng ở `create_app()`).
- Test MỚI cho 4 hàm trong `core/content_engine.py` (gọi trực tiếp, không
  qua Flask) trong `tests/test_pipeline.py` — bao phủ đúng những case 5
  test Flask hiện có đã kiểm, cộng thêm case error-path (`_load_regen_context`
  trả lỗi) mà trước đây chỉ test được gián tiếp qua HTTP response.
- 5 test Flask hiện có (`tests/test_pilot.py`) giữ nguyên KHÔNG SỬA — là
  bằng chứng regression rằng route vẫn hoạt động y hệt sau khi dọn.

### Ngoài phạm vi (G3, hoặc mãi mãi ngoài phạm vi)
- Re-score/re-fact-check sau regenerate (điểm số cũ vẫn còn, chưa tính
  lại) — việc của G3, xây trên nền các hàm G2 vừa tách ra (dễ hơn nhiều so
  với sửa trực tiếp trong route).
- Đổi giao diện `/duyet` (route GET, nút bấm, JS) — không đụng.
- CLI command mới gọi 3 hàm này — G2 chỉ MỞ ĐƯỜNG (hàm gọi được độc lập),
  không tự thêm CLI command nào (YAGNI, chưa có yêu cầu cụ thể).

## 3. `core/content_engine.py` — 4 hàm mới

Thêm import `content_hook, content_angle` vào dòng import có sẵn (hiện là
`from . import content_facts, content_variant, content_scoring, content_platform`),
và thêm `audit` vào dòng `from .db import now, ulid` (module này ở tầng
`core/`, import thẳng `audit` từ `.db` được — khác `web/server.py` phải đi
qua `pipeline.audit(...)` vì không import `core.db` trực tiếp).

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
    select_angle_candidates() tự động của E2 -- xem comment gốc đã có
    trong review_action() giải thích vì sao lấy từ content_angle.ANGLES
    chứ không phải select_angle_candidates()). Trả {"ok": False,
    "error": "Không còn angle nào khác để đổi"} nếu hết candidate."""
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

**Lưu ý khi chuyển:** logic bên trong 3 hàm là COPY NGUYÊN VĂN từ
`review_action()` hiện có (kể cả comment giải thích `content_angle.ANGLES`
trong `switch_angle()`) — không viết lại, không "cải tiến" gì thêm trong
lúc di chuyển (giữ đúng tinh thần refactor thuần, dễ review diff).

## 4. `web/server.py::review_action()` — rút gọn

Thay toàn bộ khối `elif action in ("doi-hook", "lam-lai", "doi-angle"):`
(hiện chiếm ~50 dòng) bằng:

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

**Đã kiểm tra lại (khác giả định ban đầu):** `content_engine` (module lõi
có `compute_variants()`/`persist_run()`) CHƯA từng được import trong
`web/server.py` — `review()`/`_attach_content_variants()` tự đọc thẳng
`content_generation_run`/`content_variant_row` qua SQL, không gọi vào
`core/content_engine.py`. G2 là lần ĐẦU TIÊN `web/server.py` cần import
module này. Thêm `content_engine` vào dòng import có sẵn (dòng 32, hiện
là `from ..core import content_angle, content_checker, content_facts,
content_hook, content_platform, content_scoring, content_variant`), chèn
đúng vị trí alphabet giữa `content_checker` và `content_facts`.

Sau khi rút gọn, kiểm tra `content_angle` còn được dùng chỗ nào khác trong
`web/server.py` không (grep) — nếu không, xoá khỏi dòng import (dead
import). `content_facts`/`content_hook` GIỮ NGUYÊN trong import list vì
G1's `create_app()` vẫn dùng (`set_extractor`/`set_hook_generator`/`set_hook_judge`).

## 5. Testing plan

- 4 test mới cho `core/content_engine.py`'s hàm mới, gọi TRỰC TIẾP (không
  qua Flask), thêm vào `tests/test_pipeline.py`:
  - `regenerate_hook()`: hook đổi, angle/main_message/cta không đổi (tương
    đương `test_review_action_doi_hook_changes_only_hook` nhưng gọi thẳng
    hàm).
  - `regenerate_variant()`: angle giữ nguyên, nội dung (hook/main_message/
    cta/body) thực sự đổi so với trước.
  - `switch_angle()`: angle đổi sang giá trị chưa dùng trong run; case hết
    candidate trả lỗi rõ, không crash.
  - `_load_regen_context()`'s error path (qua 1 trong 3 hàm public, vd
    `regenerate_hook()` với `variant_id` không tồn tại, hoặc variant thuộc
    post khác) — trả `{"ok": False, "error": ...}`, không raise.
- 5 test Flask hiện có (`tests/test_pilot.py`) chạy lại KHÔNG SỬA, xác
  nhận route vẫn hoạt động y hệt bên ngoài sau khi logic đã chuyển vào
  `core/content_engine.py`.
- Tương thích ngược: toàn bộ 4 file test hiện có của `main` (bao gồm G1)
  phải giữ nguyên PASS 100% sau G2.
