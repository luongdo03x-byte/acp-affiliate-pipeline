# ACP 2.0 — Thiết kế Tích hợp `/duyet` + pipeline.py (Content Engine v2, phần E6)

**Ngày:** 2026-08-17
**Trạng thái:** Thiết kế đã được chốt trong hội thoại; chờ review bản spec trước khi lập implementation plan.
**Thuộc:** E6 — phần cuối cùng trong 6 phần (E1→E2→E3→E4→E5→E6) chia nhỏ
từ `PTYC_ACP_CONTENT_ENGINE_V2.md`. Toàn bộ E1-E5 (`core/content_facts.py`,
`core/content_angle.py`, `core/content_hook.py`, `core/content_variant.py`,
`core/content_checker.py`, `core/content_scoring.py`, `core/content_platform.py`)
đã merge, dormant, đều đã qua final review — **không sửa lại bất kỳ file
nào trong 7 module đó**, E6 chỉ import và gọi.

## 1. Mục tiêu

PTYC §49-56, §73: nối Content Engine v2 vào luồng tạo bài thật, cho
operator xem 3 variant + điểm ngay tại `/duyet`, chọn/sửa/regenerate được,
theo đúng rollout §73 Phase 1-2 (feature flag, không auto publish) —
nhưng đủ bộ action tương tác (§50) chứ không chỉ preview đọc.

**Ranh giới cứng đã chốt:**
- Feature flag `content_engine_v2_enabled` (bảng `system_setting` mới),
  **mặc định TẮT** — khi tắt, hành vi tạo bài/duyệt/đăng **byte-identical**
  với trước E6 (§56 regression an toàn tuyệt đối).
- **Không sửa `approve_post()`/`publish_post()`** — luồng duyệt/lên lịch/
  đăng bài giữ nguyên 100%, chỉ khác **nguồn caption ban đầu** khi tạo bài.
  Đúng PTYC §55 "Content Engine chỉ sinh và chấm nội dung, không được gọi
  approve_post()/publisher/publish_now/schedule".
- Content Engine v2 sinh lỗi bất ngờ (exception, không phải fact-unsafe —
  đó là trạng thái hợp lệ `FACT_CHECK_FAILED`) → **fallback êm về v1**
  (`content.generate()`), ghi audit, **không làm crash việc tạo bài**.
- `content.validate()` (blacklist BANNED_SUPERLATIVES + banned theo niche)
  vẫn chạy trên caption cuối cùng **dù nguồn là v1 hay v2** — Content
  Engine v2 (E1-E5) không kiểm tra 2 nhóm này (khác nhóm với
  `check_fact_safety()`), nên không được bỏ qua `content.validate()`.
  `approve_post()` đã có sẵn cơ chế validate lại từng caption theo platform
  lúc duyệt (không đổi ở E6) — lưới an toàn thứ 2 độc lập.

## 2. Phạm vi

### Trong phạm vi
- Bảng mới: `system_setting` (flag key-value), `content_generation_run`,
  `content_variant_row`.
- Module mới `core/system_settings.py`: `get_setting()`, `set_setting()`,
  `is_content_engine_v2_enabled()`.
- Module mới `core/content_engine.py`: `compute_variants()` (thuần, gọi
  E1-E5), `persist_run()` (ghi DB sau khi `post` đã tồn tại), helper truy
  vấn "bài gần đây" cho Anti-Repetition (E4).
- Sửa `core/pipeline.py`'s `_create_post_from_raw_product()`: thêm nhánh
  v2 (sau flag), **không sửa câu `INSERT INTO post` hiện có** — chỉ đổi
  nguồn biến `caption` trước INSERT, thêm 2 `UPDATE` + `persist_run()` sau
  INSERT (mirror đúng pattern `_save_channel_selection`/`post_media` đã
  có).
- Sửa `web/templates/review.html`: khối hiển thị 3 variant + điểm (§49,
  §51), nút "Chọn variant khác" (JS đổi textarea tại chỗ, không round-trip
  — giống pattern AccountGroup D4-A).
