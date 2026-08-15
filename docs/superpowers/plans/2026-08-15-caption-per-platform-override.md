# Caption theo platform + override theo account (Sub-project D2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cho phép operator viết caption riêng theo platform (Facebook/
Instagram) và/hoặc ghi đè riêng cho từng account cụ thể ở `/duyệt`, thay vì
mọi `publish_target` của một post luôn dùng chung đúng 1 caption như D1.

**Architecture:** Thêm 2 tầng ghi đè lên trên caption gốc sẵn có
(`post.caption_final`): `post.caption_facebook`/`post.caption_instagram`
(theo platform, áp dụng cho mọi account cùng nền tảng) và
`publish_target.caption_override` (theo từng account cụ thể, ưu tiên cao
nhất). Một hàm thuần `_resolve_caption()` tính caption hiệu lực theo đúng
thứ tự ưu tiên, dùng cả lúc validate (`approve_post()`) lẫn lúc đăng
(`publish_post()`). Không tự sinh nội dung khác nhau theo platform —
`content.generate()` không đổi, chỉ cho sửa tay.

**Tech Stack:** Python 3, Flask, SQLite, Jinja2. Test bằng test runner tự
viết (`check()` + `PASS`/`FAIL`) — chạy `python3 -m acp.tests.test_pipeline`
/ `acp/.venv/bin/python3 -m acp.tests.test_pilot` (test_pilot.py cần venv
riêng của repo vì python3 hệ thống không có Flask) từ thư mục **cha** của
repo (repo tên `acp/`).

**Spec:** `docs/superpowers/specs/2026-08-15-caption-per-platform-override-design.md`

## Global Constraints

- Toàn bộ code mới, comment, docstring, copy UI viết bằng tiếng Việt, đúng
  giọng văn hiện có trong file đang sửa.
- Không đổi ý nghĩa cột nào đã có. 3 cột mới (`post.caption_facebook`,
  `post.caption_instagram`, `publish_target.caption_override`) đều
  nullable, `NULL` = "dùng tầng phía trên".
- Không có `caption_threads` — `post.caption_final` vừa là bản gốc vừa là
  caption hiệu lực cho Threads.
- Thứ tự ưu tiên cố định: `publish_target.caption_override` >
  `post.caption_{platform}` > `post.caption_final`. Không đảo thứ tự này ở
  bất kỳ chỗ nào.
- `content.PLATFORM_MAX_LEN = {"threads": 500, "facebook": 63206,
  "instagram": 2200}` — trùng đúng `Publisher.max_caption_length` đã có sẵn
  ở `adapters/base.py`/`mock.py`/`live.py`; ghi comment cross-reference ở
  cả 2 nơi.
- Mọi tham số mới ở `approve_post()` đều optional, mặc định `None` = giữ
  nguyên hành vi trước D2 — không được phá bất kỳ test nào đang xanh
  (`test_pipeline.py` 222/0, `test_pilot.py` 279/0 tính đến khi bắt đầu D2).
- Caption chỉ sửa được ở `/duyệt` — không đụng `/sanpham` trong plan này.

---

## Task 1: Schema — 3 cột caption mới

