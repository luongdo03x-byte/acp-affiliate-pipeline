# PublishTarget & Publisher Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tái cấu trúc lớp publish của ACP thành đơn vị `publish_target` độc
lập theo platform, thay `PublishingChannel` cố định bằng `Publisher` registry
qua `ctx["publishers"]`, làm nền cho Facebook/Instagram sau này (sub-project
B/C/D) mà không đổi hành vi Threads/Shopee/ACCESSTRADE hiện tại.

**Architecture:** Bảng `publish_target` mới hoàn toàn additive cạnh `post`.
`approve_post()` tạo một `publish_target` và enqueue `PUBLISH_POST` với
payload **giữ nguyên** `post_id`/`channel_id` (để không phá side-effect có
sẵn trong `jobs.py`) cộng thêm `publish_target_id`. Handler `publish_post`/
`fetch_insights` tra cứu publisher qua `ctx["publishers"][platform]` thay vì
`ctx["channel"]` cố định. Retry theo target là một thao tác thủ công riêng,
không đụng cơ chế backoff tự động của `job_queue`.

**Tech Stack:** Python 3.14, Flask, SQLite (WAL). Test runner tự viết
(hàm `check()` in kết quả), **không dùng pytest** — chạy qua
`python -m acp.tests.test_pipeline` / `acp.tests.test_pilot`.

**Spec:** `docs/superpowers/specs/2026-08-14-publish-target-publisher-foundation-design.md`

## Global Constraints

- `post` schema không đổi cột nào — `publish_target` là bảng mới hoàn toàn additive.
- `post_id` trên `publish_target` **không UNIQUE** (chuẩn bị cho N target/post ở sub-project D).
- `Publisher.publish()` nhận `media: list[str]`; `ThreadsPublisher`/`MockThreads` chỉ chấp nhận đúng 1 phần tử, raise lỗi rõ ràng (không âm thầm bỏ ảnh thừa) nếu `len(media) != 1`.
- Idempotency chuyển từ khoá theo `post` sang khoá theo `publish_target` (`publish_target.status == 'SUCCESS'` chặn đăng trùng, không phải `post["thread_id"]`).
- Cơ chế backoff/retry của `job_queue` (`core/jobs.py`) giữ nguyên **hoàn toàn** — không sửa file `core/jobs.py`.
- Payload job `PUBLISH_POST` giữ nguyên `post_id` và `channel_id` (không xoá hai key này) để `jobs.py`'s xử lý `AuthError`/`ContentViolationError` (đánh dấu kênh `NEEDS_REAUTH`, đẩy bài về `PENDING_REVIEW`) tiếp tục hoạt động; chỉ **thêm** `publish_target_id`.
- Test chạy qua `ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_pipeline` và `acp.tests.test_pilot`, chạy từ thư mục **cha** của repo (repo nằm trong thư mục tên `acp/`). Không dùng pytest, không dùng test runner khác.
- Không commit secrets/runtime data (`var/`, `.env*`, DB thật).

---

## Task 1: Bảng `publish_target`