- Route mới trong `web/server.py`: regenerate hook/variant/đổi angle
  (round-trip thật, gọi lại E1-E5, cập nhật `content_variant_row`,
  `manual_edited` flag theo §52).
- Test cho toàn bộ trên + regression test xác nhận flag tắt = hành vi cũ
  y hệt.

### Ngoài phạm vi (P1)
- "Dùng nội dung này cho tất cả kênh" — **đã có sẵn** từ D2 (để trống ô
  caption override = dùng caption gốc/theo platform), không cần xây lại,
  chỉ cần xác nhận UI hiện có vẫn hoạt động đúng với caption nguồn v2.
- "Sửa caption" — **đã có sẵn** từ D1-D2 (textarea `caption`/
  `caption_facebook`/`caption_instagram` trong `review.html`), E6 chỉ cần
  đảm bảo textarea được điền sẵn đúng nội dung v2 khi flag bật.
- State machine đầy đủ theo §53 (`GENERATING` là trạng thái tức thời,
  không cần persist vì sinh nội dung P0 luôn đồng bộ trong 1 request —
  không có job nền). `content_generation_run.status` chỉ cần 3 giá trị:
  `READY` / `GENERATION_FAILED` / `FACT_CHECK_FAILED`.
- Winning Pattern Library, Performance Feedback (P2).
- Async/background generation (nếu LLM thật chậm, P1 sẽ cần job riêng —
  P0 chấp nhận generation đồng bộ trong request, khớp cách toàn bộ
  `_create_post_from_raw_product()` hiện tại hoạt động).

## 3. Data model

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

Cả 3 bảng mới hoàn toàn → thêm vào `SCHEMA` (không phải `MIGRATIONS`),
đúng pattern đã dùng cho `account_group`/`product_facts`.

`content_generation_run.post_id` **có FK tới `post(id)`** — vì vậy
`persist_run()` **bắt buộc gọi sau khi `INSERT INTO post` đã chạy**
(khác với việc TÍNH TOÁN variant, có thể làm trước insert để biết nội
dung caption cần điền vào chính câu INSERT đó). Đây là lý do tách
`compute_variants()` (thuần, không ghi DB) và `persist_run()` (ghi DB,
cần `post_id` đã tồn tại) thành 2 hàm riêng thay vì 1 hàm gộp.

## 4. `core/system_settings.py`

```python
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

Không có UI bật/tắt flag trong phạm vi E6 (vận hành viên bật qua
`set_setting()` trực tiếp/script nội bộ — đúng tinh thần §73 Phase 1 "chỉ
dùng nội bộ", chưa cần UI công khai).

## 5. `core/content_engine.py`

### 5.1. `compute_variants(conn, product, channel_id, platforms, affiliate_link) -> dict`

Thuần — gọi E1-E5, **không ghi DB** (trừ `build_product_facts()`'s cache
`product_facts` — đã có từ E1, không tính là "ghi DB của E6"):

```python
def compute_variants(conn, product, channel_id: str, platforms: list, affiliate_link: str) -> dict:
    facts = content_facts.build_product_facts(conn, product)
    variants = content_variant.generate_variants(facts, product)
    recent = _recent_variants(conn, channel_id)
    result = content_scoring.select_best_variant(variants, recent_variants=recent)
    status = "FACT_CHECK_FAILED" if result["all_rejected"] else "READY"
    captions = {}
    if status == "READY":
        captions = content_platform.adapt_for_platforms(result["best"], platforms, affiliate_link)
    return {"status": status, "variants": variants, "result": result, "captions": captions}