**Files:**
- Modify: `core/db.py` (`MIGRATIONS`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `post.caption_facebook TEXT`, `post.caption_instagram TEXT`,
  `publish_target.caption_override TEXT` — cả 3 nullable.

- [ ] **Step 1: Viết test kiểm tra 3 cột tồn tại**

Thêm vào `tests/test_pipeline.py`, ngay trước dòng
`if __name__ == "__main__":`:

```python
def test_caption_override_columns_exist():
    print("\ncột caption theo platform/account đã có trong schema")
    conn = connect()
    post_cols = {r["name"] for r in conn.execute("PRAGMA table_info(post)").fetchall()}
    check("post có cột caption_facebook", "caption_facebook" in post_cols, post_cols)
    check("post có cột caption_instagram", "caption_instagram" in post_cols, post_cols)
    target_cols = {r["name"] for r in conn.execute("PRAGMA table_info(publish_target)").fetchall()}
    check("publish_target có cột caption_override", "caption_override" in target_cols, target_cols)
    conn.close()
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | grep -B1 -A3 "caption override columns\|caption theo platform"
```
Expected: FAIL — 3 cột chưa tồn tại.

- [ ] **Step 3: Thêm migration**

Trong `core/db.py`, tìm khối `MIGRATIONS = [...]`, thêm 3 dòng vào cuối
danh sách (trước dấu `]` đóng):
```python
    ("post", "caption_facebook", "ALTER TABLE post ADD COLUMN caption_facebook TEXT"),
    ("post", "caption_instagram", "ALTER TABLE post ADD COLUMN caption_instagram TEXT"),
    ("publish_target", "caption_override", "ALTER TABLE publish_target ADD COLUMN caption_override TEXT"),
```

- [ ] **Step 4: Chạy lại test, xác nhận PASS**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -10
```

- [ ] **Step 5: Thêm lời gọi test vào `__main__`**

Thêm `test_caption_override_columns_exist()` vào danh sách trong
`if __name__ == "__main__":`, ngay trước dòng `test_disabled_channel_does_not_corrupt_status()`
cuối cùng hiện có (hoặc bất kỳ vị trí nào trong danh sách — không phụ
thuộc thứ tự với test khác).

- [ ] **Step 6: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add core/db.py tests/test_pipeline.py
git commit -m "feat: thêm cột caption theo platform + override theo account (D2)"
```

---

## Task 2: `content.validate()` nhận `max_len`, thêm `PLATFORM_MAX_LEN`

**Files:**
- Modify: `core/content.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `content.PLATFORM_MAX_LEN: dict`,
  `content.validate(caption, disclosure=DISCLOSURE_DEFAULT, niches=None,
  max_len: int = MAX_LEN) -> list`.

- [ ] **Step 1: Viết test cho `max_len` tuỳ chỉnh**

Thêm vào `tests/test_pipeline.py`, ngay sau `test_content_guards`:

```python
def test_content_validate_platform_max_len():
    print("\ncontent.validate dùng đúng max_len theo platform, không hard-code Threads")
    link = "https://go.isclix.com/x?sub1=abc"
    long_caption = ("Nồi chiên Bear 4L. " * 30 + f"\n\n{link}\n\n{content.DISCLOSURE_DEFAULT}")
    check("caption dài 3000+ ký tự vượt quá mặc định (Threads, 500)",
          any("500" in p or "Dài" in p for p in content.validate(long_caption)),
          content.validate(long_caption))
    check("cùng caption đó PASS khi max_len=63206 (Facebook)",
          content.validate(long_caption, max_len=content.PLATFORM_MAX_LEN["facebook"]) == [],
          content.validate(long_caption, max_len=content.PLATFORM_MAX_LEN["facebook"]))
    check("PLATFORM_MAX_LEN có đủ 3 platform đúng giá trị đã biết",
          content.PLATFORM_MAX_LEN == {"threads": 500, "facebook": 63206, "instagram": 2200},
          content.PLATFORM_MAX_LEN)
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | grep -B1 -A3 "max_len theo platform"
```
Expected: FAIL — `content.PLATFORM_MAX_LEN` chưa tồn tại (`AttributeError`),
hoặc `validate()` không nhận `max_len` (`TypeError`).

- [ ] **Step 3: Thêm `PLATFORM_MAX_LEN`**

Trong `core/content.py`, tìm:
```python
MAX_LEN = 500

DISCLOSURE_DEFAULT = "#tiepthilienket — mình có nhận hoa hồng nếu bạn mua qua link này"
```
Thay bằng:
```python
MAX_LEN = 500

# Trùng đúng Publisher.max_caption_length ở adapters/base.py (mặc định),
# adapters/mock.py, adapters/live.py cho từng platform -- 2 nguồn cùng giá
# trị, sửa 1 chỗ nhớ sửa chỗ kia (không lấy động từ ctx["publishers"] vì
# approve_post() không nhận ctx).
PLATFORM_MAX_LEN = {"threads": 500, "facebook": 63206, "instagram": 2200}

DISCLOSURE_DEFAULT = "#tiepthilienket — mình có nhận hoa hồng nếu bạn mua qua link này"
```

- [ ] **Step 4: Sửa `validate()`**

Thay:
```python
def validate(caption: str, disclosure: str = DISCLOSURE_DEFAULT, niches=None) -> list:
    """Trả về danh sách vi phạm. Rỗng nghĩa là được phép đưa vào hàng đợi duyệt.

    niches: danh sách mã chủ đề đang bật. Mỗi chủ đề có thể thêm cụm cấm riêng --
    mỹ phẩm là nhóm hàng quảng cáo có điều kiện nên cấm mọi khẳng định điều trị.
    """
    problems = []
    flat = unicodedata.normalize("NFC", caption).lower()

    if len(caption) > MAX_LEN:
        problems.append(f"Dài {len(caption)} ký tự, Threads chỉ cho {MAX_LEN}")
```
bằng:
```python
def validate(caption: str, disclosure: str = DISCLOSURE_DEFAULT, niches=None,
             max_len: int = MAX_LEN) -> list:
    """Trả về danh sách vi phạm. Rỗng nghĩa là được phép đưa vào hàng đợi duyệt.

    niches: danh sách mã chủ đề đang bật. Mỗi chủ đề có thể thêm cụm cấm riêng --
    mỹ phẩm là nhóm hàng quảng cáo có điều kiện nên cấm mọi khẳng định điều trị.
    max_len: giới hạn ký tự theo platform sẽ nhận caption này -- mặc định 500
    (Threads). Facebook/Instagram có giới hạn khác, xem PLATFORM_MAX_LEN.
    """
    problems = []
    flat = unicodedata.normalize("NFC", caption).lower()

    if len(caption) > max_len:
        problems.append(f"Dài {len(caption)} ký tự, giới hạn {max_len}")
```

- [ ] **Step 5: Chạy toàn bộ `test_pipeline.py`, xác nhận PASS, không
      regression**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -20
```
Expected: test mới PASS. Mọi test cũ gọi `content.validate(caption)` không
truyền `max_len` vẫn PASS y hệt (mặc định vẫn là 500).

- [ ] **Step 6: Thêm lời gọi test vào `__main__`**

Thêm `test_content_validate_platform_max_len()` vào danh sách, ngay sau
`test_content_guards()`.

- [ ] **Step 7: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add core/content.py tests/test_pipeline.py
git commit -m "feat: content.validate nhận max_len theo platform, thêm PLATFORM_MAX_LEN (D2)"
```

---

## Task 3: `_resolve_caption()` — hàm resolve thứ tự ưu tiên

**Files:**
- Modify: `core/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: không có (hàm thuần, không query CSDL).
- Produces: `_resolve_caption(post, target, channel) -> str`. `post`/
  `target` chỉ cần hỗ trợ `post["col"]`/`target["col"]` (dict thường hoặc
  sqlite3.Row đều dùng được).

- [ ] **Step 1: Viết test cho cả 3 nhánh ưu tiên**

Thêm vào `tests/test_pipeline.py`, ngay trước `if __name__ == "__main__":`:

```python
def test_resolve_caption_precedence():
    print("\n_resolve_caption: override account > caption theo platform > caption gốc")
    post = {"caption_final": "gốc", "caption_facebook": "riêng facebook", "caption_instagram": None}
    ch_fb = {"platform": "facebook"}
    ch_ig = {"platform": "instagram"}
    ch_th = {"platform": "threads"}

    check("có override account -> dùng override, bất kể platform gì",
          pipeline._resolve_caption(post, {"caption_override": "riêng account"}, ch_fb) == "riêng account")
    check("không override, facebook có caption riêng -> dùng caption riêng facebook",
          pipeline._resolve_caption(post, {"caption_override": None}, ch_fb) == "riêng facebook")
    check("không override, instagram KHÔNG có caption riêng (None) -> rơi về gốc",
          pipeline._resolve_caption(post, {"caption_override": None}, ch_ig) == "gốc")
    check("threads không có cột riêng -> luôn rơi về gốc dù post có caption_facebook",
          pipeline._resolve_caption(post, {"caption_override": None}, ch_th) == "gốc")
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | grep -B1 -A5 "_resolve_caption"
```
Expected: FAIL — `AttributeError: module 'pipeline' has no attribute '_resolve_caption'`.

- [ ] **Step 3: Thêm `_resolve_caption()`**

Trong `core/pipeline.py`, thêm ngay sau hàm `_resolve_channels_by_id`
(tìm đoạn kết thúc bằng `return rows, None` của hàm đó, chèn ngay sau):

```python
def _resolve_caption(post, target, channel) -> str:
    """Thứ tự ưu tiên: override riêng account > caption riêng theo platform
    > caption gốc. Thuần tính toán, không query. `post`/`target` chỉ cần hỗ
    trợ post["col"]/target["col"] -- dict thường (lúc validate, chưa có
    publish_target thật) hay sqlite3.Row (lúc publish, target là row CSDL
    thật) đều dùng được, không cần phân biệt loại."""
    if target["caption_override"]:
        return target["caption_override"]
    platform_col = {"facebook": "caption_facebook", "instagram": "caption_instagram"}.get(channel["platform"])
    if platform_col and post[platform_col]:
        return post[platform_col]
    return post["caption_final"]
```

- [ ] **Step 4: Chạy lại test, xác nhận PASS**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -10
```

- [ ] **Step 5: Thêm lời gọi test vào `__main__`**

Thêm `test_resolve_caption_precedence()` vào danh sách.

- [ ] **Step 6: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add core/pipeline.py tests/test_pipeline.py
git commit -m "feat: _resolve_caption() -- thứ tự ưu tiên override/platform/gốc (D2)"
```

---

## Task 4: `approve_post()` — validate theo nhóm, lưu caption theo platform + override

**Files:**
- Modify: `core/pipeline.py` (`approve_post`, dòng ~396-438)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `_resolve_caption` (Task 3), `content.PLATFORM_MAX_LEN`,
  `content.validate(..., max_len=...)` (Task 2), `_union_niches` (đã có từ
  D1), `_resolve_channels_by_id` (đã có từ D1).
- Produces: `approve_post(conn, post_id, actor="operator",
  caption_override=None, channel_ids=None, caption_facebook: str = None,
  caption_instagram: str = None, caption_overrides: dict = None) -> dict`
  — thêm 3 tham số mới, giữ nguyên toàn bộ tham số/hành vi cũ khi không
  truyền.

- [ ] **Step 1: Viết test cho `approve_post` với caption theo platform**

Thêm vào `tests/test_pipeline.py`, ngay trước `if __name__ == "__main__":`:

```python
def test_approve_post_saves_platform_captions():
    print("\napprove_post lưu caption_facebook/instagram vào post, None giữ nguyên, '' xoá")
    conn = connect()
    ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(111))
    check("có job sinh nội dung", len(ids) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=111)}})
    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()

    res = pipeline.approve_post(conn, post["id"], caption_facebook="Caption FB riêng")
    check("duyệt thành công với caption_facebook", res["ok"], res)
    post_after = conn.execute("SELECT caption_facebook, caption_instagram FROM post WHERE id=?", (post["id"],)).fetchone()
    check("caption_facebook được lưu đúng", post_after["caption_facebook"] == "Caption FB riêng", dict(post_after))
    check("caption_instagram vẫn NULL (không truyền)", post_after["caption_instagram"] is None, dict(post_after))
    conn.close()


def test_approve_post_empty_string_clears_platform_caption():
    print("\napprove_post: caption_facebook='' xoá override, quay về gốc")
    conn = connect()
    ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(112))
    check("có job sinh nội dung", len(ids) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=112)}})
    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()

    res = pipeline.approve_post(conn, post["id"], caption_facebook="tạm thời")
    check("duyệt lần 1 thành công", res["ok"], res)

    # Duyệt lại (mô phỏng bài bị bounce rồi duyệt lại) với caption_facebook="" -- xoá.
    conn.execute("UPDATE post SET status='PENDING_REVIEW' WHERE id=?", (post["id"],))
    res2 = pipeline.approve_post(conn, post["id"], caption_facebook="")
    check("duyệt lần 2 thành công", res2["ok"], res2)
    post_after = conn.execute("SELECT caption_facebook FROM post WHERE id=?", (post["id"],)).fetchone()
    check("caption_facebook về lại NULL sau khi truyền ''", post_after["caption_facebook"] is None, dict(post_after))
    conn.close()


def test_approve_post_channel_overrides_saved_to_publish_target():
    print("\napprove_post: caption_overrides ghi đúng vào publish_target.caption_override từng kênh")
    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (fb_id, "fb_caption_override_test", "facebook", "FB Caption Override", "ACTIVE", 1, 12, 0, now()))
    try:
        ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(113))
        check("có job sinh nội dung", len(ids) > 0)
        jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=113)}})
        post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
        ch1_id = post["channel_id"]

        res = pipeline.approve_post(conn, post["id"], channel_ids=[ch1_id, fb_id],
                                    caption_overrides={fb_id: "Caption riêng chỉ account facebook này"})
        check("duyệt đa kênh với override thành công", res["ok"], res)

        target_ch1 = conn.execute("SELECT caption_override FROM publish_target WHERE post_id=? AND channel_id=?",
                                  (post["id"], ch1_id)).fetchone()
        target_fb = conn.execute("SELECT caption_override FROM publish_target WHERE post_id=? AND channel_id=?",
                                 (post["id"], fb_id)).fetchone()
        check("target ch1 KHÔNG có override (không nằm trong dict)", target_ch1["caption_override"] is None, dict(target_ch1))
        check("target facebook có đúng override", target_fb["caption_override"] == "Caption riêng chỉ account facebook này",
              dict(target_fb))
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id=?", (fb_id,))
        conn.close()


def test_approve_post_validates_each_caption_group_separately():
    print("\napprove_post: 2 kênh khác caption thì validate riêng, không lẫn niches của nhau")
    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, niches, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                 (fb_id, "fb_group_validate_test", "facebook", "FB Group Validate", "ACTIVE", 1, 12, 0,
                  json.dumps(["my-pham"]), now()))
    try:
        ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(114))
        check("có job sinh nội dung", len(ids) > 0)
        jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=114)}})
        post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
        ch1_id = post["channel_id"]

        link = "https://go.isclix.com/x?sub1=abc"
        # Caption riêng cho facebook chứa cụm cấm của niche mỹ phẩm ("trị mụn") --
        # channel ch1 (không có niches nào) không bị ảnh hưởng vì 2 kênh giờ
        # dùng 2 caption KHÁC NHAU, không còn union niches qua cả 2 kênh như D1.
        fb_caption = f"Kem trị mụn hiệu quả\n\n{link}\n\n{content.DISCLOSURE_DEFAULT}"
        res = pipeline.approve_post(conn, post["id"], channel_ids=[ch1_id, fb_id],
                                    caption_overrides={fb_id: fb_caption})
        check("bị chặn vì caption facebook chứa cụm cấm điều trị của niche mỹ phẩm",
              res["ok"] is False, res)
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id=?", (fb_id,))
        conn.close()
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | grep -B1 -A3 "approve_post lưu\|approve_post:"
```
Expected: FAIL — `TypeError: approve_post() got an unexpected keyword argument 'caption_facebook'`.

- [ ] **Step 3: Sửa `approve_post()`**

Thay toàn bộ hàm:
```python
def approve_post(conn, post_id: str, actor: str = "operator", caption_override: str = None,
                  channel_ids: list = None) -> dict:
    post = conn.execute("SELECT * FROM post WHERE id=?", (post_id,)).fetchone()
    if not post:
        return {"ok": False, "error": "Không tìm thấy bài đăng"}

    ids = channel_ids or [post["channel_id"]]
    channels, err = _resolve_channels_by_id(conn, ids)
    if err:
        return {"ok": False, "error": err}

    caption = caption_override or post["caption_final"]
    problems = content.validate(caption, niches=_union_niches(conn, ids))
    if problems:
        return {"ok": False, "error": "; ".join(problems)}

    # Slot riêng cho từng kênh -- rate-limit độc lập theo channel.min_gap_minutes
    # của chính kênh đó (xem _next_slot). post.scheduled_at chỉ còn là "sớm
    # nhất trong N giờ", dùng để sort/hiển thị, không còn là giờ đăng chính xác
    # khi có từ 2 kênh trở lên.
    slots = {ch["id"]: _next_slot(conn, ch["id"]) for ch in channels}
    earliest = min(slots.values())
    conn.execute("""UPDATE post SET caption_final=?, status='SCHEDULED', scheduled_at=?,
                    reviewed_by=?, reviewed_at=?, reject_reason=NULL, updated_at=? WHERE id=?""",
                 (caption, earliest, actor, now(), now(), post_id))

    targets = []
    for ch in channels:
        target_id = ulid()
        slot = slots[ch["id"]]
        conn.execute("""INSERT INTO publish_target (id, post_id, channel_id, status, scheduled_at,
                        created_at, updated_at) VALUES (?,?,?,'SCHEDULED',?,?,?)""",
                     (target_id, post_id, ch["id"], slot, now(), now()))
        # post_id/channel_id ở lại payload để jobs.py xử lý AuthError/ContentViolationError
        # (đánh dấu kênh NEEDS_REAUTH, đẩy bài về PENDING_REVIEW) không phải sửa.
        enqueue(conn, "PUBLISH_POST",
                {"publish_target_id": target_id, "post_id": post_id, "channel_id": ch["id"]},
                priority=50, run_after=slot, idempotency_key=f"pub:{target_id}")
        targets.append({"channel_id": ch["id"], "publish_target_id": target_id, "scheduled_at": slot})

    audit(conn, "post", post_id, "approved", actor=actor, detail={"targets": targets})
    return {"ok": True, "scheduled_at": earliest, "publish_target_id": targets[0]["publish_target_id"],
            "targets": targets}
```
bằng:
```python
def approve_post(conn, post_id: str, actor: str = "operator", caption_override: str = None,
                  channel_ids: list = None, caption_facebook: str = None,
                  caption_instagram: str = None, caption_overrides: dict = None) -> dict:
    post = conn.execute("SELECT * FROM post WHERE id=?", (post_id,)).fetchone()
    if not post:
        return {"ok": False, "error": "Không tìm thấy bài đăng"}

    ids = channel_ids or [post["channel_id"]]
    channels, err = _resolve_channels_by_id(conn, ids)
    if err:
        return {"ok": False, "error": err}

    caption = caption_override or post["caption_final"]

    # Validate phải dùng đúng giá trị SẮP được lưu trong lần duyệt này, không
    # phải giá trị cũ trong CSDL -- operator có thể đang set/sửa
    # caption_facebook/caption_instagram ngay trong request này.
    post_effective = dict(post)
    post_effective["caption_final"] = caption
    if caption_facebook is not None:
        post_effective["caption_facebook"] = caption_facebook.strip() or None
    if caption_instagram is not None:
        post_effective["caption_instagram"] = caption_instagram.strip() or None

    # Gom kênh theo đúng chuỗi caption chúng sẽ dùng -- validate mỗi nhóm 1
    # lần bằng union niches TRONG NHÓM ĐÓ thôi (không phải toàn bộ kênh được
    # chọn như D1, vì giờ các nhóm có thể dùng caption khác nhau hoàn toàn).
    # Khi mọi kênh vẫn dùng chung 1 caption thì công thức này tự nhiên rút
    # gọn về đúng y hệt cách D1 làm.
    groups = {}  # caption_text -> [channel row, ...]
    for ch in channels:
        text = _resolve_caption(post_effective, {"caption_override": (caption_overrides or {}).get(ch["id"])}, ch)
        groups.setdefault(text, []).append(ch)
    for text, chs in groups.items():
        ids_in_group = [c["id"] for c in chs]
        platforms_in_group = {c["platform"] for c in chs}
        max_len = min(content.PLATFORM_MAX_LEN.get(p, content.MAX_LEN) for p in platforms_in_group)
        problems = content.validate(text, niches=_union_niches(conn, ids_in_group), max_len=max_len)
        if problems:
            return {"ok": False, "error": "; ".join(problems)}

    # Slot riêng cho từng kênh -- rate-limit độc lập theo channel.min_gap_minutes
    # của chính kênh đó (xem _next_slot). post.scheduled_at chỉ còn là "sớm
    # nhất trong N giờ", dùng để sort/hiển thị, không còn là giờ đăng chính xác
    # khi có từ 2 kênh trở lên.
    slots = {ch["id"]: _next_slot(conn, ch["id"]) for ch in channels}
    earliest = min(slots.values())
    conn.execute("""UPDATE post SET caption_final=?, status='SCHEDULED', scheduled_at=?,
                    reviewed_by=?, reviewed_at=?, reject_reason=NULL, updated_at=? WHERE id=?""",
                 (caption, earliest, actor, now(), now(), post_id))
    if caption_facebook is not None:
        conn.execute("UPDATE post SET caption_facebook=? WHERE id=?",
                     (caption_facebook.strip() or None, post_id))
    if caption_instagram is not None:
        conn.execute("UPDATE post SET caption_instagram=? WHERE id=?",
                     (caption_instagram.strip() or None, post_id))

    targets = []
    for ch in channels:
        target_id = ulid()
        slot = slots[ch["id"]]
        override = (caption_overrides or {}).get(ch["id"])
        conn.execute("""INSERT INTO publish_target (id, post_id, channel_id, status, scheduled_at,
                        caption_override, created_at, updated_at) VALUES (?,?,?,'SCHEDULED',?,?,?,?)""",
                     (target_id, post_id, ch["id"], slot, override, now(), now()))
        # post_id/channel_id ở lại payload để jobs.py xử lý AuthError/ContentViolationError
        # (đánh dấu kênh NEEDS_REAUTH, đẩy bài về PENDING_REVIEW) không phải sửa.
        enqueue(conn, "PUBLISH_POST",
                {"publish_target_id": target_id, "post_id": post_id, "channel_id": ch["id"]},
                priority=50, run_after=slot, idempotency_key=f"pub:{target_id}")
        targets.append({"channel_id": ch["id"], "publish_target_id": target_id, "scheduled_at": slot})

    audit(conn, "post", post_id, "approved", actor=actor, detail={"targets": targets})
    return {"ok": True, "scheduled_at": earliest, "publish_target_id": targets[0]["publish_target_id"],
            "targets": targets}
```

- [ ] **Step 4: Chạy toàn bộ `test_pipeline.py`, xác nhận PASS, không
      regression**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -30
```
Expected: 4 test mới PASS. Mọi test cũ gọi `approve_post()` không truyền 3
tham số mới vẫn PASS y hệt (nhánh `groups` có đúng 1 nhóm chứa mọi kênh khi
không ai dùng caption riêng, kết quả validate giống hệt D1).

- [ ] **Step 5: Thêm lời gọi test vào `__main__`**

Thêm 4 hàm test mới vào danh sách trong `if __name__ == "__main__":`:
`test_approve_post_saves_platform_captions()`,
`test_approve_post_empty_string_clears_platform_caption()`,
`test_approve_post_channel_overrides_saved_to_publish_target()`,
`test_approve_post_validates_each_caption_group_separately()`.

- [ ] **Step 6: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add core/pipeline.py tests/test_pipeline.py
git commit -m "feat: approve_post nhận caption theo platform + override theo account, validate theo nhóm (D2)"
```

---

## Task 5: `publish_post()` dùng caption đã resolve

**Files:**
- Modify: `core/pipeline.py` (`publish_post`, dòng ~622)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `_resolve_caption` (Task 3).
- Produces: không đổi chữ ký `publish_post()`.

- [ ] **Step 1: Viết test — publisher nhận đúng caption theo từng tầng ưu
      tiên**

Thêm vào `tests/test_pipeline.py`, ngay trước `if __name__ == "__main__":`:

```python
def test_publish_post_uses_resolved_caption_per_target():
    print("\npublish_post: mỗi target dùng đúng caption theo thứ tự ưu tiên override/platform/gốc")
    conn = connect()
    fb_override_id, fb_platform_id, ig_fallback_id = ulid(), ulid(), ulid()
    for cid, code, platform, handle in [
        (fb_override_id, "fb_pub_override_test", "facebook", "FB Override"),
        (fb_platform_id, "fb_pub_platform_test", "facebook", "FB Platform"),
        (ig_fallback_id, "ig_pub_fallback_test", "instagram", "IG Fallback"),
    ]:
        conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                        daily_post_cap, min_gap_minutes, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (cid, code, platform, handle, "ACTIVE", 1, 12, 0, now()))
    try:
        ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(121))
        check("có job sinh nội dung", len(ids) > 0)
        jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=121)}})
        post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()

        res = pipeline.approve_post(
            conn, post["id"], channel_ids=[fb_override_id, fb_platform_id, ig_fallback_id],
            caption_facebook="Caption riêng cho Facebook",
            caption_overrides={fb_override_id: "Caption riêng chỉ account này"})
        check("duyệt thành công", res["ok"], res)
        for t in res["targets"]:
            conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?",
                         (now(), f"pub:{t['publish_target_id']}"))

        fb_pub, ig_pub = MockFacebookPublisher(seed=122), MockInstagramPublisher(seed=123)
        jobs.drain(conn, ctx={"source": MockAccessTrade(),
                              "publishers": {"threads": MockThreads(seed=121), "facebook": fb_pub, "instagram": ig_pub}})

        fb_captions = [c for _, c, _ in fb_pub.published]
        ig_captions = [c for _, c, _ in ig_pub.published]
        check("account có override riêng nhận đúng override, không phải caption_facebook",
              "Caption riêng chỉ account này" in fb_captions, fb_captions)
        check("account facebook còn lại (không override) nhận đúng caption_facebook",
              "Caption riêng cho Facebook" in fb_captions, fb_captions)
        check("account instagram (không có caption riêng, không override) rơi về caption gốc",
              ig_captions == [post["caption_final"]], (ig_captions, post["caption_final"]))
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id IN (?,?,?)",
                     (fb_override_id, fb_platform_id, ig_fallback_id))
        conn.close()
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | grep -B1 -A5 "dùng đúng caption theo thứ tự"
```
Expected: FAIL — mọi target đều nhận `post["caption_final"]` (chưa resolve
theo tầng nào).

- [ ] **Step 3: Sửa `publish_post()`**

Trong `core/pipeline.py`, tìm:
```python
    conn.execute("UPDATE publish_target SET status='RUNNING', updated_at=? WHERE id=?", (now(), target["id"]))
    try:
        publisher = ctx["publishers"][channel["platform"]]
        media = [post["image_url_composited"]] if post["image_url_composited"] else []
        result = publisher.publish(channel, post["caption_final"], media=media)
```
Thay bằng:
```python
    conn.execute("UPDATE publish_target SET status='RUNNING', updated_at=? WHERE id=?", (now(), target["id"]))
    try:
        publisher = ctx["publishers"][channel["platform"]]
        media = [post["image_url_composited"]] if post["image_url_composited"] else []
        caption = _resolve_caption(post, target, channel)
        result = publisher.publish(channel, caption, media=media)
```

- [ ] **Step 4: Chạy toàn bộ `test_pipeline.py`, xác nhận PASS, không
      regression**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -20
```
Expected: test mới PASS. Mọi test cũ (post chỉ có caption gốc, không dùng
tầng nào của D2) vẫn PASS y hệt vì `_resolve_caption` rơi thẳng về
`post["caption_final"]` khi không có override/caption theo platform.

- [ ] **Step 5: Thêm lời gọi test vào `__main__`**, và thêm import cần
      thiết

Đầu `tests/test_pipeline.py`, tìm dòng:
```python
from acp.adapters.mock import MockAccessTrade, MockFacebookPublisher, MockThreads  # noqa: E402
```
thêm `MockInstagramPublisher` vào cùng dòng import đó (hiện chỉ được import
cục bộ bên trong 1 hàm test khác, chưa có ở top-level — xác nhận bằng
`grep -n MockInstagramPublisher tests/test_pipeline.py` trước khi sửa nếu
muốn chắc chắn, nhưng thêm là đúng, không có nhánh điều kiện nào ở đây):
```python
from acp.adapters.mock import MockAccessTrade, MockFacebookPublisher, MockInstagramPublisher, MockThreads  # noqa: E402
```

Thêm `test_publish_post_uses_resolved_caption_per_target()` vào danh sách
trong `if __name__ == "__main__":`.

- [ ] **Step 6: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add core/pipeline.py tests/test_pipeline.py
git commit -m "feat: publish_post dùng caption đã resolve theo thứ tự ưu tiên (D2)"
```

---

## Task 6: Web layer — route + template + test end-to-end

**Bài học từ D1 (đọc trước khi làm):** final review của D1 phát hiện 1 lỗi
Critical vì route và template bị tách thành 2 task riêng, không task nào có
test kiểm tra cả luồng thật — chỗ ghép nối giữa 2 task là chỗ không ai
review tới. Task này **gộp route + template + 1 test end-to-end thật**
trong cùng 1 task, tránh lặp lại đúng lỗ hổng đó.

**Files:**
- Modify: `web/server.py` (`review_action`, dòng ~403-422)
- Modify: `web/templates/review.html`
- Test: `tests/test_pilot.py`

**Interfaces:**
- Consumes: `pipeline.approve_post(caption_facebook=, caption_instagram=,
  caption_overrides=)` (Task 4).
- `review()` (route `GET /duyet`) **không cần sửa** — đã `SELECT p.*`,
  2 cột mới (`caption_facebook`, `caption_instagram`) tự động có mặt trong
  `r["caption_facebook"]`/`r["caption_instagram"]` sau Task 1, không cần
  thêm cột nào vào câu SELECT.

- [ ] **Step 1: Đọc `review_action()` hiện tại để xác nhận đúng vị trí**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
grep -n "def review_action" -A 20 web/server.py
```

- [ ] **Step 2: Viết test end-to-end (RED trước khi sửa route/template)**

Thêm vào `tests/test_pilot.py`, ngay trước `if __name__ == "__main__":`:

```python
def test_duyet_approve_saves_caption_platform_and_override():
    print("\n/duyet approve lưu đúng caption theo platform + override theo account, đăng đúng caption")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (fb_id, "fb_duyet_caption_test", "facebook", "FB Duyệt Caption", "ACTIVE", 1, 12, 0, now()))
    src = MockAccessTrade()
    target = next(p for p in src.fetch_products(limit=50) if p.product_url)
    ctx = {"source": src, "publishers": {}}
    # Tạo post qua create_post_for_product(channel_codes=[...]) (đã có từ D1)
    # để post_channel_selection có SẴN cả Threads lẫn Facebook ngay từ lúc
    # tạo -- nhờ vậy /duyet render field caption_facebook NGAY LẦN GET ĐẦU
    # TIÊN, kiểm tra được tên field template render khớp với tên route đọc
    # (D1 từng lọt 1 lỗi Critical vì route/template lệch nhau mà không test
    # nào bắt được, xem đầu Task 6) mà không cần approve trước rồi mới có.
    # "ch1" đứng đầu -> kênh chính (post.channel_id) là Threads, giống kịch
    # bản thường gặp nhất.
    res = pipeline.create_post_for_product(
        conn, ctx, target.external_product_id, "test",
        channel_codes=["ch1", "fb_duyet_caption_test"])
    check("tạo bài đa kênh (facebook + threads) thành công", res.get("ok"), res.get("error"))
    post = conn.execute("SELECT * FROM post WHERE id=?", (res["post_id"],)).fetchone()
    conn.close()

    # Kiểm tra TEMPLATE thực sự render đúng tên field mà route sẽ đọc --
    # không chỉ POST thẳng bằng tên field đúng sẵn.
    page_before = c.get("/duyet")
    body_before = page_before.get_data(as_text=True)
    check("form /duyet render field caption_facebook (post có kênh facebook trong lựa chọn)",
          'name="caption_facebook"' in body_before, body_before[:2000])
    check("form /duyet render đúng field caption_override_<channel_id> cho account facebook",
          f'name="caption_override_{fb_id}"' in body_before, body_before[:2000])

    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    r = c.post(f"/duyet/{post['id']}/approve", data={
        "_csrf": csrf,
        "caption": post["caption_final"],
        "channel_ids": [post["channel_id"], fb_id],
        "caption_facebook": "Caption Facebook riêng nhập từ /duyet",
        f"caption_override_{fb_id}": "",
    })
    check("duyệt thành công, redirect về /duyet", r.status_code == 302 and "err=" not in (r.location or ""),
          (r.status_code, r.location))

    conn = connect()
    post_after = conn.execute("SELECT caption_facebook FROM post WHERE id=?", (post["id"],)).fetchone()
    check("post.caption_facebook lưu đúng giá trị từ form",
          post_after["caption_facebook"] == "Caption Facebook riêng nhập từ /duyet", dict(post_after))
    target_fb = conn.execute("SELECT caption_override FROM publish_target WHERE post_id=? AND channel_id=?",
                             (post["id"], fb_id)).fetchone()
    check("target facebook không có override (form gửi rỗng)", target_fb["caption_override"] is None, dict(target_fb))
    conn.close()

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)
```

- [ ] **Step 3: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
acp/.venv/bin/python3 -m acp.tests.test_pilot 2>&1 | grep -B1 -A5 "lưu đúng caption theo platform"
```
Expected: FAIL — `caption_facebook` gửi lên không được route đọc/truyền
xuống `approve_post()` (route chưa sửa).