**Files:**
- Modify: `core/db.py:137-146` (chèn `CREATE TABLE` mới ngay sau khối `post_metrics`, trước khối `conversion`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: bảng `publish_target(id, post_id, channel_id, status, scheduled_at, external_post_id, last_error, attempt_count, created_at, updated_at)`. Task 3 sẽ ghi/đọc bảng này từ `core/pipeline.py`.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `tests/test_pipeline.py`, ngay sau hàm `test_db_constraints` (trước dòng `if __name__ == "__main__":`):

```python
def test_publish_target_schema():
    print("\npublish_target schema")
    conn = connect()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(publish_target)").fetchall()}
    expected = {"id", "post_id", "channel_id", "status", "scheduled_at",
                "external_post_id", "last_error", "attempt_count",
                "created_at", "updated_at"}
    check("publish_target có đủ cột", expected <= cols, cols)

    product = conn.execute("SELECT id FROM product LIMIT 1").fetchone()
    channel = conn.execute("SELECT id FROM channel LIMIT 1").fetchone()
    campaign = conn.execute("SELECT id FROM campaign LIMIT 1").fetchone()
    post_id = ulid()
    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, variant_code,
                    caption_body, disclosure_text, caption_final, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                 (post_id, product["id"], channel["id"], campaign["id"], "A",
                  "thân bài", "nhãn tiếp thị", "thân bài", now(), now()))

    target_id = ulid()
    conn.execute("""INSERT INTO publish_target (id, post_id, channel_id, created_at, updated_at)
                    VALUES (?,?,?,?,?)""",
                 (target_id, post_id, channel["id"], now(), now()))
    row = conn.execute("SELECT * FROM publish_target WHERE id=?", (target_id,)).fetchone()
    check("status mặc định PENDING", row["status"] == "PENDING", row["status"])
    check("attempt_count mặc định 0", row["attempt_count"] == 0, row["attempt_count"])
    check("external_post_id mặc định NULL", row["external_post_id"] is None)
    conn.close()
```

Và thêm lời gọi `test_publish_target_schema()` vào khối `if __name__ == "__main__":`, ngay sau `test_db_constraints()`.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `sqlite3.OperationalError: no such table: publish_target`.

- [ ] **Step 3: Thêm bảng vào schema**

Trong `core/db.py`, ngay sau khối:

```sql
CREATE TABLE IF NOT EXISTS post_metrics (
    post_id     TEXT PRIMARY KEY REFERENCES post(id),
    views       INTEGER DEFAULT 0,
    likes       INTEGER DEFAULT 0,
    replies     INTEGER DEFAULT 0,
    reposts     INTEGER DEFAULT 0,
    clicks      INTEGER DEFAULT 0,
    updated_at  TEXT
);
```

chèn:

```sql
CREATE TABLE IF NOT EXISTS publish_target (
    id                TEXT PRIMARY KEY,
    post_id           TEXT NOT NULL REFERENCES post(id),
    channel_id        TEXT NOT NULL REFERENCES channel(id),
    status            TEXT NOT NULL DEFAULT 'PENDING',
    scheduled_at      TEXT,
    external_post_id  TEXT,
    last_error        TEXT,
    attempt_count     INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_publish_target_post   ON publish_target(post_id);
CREATE INDEX IF NOT EXISTS idx_publish_target_status ON publish_target(status, scheduled_at);
```

Không thêm gì vào `MIGRATIONS` — đây là bảng mới hoàn toàn, `CREATE TABLE IF NOT EXISTS` đủ an toàn cho cả DB mới lẫn DB cũ.

- [ ] **Step 4: Chạy lại test, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `38 đạt, 0 hỏng`.

- [ ] **Step 5: Commit**

```bash
git add core/db.py tests/test_pipeline.py
git commit -m "feat: add publish_target table (additive schema)"
```

---

## Task 2: `Publisher` abstraction + `media: list[str]`

**Files:**
- Modify: `adapters/base.py:69-82` (đổi tên `PublishingChannel` → `Publisher`, chữ ký `publish`)
- Modify: `adapters/mock.py:13-14,87,97` (`MockThreads` kế thừa `Publisher`, nhận `media`)
- Modify: `adapters/live.py:20-23,171,199` (`ThreadsChannel` kế thừa `Publisher`, nhận `media`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: không có (thuần đổi tên/chữ ký nội bộ).
- Produces: `class Publisher` với `publish(channel_row, caption: str, media: list) -> PublishResult`. Task 3 dùng qua `ctx["publishers"][platform].publish(...)`.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `tests/test_pipeline.py`, ngay sau `test_publish_target_schema`:

```python
def test_publisher_media_list():
    print("\nPublisher nhận danh sách media")
    from acp.adapters.base import Publisher
    ch = MockThreads(seed=1)
    check("MockThreads là Publisher", isinstance(ch, Publisher))

    result = ch.publish({}, "caption ngắn", media=["https://img.example/a.jpg"])
    check("publish 1 ảnh trả về PublishResult", bool(result.external_post_id))

    try:
        ch.publish({}, "caption ngắn", media=["https://img.example/a.jpg", "https://img.example/b.jpg"])
        check("publish nhiều ảnh với Threads phải báo lỗi", False, "không ném lỗi")
    except ValueError as e:
        check("publish nhiều ảnh với Threads phải báo lỗi", True, str(e))
```

Gọi thêm `test_publisher_media_list()` trong khối `__main__`, sau `test_publish_target_schema()`.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `TypeError: publish() got an unexpected keyword argument 'media'` (chữ ký cũ là `image_url`).

- [ ] **Step 3: Đổi `adapters/base.py`**

Thay:

```python
class PublishingChannel:
    """Kênh đăng bài."""

    platform: str = "base"
    max_caption_length: int = 500

    def publish(self, channel_row, caption: str, image_url: Optional[str]) -> PublishResult:
        raise NotImplementedError

    def remaining_quota(self, channel_row) -> int:
        raise NotImplementedError

    def fetch_insights(self, channel_row, external_post_id: str) -> dict:
        return {}
```

bằng:

```python
class Publisher:
    """Kênh đăng bài theo platform. `media` luôn là list, kể cả khi chỉ 1 ảnh --
    chuẩn bị cho carousel Facebook/Instagram mà không phải đổi chữ ký lần hai."""

    platform: str = "base"
    max_caption_length: int = 500

    def publish(self, channel_row, caption: str, media: list) -> PublishResult:
        raise NotImplementedError

    def remaining_quota(self, channel_row) -> int:
        raise NotImplementedError

    def fetch_insights(self, channel_row, external_post_id: str) -> dict:
        return {}
```

- [ ] **Step 4: Đổi `adapters/mock.py`**

Đổi import (dòng 13-14):

```python
from .base import (
    ContentSource, PublishingChannel, RawProduct, PublishResult,
    PublishError, RateLimitError, ContentViolationError,
)
```

thành:

```python
from .base import (
    ContentSource, Publisher, RawProduct, PublishResult,
    PublishError, RateLimitError, ContentViolationError,
)
```

Đổi khai báo lớp (dòng 87) `class MockThreads(PublishingChannel):` thành `class MockThreads(Publisher):`.

Đổi phương thức `publish` (dòng 97-110):

```python
    def publish(self, channel_row, caption: str, image_url=None) -> PublishResult:
        if self.rate_limited:
            raise RateLimitError("Đã dùng hết 250 bài trong cửa sổ 24 giờ")
        if len(caption) > self.max_caption_length:
            raise ContentViolationError(
                f"Caption {len(caption)} ký tự, vượt giới hạn {self.max_caption_length} của Threads"
            )
        if self._rng.random() < self.fail_rate:
            raise PublishError("Hết thời gian chờ khi tạo media container")
        # Threads dùng container model 2 bước: tạo container -> poll status -> publish.
        # Bản mock gộp lại, bản live tách đúng như thật.
        pid = f"mock_{self._rng.randrange(10**12, 10**13)}"
        self.published.append((pid, caption))
        return PublishResult(external_post_id=pid, published_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
```

thành:

```python
    def publish(self, channel_row, caption: str, media: list = None) -> PublishResult:
        media = media or []
        if len(media) > 1:
            raise ValueError(f"Threads chưa hỗ trợ nhiều ảnh, nhận {len(media)}")
        if self.rate_limited:
            raise RateLimitError("Đã dùng hết 250 bài trong cửa sổ 24 giờ")
        if len(caption) > self.max_caption_length:
            raise ContentViolationError(
                f"Caption {len(caption)} ký tự, vượt giới hạn {self.max_caption_length} của Threads"
            )
        if self._rng.random() < self.fail_rate:
            raise PublishError("Hết thời gian chờ khi tạo media container")
        # Threads dùng container model 2 bước: tạo container -> poll status -> publish.
        # Bản mock gộp lại, bản live tách đúng như thật.
        pid = f"mock_{self._rng.randrange(10**12, 10**13)}"
        self.published.append((pid, caption))
        return PublishResult(external_post_id=pid, published_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
```

- [ ] **Step 5: Đổi `adapters/live.py`**

Đổi import (dòng 20-23):

```python
from .base import (
    ContentSource, PublishingChannel, RawProduct, PublishResult,
    PublishError, RateLimitError, ContentViolationError, AuthError,
)
```

thành:

```python
from .base import (
    ContentSource, Publisher, RawProduct, PublishResult,
    PublishError, RateLimitError, ContentViolationError, AuthError,
)
```

Đổi khai báo lớp (dòng 171) `class ThreadsChannel(PublishingChannel):` thành `class ThreadsChannel(Publisher):`.

Đổi phương thức `publish` (dòng 199-207), chỉ 3 dòng đầu:

```python
    def publish(self, channel_row, caption: str, image_url=None) -> PublishResult:
        if len(caption) > self.max_caption_length:
            raise ContentViolationError(f"Caption {len(caption)} ký tự, Threads chỉ cho {self.max_caption_length}")
        uid, token = channel_row["external_user_id"], self._token(channel_row)

        # Bước 1 -- tạo media container.
        payload = {"media_type": "IMAGE" if image_url else "TEXT", "text": caption, "access_token": token}
        if image_url:
            payload["image_url"] = image_url  # bắt buộc là URL công khai
```

thành:

```python
    def publish(self, channel_row, caption: str, media: list = None) -> PublishResult:
        media = media or []
        if len(media) > 1:
            raise ValueError(f"Threads chưa hỗ trợ nhiều ảnh, nhận {len(media)}")
        if len(caption) > self.max_caption_length:
            raise ContentViolationError(f"Caption {len(caption)} ký tự, Threads chỉ cho {self.max_caption_length}")
        uid, token = channel_row["external_user_id"], self._token(channel_row)
        image_url = media[0] if media else None

        # Bước 1 -- tạo media container.
        payload = {"media_type": "IMAGE" if image_url else "TEXT", "text": caption, "access_token": token}
        if image_url:
            payload["image_url"] = image_url  # bắt buộc là URL công khai
```

Phần còn lại của hàm (poll + publish, dòng 208 trở đi) không đổi.

- [ ] **Step 6: Chạy lại test, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `40 đạt, 0 hỏng`.

- [ ] **Step 7: Commit**

```bash
git add adapters/base.py adapters/mock.py adapters/live.py tests/test_pipeline.py
git commit -m "refactor: rename PublishingChannel to Publisher, publish() takes media list"
```

---

## Task 3: `ctx["publishers"]` registry + wire `publish_target` vào pipeline

Đây là task lõi: gộp việc đổi `factory.build_context()` và việc `core/pipeline.py` tiêu thụ nó thành một task, vì hai phần chỉ đúng khi đi cùng nhau (đổi riêng một bên sẽ để lại hệ thống không publish được).

**Files:**
- Modify: `adapters/factory.py:41-56` (`get_channel()`/`build_context()` → `get_publishers()`/`publishers`)
- Modify: `core/pipeline.py:298-388` (`approve_post`, `publish_post`, `fetch_insights`, hàm mới `_mark_target_failed`)
- Modify: `tests/test_pipeline.py` (`test_idempotency_and_double_post`, `test_daily_cap`, 2 test mới)
- Modify: `tests/test_pilot.py:175-177,197` (`test_factory`, `test_single_product_flow`)

**Interfaces:**
- Consumes: `Publisher` (Task 2), bảng `publish_target` (Task 1), `enqueue`/`handler` từ `core/jobs.py` (không đổi).
- Produces:
  - `factory.build_context()["publishers"]: dict[str, Publisher]` (thay cho `["channel"]`).
  - `pipeline.approve_post(conn, post_id, actor=..., caption_override=...) -> dict` trả thêm key `"publish_target_id"`.
  - `pipeline.retry_publish_target(conn, target_id, actor=...) -> dict` (Task 4 dùng).

- [ ] **Step 1: Đổi `adapters/factory.py`**

Thay:

```python
def get_channel():
    if is_live():
        from .live import ThreadsChannel
        return ThreadsChannel()
    from .mock import MockThreads
    return MockThreads(fail_rate=0.08, seed=7)


def build_context(source_name: str = None) -> dict:
    """Ngữ cảnh truyền vào các job handler."""
    from ..core import storage
    return {
        "source": get_source(source_name),
        "channel": get_channel(),
        "storage": storage.get_storage(),
    }
```

bằng:

```python
def get_channel():
    if is_live():
        from .live import ThreadsChannel
        return ThreadsChannel()
    from .mock import MockThreads
    return MockThreads(fail_rate=0.08, seed=7)


def get_publishers() -> dict:
    """platform -> Publisher. Chỉ có 'threads' cho tới khi sub-project B/C
    đăng ký thêm 'facebook'/'instagram'."""
    return {"threads": get_channel()}


def build_context(source_name: str = None) -> dict:
    """Ngữ cảnh truyền vào các job handler."""
    from ..core import storage
    return {
        "source": get_source(source_name),
        "publishers": get_publishers(),
        "storage": storage.get_storage(),
    }
```

`get_channel()` giữ nguyên (vẫn dùng trong `get_publishers()`), không xoá.

- [ ] **Step 2: Đổi test_pilot.py::test_factory và test_single_product_flow (viết trước, sẽ fail cho tới Step 4)**

Trong `tests/test_pilot.py`, thay (dòng 175-177):

```python
    ctx = factory.build_context()
    check("context có đủ source, channel, storage",
          all(k in ctx for k in ("source", "channel", "storage")), list(ctx))
```

bằng:

```python
    from acp.adapters.base import Publisher

    ctx = factory.build_context()
    check("context có đủ source, publishers, storage",
          all(k in ctx for k in ("source", "publishers", "storage")), list(ctx))
    check("publishers có threads là Publisher",
          isinstance(ctx["publishers"].get("threads"), Publisher), ctx["publishers"])
```

Và thay dòng 197:

```python
    ctx = {"source": src, "channel": None, "storage": _FakeStorage()}
```

bằng:

```python
    ctx = {"source": src, "publishers": {}, "storage": _FakeStorage()}
```

(`test_single_product_flow` không bao giờ chạm publish, key này chỉ cần đúng shape.)

- [ ] **Step 3: Đổi test_pipeline.py — cập nhật 2 test hiện có dùng `ctx["channel"]`/payload cũ**

Trong `tests/test_pipeline.py`, thay toàn bộ `test_idempotency_and_double_post`:

```python
def test_idempotency_and_double_post():
    print("\nChống đăng trùng")
    conn = connect()
    n1 = jobs.enqueue(conn, "NOOP", {"a": 1}, idempotency_key="same-key")
    n2 = jobs.enqueue(conn, "NOOP", {"a": 1}, idempotency_key="same-key")
    check("cùng idempotency_key chỉ tạo một job", n1 > 0 and n2 == 0)

    ids = pipeline.plan_content(conn, "test", limit=3, rng=random.Random(1))
    check("chấm điểm tạo được job sinh nội dung", len(ids) > 0)
    ch = MockThreads(seed=1)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": ch}})

    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
    check("bài sinh ra ở trạng thái chờ duyệt", post is not None)
    res = pipeline.approve_post(conn, post["id"])
    check("duyệt xong thì lên lịch", res["ok"])
    target_id = res["publish_target_id"]
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?", (now(), f"pub:{target_id}"))
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": ch}})

    before = len(ch.published)
    # Ép chạy lại đúng job publish đó -- mô phỏng retry sau khi bài đã lên thành công.
    jobs.enqueue(conn, "PUBLISH_POST",
                 {"publish_target_id": target_id, "post_id": post["id"], "channel_id": post["channel_id"]})
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": ch}})
    check("chạy lại job publish không đăng bài lần hai", len(ch.published) == before,
          f"{before} → {len(ch.published)}")

    row = conn.execute("SELECT status, thread_id FROM post WHERE id=?", (post["id"],)).fetchone()
    check("bài đã có thread_id sau khi đăng", row["status"] == "PUBLISHED" and row["thread_id"])
    target = conn.execute("SELECT status, external_post_id FROM publish_target WHERE id=?", (target_id,)).fetchone()
    check("publish_target cũng SUCCESS", target["status"] == "SUCCESS" and target["external_post_id"])
    conn.close()
```

Trong `test_daily_cap`, chỉ đổi 2 chỗ `ctx={"source": MockAccessTrade(), "channel": ch}` thành `ctx={"source": MockAccessTrade(), "publishers": {"threads": ch}}` (giữ nguyên mọi assertion khác).

- [ ] **Step 4: Chạy test, xác nhận thất bại đúng chỗ**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: `test_pipeline` fail ở `res["publish_target_id"]` (KeyError, `approve_post` chưa trả key này) và `ctx["publishers"]` chưa được `publish_post` đọc (`KeyError: 'channel'` bên trong `publish_post`). `test_pilot::test_factory` fail ở check `publishers` không tồn tại trong ctx.

- [ ] **Step 5: Sửa `core/pipeline.py` — `approve_post`**

Thay (dòng 298-314):

```python
def approve_post(conn, post_id: str, actor: str = "operator", caption_override: str = None) -> dict:
    post = conn.execute("SELECT * FROM post WHERE id=?", (post_id,)).fetchone()
    if not post:
        return {"ok": False, "error": "Không tìm thấy bài đăng"}
    caption = caption_override or post["caption_final"]
    problems = content.validate(caption, niches=channel_niches(conn, post["channel_id"]))
    if problems:
        return {"ok": False, "error": "; ".join(problems)}

    scheduled = _next_slot(conn, post["channel_id"])
    conn.execute("""UPDATE post SET caption_final=?, status='SCHEDULED', scheduled_at=?,
                    reviewed_by=?, reviewed_at=?, reject_reason=NULL, updated_at=? WHERE id=?""",
                 (caption, scheduled, actor, now(), now(), post_id))
    enqueue(conn, "PUBLISH_POST", {"post_id": post_id, "channel_id": post["channel_id"]},
            priority=50, run_after=scheduled, idempotency_key=f"pub:{post_id}")
    audit(conn, "post", post_id, "approved", actor=actor, detail={"scheduled_at": scheduled})
    return {"ok": True, "scheduled_at": scheduled}
```

bằng:

```python
def approve_post(conn, post_id: str, actor: str = "operator", caption_override: str = None) -> dict:
    post = conn.execute("SELECT * FROM post WHERE id=?", (post_id,)).fetchone()
    if not post:
        return {"ok": False, "error": "Không tìm thấy bài đăng"}
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

- [ ] **Step 6: Sửa `core/pipeline.py` — `publish_post` và `fetch_insights`**

Thay (dòng 344-388):

```python
@handler("PUBLISH_POST")
def publish_post(conn, payload, ctx):
    post = conn.execute("SELECT * FROM post WHERE id=?", (payload["post_id"],)).fetchone()
    if not post:
        raise ValueError("Không tìm thấy bài đăng")

    # Tuyến phòng thủ chống đăng trùng. Timeout mạng rồi retry trong khi bài đã
    # lên thành công là lỗi nghiêm trọng nhất của loại hệ thống này.
    if post["thread_id"]:
        return
    if post["status"] not in ("SCHEDULED", "APPROVED"):
        return

    channel = conn.execute("SELECT * FROM channel WHERE id=?", (post["channel_id"],)).fetchone()
    if channel["status"] != "ACTIVE":
        from ..adapters.base import AuthError
        raise AuthError(f"Kênh {channel['code']} đang ở trạng thái {channel['status']}")

    if _published_today(conn, channel["id"]) >= channel["daily_post_cap"]:
        from ..adapters.base import RateLimitError
        raise RateLimitError(f"Kênh {channel['code']} đã đạt trần {channel['daily_post_cap']} bài trong ngày")

    result = ctx["channel"].publish(channel, post["caption_final"], post["image_url_composited"])
    conn.execute("UPDATE post SET status='PUBLISHED', thread_id=?, published_at=?, updated_at=? WHERE id=?",
                 (result.external_post_id, result.published_at, now(), post["id"]))
    audit(conn, "post", post["id"], "published", detail={"thread_id": result.external_post_id})
    enqueue(conn, "FETCH_INSIGHTS", {"post_id": post["id"], "channel_id": channel["id"]},
            run_after=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(timespec="seconds"),
            idempotency_key=f"ins:{post['id']}")
```

```python
@handler("FETCH_INSIGHTS")
def fetch_insights(conn, payload, ctx):
    post = conn.execute("SELECT thread_id FROM post WHERE id=?", (payload["post_id"],)).fetchone()
    channel = conn.execute("SELECT * FROM channel WHERE id=?", (payload["channel_id"],)).fetchone()
    if not post or not post["thread_id"]:
        return
    attribution.update_insights(conn, payload["post_id"], ctx["channel"].fetch_insights(channel, post["thread_id"]))
```

bằng:

```python
def _mark_target_failed(conn, target_id: str, error) -> None:
    conn.execute("""UPDATE publish_target SET status='FAILED', last_error=?,
                    attempt_count=attempt_count+1, updated_at=? WHERE id=?""",
                 (str(error)[:500], now(), target_id))


@handler("PUBLISH_POST")
def publish_post(conn, payload, ctx):
    target = conn.execute("SELECT * FROM publish_target WHERE id=?", (payload["publish_target_id"],)).fetchone()
    if not target:
        raise ValueError("Không tìm thấy publish_target")
    # Tuyến phòng thủ chống đăng trùng, khoá theo TARGET chứ không theo post --
    # cần thiết khi một post có nhiều target độc lập (sub-project D).
    if target["status"] == "SUCCESS":
        return

    post = conn.execute("SELECT * FROM post WHERE id=?", (target["post_id"],)).fetchone()
    channel = conn.execute("SELECT * FROM channel WHERE id=?", (target["channel_id"],)).fetchone()
    if not post or not channel:
        raise ValueError("publish_target trỏ tới post/channel không tồn tại")
    if post["status"] not in ("SCHEDULED", "APPROVED"):
        return

    if channel["status"] != "ACTIVE":
        from ..adapters.base import AuthError
        _mark_target_failed(conn, target["id"], f"Kênh {channel['code']} đang ở trạng thái {channel['status']}")
        raise AuthError(f"Kênh {channel['code']} đang ở trạng thái {channel['status']}")

    if _published_today(conn, channel["id"]) >= channel["daily_post_cap"]:
        from ..adapters.base import RateLimitError
        raise RateLimitError(f"Kênh {channel['code']} đã đạt trần {channel['daily_post_cap']} bài trong ngày")

    conn.execute("UPDATE publish_target SET status='RUNNING', updated_at=? WHERE id=?", (now(), target["id"]))
    try:
        publisher = ctx["publishers"][channel["platform"]]
        result = publisher.publish(channel, post["caption_final"], media=[post["image_url_composited"]])
    except Exception as e:
        from ..adapters.base import RateLimitError as _RateLimitError
        if isinstance(e, _RateLimitError):
            # Hạn mức không phải lỗi -- không tính là FAILED, không tăng attempt_count,
            # trả target về SCHEDULED để job_queue tự hoãn và thử lại đúng như trước.
            conn.execute("UPDATE publish_target SET status='SCHEDULED', last_error=?, updated_at=? WHERE id=?",
                         (str(e)[:500], now(), target["id"]))
        else:
            _mark_target_failed(conn, target["id"], e)
        raise

    conn.execute("""UPDATE publish_target SET status='SUCCESS', external_post_id=?, updated_at=?
                    WHERE id=?""", (result.external_post_id, now(), target["id"]))
    conn.execute("UPDATE post SET status='PUBLISHED', thread_id=?, published_at=?, updated_at=? WHERE id=?",
                 (result.external_post_id, result.published_at, now(), post["id"]))
    audit(conn, "post", post["id"], "published",
          detail={"thread_id": result.external_post_id, "publish_target_id": target["id"]})
    enqueue(conn, "FETCH_INSIGHTS", {"post_id": post["id"], "channel_id": channel["id"]},
            run_after=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(timespec="seconds"),
            idempotency_key=f"ins:{post['id']}")


@handler("FETCH_INSIGHTS")
def fetch_insights(conn, payload, ctx):
    post = conn.execute("SELECT thread_id FROM post WHERE id=?", (payload["post_id"],)).fetchone()
    channel = conn.execute("SELECT * FROM channel WHERE id=?", (payload["channel_id"],)).fetchone()
    if not post or not post["thread_id"]:
        return
    publisher = ctx["publishers"][channel["platform"]]
    attribution.update_insights(conn, payload["post_id"], publisher.fetch_insights(channel, post["thread_id"]))
```

- [ ] **Step 7: Chạy lại test, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: cả hai suite pass (`test_pipeline`: 40 đạt; `test_pilot`: 101 đạt — số cụ thể có thể lệch 1-2 nếu môi trường khác, quan trọng là `0 hỏng`).

- [ ] **Step 8: Thêm 2 test khoá lại hành vi mới (viết + chạy luôn, không cần vòng fail riêng vì hành vi đã tồn tại từ Step 6)**

Thêm vào `tests/test_pipeline.py`, sau `test_daily_cap`:

```python
def test_publish_target_failure_semantics():
    print("\npublish_target theo dõi lỗi")
    conn = connect()
    ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(21))
    check("có job sinh nội dung", len(ids) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=21)}})
    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
    res = pipeline.approve_post(conn, post["id"])
    target_id = res["publish_target_id"]
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?", (now(), f"pub:{target_id}"))

    failing = MockThreads(fail_rate=1.0, seed=22)  # luôn PublishError
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": failing}})
    target = conn.execute("SELECT * FROM publish_target WHERE id=?", (target_id,)).fetchone()
    check("publish_target FAILED sau lỗi mạng", target["status"] == "FAILED", target["status"])
    check("attempt_count tăng lên", target["attempt_count"] == 1, target["attempt_count"])
    check("last_error được ghi lại", bool(target["last_error"]))

    ids2 = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(23))
    check("có job sinh nội dung 2", len(ids2) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=23)}})
    post2 = conn.execute(
        "SELECT * FROM post WHERE status='PENDING_REVIEW' AND id != ? LIMIT 1", (post["id"],)).fetchone()
    res2 = pipeline.approve_post(conn, post2["id"])
    target2_id = res2["publish_target_id"]
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?", (now(), f"pub:{target2_id}"))

    limited = MockThreads(rate_limited=True, seed=24)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": limited}})
    target2 = conn.execute("SELECT * FROM publish_target WHERE id=?", (target2_id,)).fetchone()
    check("rate limit không tăng attempt_count", target2["attempt_count"] == 0, target2["attempt_count"])
    check("rate limit trả target về SCHEDULED chứ không FAILED",
          target2["status"] == "SCHEDULED", target2["status"])
    conn.close()