```

`_recent_variants(conn, channel_id, limit=5) -> list[ContentVariant]`:
truy `content_variant_row` join `content_generation_run` join `post` theo
`post.channel_id = channel_id AND content_variant_row.is_best = 1`, sắp
mới nhất trước, dựng lại `ContentVariant` từ `body_json`. Không có bản ghi
nào → `[]` (E4's `check_repetition()` đã tự xử lý `recent_variants=[]`).

### 5.2. `persist_run(conn, post_id, computed) -> dict`

Ghi `content_generation_run` (status từ `computed["status"]`) + 3
`content_variant_row` (1 dòng/variant, `is_best=1` cho đúng variant được
`select_best_variant()` chọn). Trả `{"run_id":..., "best_label":...,
"variant_rows": [{"id":..., "label":..., "is_best":...}, ...]}`.

**Bắt buộc gọi sau khi `post` đã INSERT** (xem §3) — vi phạm thứ tự này
sẽ vỡ FK `content_generation_run.post_id`.

## 6. Nối vào `core/pipeline.py`'s `_create_post_from_raw_product()`

Thay đoạn hiện tại:
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

Câu `INSERT INTO post (...)` **giữ nguyên 100%** (không thêm cột, không
đổi thứ tự placeholder — rủi ro lỗi cao nhất trong 1 câu INSERT 17 cột đã
có). Ngay sau `_save_channel_selection(conn, post_id, channel_ids)`, thêm:

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

`post_id` đã tồn tại trong DB tại điểm này (INSERT đã chạy ngay phía
trên) — `persist_run()`'s FK hợp lệ.

**`create_post_from_manual_affiliate_product()`** (đã kiểm tra
`core/pipeline.py` hiện có): hàm này chỉ là **wrapper mỏng gọi thẳng**
`_create_post_from_raw_product(..., prebuilt_affiliate_link=affiliate_url)`
— không có logic tạo `post` riêng, nên **tự động thừa hưởng** thay đổi
trên mà không cần sửa gì thêm. `create_post_for_product()` cũng vậy (gọi
thẳng `_create_post_from_raw_product()`).

**Ngoài phạm vi E6 P0 — `generate_content()` (job handler
`@handler("GENERATE_CONTENT")`)**: đây là điểm tạo `post` **thứ 3**, dùng
cho luồng `plan_content()` (chọn ứng viên tự động qua `job_queue`, khác
hẳn luồng thủ công `/sanpham`) — hàm này viết `INSERT INTO post` **riêng**
(không gọi `_create_post_from_raw_product()`), trùng lặp phần lớn logic
nhưng không dùng chung code. Nối Content Engine v2 vào đây cần lặp lại
gần như nguyên xi thay đổi ở §6 cho 1 điểm code hoàn toàn tách biệt — chi
phí không tương xứng với 1 sub-project đã đủ lớn. **P0 giữ nguyên
`generate_content()` dùng `content.generate()` (v1) vô điều kiện**, kể cả
khi flag bật — bài sinh qua đường `plan_content()` tự động sẽ không có
Content Engine v2 cho tới khi có 1 task P1 riêng hợp nhất 2 điểm tạo bài
này lại (bản thân sự trùng lặp này đã tồn tại từ trước E6, không phải nợ
kỹ thuật E6 tạo ra).

## 7. `/duyet` — hiển thị variant (`web/templates/review.html`)

Với mỗi bài có `content_generation_run` (`status='READY'`), hiển thị khối
mới **ngay trên** textarea caption hiện có:

```html
<section class="content-variants">
  <h4>CONTENT VARIANTS</h4>
  {% for v in p.variants %}
  <div class="variant-card {{ 'variant-card--best' if v.is_best }}">
    <strong>{{ '★ Bản tốt nhất' if v.is_best else 'Variant ' + v.label }}</strong>
    <div>Angle: {{ v.angle }}</div>
    <div>Hook: {{ v.hook }}</div>
    <div>Score: {{ (v.final_score * 100) | round | int if v.final_score is not none else '—' }}</div>
    <button type="button" class="btn btn--small"
      onclick="acpUseVariant('{{ p.id }}', {{ v.caption_by_platform | tojson | forceescape }})">
      Chọn variant này
    </button>
    <details><summary>Xem phân tích</summary>
      <ul>{% for msg in v.violations %}<li>{{ msg }}</li>{% endfor %}</ul>
    </details>
  </div>
  {% endfor %}