- [ ] **Step 4: Sửa `review_action()`**

Trong `web/server.py`, tìm:
```python
    def review_action(post_id, action):
        conn = connect()
        if action == "approve":
            channel_ids = request.form.getlist("channel_ids")
            if not channel_ids:
                # Checklist rỗng nghĩa là operator bỏ tích hết -- CHẶN, không
                # được âm thầm rơi về fallback 1-kênh của approve_post() (đó
                # là dành cho caller cũ gọi trực tiếp, không phải cho form này).
                res = {"ok": False, "error": "Chọn ít nhất 1 kênh trước khi duyệt"}
            else:
                res = pipeline.approve_post(conn, post_id, actor="operator",
                                            caption_override=request.form.get("caption") or None,
                                            channel_ids=channel_ids)
```
Thay bằng:
```python
    def review_action(post_id, action):
        conn = connect()
        if action == "approve":
            channel_ids = request.form.getlist("channel_ids")
            if not channel_ids:
                # Checklist rỗng nghĩa là operator bỏ tích hết -- CHẶN, không
                # được âm thầm rơi về fallback 1-kênh của approve_post() (đó
                # là dành cho caller cũ gọi trực tiếp, không phải cho form này).
                res = {"ok": False, "error": "Chọn ít nhất 1 kênh trước khi duyệt"}
            else:
                # request.form.get(...) không kèm default: None nếu field
                # không có trong form (giữ nguyên giá trị cũ), chuỗi rỗng nếu
                # có mặt nhưng để trống (xoá override) -- đúng ngữ nghĩa
                # approve_post() cần, xem D2 spec §8.
                caption_overrides = {}
                for cid in channel_ids:
                    val = request.form.get(f"caption_override_{cid}", "").strip()
                    if val:
                        caption_overrides[cid] = val
                res = pipeline.approve_post(conn, post_id, actor="operator",
                                            caption_override=request.form.get("caption") or None,
                                            channel_ids=channel_ids,
                                            caption_facebook=request.form.get("caption_facebook"),
                                            caption_instagram=request.form.get("caption_instagram"),
                                            caption_overrides=caption_overrides or None)
```