def test_publish_post_authorror_marks_channel():
    print("\nLỗi xác thực khi publish vẫn đánh dấu kênh (payload giữ channel_id)")
    from acp.adapters.base import AuthError

    class _AuthFailPublisher(MockThreads):
        def publish(self, channel_row, caption, media=None):
            raise AuthError("token thu hồi")

    conn = connect()
    ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(25))
    check("có job sinh nội dung", len(ids) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=25)}})
    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
    res = pipeline.approve_post(conn, post["id"])
    target_id = res["publish_target_id"]
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?", (now(), f"pub:{target_id}"))

    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": _AuthFailPublisher()}})

    channel = conn.execute("SELECT status FROM channel WHERE id=?", (post["channel_id"],)).fetchone()
    check("job publish AuthError vẫn đánh dấu kênh NEEDS_REAUTH", channel["status"] == "NEEDS_REAUTH", channel["status"])
    target = conn.execute("SELECT status FROM publish_target WHERE id=?", (target_id,)).fetchone()
    check("publish_target FAILED tương ứng", target["status"] == "FAILED", target["status"])
    conn.execute("UPDATE channel SET status='ACTIVE' WHERE id=?", (post["channel_id"],))
    conn.close()
```

Gọi cả hai hàm trong khối `__main__`, sau `test_daily_cap()`.

- [ ] **Step 9: Chạy toàn bộ suite, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: `0 hỏng` ở cả hai.

- [ ] **Step 10: Commit**

```bash
git add adapters/factory.py core/pipeline.py tests/test_pipeline.py tests/test_pilot.py
git commit -m "feat: wire publish_target + ctx[publishers] into approve_post/publish_post/fetch_insights"
```

---

## Task 4: `retry_publish_target()`

**Files:**
- Modify: `core/pipeline.py` (hàm mới, đặt sau `reject_post`, trước `_next_slot`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `publish_target` (Task 1), `ctx["publishers"]` + handler `publish_post` (Task 3).
- Produces: `pipeline.retry_publish_target(conn, target_id: str, actor: str = "operator") -> dict` với `{"ok": True, "job_id": int}` hoặc `{"ok": False, "error": str}`. Task 5 (route web) gọi hàm này.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `tests/test_pipeline.py`, sau `test_publish_post_authorror_marks_channel`:

```python
def test_retry_publish_target():
    print("\nThử lại publish_target lỗi")
    conn = connect()
    ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(31))
    check("có job sinh nội dung", len(ids) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=31)}})
    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
    res = pipeline.approve_post(conn, post["id"])
    target_id = res["publish_target_id"]
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?", (now(), f"pub:{target_id}"))

    failing = MockThreads(fail_rate=1.0, seed=32)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": failing}})
    target = conn.execute("SELECT status FROM publish_target WHERE id=?", (target_id,)).fetchone()
    check("target FAILED trước khi retry", target["status"] == "FAILED", target["status"])

    bad = pipeline.retry_publish_target(conn, "khong-ton-tai")
    check("retry target không tồn tại báo lỗi", bad["ok"] is False)

    res2 = pipeline.retry_publish_target(conn, target_id)
    check("retry tạo job mới", res2["ok"] and res2["job_id"], res2)
    again = pipeline.retry_publish_target(conn, target_id)
    check("retry lần hai khi đang PENDING bị chặn", again["ok"] is False, again)

    conn.execute("UPDATE job_queue SET run_after=? WHERE id=?", (now(), res2["job_id"]))
    ok_publisher = MockThreads(seed=33)  # publisher khác, không lỗi -- mô phỏng sự cố đã hết
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": ok_publisher}})
    target = conn.execute("SELECT status, external_post_id FROM publish_target WHERE id=?", (target_id,)).fetchone()
    check("retry thành công thì target SUCCESS", target["status"] == "SUCCESS" and target["external_post_id"], dict(target))

    n_targets = conn.execute("SELECT COUNT(*) FROM publish_target WHERE post_id=?", (post["id"],)).fetchone()[0]
    check("retry không tạo publish_target mới", n_targets == 1, n_targets)
    conn.close()
