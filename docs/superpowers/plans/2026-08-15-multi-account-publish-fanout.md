# Chọn nhiều account + sinh N publish_target (Sub-project D1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cho phép operator chọn nhiều account (đa nền tảng: Threads/Facebook/
Instagram) cho 1 bài ở `/sanpham`, điều chỉnh lại ở `/duyet`, và khi Duyệt sinh
ra N `publish_target` độc lập — mỗi kênh có lịch đăng/trạng thái/retry riêng.

**Architecture:** Bảng mới `post_channel_selection` lưu lựa chọn gốc lúc tạo
bài. `post.channel_id` giữ nguyên vai trò "kênh chính" (watermark khi 1 kênh,
tracking link, báo cáo) — không đổi schema `post`. `approve_post()` nhận danh
sách `channel_ids` (đọc từ form `/duyet`, không phải từ bảng lựa chọn gốc) và
lặp tạo N `publish_target`. Ba lỗi tiềm ẩn khi có N target/post (race huỷ
nhầm target, rate-limit tính sai kênh, idempotency insight đụng nhau) được sửa
trước, làm nền cho phần sinh N target.

**Tech Stack:** Python 3, Flask, SQLite (thư viện chuẩn `sqlite3`), Jinja2,
Pillow (`imaging.py`). Test bằng test runner tự viết (`check()` + `PASS`/
`FAIL`), không phải pytest — chạy bằng `python3 -m acp.tests.test_pipeline` /
`python3 -m acp.tests.test_pilot` từ thư mục **cha** của repo (repo tên `acp/`).

**Spec:** `docs/superpowers/specs/2026-08-15-multi-account-publish-fanout-design.md`

## Global Constraints

- Toàn bộ code mới, comment, docstring, copy UI viết bằng tiếng Việt, đúng
  giọng văn hiện có trong file đang sửa.
- Không đổi schema bảng `post`/`channel` — chỉ thêm bảng mới
  `post_channel_selection` (additive).
- `post.channel_id` sau D1 nghĩa là "kênh chính" (account đầu tiên được chọn
  lúc tạo bài) — không phải "kênh duy nhất". Mọi query cũ dựa vào
  `post.channel_id` (funnel, `epc_by`, `/kenh`, `scoring.py`) **không được
  sửa** trong D1 (xem spec §10 — giới hạn đã biết, cố ý để lại).
- Hai không gian định danh KHÔNG được lẫn lộn trong toàn bộ plan này:
  - `channel_codes: list[str]` — mã kênh người đọc được (`channel.code`),
    dùng ở các hàm **tạo bài** (`_create_post_from_raw_product` và 2 hàm
    gọi nó) và ở form `/sanpham`, khớp với tham số đơn `channel_code: str`
    đã có sẵn.
  - `channel_ids: list[str]` — ULID (`channel.id`), dùng ở `approve_post()`
    và form `/duyet`, khớp với việc `post.channel_id`/`publish_target.
    channel_id` đều là ULID.
- `post_channel_selection` chỉ ghi 1 lần lúc tạo bài, **không bao giờ bị
  UPDATE/DELETE** sau đó (kể cả khi operator bỏ tick ở `/duyet`) — nó là audit
  trail của lựa chọn gốc, không phải nguồn dữ liệu cho `approve_post()`.
- Ảnh ghép dùng chung cho N kênh thì **không đóng dấu handle** của kênh nào
  (`imaging.compose(handle=None)`) — quyết định đã chốt, không phải điểm mở.
- Mọi tham số mới đều optional với giá trị mặc định giữ nguyên hành vi cũ
  100% khi không truyền — không được phá bất kỳ test nào đang xanh
  (`test_pipeline.py` 162/0, `test_pilot.py` 265/0 tính đến khi bắt đầu D1)
  trừ 2 test đã xác định lỗi thời, sửa có chủ đích ở Task 8 (xem task đó).

---

## Task 1: Bảng `post_channel_selection`

**Files:**
- Modify: `core/db.py` (thêm `CREATE TABLE` vào `SCHEMA`, ngay sau khối
  `publish_target`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: bảng `post_channel_selection(post_id TEXT, channel_id TEXT,
  created_at TEXT, PRIMARY KEY(post_id, channel_id))`.

- [ ] **Step 1: Viết test kiểm tra bảng tồn tại và ràng buộc PK**

Thêm vào `tests/test_pipeline.py`, ngay sau `test_publish_target_schema`:

```python
def test_post_channel_selection_schema():
    print("\npost_channel_selection schema")
    conn = connect()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(post_channel_selection)").fetchall()}
    check("post_channel_selection có đủ cột", {"post_id", "channel_id", "created_at"} <= cols, cols)

    product = conn.execute("SELECT id FROM product LIMIT 1").fetchone()
    channel = conn.execute("SELECT id FROM channel LIMIT 1").fetchone()
    campaign = conn.execute("SELECT id FROM campaign LIMIT 1").fetchone()
    post_id = ulid()
    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, variant_code,
                    caption_body, disclosure_text, caption_final, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                 (post_id, product["id"], channel["id"], campaign["id"], "A",
                  "thân bài", "nhãn tiếp thị", "thân bài", now(), now()))
    conn.execute("INSERT INTO post_channel_selection (post_id, channel_id, created_at) VALUES (?,?,?)",
                 (post_id, channel["id"], now()))
    import sqlite3
    try:
        conn.execute("INSERT INTO post_channel_selection (post_id, channel_id, created_at) VALUES (?,?,?)",
                     (post_id, channel["id"], now()))
        check("PK (post_id, channel_id) chặn trùng lặp", False, "insert trùng lọt qua")
    except sqlite3.IntegrityError as e:
        check("PK (post_id, channel_id) chặn trùng lặp", "UNIQUE constraint failed" in str(e), str(e))
    conn.close()
```

- [ ] **Step 2: Chạy test, xác nhận FAIL vì bảng chưa tồn tại**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -c "
import sys; sys.path.insert(0, '.')
" 2>/dev/null
python3 -m acp.tests.test_pipeline 2>&1 | grep -A1 "post_channel_selection"
```
Expected: lỗi `no such table: post_channel_selection` hoặc test FAIL.

- [ ] **Step 3: Thêm bảng vào `SCHEMA`**

Trong `core/db.py`, tìm khối:
```sql
CREATE TABLE IF NOT EXISTS publish_target (
...
CREATE INDEX IF NOT EXISTS idx_publish_target_post   ON publish_target(post_id);
CREATE INDEX IF NOT EXISTS idx_publish_target_status ON publish_target(status, scheduled_at);
```
Thêm ngay sau, trước `CREATE TABLE IF NOT EXISTS meta_connection`:
```sql
CREATE TABLE IF NOT EXISTS post_channel_selection (
    post_id     TEXT NOT NULL REFERENCES post(id),
    channel_id  TEXT NOT NULL REFERENCES channel(id),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (post_id, channel_id)
);
```

- [ ] **Step 4: Chạy lại test, xác nhận PASS**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -20
```
Expected: `post_channel_selection` cả 2 check đều PASS, tổng FAIL không tăng.

- [ ] **Step 5: Thêm lời gọi test vào khối `__main__`**

Trong `tests/test_pipeline.py`, thêm `test_post_channel_selection_schema()` vào
danh sách trong `if __name__ == "__main__":`, ngay sau
`test_publish_target_schema()`.

- [ ] **Step 6: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add core/db.py tests/test_pipeline.py
git commit -m "feat: thêm bảng post_channel_selection (D1)"
```

---

## Task 2: `_next_slot`/`_published_today` đọc từ `publish_target` thay vì `post`

**Files:**
- Modify: `core/pipeline.py:370-385` (`_next_slot`), `core/pipeline.py:532-536`
  (`_published_today`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: bảng `publish_target` (đã có từ sub-project A).
- Produces: `_next_slot(conn, channel_id) -> str`,
  `_published_today(conn, channel_id) -> int` — chữ ký không đổi, chỉ đổi
  nguồn dữ liệu bên trong.

- [ ] **Step 1: Viết test cho rate-limit theo đúng kênh khi có nhiều kênh**

Thêm vào `tests/test_pipeline.py`, sau `test_daily_cap`:

```python
def test_next_slot_and_daily_cap_scoped_per_channel_via_publish_target():
    print("\n_next_slot/_published_today tính theo publish_target, không rò rỉ giữa các kênh")
    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (fb_id, "fb_slot_test", "facebook", "FB Slot Test", "ACTIVE", 1, 12, 90, now()))
    try:
        ch1 = conn.execute("SELECT id FROM channel WHERE code='ch1'").fetchone()

        # Kênh ch1 đã có publish_target SUCCESS gần đây (từ các test trước, hoặc
        # tự tạo một cái) -- kênh facebook mới thì chưa có gì.
        product = conn.execute("SELECT id FROM product LIMIT 1").fetchone()
        campaign = conn.execute("SELECT id FROM campaign LIMIT 1").fetchone()
        post_id = ulid()
        conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, variant_code,
                        caption_body, disclosure_text, caption_final, created_at, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (post_id, product["id"], ch1["id"], campaign["id"], "A",
                      "thân bài", "nhãn tiếp thị", "thân bài", now(), now()))
        target_id = ulid()
        conn.execute("""INSERT INTO publish_target (id, post_id, channel_id, status,
                        created_at, updated_at) VALUES (?,?,?,'SUCCESS',?,?)""",
                     (target_id, post_id, ch1["id"], now(), now()))

        slot_ch1 = pipeline._next_slot(conn, ch1["id"])
        slot_fb = pipeline._next_slot(conn, fb_id)
        check("kênh vừa có publish_target SUCCESS thì slot bị đẩy về tương lai (giãn cách)",
              slot_ch1 > now(), (slot_ch1, now()))
        check("kênh facebook mới, chưa có publish_target nào thì slot = ngay bây giờ (không bị ảnh hưởng bởi ch1)",
              slot_fb <= now(), (slot_fb, now()))

        conn.execute("UPDATE publish_target SET status='SUCCESS', updated_at=? WHERE id=?",
                     (now(), target_id))
        check("_published_today đếm đúng kênh ch1", pipeline._published_today(conn, ch1["id"]) >= 1)
        check("_published_today KHÔNG đếm nhầm sang kênh facebook", pipeline._published_today(conn, fb_id) == 0)
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id=?", (fb_id,))
        conn.close()
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | grep -B1 -A1 "publish_target, không rò rỉ"
```
Expected: check "kênh vừa có publish_target SUCCESS thì slot bị đẩy về tương
lai" FAIL (hàm hiện tại đọc `post`, không thấy target vừa insert vì `post`
của nó không có `published_at`/`scheduled_at` tương ứng khớp trạng thái).

- [ ] **Step 3: Sửa `_next_slot`**

Trong `core/pipeline.py`, thay:
```python
def _next_slot(conn, channel_id: str) -> str:
    """Giãn cách tối thiểu giữa hai bài cùng kênh. Trần mềm 8-15 bài/ngày không
    phải để né gì -- đăng dày hơn thì chất lượng feed giảm và người theo dõi bỏ đi."""
    ch = conn.execute("SELECT * FROM channel WHERE id=?", (channel_id,)).fetchone()
    gap = timedelta(minutes=ch["min_gap_minutes"])
    last = conn.execute("""SELECT MAX(COALESCE(published_at, scheduled_at)) FROM post
                           WHERE channel_id=? AND status IN ('SCHEDULED','PUBLISHED')""",
                        (channel_id,)).fetchone()[0]
    base = datetime.now(timezone.utc)
