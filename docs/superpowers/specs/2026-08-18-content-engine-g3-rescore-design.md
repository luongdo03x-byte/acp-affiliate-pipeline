# ACP 2.0 — Thiết kế Re-score + re-fact-check sau regenerate (G3)

**Ngày:** 2026-08-18
**Trạng thái:** Thiết kế đã được chốt trong hội thoại (phần của scope G1-G4 đã duyệt tổng thể); chờ review bản spec trước khi lập implementation plan.
**Thuộc:** G3 trong 4 phần (G1 → G2 → G3 → G4). G1 (gắn LLM Gemini thật) và G2 (dọn kiến trúc regenerate ra khỏi route) đã merge vào `main`. G3 đóng 2 lỗ hổng mà chính G1/G2's final review đã nêu rõ:
1. Sau `regenerate_hook()`/`regenerate_variant()`/`switch_angle()`, `rule_score`/`hybrid_score`/`final_score` của variant vẫn giữ giá trị TRƯỚC khi regenerate — điểm hiển thị ở `/duyet` không khớp nội dung thực tế.
2. Nội dung MỚI (sau regenerate) không được fact-check lại — nếu LLM thật (G1) tạo ra nội dung không an toàn lúc regenerate, card đó vẫn hiện ra chọn được bình thường, không có cảnh báo (đúng lỗ hổng E6's final fix wave đã vá cho lúc TẠO bài lần đầu, nhưng chưa vá cho lúc REGENERATE).
3. (G2's final review bổ sung, đưa vào G3 vì cùng chỗ code) `review_action()`'s nhánh regenerate không có try/except — với G1 đã gắn LLM thật, đây là điểm DUY NHẤT trong app gọi LLM mà không có bọc lỗi, operator gặp lỗi 500 thô thay vì redirect có thông báo.
4. (G2's spec hứa nhưng plan quên viết) Test case `switch_angle()` hết candidate — chưa từng được viết.

## 1. Mục tiêu

Sau khi `regenerate_hook()`/`regenerate_variant()`/`switch_angle()` ghi nội
dung MỚI vào `content_variant_row`, chấm lại điểm bằng đúng cơ chế đã dùng
lúc tạo bài lần đầu (`content_scoring.score_variant_hybrid()` +
`repetition_penalty()`, xem `core/content_engine.py::persist_run()`), cập
nhật `rule_score`/`hybrid_score`/`final_score`. Nếu nội dung mới KHÔNG an
toàn (fact safety fail) — áp dụng đúng cơ chế "ẩn khỏi `/duyet`" đã có từ
E6's final fix wave (`web/server.py::review()` bỏ qua variant có cả 3 điểm
NULL) bằng cách set cả 3 cột về NULL.

**Ranh giới cứng đã chốt:**
- TUYỆT ĐỐI không sửa `core/content_facts.py`, `core/content_angle.py`,
  `core/content_hook.py`, `core/content_variant.py`, `core/content_checker.py`,
  `core/content_scoring.py`, `core/content_platform.py` (E1-E5, đã merge +
  review) — chỉ GỌI hàm public của chúng.
- `core/content_engine.py` KHÔNG bị khoá (module do E6/G-series tạo, đã
  sửa ở G2) — G3 tiếp tục sửa module này.
- KHÔNG đụng `approve_post()`/`publish_post()`/chặng 4 của `core/pipeline.py`.
- 5 test Flask hiện có của E6/G2 (`test_review_action_doi_hook_changes_only_hook`
  v.v.) và 4 test core-level của G2 (`test_regenerate_hook_changes_only_hook`
  v.v.) PHẢI tiếp tục pass KHÔNG SỬA — đã kiểm tra trực tiếp (grep), không
  test nào trong số 9 test này đọc `rule_score`/`hybrid_score`/`final_score`/
  `is_best`, nên re-score của G3 không đụng gì tới assertion của chúng.
- KHÔNG dùng LLM thật trong test — toàn bộ test G3 chạy với `fn=None`
  (rule-based fallback), đúng nguyên tắc mock-first xuyên suốt E1-G2.

## 2. Phạm vi

### Trong phạm vi

**2.1. `core/content_engine.py`: `_rescore_variant()` + tích hợp vào 3 hàm regenerate**

`_load_regen_context()` (đã có từ G2) mở rộng trả thêm `post` (để lấy
`channel_id` cho `_recent_variants()`):

```python
def _load_regen_context(conn, post_id: str, variant_id: str):
    """Trả (variant_row, run, post, product, None) nếu hợp lệ, hoặc
    (None, None, None, None, "<lý do>") nếu không."""
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

Hàm mới `_rescore_variant()`, gọi ngay sau UPDATE nội dung trong cả 3 hàm
regenerate, TRƯỚC dòng `audit(...)` của chính hành động đó:

```python
def _rescore_variant(conn, variant_id: str, channel_id: str) -> None:
    """Chấm lại rule_score/hybrid_score/final_score dựa trên nội dung MỚI
    của variant (đọc thẳng từ DB, không nhận tham số variant object -- gọi
    SAU khi UPDATE nội dung đã commit, đảm bảo luôn chấm đúng bản mới nhất
    bất kể caller quên truyền gì). Nếu nội dung mới KHÔNG an toàn (fact
    safety fail) -- set cả 3 cột điểm về NULL + is_best=0, đúng tín hiệu
    "ẩn khỏi /duyet" đã có từ E6's final fix wave (web/server.py::review()
    bỏ qua variant có is_best/scores NULL). Không raise -- lỗi LLM đã được
    score_variant_hybrid()/content_checker.score_variant_rules() tự xử lý
    nội bộ (retry + fallback rule_score), không có gì cần bắt thêm ở đây.
    """
    row = conn.execute("SELECT * FROM content_variant_row WHERE id=?", (variant_id,)).fetchone()
    variant_obj = _row_to_variant(row)
    hybrid = content_scoring.score_variant_hybrid(variant_obj)
    if not hybrid["rules"].fact_safety_pass:
        conn.execute("""UPDATE content_variant_row SET rule_score=NULL, hybrid_score=NULL,
                        final_score=NULL, is_best=0, updated_at=? WHERE id=?""", (now(), variant_id))
        audit(conn, "content_variant_row", variant_id, "rescore_unsafe", actor="system",
              detail={"violations": hybrid["rules"].violations})
        return
    recent = _recent_variants(conn, channel_id)
    penalty = content_scoring.repetition_penalty(variant_obj, recent)
    final_score = max(0.0, round(hybrid["hybrid_score"] - penalty, 4))
    conn.execute("""UPDATE content_variant_row SET rule_score=?, hybrid_score=?, final_score=?,
                    updated_at=? WHERE id=?""",
                 (hybrid["rules"].score, hybrid["hybrid_score"], final_score, now(), variant_id))
```

3 hàm regenerate gọi `_rescore_variant(conn, variant_id, post["channel_id"])`
ngay sau UPDATE nội dung của chính chúng, cập nhật lời gọi
`_load_regen_context()` để nhận thêm `post` từ tuple trả về (5 phần tử
thay vì 4). Thứ tự trong mỗi hàm: UPDATE nội dung → `_rescore_variant()`
→ `audit(..., "doi-hook"/...)` (audit của hành động chính vẫn ghi cuối
cùng, giữ đúng vị trí tương đối so với G2, không đổi thứ tự audit_log).

Import cần thêm vào `core/content_engine.py`: không có gì mới —
`content_scoring` đã import sẵn từ E6, `_recent_variants`/`_row_to_variant`
đã có sẵn trong cùng module.

**2.2. `web/server.py::review_action()`: bọc try/except quanh regenerate**

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

Đúng quy ước sẵn có của file (`pipeline.audit(...)`, không phải
`audit(...)` trần trụi -- xem G2/E6's bài học về namespace).

**2.3. Test case còn thiếu từ G2 (spec đã hứa, plan quên viết)**

`switch_angle()` hết candidate — tạo run đã dùng đủ 11/11 angle (hoặc
mock `content_angle.ANGLES` ngắn lại cho test dễ dựng), xác nhận trả
`{"ok": False, "error": "Không còn angle nào khác để đổi"}`, không crash,
KHÔNG ghi đè `angle` hiện có.

**2.4. Test khoá lại bất đối xứng ghi audit (G2's parked finding)**

1 test xác nhận: lỗi lookup (`_load_regen_context()` trả error) → KHÔNG
ghi audit; lỗi "hết angle" (`switch_angle()`'s riêng) → CÓ ghi audit. Giữ
đúng hành vi đã xác nhận ở G2's final review, khoá lại bằng test thay vì
chỉ đọc code bằng mắt.

### Ngoài phạm vi (G4, hoặc mãi mãi ngoài phạm vi)
- CSS cho variant card, sửa N+1 query — việc của G4.
- Đổi ngưỡng/công thức chấm điểm (`_RULE_PENALTY`, `_REPETITION_PENALTY`)
  — thuộc E3/E4, không đụng.
- Retry/backoff riêng cho `_rescore_variant()` — `score_variant_hybrid()`
  đã tự retry 3 lần nội bộ (E4), không cần thêm lớp retry ở G3.

## 3. Testing plan

- `_rescore_variant()` sau `regenerate_hook()`: điểm thực sự đổi so với
  trước regenerate (không còn là điểm cũ).
- `_rescore_variant()` khi nội dung mới fact-unsafe: tái dùng đúng khuôn
  `test_duyet_does_not_render_fact_unsafe_variant_as_selectable_card()`
  (`tests/test_pilot.py:3167`) — monkeypatch tạm thời
  `content_variant.set_body_generator()` trả về text chứa cụm bịa trải
  nghiệm cá nhân (vd `"Mình đã dùng 2 tuần rồi..."`, khớp
  `content_facts.FABRICATED_EXPERIENCE`), gọi `regenerate_variant()`, xác
  nhận: 3 cột điểm về NULL, `is_best=0`, có audit `rescore_unsafe`, khôi
  phục `set_body_generator(None)` trong `finally`.
- `review_action()`'s try/except: giả lập `content_engine.regenerate_hook`
  raise (monkeypatch), xác nhận route trả redirect có `err=`, không phải
  500, có audit `doi-hook_failed`.
- `switch_angle()` hết candidate: test còn thiếu từ G2, viết ở đây.
- Bất đối xứng audit: test còn thiếu từ G2, viết ở đây.
- Tương thích ngược: 5 test Flask hiện có + toàn bộ 4 file test hiện có
  của `main` phải giữ nguyên PASS 100% (trừ khi cần cập nhật NHẸ theo
  mục Global Constraints ở trên — kiểm tra kỹ trước khi viết plan).