```

Gọi `test_retry_publish_target()` trong khối `__main__`, sau `test_publish_post_authorror_marks_channel()`.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `AttributeError: module 'acp.core.pipeline' has no attribute 'retry_publish_target'`.

- [ ] **Step 3: Thêm hàm vào `core/pipeline.py`**

Chèn ngay sau `reject_post` (trước hàm `_next_slot`):

```python
def retry_publish_target(conn, target_id: str, actor: str = "operator") -> dict:
    """Chỉ retry khi FAILED. Reset về PENDING, enqueue lại đúng target đó --
    không tạo publish_target mới, không đụng target khác của cùng post."""
    target = conn.execute("SELECT * FROM publish_target WHERE id=?", (target_id,)).fetchone()
    if not target:
        return {"ok": False, "error": "Không tìm thấy publish_target"}
    if target["status"] != "FAILED":
        return {"ok": False, "error": f"Chỉ retry được target FAILED, hiện tại là {target['status']}"}

    conn.execute("UPDATE publish_target SET status='PENDING', updated_at=? WHERE id=?", (now(), target_id))
    retry_key = f"pub:{target_id}:retry:{target['attempt_count']}"
    job_id = enqueue(conn, "PUBLISH_POST",
                      {"publish_target_id": target_id, "post_id": target["post_id"], "channel_id": target["channel_id"]},
                      priority=50, idempotency_key=retry_key)
    audit(conn, "publish_target", target_id, "retry", actor=actor,
          detail={"attempt_count": target["attempt_count"]})
    return {"ok": True, "job_id": job_id}