```
bằng:
```python
def _next_slot(conn, channel_id: str) -> str:
    """Giãn cách tối thiểu giữa hai bài cùng kênh. Trần mềm 8-15 bài/ngày không
    phải để né gì -- đăng dày hơn thì chất lượng feed giảm và người theo dõi bỏ đi.

    Đọc theo publish_target (không phải post) -- kể từ sub-project D một post
    có thể có nhiều target trên nhiều kênh khác nhau, post.channel_id chỉ còn
    là "kênh chính". Đọc theo post sẽ khiến các kênh phụ không bao giờ thấy
    lịch sử đăng của chính mình."""
    ch = conn.execute("SELECT * FROM channel WHERE id=?", (channel_id,)).fetchone()
    gap = timedelta(minutes=ch["min_gap_minutes"])
    last = conn.execute("""
        SELECT MAX(CASE WHEN status='SUCCESS' THEN updated_at ELSE scheduled_at END)
        FROM publish_target WHERE channel_id=? AND status IN ('SCHEDULED','SUCCESS')""",
                        (channel_id,)).fetchone()[0]
    base = datetime.now(timezone.utc)
```
(phần còn lại của hàm, từ `if last:` trở xuống, giữ nguyên không đổi).

- [ ] **Step 4: Sửa `_published_today`**

Thay:
```python
def _published_today(conn, channel_id: str) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    return conn.execute(
        "SELECT COUNT(*) FROM post WHERE channel_id=? AND status='PUBLISHED' AND substr(published_at,1,10)=?",
        (channel_id, today)).fetchone()[0]
```
bằng:
```python
def _published_today(conn, channel_id: str) -> int:
    """Đếm theo publish_target -- cùng lý do đã ghi ở _next_slot."""
    today = datetime.now(timezone.utc).date().isoformat()
    return conn.execute(
        "SELECT COUNT(*) FROM publish_target WHERE channel_id=? AND status='SUCCESS' AND substr(updated_at,1,10)=?",
        (channel_id, today)).fetchone()[0]
```

- [ ] **Step 5: Chạy toàn bộ `test_pipeline.py`, xác nhận PASS và không có
      regression**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -20
```
Expected: test mới PASS, `test_daily_cap` và mọi test cũ khác vẫn PASS
(`test_daily_cap` không gọi `_published_today` trực tiếp, chỉ dùng `post` để
tính baseline độc lập -- không phụ thuộc hàm vừa sửa).

- [ ] **Step 6: Thêm lời gọi test vào `__main__`**

Thêm `test_next_slot_and_daily_cap_scoped_per_channel_via_publish_target()`
vào danh sách trong `if __name__ == "__main__":`, sau `test_daily_cap()`.

- [ ] **Step 7: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add core/pipeline.py tests/test_pipeline.py
git commit -m "fix: _next_slot/_published_today tính theo publish_target, không theo post (D1)"
```

---

## Task 3: Sửa race huỷ nhầm target khi target khác cùng post publish trước

**Files:**
- Modify: `core/pipeline.py` (`publish_post`, quanh dòng 460)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `publish_post()` handler đã có (sub-project A).
- Produces: không đổi chữ ký, chỉ đổi điều kiện huỷ target.

**Lưu ý trước khi viết test:** Task 3 chỉ sửa `publish_post()`, KHÔNG sửa
`approve_post()` (đó là Task 7) — nên test không được gọi
`approve_post(channel_ids=...)` (tham số đó chưa tồn tại). Target B (kênh
thứ hai) phải được tạo thủ công bằng `INSERT INTO publish_target` +
`jobs.enqueue`, mô phỏng đúng trạng thái Task 7 sẽ tạo ra sau này, để cô lập
đúng phần logic đang sửa ở Task 3.

- [ ] **Step 1: Thêm import `datetime`/`timedelta` và `MockFacebookPublisher`**

Ở đầu `tests/test_pipeline.py`, cùng khối với `import tempfile`, thêm một
dòng import mới (không đụng dòng `from acp.core import attribution, content,
crypto, jobs, pipeline, scoring` — Task 5 sẽ sửa đúng dòng đó sau, phải giữ
nguyên để không xung đột):
```python
from datetime import datetime, timedelta
```
Đổi dòng:
```python
from acp.adapters.mock import MockAccessTrade, MockThreads  # noqa: E402
```
thành (dùng `MockFacebookPublisher` thật thay vì lách bằng `MockThreads` gắn
nhãn "facebook" -- test đúng bản chất publisher facebook hơn, kể cả ràng buộc
1-10 ảnh/bài của nó):
```python
from acp.adapters.mock import MockAccessTrade, MockFacebookPublisher, MockThreads  # noqa: E402
```

- [ ] **Step 2: Viết test regression cho đúng kịch bản bug**

Thêm vào `tests/test_pipeline.py`, sau
`test_publish_target_cancelled_on_stale_post_status`:

```python
def test_sibling_target_not_cancelled_after_first_target_publishes():
    print("\nTarget B (kênh khác) không bị huỷ khi target A (kênh khác) cùng post đã publish trước")
    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (fb_id, "fb_sibling_test", "facebook", "FB Sibling Test", "ACTIVE", 1, 12, 0, now()))
    try:
        ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(81))
        check("có job sinh nội dung", len(ids) > 0)
        jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=81)}})
        post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
        ch1_id = post["channel_id"]

        # approve_post() 1-kênh như hiện tại -- tạo target A trên ch1.
        res = pipeline.approve_post(conn, post["id"])
        check("duyệt thành công", res["ok"], res)
        target_a_id = res["publish_target_id"]
        conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?",
                     (now(), f"pub:{target_a_id}"))

        # Target B thủ công trên kênh facebook, cùng post -- mô phỏng đúng
        # trạng thái Task 7 sẽ tạo ra, mà không phụ thuộc approve_post đã sửa.
        # run_after cố ý đặt XA trong tương lai để job B chắc chắn KHÔNG chạy
        # ở lượt drain() đầu tiên -- tránh phụ thuộc vào thứ tự xử lý job cùng
        # run_after mà job_queue không cam kết.
        future = (datetime.fromisoformat(now()) + timedelta(hours=1)).isoformat(timespec="seconds")
        target_b_id = ulid()
        conn.execute("""INSERT INTO publish_target (id, post_id, channel_id, status,
                        scheduled_at, created_at, updated_at)
                        VALUES (?,?,?,'SCHEDULED',?,?,?)""",
                     (target_b_id, post["id"], fb_id, future, now(), now()))
        jobs.enqueue(conn, "PUBLISH_POST",
                     {"publish_target_id": target_b_id, "post_id": post["id"], "channel_id": fb_id},
                     run_after=future, idempotency_key=f"pub:{target_b_id}")

        # Lượt 1: chỉ job A sẵn sàng (job B còn ở tương lai) -- target A
        # publish thành công, post.status -> PUBLISHED.
        jobs.drain(conn, ctx={"source": MockAccessTrade(),
                              "publishers": {"threads": MockThreads(seed=82), "facebook": MockFacebookPublisher(seed=83)}})

        post_after_a = conn.execute("SELECT status FROM post WHERE id=?", (post["id"],)).fetchone()
        check("post.status = PUBLISHED sau khi target A thành công",
              post_after_a["status"] == "PUBLISHED", post_after_a["status"])
        target_a_after = conn.execute(
            "SELECT status FROM publish_target WHERE id=?", (target_a_id,)).fetchone()
        check("target A SUCCESS", target_a_after["status"] == "SUCCESS", target_a_after["status"])

        # Lượt 2: đưa job B về sẵn sàng ngay -- đây là phép thử thật của bug:
        # post.status giờ đã là PUBLISHED (không phải SCHEDULED), target B có
        # bị huỷ oan không.
        conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?",
                     (now(), f"pub:{target_b_id}"))
        jobs.drain(conn, ctx={"source": MockAccessTrade(),
                              "publishers": {"threads": MockThreads(seed=82), "facebook": MockFacebookPublisher(seed=83)}})
        target_b_after = conn.execute(
            "SELECT status, last_error FROM publish_target WHERE id=?", (target_b_id,)).fetchone()
        check("target B (kênh facebook) vẫn được publish, KHÔNG bị CANCELLED vì post đã PUBLISHED",
              target_b_after["status"] == "SUCCESS", dict(target_b_after))
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id=?", (fb_id,))
        conn.close()
```

- [ ] **Step 3: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | grep -B1 -A1 "KHÔNG bị CANCELLED"
```
Expected: FAIL, `target_b_after["status"] == 'CANCELLED'` (bug hiện tại).

- [ ] **Step 4: Sửa điều kiện trong `publish_post()`**

Trong `core/pipeline.py`, tìm:
```python
    if post["status"] not in ("SCHEDULED", "APPROVED"):
        _cancel_target_stale_post(conn, target["id"], post["status"])
        return
```
Thay bằng:
```python
    # Blocklist chứ không phải allowlist: kể từ sub-project D một post có thể
    # có N target trên N kênh khác nhau. Target đầu tiên publish thành công sẽ
    # đẩy post.status sang PUBLISHED (dưới đây) -- các target còn lại (kênh
    # khác) vẫn phải được publish bình thường, KHÔNG được coi PUBLISHED là
    # "bài không còn đăng được" như trước đây (khi 1 post luôn chỉ có 1
    # target). Chỉ thực sự huỷ khi post bị bounce khỏi trạng thái duyệt được:
    # PENDING_REVIEW (một target khác gặp ContentViolationError), REJECTED,
    # hoặc DRAFT (chưa từng qua duyệt). "APPROVED" trong điều kiện cũ chưa
    # từng được set ở bất kỳ đâu trong code -- bỏ, không mất hành vi nào.
    if post["status"] in ("PENDING_REVIEW", "REJECTED", "DRAFT"):
        _cancel_target_stale_post(conn, target["id"], post["status"])
        return
```

- [ ] **Step 5: Chạy lại test, xác nhận PASS**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -20
```
Expected: test mới PASS.
`test_publish_target_cancelled_on_stale_post_status` vẫn PASS (kịch bản của
nó là PENDING_REVIEW, vẫn nằm trong blocklist mới).

- [ ] **Step 6: Thêm lời gọi test vào `__main__`**

Thêm `test_sibling_target_not_cancelled_after_first_target_publishes()` vào
danh sách, ngay sau
`test_publish_target_cancelled_on_stale_post_status()`.

- [ ] **Step 7: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add core/pipeline.py tests/test_pipeline.py
git commit -m "fix: target kênh khác không tự huỷ khi target khác cùng post đã publish (D1)"
```

---

## Task 4: `FETCH_INSIGHTS` theo từng target, không đụng nhau giữa các kênh

**Files:**
- Modify: `core/pipeline.py` (`publish_post` dòng ~527-529, `fetch_insights`
  dòng ~539-545)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `publish_target.external_post_id` (đã có).