- [ ] **Step 5: Sửa `review.html`**

Tìm khối:
```html
    <form method="post" action="/duyet/{{ p.id }}/approve">
      <input type="hidden" name="_csrf" value="{{ csrf_token }}">
      <div class="field"><label for="caption-{{ p.id }}">Caption</label><textarea id="caption-{{ p.id }}" name="caption" rows="7" maxlength="500">{{ p.caption_final }}</textarea></div>
      <div class="field">
        <label>Kênh sẽ đăng (bỏ tích để không đăng lên kênh đó)</label>
        <div class="niche-grid">
        {% for sel in p.selected_channels %}
          <label class="niche-tile"><input type="checkbox" name="channel_ids" value="{{ sel.id }}" checked><span>[{{ platform_labels[sel.platform] }}] {{ sel.handle }}<small>{{ sel.code }}</small></span></label>
        {% endfor %}
        </div>
      </div>
      <div class="review-actions"><span class="dim mono">{{ p.caption_final|length }}/500 ký tự</span><div class="review-actions__buttons"><button class="btn btn--danger" type="submit" formaction="/duyet/{{ p.id }}/reject">Bỏ qua</button><button class="btn btn--primary" type="submit">Duyệt & lên lịch</button></div></div>
    </form>
```
Thay bằng:
```html
    <form method="post" action="/duyet/{{ p.id }}/approve">
      <input type="hidden" name="_csrf" value="{{ csrf_token }}">
      <div class="field"><label for="caption-{{ p.id }}">Caption (gốc)</label><textarea id="caption-{{ p.id }}" name="caption" rows="7" maxlength="500">{{ p.caption_final }}</textarea></div>
      {% set platforms = p.selected_channels | map(attribute='platform') | unique | list %}
      {% if 'facebook' in platforms %}
      <div class="field"><label for="caption-fb-{{ p.id }}">Caption riêng cho Facebook (để trống = dùng caption gốc)</label>
        <textarea id="caption-fb-{{ p.id }}" name="caption_facebook" rows="4">{{ p.caption_facebook or '' }}</textarea></div>
      {% endif %}
      {% if 'instagram' in platforms %}
      <div class="field"><label for="caption-ig-{{ p.id }}">Caption riêng cho Instagram (để trống = dùng caption gốc)</label>
        <textarea id="caption-ig-{{ p.id }}" name="caption_instagram" rows="4">{{ p.caption_instagram or '' }}</textarea></div>
      {% endif %}
      <div class="field">
        <label>Kênh sẽ đăng (bỏ tích để không đăng lên kênh đó)</label>
        <div class="niche-grid">
        {% for sel in p.selected_channels %}
          <div class="channel-caption-row">
            <label class="niche-tile"><input type="checkbox" name="channel_ids" value="{{ sel.id }}" checked><span>[{{ platform_labels[sel.platform] }}] {{ sel.handle }}<small>{{ sel.code }}</small></span></label>
            <details><summary>✎ caption riêng cho account này</summary>
              <textarea name="caption_override_{{ sel.id }}" rows="3" placeholder="Để trống = dùng caption theo platform/gốc"></textarea>
            </details>
          </div>
        {% endfor %}
        </div>
      </div>
      <div class="review-actions"><span class="dim mono">{{ p.caption_final|length }}/500 ký tự</span><div class="review-actions__buttons"><button class="btn btn--danger" type="submit" formaction="/duyet/{{ p.id }}/reject">Bỏ qua</button><button class="btn btn--primary" type="submit">Duyệt & lên lịch</button></div></div>
    </form>
```