```

- [ ] **Step 4: Chạy lại test, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `0 hỏng`.

- [ ] **Step 5: Commit**

```bash
git add core/pipeline.py tests/test_pipeline.py
git commit -m "feat: add retry_publish_target for per-target manual retry"
```

---

## Task 5: `/vanhanh` UI — bảng publish_target + nút Thử lại

**Files:**
- Modify: `web/server.py:376-397` (route `ops`, thêm route `retry_publish_target_route`)
- Modify: `web/templates/ops.html` (bảng mới)
- Test: `tests/test_pilot.py`

**Interfaces:**
- Consumes: `pipeline.retry_publish_target` (Task 4).
- Produces: `GET /vanhanh` hiển thị `publish_targets`; `POST /vanhanh/<target_id>/retry`.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `tests/test_pilot.py`, ngay sau `test_web_security` (trước `test_production_guard`):

```python
def test_publish_target_retry_route():
    print("\nRoute thử lại publish target")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()

    check("route thử lại yêu cầu đăng nhập",
          c.post("/vanhanh/khong-ton-tai/retry").status_code == 302)

    c.post("/dangnhap", data={"password": "matkhau-test"})
    check("trang vận hành mở được sau đăng nhập", c.get("/vanhanh").status_code == 200)

    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    r = c.post("/vanhanh/khong-ton-tai/retry", data={"_csrf": csrf})
    check("route thử lại target không tồn tại vẫn redirect (không sập trang)", r.status_code == 302, r.status_code)
    check("báo lỗi target không tồn tại qua query", "err=" in r.location, r.location)

    r2 = c.post("/vanhanh/khong-ton-tai/retry", data={})
    check("thiếu CSRF bị chặn", r2.status_code == 400, r2.status_code)

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)
```

Thêm lời gọi `test_publish_target_retry_route()` vào khối `__main__`, sau `test_web_security()`.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: `404 NOT FOUND` cho `/vanhanh/khong-ton-tai/retry`.

- [ ] **Step 3: Thêm route vào `web/server.py`**

Trong route `ops()` (dòng 376-397), thêm truy vấn `publish_targets` vào `data`:

```python
    @app.route("/vanhanh")
    def ops():
        conn = connect()
        data = dict(
            queue=jobs.queue_summary(conn),
            failed=[dict(r) for r in conn.execute(
                "SELECT * FROM job_queue WHERE status='FAILED' ORDER BY updated_at DESC LIMIT 10").fetchall()],
            deferred=[dict(r) for r in conn.execute(
                "SELECT * FROM job_queue WHERE status='READY' AND last_error IS NOT NULL "
                "ORDER BY run_after LIMIT 5").fetchall()],
            channels=[dict(r) for r in conn.execute("""
                SELECT c.*, (SELECT COUNT(*) FROM post p WHERE p.channel_id=c.id AND p.status='PUBLISHED'
                             AND substr(p.published_at,1,10)=substr(?,1,10)) AS today,
                       (SELECT COUNT(*) FROM post p WHERE p.channel_id=c.id AND p.status='SCHEDULED') AS queued
                FROM channel c""", (now(),)).fetchall()],
            posts_by_status=[dict(r) for r in conn.execute(
                "SELECT status, COUNT(*) AS n FROM post GROUP BY status ORDER BY n DESC").fetchall()],
            publish_targets=[dict(r) for r in conn.execute("""
                SELECT pt.*, pr.name AS product_name, ch.handle AS channel_handle
                FROM publish_target pt
                JOIN post p ON p.id = pt.post_id
                JOIN product pr ON pr.id = p.product_id
                JOIN channel ch ON ch.id = pt.channel_id
                ORDER BY pt.updated_at DESC LIMIT 20""").fetchall()],
            audit=[dict(r) for r in conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT 12").fetchall()],
        )
        conn.close()
        return render_template("ops.html", page="van-hanh", **data)

    @app.route("/vanhanh/<target_id>/retry", methods=["POST"])
    def retry_publish_target_route(target_id):
        conn = connect()
        res = pipeline.retry_publish_target(conn, target_id, actor="operator")
        conn.close()
        return redirect(url_for("ops", err=None if res.get("ok") else res.get("error")))
```

(route `ops` chỉ thêm 1 key `publish_targets` vào `data`; route `retry_publish_target_route` là mới hoàn toàn, đặt ngay sau `ops`.)

- [ ] **Step 4: Thêm bảng vào `web/templates/ops.html`**

Chèn ngay sau khối "Kênh đăng bài" (sau dòng `</tbody></table></div></div>` đầu tiên, trước `<div class="grid2">`):

```html
<div class="section-heading section-heading--spaced"><div><h2>Publish targets gần đây</h2><p class="note">Mỗi lượt đăng theo từng kênh, độc lập với các lượt khác của cùng bài.</p></div></div>
{% if publish_targets %}<div class="table-card"><div class="table-scroll"><table class="data-table"><thead><tr><th>Bài</th><th>Kênh</th><th>Trạng thái</th><th>Lỗi gần nhất</th><th class="n">Lần thử</th><th></th></tr></thead><tbody>
{% for t in publish_targets %}<tr>
  <td>{{ t.product_name }}<span class="mono-sub">{{ t.id[:10] }}</span></td>
  <td>{{ t.channel_handle }}</td>
  <td><span class="tag {{ 'ok' if t.status=='SUCCESS' else ('bad' if t.status=='FAILED' else '') }}">{{ t.status }}</span></td>
  <td class="dim">{{ (t.last_error or '')[:80] }}</td>
  <td class="n dim">{{ t.attempt_count }}</td>
  <td>{% if t.status=='FAILED' %}<form method="post" action="/vanhanh/{{ t.id }}/retry"><input type="hidden" name="_csrf" value="{{ csrf_token }}"><button class="btn btn--small" type="submit">Thử lại</button></form>{% endif %}</td>
</tr>{% endfor %}
</tbody></table></div></div>{% else %}<div class="empty-state">Chưa có publish target nào.</div>{% endif %}
```

Và ngay sau dòng `<h1>Vận hành</h1>` trong `page-header`, thêm banner lỗi cùng kiểu `review.html`:

```html
{% if request.args.get('err') %}<div class="alert alert--error"><strong>Không thử lại được.</strong><span>{{ request.args.get('err') }}</span></div>{% endif %}
```

- [ ] **Step 5: Chạy lại test, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: `0 hỏng`.

- [ ] **Step 6: Commit**

```bash
git add web/server.py web/templates/ops.html tests/test_pilot.py
git commit -m "feat: show publish_target table and retry button on /vanhanh"
```

---

## Task 6: Hồi quy toàn bộ + hoàn tất

**Files:** không tạo file mới, chỉ chạy kiểm tra.

- [ ] **Step 1: Chạy toàn bộ 2 suite từ thư mục cha của repo**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: cả hai in dòng cuối `... đạt, 0 hỏng`. Nếu có test nào FAIL, dừng lại và debug trước khi qua bước sau — theo `superpowers:systematic-debugging` nếu lỗi không rõ nguyên nhân ngay.

- [ ] **Step 2: Chạy `manage.sh test` nếu môi trường release có sẵn (tùy chọn, best-effort)**

```bash
cd .. && ./acp/manage.sh test 2>&1 | tail -20
```

Nếu `manage.sh` chưa cấu hình release trong môi trường phát triển (không có `~/Downloads/ACP/releases`), bỏ qua bước này — Step 1 đã đủ để xác nhận hồi quy vì đó chính là những gì `run_release_tests` gọi.

- [ ] **Step 3: Kiểm tra không lọt secrets/runtime data**

```bash
git status --porcelain
git diff --check
```

Xác nhận không có file trong `var/`, `.env*`, hoặc DB thật nằm trong danh sách thay đổi.

- [ ] **Step 4: Commit cuối (nếu còn thay đổi chưa commit)**

```bash
git add -A
git commit -m "chore: finalize publish_target/publisher foundation (sub-project A)"
```

(Nếu Task 1-5 đã commit hết theo từng bước, task này có thể không có gì để commit — chỉ cần xác nhận `git status` sạch.)