- Produces: payload `FETCH_INSIGHTS` thêm khoá `publish_target_id`.

- [ ] **Step 1: Đọc `fetch_insights()` hiện tại để biết đúng phần cần sửa**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
sed -n '539,560p' core/pipeline.py
```
(Chỉ để implementer xác nhận đúng vị trí trước khi sửa — không phải bước có
thể fail/pass.)

- [ ] **Step 2: Viết test cho 2 target cùng post, cùng enqueue FETCH_INSIGHTS
      không bị coi trùng**

Thêm vào `tests/test_pipeline.py`, sau
`test_sibling_target_not_cancelled_after_first_target_publishes` (Task 3):

```python
def test_fetch_insights_idempotency_key_per_target_not_per_post():
    print("\nFETCH_INSIGHTS của 2 target cùng post không bị coi trùng idempotency")
    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (fb_id, "fb_insights_test", "facebook", "FB Insights Test", "ACTIVE", 1, 12, 0, now()))
    try:
        ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(91))
        check("có job sinh nội dung", len(ids) > 0)
        jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=91)}})
        post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()

        res = pipeline.approve_post(conn, post["id"])
        target_a_id = res["publish_target_id"]
        conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?",
                     (now(), f"pub:{target_a_id}"))

        target_b_id = ulid()
        conn.execute("""INSERT INTO publish_target (id, post_id, channel_id, status,
                        scheduled_at, created_at, updated_at)
                        VALUES (?,?,?,'SCHEDULED',?,?,?)""",
                     (target_b_id, post["id"], fb_id, now(), now(), now()))
        jobs.enqueue(conn, "PUBLISH_POST",
                     {"publish_target_id": target_b_id, "post_id": post["id"], "channel_id": fb_id},
                     run_after=now(), idempotency_key=f"pub:{target_b_id}")

        jobs.drain(conn, ctx={"source": MockAccessTrade(),
                              "publishers": {"threads": MockThreads(seed=92), "facebook": MockFacebookPublisher(seed=93)}})

        insight_jobs = conn.execute(
            "SELECT idempotency_key FROM job_queue WHERE job_type='FETCH_INSIGHTS'").fetchall()
        check("có đúng 2 job FETCH_INSIGHTS (1 mỗi target), không bị dedupe nhầm",
              len(insight_jobs) == 2, [r["idempotency_key"] for r in insight_jobs])
        check("idempotency_key theo target chứ không theo post (2 key khác nhau)",
              len({r["idempotency_key"] for r in insight_jobs}) == 2)

        target_a = conn.execute("SELECT external_post_id FROM publish_target WHERE id=?", (target_a_id,)).fetchone()
        target_b = conn.execute("SELECT external_post_id FROM publish_target WHERE id=?", (target_b_id,)).fetchone()
        check("target A và B có external_post_id khác nhau (2 lần publish riêng biệt)",
              target_a["external_post_id"] != target_b["external_post_id"],
              (target_a["external_post_id"], target_b["external_post_id"]))
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id=?", (fb_id,))
        conn.close()
```

- [ ] **Step 3: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | grep -B1 -A1 "không bị dedupe nhầm"
```
Expected: FAIL, chỉ có 1 job `FETCH_INSIGHTS` (key `ins:{post_id}` trùng cho
cả 2 lần enqueue).

- [ ] **Step 4: Sửa `publish_post()` — key theo target**

Tìm:
```python
    enqueue(conn, "FETCH_INSIGHTS", {"post_id": post["id"], "channel_id": channel["id"]},
            run_after=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(timespec="seconds"),
            idempotency_key=f"ins:{post['id']}")
```
Thay bằng:
```python
    enqueue(conn, "FETCH_INSIGHTS",
            {"post_id": post["id"], "channel_id": channel["id"], "publish_target_id": target["id"]},
            run_after=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(timespec="seconds"),
            idempotency_key=f"ins:{target['id']}")
```

- [ ] **Step 5: Sửa `fetch_insights()` — đọc `external_post_id` từ đúng
      target**

Tìm:
```python
@handler("FETCH_INSIGHTS")
def fetch_insights(conn, payload, ctx):
    post = conn.execute("SELECT thread_id FROM post WHERE id=?", (payload["post_id"],)).fetchone()
    channel = conn.execute("SELECT * FROM channel WHERE id=?", (payload["channel_id"],)).fetchone()
    if not post or not post["thread_id"]:
        return
```
Thay bằng:
```python
@handler("FETCH_INSIGHTS")
def fetch_insights(conn, payload, ctx):
    # Đọc external_post_id từ ĐÚNG publish_target, không phải post.thread_id
    # (post.thread_id chỉ phản ánh target thành công đầu tiên -- sai cho các
    # kênh phụ kể từ sub-project D). payload cũ (trước D1) không có
    # publish_target_id -- fallback về post.thread_id để tương thích ngược.
    target = None
    if payload.get("publish_target_id"):
        target = conn.execute(
            "SELECT external_post_id FROM publish_target WHERE id=?", (payload["publish_target_id"],)).fetchone()
    post = conn.execute("SELECT thread_id FROM post WHERE id=?", (payload["post_id"],)).fetchone()
    external_post_id = (target["external_post_id"] if target else None) or (post["thread_id"] if post else None)
    channel = conn.execute("SELECT * FROM channel WHERE id=?", (payload["channel_id"],)).fetchone()
    if not post or not external_post_id:
        return
```

- [ ] **Step 6: Đọc phần còn lại của `fetch_insights()` để sửa biến dùng
      đúng `external_post_id`**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
sed -n '539,560p' core/pipeline.py
```
Tìm lời gọi `publisher.fetch_insights(...)` bên dưới — nếu nó đang truyền
`post["thread_id"]` làm tham số, đổi thành `external_post_id` (biến vừa tính
ở Step 5). Giữ nguyên mọi phần khác của hàm (gọi `attribution.update_insights`,
v.v.) không đổi.

- [ ] **Step 7: Chạy lại toàn bộ test, xác nhận PASS**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -20
```
Expected: test mới PASS, `test_update_insights_empty_dict_noop` và mọi test
liên quan insight khác vẫn PASS.

- [ ] **Step 8: Thêm lời gọi test vào `__main__`**

Thêm `test_fetch_insights_idempotency_key_per_target_not_per_post()` vào danh
sách, sau `test_sibling_target_not_cancelled_after_first_target_publishes()`.

- [ ] **Step 9: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add core/pipeline.py tests/test_pipeline.py
git commit -m "fix: FETCH_INSIGHTS idempotency + external_post_id theo từng publish_target (D1)"
```

---

## Task 5: `imaging.compose()` bỏ watermark handle khi `handle=None`

**Files:**
- Modify: `core/imaging.py:53-115`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `compose(product, out_dir, discount_pct=0.0, handle=None) -> str`
  — tham số `handle` đổi default từ chuỗi sang `None`; khi falsy thì không vẽ
  layer watermark chữ, đường kẻ phân cách phía dưới vẫn giữ nguyên.

- [ ] **Step 1: Viết test — có handle thì có pixel watermark, không có
      handle thì không**

Thêm vào `tests/test_pipeline.py`. Trước tiên đổi dòng import ở đầu file:
```python
from acp.core import attribution, content, crypto, jobs, pipeline, scoring
```
thành:
```python
from acp.core import attribution, content, crypto, imaging, jobs, pipeline, scoring
```

Sau đó thêm hàm test (đặt sau `test_content_guards`):

```python
def test_imaging_compose_skips_watermark_when_handle_none():
    print("\nimaging.compose bỏ watermark handle khi handle=None")
    from PIL import Image
    out_dir = tempfile.mkdtemp()
    product_with = {"id": "imgtest_with", "external_product_id": "imgtest_with",
                    "name": "Sản phẩm test watermark", "current_price": 199000,
                    "original_price": None, "image_path_local": None}
    product_without = dict(product_with, id="imgtest_without", external_product_id="imgtest_without")

    path_with = imaging.compose(product_with, out_dir, discount_pct=0.0, handle="@kenhtest")
    path_without = imaging.compose(product_without, out_dir, discount_pct=0.0, handle=None)

    img_with = Image.open(path_with).convert("RGB")
    img_without = Image.open(path_without).convert("RGB")
    region = (imaging.PAD, imaging.CANVAS[1] - imaging.PAD - 40,
              imaging.CANVAS[0] - imaging.PAD, imaging.CANVAS[1] - imaging.PAD - 10)
    pixels_with = set(img_with.crop(region).getdata())
    pixels_without = set(img_without.crop(region).getdata())
    check("có handle: vùng watermark có pixel màu MUTED (chữ được vẽ)",
          imaging.MUTED in pixels_with, len(pixels_with))
    check("handle=None: vùng watermark KHÔNG có pixel màu MUTED (không vẽ chữ)",
          imaging.MUTED not in pixels_without, len(pixels_without))
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | grep -B1 -A1 "handle=None: vùng watermark"
```
Expected: FAIL (hiện tại `handle=None` bị Pillow vẽ chuỗi `"None"` thay vì bỏ
qua — vẫn có pixel MUTED, hoặc TypeError tuỳ phiên bản Pillow xử lý
`draw.text` với text không phải str).

- [ ] **Step 3: Sửa `compose()`**

Đổi chữ ký:
```python
def compose(product, out_dir: str, discount_pct: float = 0.0, handle: str = "@kenhcuaban") -> str:
```
thành:
```python
def compose(product, out_dir: str, discount_pct: float = 0.0, handle: str = None) -> str:
```

Tìm:
```python
    # Layer 4 -- nhận diện kênh.
    hf = _font(30)
    draw.text((PAD, CANVAS[1] - PAD - 12), handle, font=hf, fill=MUTED)
    draw.line([PAD, CANVAS[1] - PAD - 34, CANVAS[0] - PAD, CANVAS[1] - PAD - 34], fill=(224, 228, 223), width=2)
```
Thay bằng:
```python
    # Layer 4 -- nhận diện kênh. handle=None khi ảnh dùng chung cho nhiều
    # kênh (sub-project D) -- không đóng dấu tên kênh nào để tránh trường hợp
    # đăng lên kênh A nhưng ảnh lại ghi tên kênh B. Đường kẻ phân cách vẫn giữ
    # để layer giá/tên sản phẩm phía trên không bị trống chân trang đột ngột.
    if handle:
        hf = _font(30)
        draw.text((PAD, CANVAS[1] - PAD - 12), handle, font=hf, fill=MUTED)
    draw.line([PAD, CANVAS[1] - PAD - 34, CANVAS[0] - PAD, CANVAS[1] - PAD - 34], fill=(224, 228, 223), width=2)
```

- [ ] **Step 4: Chạy lại test, xác nhận PASS**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -20
```

- [ ] **Step 5: Thêm lời gọi test vào `__main__`**

Thêm `test_imaging_compose_skips_watermark_when_handle_none()` vào danh sách,
sau `test_content_guards()`.