</section>
```

**Cách `p.variants` được dựng** (trong route `review()` hiện có ở
`web/server.py`, theo đúng pattern augment-per-post-dict đã dùng cho
`selected_channels`/`prior_override` — KHÔNG viết route mới): sau vòng lặp
hiện có, với mỗi `r` (post dict) có `content_generation_run.status='READY'`,
query `content_variant_row` theo `run_id`, với mỗi row dựng lại
`ContentVariant` (từ `body_json`), rồi tính 2 field không lưu DB (chi phí
tính lại không đáng kể so với chi phí lưu N×M bản caption cho mọi tổ hợp
platform, hoặc lưu lại toàn bộ `violations` mỗi khi có thay đổi nhỏ ở E3's
checker):
- `caption_by_platform`: `content_platform.adapt_for_platforms(variant,
  platforms, affiliate_link=r["affiliate_link"])`.
- `violations`: `content_checker.check_variant_rules(variant)` (chỉ lấy
  field `"message"` từ mỗi dict trả về — đúng cách `score_variant_rules()`
  của E4 tự làm khi lộ `violations` ra ngoài).

Bài KHÔNG có `content_generation_run` (tạo lúc flag tắt, hoặc v2 fallback
`GENERATION_FAILED`) → `r["variants"] = []`, template không render khối
`content-variants` (dùng `{% if p.variants %}`).

`acpUseVariant(postId, captionByPlatform)`: JS đổi giá trị textarea
`#caption-{{postId}}`/`#caption-fb-{{postId}}`/`#caption-ig-{{postId}}`
tại chỗ — **không round-trip server**, giống hệt `acpTickGroup()` (D4-A).
Operator vẫn có thể tự sửa tiếp sau khi bấm (textarea vẫn là input
thường), rồi bấm "Duyệt" như luồng đã có — không đổi `approve_post()`.

Điểm hiển thị chỉ 4 số theo §51 (BEST/Natural/Hook/Overall) — P0 rút gọn
còn 2 (Overall = `final_score`, chi tiết rule nằm trong "Xem phân tích")
vì `naturalness`/`hook_strength` riêng lẻ chỉ có ý nghĩa khi có AI Judge
thật đăng ký (E3/E4 mock-first, mặc định = rule score, hiển thị riêng
từng con số sẽ gây hiểu lầm là có đánh giá AI thật trong khi thực chất là
cùng 1 con số lặp lại 4 lần).

## 8. Regenerate actions (§50) — round-trip thật

**Tái dùng route generic đã có** `POST /duyet/<post_id>/<action>` (hàm
`review_action()` trong `web/server.py`, hiện xử lý `action in
("approve", "reject")`) — thêm 3 giá trị `action` mới thay vì tạo route
riêng, đúng kiến trúc "1 route, nhiều action" đã có sẵn. `variant_id` đọc
từ `request.form.get("variant_id")` (hidden input trong form của từng
variant card, xem §7), không phải URL segment. Tất cả yêu cầu
`content_generation_run` đang `READY` cho bài đó:

- `action="doi-hook"` — gọi lại
  `content_hook.select_best_hook(variant.angle, facts)` (facts dựng lại
  từ `build_product_facts()`, cache hit vì description không đổi), cập
  nhật `content_variant_row.hook` + set `manual_edited=0` (đây là
  regenerate, không phải sửa tay — không tính là manual edit theo §52) →
  redirect lại `/duyet` với caption mới hiển thị.
- `action="lam-lai"` ("regenerate variant" — sinh lại toàn bộ variant
  cùng angle): gọi lại `content_variant.generate_variant(angle, facts)`,
  cập nhật cả `hook`/`main_message`/`body_json`/`cta`.
- `action="doi-angle"` — lấy angle KẾ TIẾP chưa dùng từ
  `content_angle.select_angle_candidates(product)` (nếu còn), gọi
  `content_variant.generate_variant(angle_moi, facts)`, cập nhật cả
  `angle`/`hook`/`main_message`/`body_json`/`cta`/`structure`.
  Hết angle khả dụng (đã dùng hết candidates) → lỗi rõ, không đổi gì.