- [ ] **Step 6: Chạy lại test end-to-end, xác nhận PASS**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
acp/.venv/bin/python3 -m acp.tests.test_pilot 2>&1 | grep -B1 -A5 "lưu đúng caption theo platform"
```

- [ ] **Step 7: Chạy toàn bộ 2 test suite, xác nhận không regression**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -5
acp/.venv/bin/python3 -m acp.tests.test_pilot 2>&1 | tail -5
```
Expected: cả 2 dòng cuối đều `0 hỏng`.

- [ ] **Step 8: Thêm lời gọi test vào `__main__` của `test_pilot.py`**

Thêm `test_duyet_approve_saves_caption_platform_and_override()` vào danh
sách trong `if __name__ == "__main__":` của `tests/test_pilot.py`.

- [ ] **Step 9: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add web/server.py web/templates/review.html tests/test_pilot.py
git commit -m "feat: /duyet nhận caption theo platform + override theo account (D2)"
```

---

## Sau khi cả 6 task hoàn tất

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -5
acp/.venv/bin/python3 -m acp.tests.test_pilot 2>&1 | tail -5
```

Baseline trước D2: `test_pipeline.py` 222/0, `test_pilot.py` 279/0. Sau D2
kỳ vọng: `test_pipeline.py` tăng thêm ~9 test mới (Task 1: 1, Task 2: 1,
Task 3: 1, Task 4: 4, Task 5: 1) = khoảng 231/0 (con số check() chính xác
tuỳ implementer đếm lại khi chạy thật); `test_pilot.py` +1 test = 280/0.