- [ ] **Step 6: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add core/imaging.py tests/test_pipeline.py
git commit -m "feat: imaging.compose bỏ watermark handle khi dùng chung cho nhiều kênh (D1)"
```

---

## Task 6: Luồng tạo post nhận `channel_codes`, ghi `post_channel_selection`

**Files:**
- Modify: `core/pipeline.py` (`_create_post_from_raw_product`,
  `create_post_for_product`, `create_post_from_manual_affiliate_product`,
  khoảng dòng 169-270)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `channel_niches(conn, channel_id)` (đã có),
  `imaging.compose(handle=...)` (Task 5), bảng `post_channel_selection`
  (Task 1).
- Produces:
  - `_resolve_channels_by_code(conn, codes: list) -> (rows_or_None, err_or_None)`
  - `_union_niches(conn, channel_ids: list) -> list`
  - `_save_channel_selection(conn, post_id: str, channel_ids: list) -> None`
  - `post_channel_selections(conn, post_ids: list) -> dict[str, list[dict]]`
  - `_create_post_from_raw_product(..., channel_codes: list = None, ...)`
  - `create_post_for_product(..., channel_codes: list = None, ...)`
  - `create_post_from_manual_affiliate_product(..., channel_codes: list = None, ...)`

- [ ] **Step 1: Viết test tạo post với nhiều kênh**

Thêm vào `tests/test_pipeline.py`, sau
`test_default_channel_fallback_skips_facebook`:

```python
def test_create_post_with_multiple_channel_codes():
    print("\nTạo post với nhiều channel_codes -> post_channel_selection đủ N dòng, kênh đầu là kênh chính")
    conn = connect()
    fb_id, ig_id = ulid(), ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                 (fb_id, "fb_multi_test", "facebook", "FB Multi Test", "ACTIVE", 1, now()))
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                 (ig_id, "ig_multi_test", "instagram", "IG Multi Test", "ACTIVE", 1, now()))
    try:
        src = MockAccessTrade()
        target = next(p for p in src.fetch_products(limit=50) if p.product_url)
        ctx = {"source": src, "publishers": {}}
        res = pipeline.create_post_for_product(
            conn, ctx, target.external_product_id, "test",
            channel_codes=["ch1", "fb_multi_test", "ig_multi_test"])
        check("tạo bài thành công", res.get("ok"), res.get("error"))

        post = conn.execute("SELECT * FROM post WHERE id=?", (res["post_id"],)).fetchone()
        ch1 = conn.execute("SELECT id FROM channel WHERE code='ch1'").fetchone()
        check("post.channel_id = kênh đầu tiên trong danh sách (ch1)",
              post["channel_id"] == ch1["id"], post["channel_id"])

        selections = conn.execute(
            "SELECT channel_id FROM post_channel_selection WHERE post_id=?", (post["id"],)).fetchall()
        check("đủ 3 dòng post_channel_selection", len(selections) == 3, len(selections))
        selected_ids = {r["channel_id"] for r in selections}
        check("đúng bộ 3 kênh được chọn", selected_ids == {ch1["id"], fb_id, ig_id}, selected_ids)
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id IN (?,?)", (fb_id, ig_id))
        conn.close()


def test_create_post_multiple_channel_codes_rejects_disabled_channel():
    print("\nTạo post với 1 kênh bị disabled trong danh sách -> lỗi rõ ràng, không tạo post")
    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                 (fb_id, "fb_disabled_test", "facebook", "FB Disabled Test", "ACTIVE", 0, now()))
    try:
        src = MockAccessTrade()
        target = next(p for p in src.fetch_products(limit=50) if p.product_url)
        ctx = {"source": src, "publishers": {}}
        before = conn.execute("SELECT COUNT(*) FROM post").fetchone()[0]
        res = pipeline.create_post_for_product(
            conn, ctx, target.external_product_id, "test",
            channel_codes=["ch1", "fb_disabled_test"])
        check("tạo bài thất bại vì có kênh disabled", res.get("ok") is False, res)
        check("thông báo lỗi nêu rõ tên kênh", "fb_disabled_test" in (res.get("error") or ""), res.get("error"))
        after = conn.execute("SELECT COUNT(*) FROM post").fetchone()[0]
        check("không tạo post nào (tất-cả-hoặc-không-gì)", before == after, (before, after))
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id=?", (fb_id,))
        conn.close()
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | grep -B1 -A1 "channel_codes"
```
Expected: FAIL — `create_post_for_product()` chưa nhận `channel_codes`
(TypeError hoặc bị bỏ qua tuỳ cách Python xử lý kwarg lạ — thực tế sẽ là
`TypeError: create_post_for_product() got an unexpected keyword argument`).

- [ ] **Step 3: Thêm 4 hàm helper mới, đặt ngay trước
      `_create_post_from_raw_product`**

Trong `core/pipeline.py`, chèn trước dòng
`def _create_post_from_raw_product(conn, ctx, source, raw, campaign_code: str,`:

```python
def _resolve_channels_by_code(conn, codes: list):
    """Trả (list channel row theo đúng thứ tự codes, None) hoặc (None, lỗi).
    Dùng ở luồng TẠO bài -- codes là channel.code (mã người đọc được), khớp
    với tham số channel_code đơn đã có sẵn."""
    rows = []
    for code in codes:
        row = conn.execute("SELECT * FROM channel WHERE code=? AND status='ACTIVE'", (code,)).fetchone()
        if not row:
            return None, f"Kênh {code} không tồn tại hoặc không hoạt động"
        if not row["enabled"]:
            return None, f"Kênh {code} đã bị tắt (disabled), không thể tạo bài"
        rows.append(row)
    return rows, None


def _union_niches(conn, channel_ids: list) -> list:
    """Hợp (union) niches của nhiều kênh, giữ thứ tự xuất hiện đầu tiên,
    không trùng lặp. Dùng để validate caption chặt hơn khi có nhiều kênh."""
    seen = []
    for cid in channel_ids:
        for n in channel_niches(conn, cid):
            if n not in seen:
                seen.append(n)
    return seen


def _save_channel_selection(conn, post_id: str, channel_ids: list) -> None:
    """Ghi lựa chọn kênh GỐC lúc tạo bài. KHÔNG bao giờ được UPDATE/DELETE lại
    sau đó -- đây là audit trail, không phải nguồn dữ liệu cho approve_post()."""
    for cid in channel_ids:
        conn.execute("INSERT INTO post_channel_selection (post_id, channel_id, created_at) VALUES (?,?,?)",
                     (post_id, cid, now()))


def post_channel_selections(conn, post_ids: list) -> dict:
    """post_id -> list[channel row dict] đã chọn lúc tạo bài (sắp theo
    platform, code). Dùng để tick sẵn checklist ở /duyet."""
    if not post_ids:
        return {}
    placeholders = ",".join("?" * len(post_ids))
    rows = conn.execute(f"""
        SELECT pcs.post_id AS post_id, ch.id AS id, ch.code AS code,
               ch.platform AS platform, ch.handle AS handle
        FROM post_channel_selection pcs JOIN channel ch ON ch.id = pcs.channel_id
        WHERE pcs.post_id IN ({placeholders})
        ORDER BY ch.platform, ch.code
    """, post_ids).fetchall()
    result = {}
    for r in rows:
        result.setdefault(r["post_id"], []).append(dict(r))
    return result
```

- [ ] **Step 4: Sửa `_create_post_from_raw_product`**

Thay chữ ký hàm:
```python
def _create_post_from_raw_product(conn, ctx, source, raw, campaign_code: str,
                                  channel_code: str = None, template_code: str = None,
                                  variant_code: str = "A", prebuilt_affiliate_link: str = None,
                                  attribution_payload: dict = None,
                                  audit_action: str = "created_single") -> dict:
```
thành:
```python
def _create_post_from_raw_product(conn, ctx, source, raw, campaign_code: str,
                                  channel_code: str = None, channel_codes: list = None,
                                  template_code: str = None,
                                  variant_code: str = "A", prebuilt_affiliate_link: str = None,
                                  attribution_payload: dict = None,
                                  audit_action: str = "created_single") -> dict:
```

Thay khối lookup kênh:
```python
    channel = conn.execute(
        # Nhánh có channel_code giữ nguyên, không lọc platform -- gọi rõ tên kênh
        # thì tôn trọng lựa chọn đó (sau này facebook/instagram có publisher thật
        # vẫn dùng lại được hàm này). Nhánh mặc định (không truyền channel_code)
        # chỉ được rơi vào Threads -- kênh facebook/instagram import về chưa có
        # publisher đăng ký, để lọt vào đây thì bài kẹt vĩnh viễn ở SCHEDULED.
        "SELECT * FROM channel WHERE code=? AND status='ACTIVE'" if channel_code
        else "SELECT * FROM channel WHERE status='ACTIVE' AND platform='threads' ORDER BY code LIMIT 1",
        (channel_code,) if channel_code else ()).fetchone()
    if not channel:
        return {"ok": False, "error": "Không có kênh nào đang hoạt động"}
    if not channel["enabled"]:
        return {"ok": False, "error": f"Kênh {channel['code']} đã bị tắt (disabled), không thể tạo bài"}
```
bằng:
```python
    if channel_codes:
        # Đa kênh (sub-project D) -- kênh ĐẦU TIÊN trong danh sách là "kênh
        # chính" (post.channel_id, watermark khi chỉ 1 kênh, tracking link).
        channels, err = _resolve_channels_by_code(conn, channel_codes)
        if err:
            return {"ok": False, "error": err}
    else:
        channel = conn.execute(
            # Nhánh có channel_code giữ nguyên, không lọc platform -- gọi rõ tên kênh
            # thì tôn trọng lựa chọn đó (sau này facebook/instagram có publisher thật
            # vẫn dùng lại được hàm này). Nhánh mặc định (không truyền channel_code)
            # chỉ được rơi vào Threads -- kênh facebook/instagram import về chưa có
            # publisher đăng ký, để lọt vào đây thì bài kẹt vĩnh viễn ở SCHEDULED.
            "SELECT * FROM channel WHERE code=? AND status='ACTIVE'" if channel_code
            else "SELECT * FROM channel WHERE status='ACTIVE' AND platform='threads' ORDER BY code LIMIT 1",
            (channel_code,) if channel_code else ()).fetchone()
        if not channel:
            return {"ok": False, "error": "Không có kênh nào đang hoạt động"}
        if not channel["enabled"]:
            return {"ok": False, "error": f"Kênh {channel['code']} đã bị tắt (disabled), không thể tạo bài"}
        channels = [channel]

    channel = channels[0]  # kênh chính
    channel_ids = [ch["id"] for ch in channels]
```

Thay dòng ghép ảnh:
```python
    discount = scoring.real_discount_depth(conn, product_id, product["current_price"])
    image_path = imaging.compose(product, MEDIA_DIR, discount_pct=discount, handle=channel["handle"])
```
bằng:
```python
    discount = scoring.real_discount_depth(conn, product_id, product["current_price"])
    image_path = imaging.compose(product, MEDIA_DIR, discount_pct=discount,
                                 handle=channel["handle"] if len(channels) == 1 else None)
```

Thay dòng validate caption:
```python
    caption = content.generate(product, template["code"], link, discount_pct=discount)
    problems = content.validate(caption, niches=channel_niches(conn, channel["id"]))
```
bằng:
```python
    caption = content.generate(product, template["code"], link, discount_pct=discount)
    problems = content.validate(caption, niches=_union_niches(conn, channel_ids))
```

Thay khối cuối (sau `INSERT INTO post ...` và `audit(...)`, trước `return`):
```python
    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, caption_template_id,
                    variant_code, caption_body, disclosure_text, caption_final, image_url_composited,
                    affiliate_link, sub_id_payload, score, status, reject_reason, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (post_id, product_id, channel["id"], campaign["id"], template["id"],
                  variant_code, caption, content.DISCLOSURE_DEFAULT, caption,
                  image_url, link, json.dumps(stored_attribution, ensure_ascii=False, sort_keys=True), None,
                  status, "; ".join(problems) if problems else None, now(), now()))
    audit(conn, "post", post_id, audit_action, actor="operator",
          detail={"source": source.name, "external_product_id": raw.external_product_id,
                  "template": template["code"], "problems": problems})

    return {"ok": True, "post_id": post_id, "product_id": product_id,
            "product_name": product["name"], "affiliate_link": link,
            "image_url": image_url, "caption": caption, "problems": problems,
            "status": status}