Sau mỗi regenerate: **không tự động re-tính `is_best`** trong P0 (regenerate
1 variant không chạy lại `select_best_variant()` cho cả 3 — chi phí thêm
không tương xứng, và operator vừa chủ động regenerate nghĩa là họ đang tự
đánh giá, không cần hệ thống tự chọn lại BEST). `manual_edited` chỉ set
`1` khi operator **sửa tay caption trong textarea** (đã có UI, không có
route mới cho việc này — D1-D2's approve flow vốn đã đọc trực tiếp giá
trị textarea, không cần đánh dấu `manual_edited` ở tầng `content_variant_row`
vì DB row đó không phải nguồn caption cuối — chỉ là lịch sử/tài liệu tham
khảo. `manual_edited` field trong bảng vẫn giữ để tương thích §52's data
model nhưng P0 không có cơ chế set nó thành 1 tự động từ textarea edit —
ghi rõ đây là giới hạn P0, không phải thiếu sót).

## 9. Testing plan

- `system_settings`: get/set roundtrip, default khi chưa có key.
- `content_engine.compute_variants()`: fact-unsafe cả 3 variant →
  `status='FACT_CHECK_FAILED'`, `captions={}`; bình thường →
  `status='READY'`, `captions` có đủ platform yêu cầu.
- `content_engine.persist_run()`: ghi đúng 1 `content_generation_run` + 3
  `content_variant_row`, đúng 1 dòng `is_best=1`.
- `_recent_variants()`: trả đúng theo channel_id, giới hạn 5, sắp mới
  nhất trước.
- `_create_post_from_raw_product()` **với flag TẮT**: hành vi y hệt trước
  E6 (so sánh trực tiếp với test đã có — không có `content_generation_run`
  nào được tạo, `caption` vẫn từ `content.generate()`).
- `_create_post_from_raw_product()` **với flag BẬT**: có
  `content_generation_run`, `post.caption_body` khớp v2's BEST variant đã
  platform-adapt, `caption_facebook`/`caption_instagram` được điền nếu
  channel có platform tương ứng.
- `_create_post_from_raw_product()` v2 raise exception giả lập (mock
  `content_facts.build_product_facts` ném lỗi) → fallback về v1, tạo bài
  vẫn thành công, có audit `content_engine_v2_failed`.
- 3 route regenerate: đổi đúng field tương ứng, không đổi field khác,
  redirect đúng, lỗi rõ khi hết angle khả dụng.
- `/duyet` GET: render đúng khối variant khi có `content_generation_run`,
  không render gì thêm khi không có (bài tạo lúc flag tắt).
- Tương thích ngược: toàn bộ test `feat/content-engine-v2` hiện có (E1-E5,
  505/0 + 340/0) VÀ toàn bộ test hiện có của `feat/shopee-affiliate-import`
  (D1-D4B) phải giữ nguyên xanh — đặc biệt các test Threads end-to-end
  (§56).

## 10. Definition of Done (đối chiếu PTYC §76)

| Tiêu chí | Trạng thái sau E6 |
|---|---|
| ProductFacts gate, Angle Selector, 3 variants, Hook Generator+scoring, Anti-industrial, Fact Safety gate, Hybrid Scoring, BEST selection, Anti-repetition | ✅ E1-E4, nay đã nối vào luồng thật |
| Threads/Facebook/Instagram adaptation | ✅ E5, nối vào luồng thật |
| `/duyệt` xem được 3 variant, đổi variant được | ✅ §7 |
| User edit không bị overwrite | ✅ đã có sẵn từ D1-D2 (textarea là nguồn sự thật cuối khi duyệt) |
| Regenerate hook riêng được | ✅ §8 |
| Affiliate link không đổi | ✅ `affiliate_link` truyền nguyên vẹn qua toàn chuỗi E1-E6 |
| Không auto publish | ✅ không đụng `approve_post()`/`publish_post()` |
| Regression tests đạt | Task cuối cùng của plan |
| Browser pilot được người dùng duyệt | Sau khi merge, người dùng tự kiểm tra qua `manage.sh` |