```
bằng:
```python
    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, caption_template_id,
                    variant_code, caption_body, disclosure_text, caption_final, image_url_composited,
                    affiliate_link, sub_id_payload, score, status, reject_reason, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (post_id, product_id, channel["id"], campaign["id"], template["id"],
                  variant_code, caption, content.DISCLOSURE_DEFAULT, caption,
                  image_url, link, json.dumps(stored_attribution, ensure_ascii=False, sort_keys=True), None,
                  status, "; ".join(problems) if problems else None, now(), now()))
    _save_channel_selection(conn, post_id, channel_ids)
    audit(conn, "post", post_id, audit_action, actor="operator",
          detail={"source": source.name, "external_product_id": raw.external_product_id,
                  "template": template["code"], "problems": problems, "channel_ids": channel_ids})

    return {"ok": True, "post_id": post_id, "product_id": product_id,
            "product_name": product["name"], "affiliate_link": link,
            "image_url": image_url, "caption": caption, "problems": problems,
            "status": status}
```

- [ ] **Step 5: Sửa `create_post_for_product` và
      `create_post_from_manual_affiliate_product`**

Thay:
```python
def create_post_for_product(conn, ctx, external_product_id: str, campaign_code: str,
                            channel_code: str = None, template_code: str = None,
                            variant_code: str = "A") -> dict:
    """Một sản phẩm cụ thể -> một bài PENDING_REVIEW. Không đăng."""
    source = ctx["source"]
    raw = source.get_product(external_product_id) if hasattr(source, "get_product") else None
    if raw is None:
        return {"ok": False, "error": f"Không tìm thấy sản phẩm {external_product_id} trong nguồn {source.name}"}
    if not raw.product_url:
        return {"ok": False, "error": "Sản phẩm không có product_url, không tạo được tracking link"}
    return _create_post_from_raw_product(
        conn, ctx, source, raw, campaign_code,
        channel_code=channel_code, template_code=template_code,
        variant_code=variant_code)
```
bằng:
```python
def create_post_for_product(conn, ctx, external_product_id: str, campaign_code: str,
                            channel_code: str = None, channel_codes: list = None,
                            template_code: str = None, variant_code: str = "A") -> dict:
    """Một sản phẩm cụ thể -> một bài PENDING_REVIEW. Không đăng."""
    source = ctx["source"]
    raw = source.get_product(external_product_id) if hasattr(source, "get_product") else None
    if raw is None:
        return {"ok": False, "error": f"Không tìm thấy sản phẩm {external_product_id} trong nguồn {source.name}"}
    if not raw.product_url:
        return {"ok": False, "error": "Sản phẩm không có product_url, không tạo được tracking link"}
    return _create_post_from_raw_product(
        conn, ctx, source, raw, campaign_code,
        channel_code=channel_code, channel_codes=channel_codes,
        template_code=template_code, variant_code=variant_code)
```

Thay:
```python
def create_post_from_manual_affiliate_product(conn, ctx, source, raw, affiliate_url: str,
                                               campaign_code: str, channel_code: str = None,
                                               template_code: str = None,
                                               variant_code: str = "A") -> dict:
    """Tạo bài review từ sản phẩm Shopee + affiliate URL có sẵn; không publish."""
    if not affiliate_url or not affiliate_url.startswith(("http://", "https://")):
        return {"ok": False, "error": "Thiếu link affiliate hợp lệ"}
    if not raw.name or raw.current_price <= 0 or not raw.image_url_original:
        return {"ok": False, "error": "Thiếu tên, giá hoặc ảnh sản phẩm"}
    return _create_post_from_raw_product(
        conn, ctx, source, raw, campaign_code,
        channel_code=channel_code, template_code=template_code,
        variant_code=variant_code,
        prebuilt_affiliate_link=affiliate_url,
        attribution_payload={"provider": "shopee_direct", "link_mode": "prebuilt"},
        audit_action="created_manual_shopee")
```
bằng:
```python
def create_post_from_manual_affiliate_product(conn, ctx, source, raw, affiliate_url: str,
                                               campaign_code: str, channel_code: str = None,
                                               channel_codes: list = None,
                                               template_code: str = None,
                                               variant_code: str = "A") -> dict:
    """Tạo bài review từ sản phẩm Shopee + affiliate URL có sẵn; không publish."""
    if not affiliate_url or not affiliate_url.startswith(("http://", "https://")):
        return {"ok": False, "error": "Thiếu link affiliate hợp lệ"}
    if not raw.name or raw.current_price <= 0 or not raw.image_url_original:
        return {"ok": False, "error": "Thiếu tên, giá hoặc ảnh sản phẩm"}
    return _create_post_from_raw_product(
        conn, ctx, source, raw, campaign_code,
        channel_code=channel_code, channel_codes=channel_codes,
        template_code=template_code, variant_code=variant_code,
        prebuilt_affiliate_link=affiliate_url,
        attribution_payload={"provider": "shopee_direct", "link_mode": "prebuilt"},
        audit_action="created_manual_shopee")
```

- [ ] **Step 6: Chạy toàn bộ `test_pipeline.py`, xác nhận PASS, không
      regression**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -30
```
Expected: 2 test mới PASS; `test_default_channel_fallback_skips_facebook`,
`test_create_post_blocked_for_disabled_channel` và mọi test cũ dùng
`channel_code` đơn vẫn PASS nguyên vẹn (nhánh `channel_codes` falsy giữ y hệt
code cũ).

- [ ] **Step 7: Thêm lời gọi test vào `__main__`**

Thêm `test_create_post_with_multiple_channel_codes()` và
`test_create_post_multiple_channel_codes_rejects_disabled_channel()` vào danh
sách, sau `test_default_channel_fallback_skips_facebook()`.

- [ ] **Step 8: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add core/pipeline.py tests/test_pipeline.py
git commit -m "feat: tạo post nhận channel_codes, ghi post_channel_selection (D1)"
```

---

## Task 7: `approve_post()` sinh N `publish_target` từ `channel_ids`

**Files:**
- Modify: `core/pipeline.py` (`approve_post`, dòng ~309-337)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `_next_slot(conn, channel_id)` (Task 2), `_union_niches` (Task 6).
- Produces:
  - `_resolve_channels_by_id(conn, channel_ids: list) -> (rows_or_None, err_or_None)`
  - `approve_post(conn, post_id, actor="operator", caption_override=None, channel_ids: list = None) -> dict`
    trả `{"ok": True, "scheduled_at": <sớm nhất>, "publish_target_id": <target
    đầu tiên>, "targets": [{"channel_id","publish_target_id","scheduled_at"}, ...]}`

- [ ] **Step 1: Viết test cho `approve_post` đa kênh**

Thêm vào `tests/test_pipeline.py`, sau
`test_fetch_insights_idempotency_key_per_target_not_per_post` (Task 4):

```python
def test_approve_post_multi_channel_creates_n_targets():
    print("\napprove_post(channel_ids=[...]) sinh đúng N publish_target, mỗi kênh 1 slot riêng")
    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (fb_id, "fb_approve_test", "facebook", "FB Approve Test", "ACTIVE", 1, 12, 90, now()))
    try:
        ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(101))
        check("có job sinh nội dung", len(ids) > 0)
        jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=101)}})
        post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
        ch1_id = post["channel_id"]

        res = pipeline.approve_post(conn, post["id"], channel_ids=[ch1_id, fb_id])
        check("duyệt đa kênh thành công", res["ok"], res)
        check("trả về đúng 2 target trong 'targets'", len(res["targets"]) == 2, res["targets"])
        check("giữ tương thích ngược: publish_target_id trỏ target đầu tiên",
              res["publish_target_id"] == res["targets"][0]["publish_target_id"])

        rows = conn.execute("SELECT channel_id, status FROM publish_target WHERE post_id=?",
                            (post["id"],)).fetchall()
        check("có đúng 2 dòng publish_target trong DB", len(rows) == 2, len(rows))
        check("cả 2 đều SCHEDULED", all(r["status"] == "SCHEDULED" for r in rows), [dict(r) for r in rows])

        jobs_count = conn.execute(
            "SELECT COUNT(*) FROM job_queue WHERE job_type='PUBLISH_POST' AND idempotency_key LIKE ?",
            (f"pub:%",)).fetchone()[0]
        check("có ít nhất 2 job PUBLISH_POST đang chờ (1 mỗi target)", jobs_count >= 2, jobs_count)

        post_after = conn.execute("SELECT status FROM post WHERE id=?", (post["id"],)).fetchone()
        check("post.status = SCHEDULED (1 lần, dùng chung)", post_after["status"] == "SCHEDULED")
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id=?", (fb_id,))
        conn.close()


def test_approve_post_channel_ids_none_falls_back_to_post_channel_id():
    print("\napprove_post(channel_ids=None) tương thích ngược -- 1 target trên post.channel_id")
    conn = connect()
    ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(102))
    check("có job sinh nội dung", len(ids) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=102)}})
    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()

    res = pipeline.approve_post(conn, post["id"])
    check("duyệt không truyền channel_ids vẫn thành công", res["ok"], res)
    check("chỉ tạo đúng 1 target trên kênh của post", len(res["targets"]) == 1, res["targets"])
    check("target đó đúng post.channel_id", res["targets"][0]["channel_id"] == post["channel_id"])
    conn.close()


def test_approve_post_rejects_disabled_channel_in_list_creates_no_target():
    print("\napprove_post với 1 kênh bị disabled trong channel_ids -> lỗi, không tạo target nào")
    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                 (fb_id, "fb_approve_disabled_test", "facebook", "FB Approve Disabled", "ACTIVE", 0, now()))
    try:
        ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(103))
        check("có job sinh nội dung", len(ids) > 0)
        jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=103)}})
        post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
        ch1_id = post["channel_id"]

        before = conn.execute("SELECT COUNT(*) FROM publish_target").fetchone()[0]
        res = pipeline.approve_post(conn, post["id"], channel_ids=[ch1_id, fb_id])
        check("duyệt thất bại vì có kênh disabled", res["ok"] is False, res)
        check("lỗi nêu rõ tên kênh", "fb_approve_disabled_test" in (res.get("error") or ""), res.get("error"))
        after = conn.execute("SELECT COUNT(*) FROM publish_target").fetchone()[0]
        check("không tạo publish_target nào (tất-cả-hoặc-không-gì)", before == after, (before, after))
        post_after = conn.execute("SELECT status FROM post WHERE id=?", (post["id"],)).fetchone()
        check("post vẫn PENDING_REVIEW, không bị đổi trạng thái",
              post_after["status"] == "PENDING_REVIEW", post_after["status"])
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id=?", (fb_id,))
        conn.close()
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | grep -B1 -A1 "approve_post"
```
Expected: FAIL, `TypeError: approve_post() got an unexpected keyword argument
'channel_ids'`.

- [ ] **Step 3: Thêm `_resolve_channels_by_id`, đặt ngay trước
      `def approve_post`**

```python
def _resolve_channels_by_id(conn, channel_ids: list):
    """Trả (list channel row theo đúng thứ tự channel_ids, None) hoặc
    (None, lỗi). Dùng ở luồng DUYỆT bài -- channel_ids là channel.id (ULID),
    khớp với post.channel_id/publish_target.channel_id."""
    rows = []
    for cid in channel_ids:
        row = conn.execute("SELECT * FROM channel WHERE id=?", (cid,)).fetchone()
        if not row:
            return None, f"Không tìm thấy kênh {cid}"
        if not row["enabled"]:
            return None, f"Kênh {row['code']} đang bị tắt (disabled), không thể duyệt"
        rows.append(row)
    return rows, None
```

- [ ] **Step 4: Sửa `approve_post()`**

Thay toàn bộ hàm:
```python
def approve_post(conn, post_id: str, actor: str = "operator", caption_override: str = None) -> dict:
    post = conn.execute("SELECT * FROM post WHERE id=?", (post_id,)).fetchone()
    if not post:
        return {"ok": False, "error": "Không tìm thấy bài đăng"}
    channel = conn.execute("SELECT enabled FROM channel WHERE id=?", (post["channel_id"],)).fetchone()
    if channel and not channel["enabled"]:
        return {"ok": False, "error": "Kênh của bài này đang bị tắt (disabled), không thể duyệt"}
    caption = caption_override or post["caption_final"]
    problems = content.validate(caption, niches=channel_niches(conn, post["channel_id"]))
    if problems:
        return {"ok": False, "error": "; ".join(problems)}

    scheduled = _next_slot(conn, post["channel_id"])
    conn.execute("""UPDATE post SET caption_final=?, status='SCHEDULED', scheduled_at=?,
                    reviewed_by=?, reviewed_at=?, reject_reason=NULL, updated_at=? WHERE id=?""",
                 (caption, scheduled, actor, now(), now(), post_id))

    target_id = ulid()
    conn.execute("""INSERT INTO publish_target (id, post_id, channel_id, status, scheduled_at,
                    created_at, updated_at) VALUES (?,?,?,'SCHEDULED',?,?,?)""",
                 (target_id, post_id, post["channel_id"], scheduled, now(), now()))
    # post_id/channel_id ở lại payload để jobs.py xử lý AuthError/ContentViolationError
    # (đánh dấu kênh NEEDS_REAUTH, đẩy bài về PENDING_REVIEW) không phải sửa.
    enqueue(conn, "PUBLISH_POST",
            {"publish_target_id": target_id, "post_id": post_id, "channel_id": post["channel_id"]},
            priority=50, run_after=scheduled, idempotency_key=f"pub:{target_id}")
    audit(conn, "post", post_id, "approved", actor=actor,
          detail={"scheduled_at": scheduled, "publish_target_id": target_id})
    return {"ok": True, "scheduled_at": scheduled, "publish_target_id": target_id}
```
bằng:
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

- [ ] **Step 5: Chạy toàn bộ `test_pipeline.py`, xác nhận PASS, không
      regression**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -30
```
Expected: 3 test mới PASS. Mọi test cũ gọi `pipeline.approve_post(conn,
post["id"])` (không truyền `channel_ids`) vẫn PASS y hệt — kể cả
`res["publish_target_id"]`/`res["scheduled_at"]` mà nhiều test cũ đọc trực
tiếp (xem Task 3, Task 4 vừa thêm cũng dựa vào 2 khoá này).

- [ ] **Step 6: Thêm lời gọi test vào `__main__`**

Thêm 3 hàm test mới vào danh sách, sau
`test_fetch_insights_idempotency_key_per_target_not_per_post()`.

- [ ] **Step 7: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add core/pipeline.py tests/test_pipeline.py
git commit -m "feat: approve_post nhận channel_ids, sinh N publish_target độc lập (D1)"
```

---

## Task 8: Web routes `/sanpham` — checklist đa nền tảng, thay 2 test lỗi thời

**Files:**
- Modify: `web/server.py` (`_product_common_context`, `_render_affiliate`,
  `products()`, `create_from_product()`, `create_affiliate_product()`)
- Modify: `tests/test_pilot.py` (thay
  `test_product_dropdown_only_shows_threads`,
  `test_create_affiliate_product_rejects_non_threads_channel`)

**Interfaces:**
- Consumes: `pipeline.create_post_for_product(channel_codes=...)`,
  `pipeline.create_post_from_manual_affiliate_product(channel_codes=...)`
  (Task 6).
- Produces: `PLATFORM_LABELS` dict module-level trong `web/server.py`;
  `channels` context giờ có cột `platform`; template nhận thêm
  `platform_labels`.

**Lưu ý quan trọng trước khi bắt đầu:** 2 test hiện có
`test_product_dropdown_only_shows_threads` và
`test_create_affiliate_product_rejects_non_threads_channel` (trong
`tests/test_pilot.py`) khẳng định hành vi **cũ có chủ đích** (chỉ Threads
được chọn ở `/sanpham`) — D1 đảo ngược đúng hành vi đó (đa nền tảng, có chủ
đích, theo spec §4). Đây không phải xoá test tuỳ tiện — thay bằng phiên bản
đối lập, xác nhận rõ hành vi mới.

- [ ] **Step 1: Đọc 2 test sẽ thay, xác nhận đúng vị trí**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
grep -n "def test_product_dropdown_only_shows_threads\|def test_create_affiliate_product_rejects_non_threads_channel" tests/test_pilot.py
```

- [ ] **Step 2: Thay `test_product_dropdown_only_shows_threads` bằng phiên
      bản đối lập**

Xoá toàn bộ thân hàm `test_product_dropdown_only_shows_threads` (từ `def
test_product_dropdown_only_shows_threads():` tới dòng trống trước hàm kế
tiếp), thay bằng:

```python
def test_product_checklist_shows_all_platforms():
    print("\nChecklist /sanpham hiện đủ các nền tảng (threads/facebook/instagram), không chỉ Threads")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                 (ulid(), "fb_test_checklist", "facebook", "Fake Page", "ACTIVE", 1, now()))
    conn.close()

    page = c.get("/sanpham")
    body = page.get_data(as_text=True)
    check("checklist CÓ chứa kênh facebook (D1: đa nền tảng, không chỉ Threads)",
          "Fake Page" in body, "không thấy trong checklist")
    check("checklist dùng tên trường channel_codes (checkbox, không phải select đơn)",
          'name="channel_codes"' in body, body[:300])

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)
```

- [ ] **Step 3: Thay `test_create_affiliate_product_rejects_non_threads_channel`
      bằng phiên bản đối lập**

Xoá toàn bộ thân hàm, thay bằng:

```python
def test_create_affiliate_product_accepts_facebook_channel():
    print("\ncreate_affiliate_product CHẤP NHẬN kênh Facebook qua checklist channel_codes (D1)")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                 (ulid(), "fb_accept_test", "facebook", "FB Accept", "ACTIVE", 1, now()))
    conn.close()

    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    r = c.post("/sanpham/affiliate/create", data={
        "_csrf": csrf,
        "affiliate_url": "https://s.shopee.vn/abc",
        "product_url": "https://shopee.vn/vay-i.123.456",
        "name": "Váy hoa nữ test",
        "current_price": "289000",
        "image_url": "https://img.example/product.jpg",
        "channel_codes": ["fb_accept_test"],
    })
    check("gửi mã kênh Facebook được chấp nhận, redirect sang /duyet",
          r.status_code == 302 and "/duyet" in r.location, (r.status_code, getattr(r, "location", "")))

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)
```

- [ ] **Step 4: Chạy `test_pilot.py`, xác nhận 2 test mới FAIL (chưa sửa
      web/server.py)**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pilot 2>&1 | grep -B1 -A1 "checklist CÓ chứa\|được chấp nhận"
```
Expected: FAIL — `/sanpham` vẫn render `<select name="channel_code">`
Threads-only; `channel_codes` chưa được route đọc.

- [ ] **Step 5: Sửa `_product_common_context()` và thêm `PLATFORM_LABELS`**

Trong `web/server.py`, thêm ở đầu file (gần các import/hằng số module-level
khác, ví dụ ngay dưới các import):
```python
PLATFORM_LABELS = {"threads": "Threads", "facebook": "Facebook", "instagram": "Instagram"}
```

Thay:
```python
    def _product_common_context():
        conn = connect()
        pending = conn.execute(
            "SELECT COUNT(*) FROM post WHERE status IN ('PENDING_REVIEW','DRAFT')").fetchone()[0]
        channels = [dict(r) for r in conn.execute(
            "SELECT code, handle FROM channel WHERE status='ACTIVE' AND platform='threads' "
            "ORDER BY code").fetchall()]
        conn.close()
        return pending, channels
```
bằng:
```python
    def _product_common_context():
        conn = connect()
        pending = conn.execute(
            "SELECT COUNT(*) FROM post WHERE status IN ('PENDING_REVIEW','DRAFT')").fetchone()[0]
        # D1: đa nền tảng -- bỏ lọc platform='threads', chỉ còn lọc kênh đang
        # dùng được (ACTIVE + enabled). Thêm enabled=1 (thiếu ở bản cũ) vì kênh
        # bị tắt ở /kenh thì không nên chọn được để tạo bài mới.
        channels = [dict(r) for r in conn.execute(
            "SELECT code, platform, handle FROM channel WHERE status='ACTIVE' AND enabled=1 "
            "ORDER BY platform, code").fetchall()]
        conn.close()
        return pending, channels
```

- [ ] **Step 6: Sửa `_render_affiliate()` — `selected_channel` ->
      `selected_channels`**

Thay:
```python
    def _render_affiliate(*, affiliate_url="", resolved=None, metadata=None,
                          err=None, warning=None, selected_channel=None, status=200):
        pending, channels = _product_common_context()
        return render_template(
            "products.html", page="san-pham", mode="affiliate", items=[], q="", err=err,
            source_name="manual_shopee", pending_review=pending, channels=channels,
            affiliate_url=affiliate_url, resolved=resolved,
            metadata=metadata or ProductMetadata(), metadata_warning=warning,
            selected_channel=selected_channel,
        ), status
```
bằng:
```python
    def _render_affiliate(*, affiliate_url="", resolved=None, metadata=None,
                          err=None, warning=None, selected_channels=None, status=200):
        pending, channels = _product_common_context()
        return render_template(
            "products.html", page="san-pham", mode="affiliate", items=[], q="", err=err,
            source_name="manual_shopee", pending_review=pending, channels=channels,
            affiliate_url=affiliate_url, resolved=resolved,
            metadata=metadata or ProductMetadata(), metadata_warning=warning,
            selected_channels=selected_channels or [], platform_labels=PLATFORM_LABELS,
        ), status
```

- [ ] **Step 7: Thêm `platform_labels` vào `products()` (chế độ search)**

Thay:
```python
        pending, channels = _product_common_context()
        return render_template(
            "products.html", page="san-pham", mode="search", items=items, q=q, err=err,
            source_name=source_name or os.environ.get("ACP_SOURCE", "mock"),
            pending_review=pending, channels=channels, resolved=None,
            metadata=ProductMetadata(), affiliate_url="")
```
bằng:
```python
        pending, channels = _product_common_context()
        return render_template(
            "products.html", page="san-pham", mode="search", items=items, q=q, err=err,
            source_name=source_name or os.environ.get("ACP_SOURCE", "mock"),
            pending_review=pending, channels=channels, resolved=None,
            metadata=ProductMetadata(), affiliate_url="", platform_labels=PLATFORM_LABELS)
```

- [ ] **Step 8: Sửa `create_from_product()` (chế độ Tìm kiếm)**

Thay:
```python
    @app.route("/sanpham/tao-bai", methods=["POST"])
    def create_from_product():
        external_id = request.form.get("external_product_id", "").strip()
        source_name = request.form.get("nguon") or None
        q = request.form.get("q", "")
        if not external_id:
            return redirect(url_for("products", q=q, err="Thiếu mã sản phẩm"))
        conn = connect()
        try:
            res = pipeline.create_post_for_product(
                conn, factory.build_context(source_name), external_id,
                campaign_code=os.environ.get("ACP_CAMPAIGN_CODE", "gd2026"))
        except Exception as e:
            conn.close()
            return redirect(url_for("products", q=q, err=str(e)))
        conn.close()
        if not res.get("ok"):
            return redirect(url_for("products", q=q, err=res.get("error")))
        return redirect(url_for("review"))
```
bằng:
```python
    @app.route("/sanpham/tao-bai", methods=["POST"])
    def create_from_product():
        external_id = request.form.get("external_product_id", "").strip()
        source_name = request.form.get("nguon") or None
        q = request.form.get("q", "")
        channel_codes = request.form.getlist("channel_codes")
        if not external_id:
            return redirect(url_for("products", q=q, err="Thiếu mã sản phẩm"))
        if not channel_codes:
            return redirect(url_for("products", q=q, err="Chọn ít nhất 1 kênh"))
        conn = connect()
        try:
            res = pipeline.create_post_for_product(
                conn, factory.build_context(source_name), external_id,
                campaign_code=os.environ.get("ACP_CAMPAIGN_CODE", "gd2026"),
                channel_codes=channel_codes)
        except Exception as e:
            conn.close()
            return redirect(url_for("products", q=q, err=str(e)))
        conn.close()
        if not res.get("ok"):
            return redirect(url_for("products", q=q, err=res.get("error")))
        return redirect(url_for("review"))
```

- [ ] **Step 9: Sửa `create_affiliate_product()` (chế độ Affiliate)**

Thay dòng đọc form:
```python
        channel_code = request.form.get("channel_code", "").strip()
```
bằng:
```python
        channel_codes = request.form.getlist("channel_codes")
```

Thay khối `missing`:
```python
        if not channel_code:
            missing.append("kênh Threads")
        if missing:
            return _render_affiliate(
                affiliate_url=affiliate_url, resolved=resolved, metadata=metadata,
                selected_channel=channel_code,
                err="Thiếu hoặc không hợp lệ: " + ", ".join(missing), status=400)

        conn = connect()
        channel = conn.execute(
            "SELECT code FROM channel WHERE code=? AND status='ACTIVE' AND platform='threads'",
            (channel_code,)).fetchone()
        if not channel:
            conn.close()
            return _render_affiliate(
                affiliate_url=affiliate_url, resolved=resolved, metadata=metadata,
                selected_channel=channel_code, err="Kênh Threads không tồn tại hoặc không hoạt động.", status=400)
```
bằng:
```python
        if not channel_codes:
            missing.append("ít nhất 1 kênh")
        if missing:
            return _render_affiliate(
                affiliate_url=affiliate_url, resolved=resolved, metadata=metadata,
                selected_channels=channel_codes,
                err="Thiếu hoặc không hợp lệ: " + ", ".join(missing), status=400)

        conn = connect()
        # Validate từng kênh nằm ở _create_post_from_raw_product (chung cho
        # web lẫn mọi caller khác) -- không lặp lại logic ở đây nữa.
```

Thay khối try/except phía dưới:
```python
        try:
            source.validate_confirmed_urls(affiliate_url, product_url)
            confirmed = ConfirmedProductInput(
                affiliate_url=affiliate_url, product_url=product_url, name=name,
                current_price=price, original_price=original_price,
                image_url=image_url, shop=shop)
            raw = source.prepare_product(confirmed, pipeline.MEDIA_DIR)
            # Important provider boundary: do not call factory.build_context() here.
            res = pipeline.create_post_from_manual_affiliate_product(
                conn, {"storage": storage.get_storage()}, source, raw,
                affiliate_url=affiliate_url,
                campaign_code=os.environ.get("ACP_CAMPAIGN_CODE", "gd2026"),
                channel_code=channel_code)
        except AffiliateImportError as exc:
            conn.close()
            return _render_affiliate(
                affiliate_url=affiliate_url, resolved=resolved, metadata=metadata,
                selected_channel=channel_code, err=str(exc), status=400)
        except Exception:
            conn.close()
            return _render_affiliate(
                affiliate_url=affiliate_url, resolved=resolved, metadata=metadata,
                selected_channel=channel_code,
                err="Không thể tạo bài nháp. Kiểm tra dữ liệu và thử lại.", status=500)
        conn.close()
        if not res.get("ok"):
            return _render_affiliate(
                affiliate_url=affiliate_url, resolved=resolved, metadata=metadata,
                selected_channel=channel_code, err=res.get("error") or "Không thể tạo bài nháp.", status=400)
        return redirect(url_for("review"))
```
bằng:
```python
        try:
            source.validate_confirmed_urls(affiliate_url, product_url)
            confirmed = ConfirmedProductInput(
                affiliate_url=affiliate_url, product_url=product_url, name=name,
                current_price=price, original_price=original_price,
                image_url=image_url, shop=shop)
            raw = source.prepare_product(confirmed, pipeline.MEDIA_DIR)
            # Important provider boundary: do not call factory.build_context() here.
            res = pipeline.create_post_from_manual_affiliate_product(
                conn, {"storage": storage.get_storage()}, source, raw,
                affiliate_url=affiliate_url,
                campaign_code=os.environ.get("ACP_CAMPAIGN_CODE", "gd2026"),
                channel_codes=channel_codes)
        except AffiliateImportError as exc:
            conn.close()
            return _render_affiliate(
                affiliate_url=affiliate_url, resolved=resolved, metadata=metadata,
                selected_channels=channel_codes, err=str(exc), status=400)
        except Exception:
            conn.close()
            return _render_affiliate(
                affiliate_url=affiliate_url, resolved=resolved, metadata=metadata,
                selected_channels=channel_codes,
                err="Không thể tạo bài nháp. Kiểm tra dữ liệu và thử lại.", status=500)
        conn.close()
        if not res.get("ok"):
            return _render_affiliate(
                affiliate_url=affiliate_url, resolved=resolved, metadata=metadata,
                selected_channels=channel_codes, err=res.get("error") or "Không thể tạo bài nháp.", status=400)
        return redirect(url_for("review"))
```

- [ ] **Step 10: Chạy `test_pilot.py`, xác nhận vẫn FAIL đúng chỗ (template
      chưa sửa — Task 10) rồi tạm bỏ qua phần template, xác nhận route/logic
      Python không lỗi cú pháp**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -c "import ast; ast.parse(open('acp/web/server.py').read())" && echo "OK: server.py không lỗi cú pháp"
python3 -m acp.tests.test_pilot 2>&1 | tail -40
```
Expected: các test liên quan `/sanpham` vẫn FAIL vì `products.html` chưa
được sửa (Task 10 sẽ làm) — đúng như dự kiến, KHÔNG phải lỗi cú pháp/logic
Python. Test không liên quan template (nếu có) phải PASS.

- [ ] **Step 11: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add web/server.py tests/test_pilot.py
git commit -m "feat: route /sanpham đọc channel_codes đa nền tảng, thay 2 test lỗi thời Threads-only (D1)"
```

---

## Task 9: Web route `/duyet` — checklist + duyệt nhiều account

**Files:**
- Modify: `web/server.py` (`review()`, `review_action()`)

**Interfaces:**
- Consumes: `pipeline.post_channel_selections(conn, post_ids)` (Task 6),
  `pipeline.approve_post(channel_ids=...)` (Task 7).
- Produces: `review()` truyền thêm `platform_labels`, mỗi post trong
  `posts` có thêm khoá `selected_channels`.

- [ ] **Step 1: Sửa `review()`**

Thay:
```python
    @app.route("/duyet")
    def review():
        conn = connect()
        rows = [dict(r) for r in conn.execute("""
            SELECT p.*, pr.name AS product_name, pr.category_code, pr.current_price,
                   pr.commission_value, pr.rating, pr.review_count, pr.sold_count,
                   ch.handle AS channel_handle, t.name AS template_name
            FROM post p
            JOIN product pr ON pr.id = p.product_id
            JOIN channel ch ON ch.id = p.channel_id
            LEFT JOIN caption_template t ON t.id = p.caption_template_id
            WHERE p.status IN ('PENDING_REVIEW', 'DRAFT')
            ORDER BY p.created_at DESC""").fetchall()]
        recent = [dict(r) for r in conn.execute("""
            SELECT p.id, p.status, p.scheduled_at, p.published_at, pr.name AS product_name
            FROM post p JOIN product pr ON pr.id = p.product_id
            WHERE p.status IN ('SCHEDULED','PUBLISHED','REJECTED')
            ORDER BY p.updated_at DESC LIMIT 8""").fetchall()]
        conn.close()
        return render_template("review.html", page="duyet", posts=rows, recent=recent)
```
bằng:
```python
    @app.route("/duyet")
    def review():
        conn = connect()
        rows = [dict(r) for r in conn.execute("""
            SELECT p.*, pr.name AS product_name, pr.category_code, pr.current_price,
                   pr.commission_value, pr.rating, pr.review_count, pr.sold_count,
                   ch.handle AS channel_handle, t.name AS template_name
            FROM post p
            JOIN product pr ON pr.id = p.product_id
            JOIN channel ch ON ch.id = p.channel_id
            LEFT JOIN caption_template t ON t.id = p.caption_template_id
            WHERE p.status IN ('PENDING_REVIEW', 'DRAFT')
            ORDER BY p.created_at DESC""").fetchall()]
        selections = pipeline.post_channel_selections(conn, [r["id"] for r in rows])
        for r in rows:
            r["selected_channels"] = selections.get(r["id"], [])
        recent = [dict(r) for r in conn.execute("""
            SELECT p.id, p.status, p.scheduled_at, p.published_at, pr.name AS product_name
            FROM post p JOIN product pr ON pr.id = p.product_id
            WHERE p.status IN ('SCHEDULED','PUBLISHED','REJECTED')
            ORDER BY p.updated_at DESC LIMIT 8""").fetchall()]
        conn.close()
        return render_template("review.html", page="duyet", posts=rows, recent=recent,
                               platform_labels=PLATFORM_LABELS)
```

- [ ] **Step 2: Sửa `review_action()`**

Thay:
```python
    @app.route("/duyet/<post_id>/<action>", methods=["POST"])
    def review_action(post_id, action):
        conn = connect()
        if action == "approve":
            res = pipeline.approve_post(conn, post_id, actor="operator",
                                        caption_override=request.form.get("caption") or None)
        elif action == "reject":
            res = pipeline.reject_post(conn, post_id, request.form.get("reason") or "Không phù hợp", "operator")
        else:
            conn.close()
            abort(404)
        conn.close()
        return redirect(url_for("review", err=None if res.get("ok") else res.get("error")))
```
bằng:
```python
    @app.route("/duyet/<post_id>/<action>", methods=["POST"])
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
        elif action == "reject":
            res = pipeline.reject_post(conn, post_id, request.form.get("reason") or "Không phù hợp", "operator")
        else:
            conn.close()
            abort(404)
        conn.close()
        return redirect(url_for("review", err=None if res.get("ok") else res.get("error")))
```

- [ ] **Step 3: Xác nhận cú pháp hợp lệ**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -c "import ast; ast.parse(open('acp/web/server.py').read())" && echo "OK: server.py không lỗi cú pháp"
```

- [ ] **Step 4: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add web/server.py
git commit -m "feat: route /duyet đọc channel_ids, duyệt nhiều account 1 lần (D1)"
```

---

## Task 10: Templates — `products.html` (2 chế độ) + `review.html`

**Files:**
- Modify: `web/templates/products.html`
- Modify: `web/templates/review.html`
- Test: `tests/test_pilot.py` (chạy lại toàn bộ, xác nhận Task 8's test mới
  giờ PASS)

**Interfaces:**
- Consumes: `channels` (list có `code`/`platform`/`handle`),
  `platform_labels` (dict), `selected_channels` (list — code ở
  `products.html`, dict ở `review.html`), `p.selected_channels` (list dict
  có `id`/`code`/`platform`/`handle`) — tất cả do Task 8/9 cung cấp.
- Tái dùng CSS class có sẵn `.niche-grid`/`.niche-tile`
  (`web/static/acp.css:279-283`, đã có, không cần CSS mới).

- [ ] **Step 1: Sửa `products.html` — chế độ Affiliate: checklist thay
      dropdown**

Thay:
```html
        <div class="field field--full">
          <label for="channel_code">Kênh Threads</label>
          <select id="channel_code" name="channel_code" required>
            <option value="">Chọn kênh...</option>
            {% for ch in channels %}
            <option value="{{ ch.code }}" {{ 'selected' if selected_channel == ch.code }}>{{ ch.handle }} · {{ ch.code }}</option>
            {% endfor %}
          </select>
        </div>
```
bằng:
```html
        <div class="field field--full">
          <label>Kênh đăng bài</label>
          <div class="niche-grid">
          {% for ch in channels %}
            <label class="niche-tile"><input type="checkbox" name="channel_codes" value="{{ ch.code }}" {{ 'checked' if ch.code in selected_channels }}><span>[{{ platform_labels[ch.platform] }}] {{ ch.handle }}<small>{{ ch.code }}</small></span></label>
          {% endfor %}
          </div>
        </div>
```

- [ ] **Step 2: Sửa `products.html` — chế độ Tìm kiếm: 1 form chung bọc cả
      checklist lẫn bảng kết quả**

Thay toàn bộ khối:
```html
  {% if items %}
  <div class="section-heading section-heading--spaced">
    <div><h2>{{ items|length }} sản phẩm</h2><p class="note">Nguồn: {{ source_name }}</p></div>
  </div>
  <div class="table-card">
    <div class="table-scroll">
      <table class="data-table">
        <thead><tr><th>Sản phẩm</th><th>Shop</th><th class="n">Giá</th><th class="n">Hoa hồng</th><th class="n">Đã bán</th><th></th></tr></thead>
        <tbody>
        {% for p in items %}
          <tr>
            <td><strong>{{ p.name }}</strong><span class="mono-sub">{{ p.external_product_id }}</span></td>
            <td class="dim">{{ p.merchant }}</td>
            <td class="n">{{ p.current_price|vnd }}</td>
            <td class="n money">{{ p.commission_value|vnd }}</td>
            <td class="n dim">{{ p.sold_count|num }}</td>
            <td class="table-action">
              <form method="post" action="/sanpham/tao-bai">
                <input type="hidden" name="_csrf" value="{{ csrf_token }}">
                <input type="hidden" name="external_product_id" value="{{ p.external_product_id }}">
                <input type="hidden" name="nguon" value="{{ request.args.get('nguon','') }}">
                <input type="hidden" name="q" value="{{ q }}">
                <button class="btn btn--small" type="submit">Tạo bài</button>
              </form>
            </td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  {% elif not err %}
  <div class="empty-state">Không tìm thấy sản phẩm nào.</div>
  {% endif %}
```
bằng (gộp checklist + bảng vào 1 `<form>`, dùng nút submit mang
`external_product_id` theo từng dòng thay vì 1 `<form>` riêng mỗi dòng — cần
thiết để checklist chọn account chỉ khai báo 1 lần, dùng chung cho mọi nút
"Tạo bài" trong bảng):
```html
  {% if items %}
  <form method="post" action="/sanpham/tao-bai" class="card">
    <input type="hidden" name="_csrf" value="{{ csrf_token }}">
    <input type="hidden" name="nguon" value="{{ request.args.get('nguon','') }}">
    <input type="hidden" name="q" value="{{ q }}">
    <div class="section-heading section-heading--spaced">
      <div><h2>{{ items|length }} sản phẩm</h2><p class="note">Nguồn: {{ source_name }}</p></div>
    </div>
    <div class="field field--full">
      <label>Kênh đăng bài (áp dụng cho nút "Tạo bài" ở bất kỳ dòng nào bên dưới)</label>
      <div class="niche-grid">
      {% for ch in channels %}
        <label class="niche-tile"><input type="checkbox" name="channel_codes" value="{{ ch.code }}"><span>[{{ platform_labels[ch.platform] }}] {{ ch.handle }}<small>{{ ch.code }}</small></span></label>
      {% endfor %}
      </div>
    </div>
    <div class="table-card">
      <div class="table-scroll">
        <table class="data-table">
          <thead><tr><th>Sản phẩm</th><th>Shop</th><th class="n">Giá</th><th class="n">Hoa hồng</th><th class="n">Đã bán</th><th></th></tr></thead>
          <tbody>
          {% for p in items %}
            <tr>
              <td><strong>{{ p.name }}</strong><span class="mono-sub">{{ p.external_product_id }}</span></td>
              <td class="dim">{{ p.merchant }}</td>
              <td class="n">{{ p.current_price|vnd }}</td>
              <td class="n money">{{ p.commission_value|vnd }}</td>
              <td class="n dim">{{ p.sold_count|num }}</td>
              <td class="table-action">
                <button class="btn btn--small" type="submit" name="external_product_id" value="{{ p.external_product_id }}">Tạo bài</button>
              </td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
  </form>
  {% elif not err %}
  <div class="empty-state">Không tìm thấy sản phẩm nào.</div>
  {% endif %}
```

- [ ] **Step 3: Sửa `review.html` — thêm checklist account vào form Duyệt**

Thay:
```html
    <form method="post" action="/duyet/{{ p.id }}/approve">
      <input type="hidden" name="_csrf" value="{{ csrf_token }}">
      <div class="field"><label for="caption-{{ p.id }}">Caption</label><textarea id="caption-{{ p.id }}" name="caption" rows="7" maxlength="500">{{ p.caption_final }}</textarea></div>
      <div class="review-actions"><span class="dim mono">{{ p.caption_final|length }}/500 ký tự</span><div class="review-actions__buttons"><button class="btn btn--danger" type="submit" formaction="/duyet/{{ p.id }}/reject">Bỏ qua</button><button class="btn btn--primary" type="submit">Duyệt & lên lịch</button></div></div>
    </form>
```
bằng:
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

- [ ] **Step 4: Chạy toàn bộ `test_pilot.py`, xác nhận PASS**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pilot 2>&1 | tail -40
```
Expected: `test_product_checklist_shows_all_platforms`,
`test_create_affiliate_product_accepts_facebook_channel` (Task 8) PASS.
Toàn bộ 265 test cũ khác vẫn PASS — đặc biệt các test liên quan `/sanpham`
affiliate flow (dòng ~430-505) vẫn phải tạo bài thành công dù giờ dùng
`channel_codes` thay `channel_code` (kiểm tra kỹ nếu có test cũ còn gửi
`channel_code` đơn — nếu FAIL, đó là test cần cập nhật sang `channel_codes`,
không phải bug ở code sản xuất).

- [ ] **Step 5: Nếu Step 4 phát hiện test cũ còn dùng `channel_code` đơn
      (không phải 2 test đã thay ở Task 8) — cập nhật sang `channel_codes`**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
grep -n '"channel_code":' tests/test_pilot.py
```
Với mỗi chỗ tìm thấy (trừ 2 hàm đã thay ở Task 8), đổi
`"channel_code": "ch1"` thành `"channel_codes": ["ch1"]` trong dict `data=`
của lời gọi `c.post(...)`. Chạy lại Step 4 sau khi sửa.

- [ ] **Step 6: Chạy toàn bộ 2 test suite lần cuối, xác nhận không
      regression**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -5
python3 -m acp.tests.test_pilot 2>&1 | tail -5
```
Expected: cả 2 dòng cuối đều `0 hỏng`.

- [ ] **Step 7: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add web/templates/products.html web/templates/review.html tests/test_pilot.py
git commit -m "feat: checklist đa account ở /sanpham (2 chế độ) + /duyet (D1)"
```

---

## Sau khi cả 10 task hoàn tất

Chạy toàn bộ 2 test suite lần cuối để xác nhận trạng thái xanh trước khi
chuyển sang `finishing-a-development-branch`:

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -5
python3 -m acp.tests.test_pilot 2>&1 | tail -5
```

Baseline trước D1: `test_pipeline.py` 162/0, `test_pilot.py` 265/0. Sau D1 kỳ
vọng: `test_pipeline.py` 162 + 13 test mới (Task 1: 1, Task 2: 1, Task 3: 1,
Task 4: 1, Task 5: 1, Task 6: 2, Task 7: 3, cộng thêm) = 175/0 (con số chính
xác tuỳ implementer đếm lại khi chạy thật); `test_pilot.py` 265 (2 test thay
thế, không đổi tổng, có thể +1 nếu Step 5 Task 10 phải sửa thêm) = 265-267/0.
