# MetaConnectionService & /kenh Multi-Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cho operator kết nối Meta (Facebook Login) một lần, tự động import
toàn bộ Facebook Page + Instagram Professional account có quyền quản lý, và
quản lý các account đó tại `/kenh` — không đổi hành vi Threads hiện tại.

**Architecture:** Bảng `meta_connection` mới (additive) lưu user token dùng
riêng cho discovery/sync. Bảng `channel` mở rộng thêm cột (additive) để đóng
vai trò `ChannelAccount` cho mọi platform — mỗi Page/IG account có token
riêng lưu thẳng vào `channel.token_encrypted`, publish vẫn gate qua
`channel.status`/`enabled` y hệt cơ chế `Publisher` registry của sub-project
A, không sửa `core/jobs.py`. Business logic OAuth/import/sync tách riêng vào
`core/connections.py`, web layer (`web/server.py`) chỉ gọi qua đó — đúng
pattern `core/pipeline.py` đã có.

**Tech Stack:** Python 3.14, Flask, SQLite (WAL). Test runner tự viết
(`check()`), **không dùng pytest** — chạy qua
`python -m acp.tests.test_pipeline` / `acp.tests.test_pilot`.

**Spec:** `docs/superpowers/specs/2026-08-14-meta-connection-service-design.md`

## Global Constraints

- `channel` mở rộng bằng cột mới (additive) — không đổi/xoá cột cũ, Threads
  không đổi hành vi.
- Không thêm giá trị `status` mới — dùng lại `ACTIVE`/`NEEDS_REAUTH` đã có.
- Mỗi Page/IG account có token riêng trong `channel.token_encrypted`;
  `meta_connection.token_encrypted` chỉ dùng cho discovery/sync, không dùng
  để publish.
- `app_secret` (`META_APP_SECRET`) chỉ đọc server-side từ biến môi trường,
  không bao giờ render ra client/log.
- Không sửa `core/jobs.py`.
- `MetaConnectionService` theo đúng pattern mock/live đã có
  (`ContentSource`/`Publisher`): `MockMetaConnectionService` test được ngay
  không cần mạng; `LiveMetaConnectionService` viết đúng chuẩn Graph API,
  chưa live-test được trong session này.
- Test chạy qua `ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_pipeline`
  và `acp.tests.test_pilot`, từ thư mục **cha** của repo (repo là thư mục
  tên `acp/`). Không dùng pytest.
- Không commit secrets/runtime data.

---

## Task 1: Schema — `meta_connection` + cột mới trên `channel`

**Files:**
- Modify: `core/db.py` (SCHEMA, MIGRATIONS)
- Test: `tests/test_pipeline.py`, `tests/test_pilot.py`

**Interfaces:**
- Produces: bảng `meta_connection(id, provider, token_encrypted, meta_user_id,
  status, expires_at, created_at, updated_at)`. Cột mới trên `channel`:
  `connection_id, external_account_id, username, enabled, last_sync_at`.
  Task 4 đọc/ghi các cột này.

- [ ] **Step 1: Viết test thất bại (schema)**

Thêm vào `tests/test_pipeline.py`, sau hàm `test_retry_publish_target`
(cuối file, trước `if __name__ == "__main__":`):

```python
def test_meta_connection_schema():
    print("\nmeta_connection + channel mở rộng")
    conn = connect()
    mc_cols = {r["name"] for r in conn.execute("PRAGMA table_info(meta_connection)").fetchall()}
    expected_mc = {"id", "provider", "token_encrypted", "meta_user_id", "status",
                   "expires_at", "created_at", "updated_at"}
    check("meta_connection có đủ cột", expected_mc <= mc_cols, mc_cols)

    ch_cols = {r["name"] for r in conn.execute("PRAGMA table_info(channel)").fetchall()}
    expected_ch_new = {"connection_id", "external_account_id", "username", "enabled", "last_sync_at"}
    check("channel có đủ cột mở rộng", expected_ch_new <= ch_cols, ch_cols)

    mc_id = ulid()
    conn.execute("""INSERT INTO meta_connection (id, provider, token_encrypted, meta_user_id,
                    status, created_at, updated_at) VALUES (?,'meta',?,?,'ACTIVE',?,?)""",
                 (mc_id, crypto.encrypt("user_token"), "mock_user_1", now(), now()))
    row = conn.execute("SELECT * FROM meta_connection WHERE id=?", (mc_id,)).fetchone()
    check("meta_connection lưu và đọc lại đúng", row["meta_user_id"] == "mock_user_1")

    channel = conn.execute("SELECT * FROM channel LIMIT 1").fetchone()
    check("channel cũ (Threads) có enabled mặc định 1", channel["enabled"] == 1, channel["enabled"])
    check("channel cũ có connection_id NULL", channel["connection_id"] is None)
    conn.close()
```

Thêm lời gọi `test_meta_connection_schema()` vào khối `if __name__ ==
"__main__":`, sau `test_retry_publish_target()`.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `sqlite3.OperationalError: no such table: meta_connection`.

- [ ] **Step 3: Thêm schema vào `core/db.py`**

Ngay sau khối `CREATE TABLE IF NOT EXISTS publish_target (...)` (kết thúc ở
dòng `CREATE INDEX IF NOT EXISTS idx_publish_target_status ...`), chèn:

```sql
CREATE TABLE IF NOT EXISTS meta_connection (
    id              TEXT PRIMARY KEY,
    provider        TEXT NOT NULL DEFAULT 'meta',
    token_encrypted BLOB NOT NULL,
    meta_user_id    TEXT,
    status          TEXT NOT NULL DEFAULT 'ACTIVE',
    expires_at      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
```

Thay khối `MIGRATIONS` hiện tại:

```python
MIGRATIONS = [
    # (bảng, cột, câu lệnh) -- chạy được nhiều lần, bỏ qua nếu cột đã có.
    ("channel", "niches", "ALTER TABLE channel ADD COLUMN niches TEXT NOT NULL DEFAULT '[]'"),
]
```

bằng:

```python
MIGRATIONS = [
    # (bảng, cột, câu lệnh) -- chạy được nhiều lần, bỏ qua nếu cột đã có.
    ("channel", "niches", "ALTER TABLE channel ADD COLUMN niches TEXT NOT NULL DEFAULT '[]'"),
    ("channel", "connection_id", "ALTER TABLE channel ADD COLUMN connection_id TEXT REFERENCES meta_connection(id)"),
    ("channel", "external_account_id", "ALTER TABLE channel ADD COLUMN external_account_id TEXT"),
    ("channel", "username", "ALTER TABLE channel ADD COLUMN username TEXT"),
    ("channel", "enabled", "ALTER TABLE channel ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"),
    ("channel", "last_sync_at", "ALTER TABLE channel ADD COLUMN last_sync_at TEXT"),
]
```

(Bảng `meta_connection` là bảng mới hoàn toàn nên không cần mục trong
`MIGRATIONS`; 5 cột trên `channel` là ALTER TABLE additive nên bắt buộc phải
qua `MIGRATIONS` vì `CREATE TABLE IF NOT EXISTS channel (...)` trong `SCHEMA`
không chạy lại trên DB đã tồn tại.)

- [ ] **Step 4: Chạy lại test, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `0 hỏng` (tổng số "đạt" tăng thêm đúng bằng số `check()` mới thêm ở
Step 1 — không cần khớp số tuyệt đối, chỉ cần `0 hỏng`).

- [ ] **Step 5: Thêm test migration cho DB cũ (test_pilot.py)**

Trong `tests/test_pilot.py`, tìm hàm `test_migration_adds_column` (cuối
file). Đọc nội dung hàm đó trước khi sửa — nó tạo một CSDL SQLite tối giản
tay (không qua `init_db()`), chèn 1 dòng `channel` cũ, gọi `db.migrate(c)`,
rồi kiểm tra cột `niches` được thêm và dữ liệu cũ còn nguyên. Thêm ngay sau
khối `check("cột mới có giá trị mặc định rỗng", ...)` trong đúng hàm này
(trước dòng `c.close()`):

```python
    row2 = c.execute("SELECT enabled, connection_id FROM channel").fetchone()
    check("cột enabled có giá trị mặc định 1 trên dữ liệu cũ", row2["enabled"] == 1, row2["enabled"])
    check("cột connection_id NULL trên dữ liệu cũ", row2["connection_id"] is None)
```

(Không cần gọi lại `db.migrate(c)` lần nữa — dòng 910 gốc (`applied =
db.migrate(c)`) đã áp dụng TOÀN BỘ `MIGRATIONS` trong một lượt, bao gồm 5 cột
mới của Task 1, và dòng 912 gốc (`db.migrate(c) == []`) đã chứng minh
idempotent cho cả lô. Đoạn thêm ở trên chỉ cần đọc lại giá trị cột mới trên
dữ liệu cũ, tận dụng cùng connection `c` đã có.)

- [ ] **Step 6: Chạy lại toàn bộ, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: `0 hỏng` cả hai.

- [ ] **Step 7: Commit**

```bash
git add core/db.py tests/test_pipeline.py tests/test_pilot.py
git commit -m "feat: add meta_connection table and channel account columns"
```

---

## Task 2: `MetaConnectionService` interface + Mock

**Files:**
- Modify: `adapters/base.py` (dataclasses + interface)
- Modify: `adapters/mock.py` (`MockMetaConnectionService`)
- Test: `tests/test_pilot.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class ExchangedToken:
      token: str
      expires_in: int
      meta_user_id: str

  @dataclass
  class PageInfo:
      external_account_id: str
      name: str
      page_token: str

  @dataclass
  class InstagramInfo:
      external_account_id: str
      username: str
      page_token: str

  class MetaConnectionService:
      def oauth_authorize_url(self, state: str, redirect_uri: str) -> str: ...
      def exchange_code(self, code: str, redirect_uri: str) -> ExchangedToken: ...
      def list_pages(self, user_token: str) -> list: ...          # list[PageInfo]
      def instagram_for_page(self, page_id: str, page_token: str): ...  # InstagramInfo | None
  ```
  Task 3 dùng interface này cho bản live; Task 4 dùng qua `ctx`/factory.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `tests/test_pilot.py`, ngay sau hàm `test_factory` (trước dòng
`# --------------------------------------------------------- single product`):

```python
def test_mock_meta_connection_service():
    print("\nMetaConnectionService (mock)")
    from acp.adapters.base import MetaConnectionService
    from acp.adapters.mock import MockMetaConnectionService

    svc = MockMetaConnectionService()
    check("là MetaConnectionService", isinstance(svc, MetaConnectionService))

    url = svc.oauth_authorize_url("state123", "https://acp.example/oauth/meta/callback")
    check("authorize URL chứa state", "state123" in url, url)
    check("authorize URL chứa redirect_uri", "acp.example" in url, url)

    exchanged = svc.exchange_code("fake-code", "https://acp.example/oauth/meta/callback")
    check("exchange_code trả token", bool(exchanged.token))
    check("exchange_code trả meta_user_id ổn định", exchanged.meta_user_id == "mock_meta_user_1",
          exchanged.meta_user_id)

    pages = svc.list_pages(exchanged.token)
    check("mock trả đúng 2 Page", len(pages) == 2, len(pages))
    check("Page có page_token", all(p.page_token for p in pages))

    ig = svc.instagram_for_page(pages[0].external_account_id, pages[0].page_token)
    check("Page đầu có Instagram gắn kèm", ig is not None and ig.username)
    ig2 = svc.instagram_for_page(pages[1].external_account_id, pages[1].page_token)
    check("Page thứ hai không có Instagram", ig2 is None)
```

Gọi thêm `test_mock_meta_connection_service()` trong khối `__main__`, ngay
sau `test_factory()`.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: `ImportError: cannot import name 'MetaConnectionService'`.

- [ ] **Step 3: Thêm interface vào `adapters/base.py`**

Thêm vào cuối file (sau class `Publisher`):

```python
@dataclass
class ExchangedToken:
    """Kết quả đổi authorization code lấy user access token."""
    token: str
    expires_in: int
    meta_user_id: str


@dataclass
class PageInfo:
    """Một Facebook Page mà user đang đăng nhập quản lý."""
    external_account_id: str
    name: str
    page_token: str


@dataclass
class InstagramInfo:
    """Instagram Professional account gắn với một Page."""
    external_account_id: str
    username: str
    page_token: str  # IG Graph API dùng chung Page token, không có token riêng


class MetaConnectionService:
    """OAuth + account discovery cho Facebook Page / Instagram Professional.

    Tách khỏi Publisher vì đây là bước KẾT NỐI (một lần, ra danh sách account),
    không phải bước ĐĂNG BÀI (mỗi account một Publisher riêng, xem
    FacebookPublisher/InstagramPublisher ở sub-project C).
    """

    def oauth_authorize_url(self, state: str, redirect_uri: str) -> str:
        raise NotImplementedError

    def exchange_code(self, code: str, redirect_uri: str) -> ExchangedToken:
        raise NotImplementedError

    def list_pages(self, user_token: str) -> list:
        raise NotImplementedError

    def instagram_for_page(self, page_id: str, page_token: str):
        raise NotImplementedError
```

- [ ] **Step 4: Thêm `MockMetaConnectionService` vào `adapters/mock.py`**

Thêm vào cuối `adapters/mock.py` (sau class `MockThreads`, trước hàm
`simulate_postbacks`). Trước tiên đổi import ở đầu file (dòng 13-16) từ:

```python
from .base import (
    ContentSource, Publisher, RawProduct, PublishResult,
    PublishError, RateLimitError, ContentViolationError,
)
```

thành:

```python
from .base import (
    ContentSource, Publisher, RawProduct, PublishResult,
    PublishError, RateLimitError, ContentViolationError,
    MetaConnectionService, ExchangedToken, PageInfo, InstagramInfo,
)
```

Rồi thêm class:

```python
class MockMetaConnectionService(MetaConnectionService):
    """Fixture cố định: 2 Page giả, Page đầu có 1 Instagram gắn kèm. Không cần
    mạng, dùng cho dev/test giống MockThreads/MockAccessTrade."""

    def oauth_authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"https://mock.meta.example/oauth/authorize?state={state}&redirect_uri={redirect_uri}"

    def exchange_code(self, code: str, redirect_uri: str) -> ExchangedToken:
        return ExchangedToken(token=f"mock_user_token_{code}", expires_in=5184000,
                               meta_user_id="mock_meta_user_1")

    def list_pages(self, user_token: str) -> list:
        return [
            PageInfo(external_account_id="1000000000001", name="Fashion Page Test",
                     page_token="mock_page_token_1"),
            PageInfo(external_account_id="1000000000002", name="Tech Deals Test",
                     page_token="mock_page_token_2"),
        ]

    def instagram_for_page(self, page_id: str, page_token: str):
        if page_id == "1000000000001":
            return InstagramInfo(external_account_id="1700000000001",
                                  username="test.fashion", page_token=page_token)
        return None
```

- [ ] **Step 5: Chạy lại test, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: `0 hỏng`.

- [ ] **Step 6: Commit**

```bash
git add adapters/base.py adapters/mock.py tests/test_pilot.py
git commit -m "feat: add MetaConnectionService interface and mock implementation"
```

---

## Task 3: `LiveMetaConnectionService` + đăng ký factory

**Files:**
- Modify: `adapters/live.py`
- Modify: `adapters/factory.py`
- Test: `tests/test_pilot.py`

**Interfaces:**
- Consumes: `MetaConnectionService`, `ExchangedToken`, `PageInfo`,
  `InstagramInfo` (Task 2).
- Produces: `factory.get_meta_connection_service() -> MetaConnectionService`
  (mock hoặc live theo `ACP_ADAPTER`). Task 4/5 dùng hàm này.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `tests/test_pilot.py`, ngay sau `test_mock_meta_connection_service`:

```python
def test_live_meta_connection_service_url_building():
    print("\nLiveMetaConnectionService (không cần mạng)")
    import os as _os
    from acp.adapters.live import LiveMetaConnectionService

    old_id, old_secret = _os.environ.get("META_APP_ID"), _os.environ.get("META_APP_SECRET")
    _os.environ["META_APP_ID"] = "test_app_id"
    _os.environ["META_APP_SECRET"] = "test_app_secret"
    try:
        svc = LiveMetaConnectionService()
        url = svc.oauth_authorize_url("state456", "https://acp.example/oauth/meta/callback")
        check("authorize URL đúng host Meta", "facebook.com" in url, url)
        check("authorize URL chứa client_id", "test_app_id" in url, url)
        check("authorize URL chứa state", "state456" in url, url)
        check("authorize URL chứa quyền pages_show_list", "pages_show_list" in url, url)
        check("authorize URL chứa quyền instagram_basic", "instagram_basic" in url, url)
        check("app_secret KHÔNG lộ trong authorize URL", "test_app_secret" not in url, url)
    finally:
        if old_id is None:
            _os.environ.pop("META_APP_ID", None)
        else:
            _os.environ["META_APP_ID"] = old_id
        if old_secret is None:
            _os.environ.pop("META_APP_SECRET", None)
        else:
            _os.environ["META_APP_SECRET"] = old_secret


def test_factory_meta_connection_service():
    print("\nFactory chọn MetaConnectionService")
    from acp.adapters.base import MetaConnectionService
    from acp.adapters.mock import MockMetaConnectionService
    factory.reset_cache()
    os.environ.pop("ACP_ADAPTER", None)
    svc = factory.get_meta_connection_service()
    check("mặc định trả về mock", isinstance(svc, MockMetaConnectionService))
    check("là MetaConnectionService", isinstance(svc, MetaConnectionService))
```

Gọi thêm cả hai hàm trong khối `__main__`, ngay sau
`test_mock_meta_connection_service()`.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: `ImportError: cannot import name 'LiveMetaConnectionService'`.

- [ ] **Step 3: Thêm `LiveMetaConnectionService` vào `adapters/live.py`**

Đổi import ở đầu file (dòng 20-23) từ:

```python
from .base import (
    ContentSource, Publisher, RawProduct, PublishResult,
    PublishError, RateLimitError, ContentViolationError, AuthError,
)
```

thành:

```python
from .base import (
    ContentSource, Publisher, RawProduct, PublishResult,
    PublishError, RateLimitError, ContentViolationError, AuthError,
    MetaConnectionService, ExchangedToken, PageInfo, InstagramInfo,
)
```

Thêm hằng số ngay dưới `THREADS_BASE = "https://graph.threads.net/v1.0"`:

```python
FACEBOOK_OAUTH_BASE = "https://www.facebook.com/v19.0/dialog/oauth"
GRAPH_BASE = "https://graph.facebook.com/v19.0"
META_OAUTH_SCOPES = "pages_show_list,pages_read_engagement,instagram_basic,business_management"
```

Thêm class vào cuối file:

```python
class LiveMetaConnectionService(MetaConnectionService):
    """Gọi Graph API thật. App ID/Secret đọc từ META_APP_ID/META_APP_SECRET --
    app_secret KHÔNG bao giờ đưa vào authorize URL (đó là bước redirect trình
    duyệt, ai cũng xem được URL), chỉ dùng ở bước exchange_code server-side."""

    def __init__(self):
        self.app_id = os.environ.get("META_APP_ID", "")
        self.app_secret = os.environ.get("META_APP_SECRET", "")
        self.session = requests.Session()

    def oauth_authorize_url(self, state: str, redirect_uri: str) -> str:
        params = {
            "client_id": self.app_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": META_OAUTH_SCOPES,
            "response_type": "code",
        }
        return f"{FACEBOOK_OAUTH_BASE}?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str) -> ExchangedToken:
        r = self.session.get(f"{GRAPH_BASE}/oauth/access_token", params={
            "client_id": self.app_id, "client_secret": self.app_secret,
            "redirect_uri": redirect_uri, "code": code,
        }, timeout=20)
        if r.status_code >= 400:
            raise AuthError(f"Đổi code lấy token thất bại: {r.text[:200]}")
        body = r.json()
        me = self.session.get(f"{GRAPH_BASE}/me", params={
            "access_token": body["access_token"], "fields": "id"}, timeout=20)
        me.raise_for_status()
        return ExchangedToken(token=body["access_token"], expires_in=body.get("expires_in", 0),
                               meta_user_id=me.json()["id"])

    def list_pages(self, user_token: str) -> list:
        r = self.session.get(f"{GRAPH_BASE}/me/accounts", params={
            "access_token": user_token, "fields": "id,name,access_token"}, timeout=20)
        if r.status_code in (401, 403):
            raise AuthError("Token Meta bị từ chối khi liệt kê Page")
        r.raise_for_status()
        return [PageInfo(external_account_id=p["id"], name=p["name"], page_token=p["access_token"])
                for p in r.json().get("data", [])]

    def instagram_for_page(self, page_id: str, page_token: str):
        r = self.session.get(f"{GRAPH_BASE}/{page_id}", params={
            "access_token": page_token, "fields": "instagram_business_account{id,username}"},
            timeout=20)
        if r.status_code >= 400:
            return None
        ig = r.json().get("instagram_business_account")
        if not ig:
            return None
        return InstagramInfo(external_account_id=ig["id"], username=ig.get("username", ""),
                              page_token=page_token)
```

- [ ] **Step 4: Thêm `get_meta_connection_service()` vào `adapters/factory.py`**

Ngay sau hàm `get_publishers()`:

```python
def get_meta_connection_service():
    if is_live():
        from .live import LiveMetaConnectionService
        return LiveMetaConnectionService()
    from .mock import MockMetaConnectionService
    return MockMetaConnectionService()
```

- [ ] **Step 5: Chạy lại test, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: `0 hỏng`.

- [ ] **Step 6: Commit**

```bash
git add adapters/live.py adapters/factory.py tests/test_pilot.py
git commit -m "feat: add LiveMetaConnectionService and factory wiring"
```

---

## Task 4: `core/connections.py` — logic kết nối/import/sync

**Files:**
- Create: `core/connections.py`
- Test: `tests/test_pilot.py`

**Interfaces:**
- Consumes: `MetaConnectionService`/`PageInfo`/`InstagramInfo`/`ExchangedToken`
  (Task 2/3), `crypto.encrypt`/`crypto.decrypt` (đã có), `db.ulid`/`db.now`/
  `db.audit` (đã có).
- Produces:
  ```python
  def connect_meta_account(conn, service, code: str, redirect_uri: str,
                            actor: str = "operator") -> dict: ...
      # {"ok": True, "connection_id": str, "imported": int, "updated": int,
      #  "reconnect_required": int} hoặc {"ok": False, "error": str}

  def sync_meta_accounts(conn, service, connection_id: str,
                          actor: str = "operator") -> dict: ...
      # cùng shape kết quả như trên (trừ connection_id không lặp lại)
  ```
  Task 5 (web routes) gọi hai hàm này.

- [ ] **Step 1: Viết test thất bại**

Tạo `tests/test_pilot.py` — thêm vào cuối file (trước khối `if __name__ ==
"__main__":`), một hàm test mới:

```python
class _FixedMetaService:
    """Fixture riêng cho test này (KHÔNG dùng MockMetaConnectionService mặc
    định) -- test_oauth_meta_routes ở Task 5 đi qua factory.get_meta_connection_service()
    và tạo account bằng fixture mặc định của MockMetaConnectionService trong
    CÙNG một CSDL tạm dùng chung cho cả file test_pilot.py; nếu test này dùng
    chung fixture đó, ai chạy trước sẽ khiến người chạy sau thấy 'đã tồn tại'
    thay vì 'mới import'. Dùng meta_user_id/external_account_id RIÊNG để không
    bao giờ đụng fixture mặc định, và luôn lọc theo connection_id của CHÍNH
    lần import này thay vì đếm toàn bảng -- đúng quy ước test_daily_cap đã có
    trong test_pipeline.py (tính theo số hiện có/delta, không đặt cứng)."""

    def oauth_authorize_url(self, state, redirect_uri):
        return f"https://mock/authorize?state={state}"

    def exchange_code(self, code, redirect_uri):
        from acp.adapters.base import ExchangedToken
        return ExchangedToken(token=f"tok_{code}", expires_in=5184000, meta_user_id="test4_user")

    def list_pages(self, user_token):
        from acp.adapters.base import PageInfo
        return [
            PageInfo("9000000000001", "Fashion Page Test", "tok_page_1"),
            PageInfo("9000000000002", "Tech Deals Test", "tok_page_2"),
        ]

    def instagram_for_page(self, page_id, page_token):
        from acp.adapters.base import InstagramInfo
        if page_id == "9000000000001":
            return InstagramInfo("9700000000001", "test.fashion", page_token)
        return None


def test_meta_account_import_and_sync():
    print("\nImport + đồng bộ account Meta")
    from acp.core import connections

    conn = connect()
    svc = _FixedMetaService()

    res = connections.connect_meta_account(conn, svc, "fake-code",
                                            "https://acp.example/oauth/meta/callback")
    check("connect_meta_account thành công", res.get("ok"), res)
    check("import đúng 3 account (2 Page + 1 IG)", res["imported"] == 3, res)
    check("lần đầu không có account cần cập nhật", res["updated"] == 0, res)
    connection_id = res["connection_id"]

    fb_rows = conn.execute(
        "SELECT * FROM channel WHERE platform='facebook' AND connection_id=?", (connection_id,)).fetchall()
    check("có 2 kênh facebook thuộc đúng connection này", len(fb_rows) == 2, len(fb_rows))
    ig_rows = conn.execute(
        "SELECT * FROM channel WHERE platform='instagram' AND connection_id=?", (connection_id,)).fetchall()
    check("có 1 kênh instagram thuộc đúng connection này", len(ig_rows) == 1, len(ig_rows))
    check("kênh instagram có username", ig_rows[0]["username"] == "test.fashion", dict(ig_rows[0]))
    check("kênh facebook có external_account_id", fb_rows[0]["external_account_id"])
    check("kênh facebook có token riêng, không rỗng", fb_rows[0]["token_encrypted"])
    check("kênh mới enabled=1", fb_rows[0]["enabled"] == 1)
    check("kênh mới status=ACTIVE", fb_rows[0]["status"] == "ACTIVE")

    connection = conn.execute("SELECT * FROM meta_connection WHERE meta_user_id=?",
                              ("test4_user",)).fetchone()
    check("tạo đúng 1 meta_connection", connection is not None and connection["id"] == connection_id)

    # Đồng bộ lại không được tạo trùng.
    res2 = connections.sync_meta_accounts(conn, svc, connection_id)
    check("sync lại không tạo account mới", res2["imported"] == 0, res2)
    total_channels = conn.execute(
        "SELECT COUNT(*) FROM channel WHERE connection_id=?", (connection_id,)).fetchone()[0]
    check("tổng số kênh thuộc connection không đổi sau sync", total_channels == 3, total_channels)

    # Kết nối lại bằng đúng meta_user_id không tạo connection thứ hai.
    res3 = connections.connect_meta_account(conn, svc, "fake-code-2",
                                             "https://acp.example/oauth/meta/callback")
    check("kết nối lại cùng user không tạo connection trùng", res3["connection_id"] == connection_id, res3)
    n_conn = conn.execute("SELECT COUNT(*) FROM meta_connection WHERE meta_user_id=?",
                          ("test4_user",)).fetchone()[0]
    check("chỉ có đúng 1 meta_connection cho user này", n_conn == 1, n_conn)

    conn.close()


def test_meta_sync_marks_vanished_account_reconnect_required():
    print("\nSync đánh dấu account mất quyền, không xoá")
    from acp.core import connections

    class _ShrinkingMetaService:
        """Lần đầu trả 2 Page, lần sau chỉ còn 1 -- mô phỏng operator gỡ quyền
        Page thứ hai trên Meta."""
        def __init__(self):
            self.calls = 0

        def oauth_authorize_url(self, state, redirect_uri):
            return "https://mock/x"

        def exchange_code(self, code, redirect_uri):
            from acp.adapters.base import ExchangedToken
            return ExchangedToken(token="tok", expires_in=1000, meta_user_id="shrink_user")

        def list_pages(self, user_token):
            from acp.adapters.base import PageInfo
            self.calls += 1
            if self.calls == 1:
                return [PageInfo("2000000000001", "Page A", "tok_a"),
                        PageInfo("2000000000002", "Page B", "tok_b")]
            return [PageInfo("2000000000001", "Page A", "tok_a")]

        def instagram_for_page(self, page_id, page_token):
            return None

    conn = connect()
    svc = _ShrinkingMetaService()
    res = connections.connect_meta_account(conn, svc, "code", "https://acp.example/oauth/meta/callback")
    check("import lần đầu 2 Page", res["imported"] == 2, res)

    res2 = connections.sync_meta_accounts(conn, svc, res["connection_id"])
    check("sync phát hiện 1 account mất quyền", res2["reconnect_required"] == 1, res2)

    page_a = conn.execute("SELECT status FROM channel WHERE external_account_id=?",
                          ("2000000000001",)).fetchone()
    page_b = conn.execute("SELECT status FROM channel WHERE external_account_id=?",
                          ("2000000000002",)).fetchone()
    check("Page còn quyền vẫn ACTIVE", page_a["status"] == "ACTIVE", page_a["status"])
    check("Page mất quyền chuyển NEEDS_REAUTH", page_b["status"] == "NEEDS_REAUTH", page_b["status"])
    check("Page mất quyền KHÔNG bị xoá", page_b is not None)
    conn.close()
```

Gọi cả hai hàm trong khối `__main__`, sau `test_production_guard()` (cuối
danh sách gọi hiện có).

- [ ] **Step 2: Chạy test, xác nhận thất bại**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: `ModuleNotFoundError: No module named 'acp.core.connections'`.

- [ ] **Step 3: Viết `core/connections.py`**

```python
"""Kết nối Meta (Facebook Login) + import/đồng bộ Facebook Page và Instagram
Professional account (PTYC §5, §20-24). Tách khỏi core/pipeline.py vì đây là
mối quan tâm khác: quản lý ACCOUNT có thể publish, không phải nội dung/luồng
publish của một post cụ thể.

Mỗi Page/IG account nhận một channel row RIÊNG với token RIÊNG
(channel.token_encrypted) -- đúng cách Publisher/publish_post đã tiêu thụ
channel từ sub-project A, không cần sửa gì ở đó. meta_connection.token_encrypted
(user token) chỉ dùng để chạy lại discovery ở đây, không dùng để publish.
"""
from .crypto import encrypt
from .db import audit, now, ulid


def _upsert_meta_connection(conn, exchanged) -> str:
    row = conn.execute("SELECT id FROM meta_connection WHERE meta_user_id=?",
                       (exchanged.meta_user_id,)).fetchone()
    if row:
        conn.execute("""UPDATE meta_connection SET token_encrypted=?, status='ACTIVE',
                        updated_at=? WHERE id=?""",
                     (encrypt(exchanged.token), now(), row["id"]))
        return row["id"]
    connection_id = ulid()
    conn.execute("""INSERT INTO meta_connection (id, provider, token_encrypted, meta_user_id,
                    status, created_at, updated_at) VALUES (?,'meta',?,?,'ACTIVE',?,?)""",
                 (connection_id, encrypt(exchanged.token), exchanged.meta_user_id, now(), now()))
    audit(conn, "meta_connection", connection_id, "connected",
          detail={"meta_user_id": exchanged.meta_user_id})
    return connection_id


def _upsert_channel_account(conn, *, connection_id: str, platform: str,
                            external_account_id: str, handle: str, username: str,
                            page_token: str) -> bool:
    """Trả True nếu đây là account MỚI (chưa từng thấy), False nếu là cập nhật
    account đã có. Khớp theo (platform, external_account_id) -- khoá tự nhiên
    của một Page/IG account trên Meta, không đổi qua các lần sync."""
    existing = conn.execute(
        "SELECT id FROM channel WHERE platform=? AND external_account_id=?",
        (platform, external_account_id)).fetchone()
    if existing:
        conn.execute("""UPDATE channel SET handle=?, username=?, token_encrypted=?,
                        status='ACTIVE', connection_id=?, last_sync_at=? WHERE id=?""",
                     (handle, username, encrypt(page_token), connection_id, now(), existing["id"]))
        return False
    code = f"{platform}_{external_account_id}"
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, token_encrypted,
                    connection_id, external_account_id, username, enabled, last_sync_at, created_at)
                    VALUES (?,?,?,?,'ACTIVE',?,?,?,?,1,?,?)""",
                 (ulid(), code, platform, handle, encrypt(page_token),
                  connection_id, external_account_id, username, now(), now()))
    return True


def sync_meta_accounts(conn, service, connection_id: str, actor: str = "operator") -> dict:
    """Chạy lại discovery cho một connection đã có -- dùng cho cả lần import
    đầu tiên (gọi từ connect_meta_account) lẫn nút "Đồng bộ lại" thủ công.
    Upsert theo (platform, external_account_id), không tạo trùng. Account
    trước đây thuộc connection này mà lần này Meta không còn trả về ->
    NEEDS_REAUTH, KHÔNG xoá (giữ lịch sử post/job)."""
    connection = conn.execute("SELECT * FROM meta_connection WHERE id=?", (connection_id,)).fetchone()
    if not connection:
        return {"ok": False, "error": "Không tìm thấy kết nối Meta"}

    from .crypto import decrypt
    user_token = decrypt(connection["token_encrypted"])
    pages = service.list_pages(user_token)

    seen_account_ids = []
    imported, updated = 0, 0
    for page in pages:
        is_new = _upsert_channel_account(
            conn, connection_id=connection_id, platform="facebook",
            external_account_id=page.external_account_id, handle=page.name,
            username=None, page_token=page.page_token)
        imported += int(is_new)
        updated += int(not is_new)
        seen_account_ids.append(page.external_account_id)

        ig = service.instagram_for_page(page.external_account_id, page.page_token)
        if ig:
            is_new_ig = _upsert_channel_account(
                conn, connection_id=connection_id, platform="instagram",
                external_account_id=ig.external_account_id, handle=f"@{ig.username}",
                username=ig.username, page_token=ig.page_token)
            imported += int(is_new_ig)
            updated += int(not is_new_ig)
            seen_account_ids.append(ig.external_account_id)

    reconnect_required = 0
    previously_seen = conn.execute(
        "SELECT id, external_account_id FROM channel WHERE connection_id=? AND status='ACTIVE'",
        (connection_id,)).fetchall()
    for row in previously_seen:
        if row["external_account_id"] and row["external_account_id"] not in seen_account_ids:
            conn.execute("UPDATE channel SET status='NEEDS_REAUTH', last_sync_at=? WHERE id=?",
                         (now(), row["id"]))
            reconnect_required += 1

    audit(conn, "meta_connection", connection_id, "synced", actor=actor,
          detail={"imported": imported, "updated": updated, "reconnect_required": reconnect_required})
    return {"ok": True, "connection_id": connection_id, "imported": imported,
            "updated": updated, "reconnect_required": reconnect_required}


def connect_meta_account(conn, service, code: str, redirect_uri: str,
                         actor: str = "operator") -> dict:
    """Điểm vào từ OAuth callback: đổi code lấy token, upsert connection,
    chạy discovery+sync ngay. Kết nối lại đúng tài khoản Meta đã có (khớp
    meta_user_id) không tạo connection thứ hai."""
    exchanged = service.exchange_code(code, redirect_uri)
    connection_id = _upsert_meta_connection(conn, exchanged)
    return sync_meta_accounts(conn, service, connection_id, actor=actor)
```

- [ ] **Step 4: Chạy lại test, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: `0 hỏng`.

- [ ] **Step 5: Commit**

```bash
git add core/connections.py tests/test_pilot.py
git commit -m "feat: add core/connections.py for Meta account import and sync"
```

---

## Task 5: Web routes — OAuth Meta + Đồng bộ lại

**Files:**
- Modify: `web/server.py`
- Test: `tests/test_pilot.py`

**Interfaces:**
- Consumes: `connections.connect_meta_account`, `connections.sync_meta_accounts`
  (Task 4), `factory.get_meta_connection_service()` (Task 3).

- [ ] **Step 1: Viết test thất bại**

Thêm vào `tests/test_pilot.py`, ngay sau `test_publish_target_retry_route`
(nếu tồn tại từ sub-project A) hoặc ngay trước `test_production_guard`:

```python
def test_oauth_meta_routes():
    print("\nRoute OAuth Meta")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()

    check("/oauth/meta/start yêu cầu đăng nhập",
          c.get("/oauth/meta/start", follow_redirects=False).status_code == 302)

    c.post("/dangnhap", data={"password": "matkhau-test"})
    start = c.get("/oauth/meta/start", follow_redirects=False)
    check("start redirect sang Meta", start.status_code == 302, start.status_code)
    check("start redirect chứa state", "state=" in start.location, start.location)
    with c.session_transaction() as sess:
        check("state được lưu vào session", bool(sess.get("meta_oauth_state")))
        real_state = sess["meta_oauth_state"]

    bad = c.get(f"/oauth/meta/callback?code=abc&state=sai-state", follow_redirects=False)
    check("callback state sai bị từ chối", bad.status_code == 400, bad.status_code)

    ok = c.get(f"/oauth/meta/callback?code=abc&state={real_state}", follow_redirects=False)
    check("callback state đúng thành công, redirect /kenh", ok.status_code == 302 and "/kenh" in ok.location,
          (ok.status_code, ok.location))

    conn = connect()
    n_channels = conn.execute("SELECT COUNT(*) FROM channel WHERE platform IN ('facebook','instagram')").fetchone()[0]
    check("import được account qua route thật", n_channels == 3, n_channels)
    connection = conn.execute("SELECT id FROM meta_connection LIMIT 1").fetchone()
    conn.close()

    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    sync = c.post("/kenh/meta/sync", data={"_csrf": csrf})
    check("đồng bộ lại thành công, redirect /kenh", sync.status_code == 302 and "/kenh" in sync.location,
          (sync.status_code, sync.location))

    no_csrf = c.post("/kenh/meta/sync", data={})
    check("đồng bộ thiếu CSRF bị chặn", no_csrf.status_code == 400, no_csrf.status_code)

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)
```

Gọi thêm `test_oauth_meta_routes()` trong khối `__main__`, ngay trước
`test_production_guard()`.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: `404 NOT FOUND` cho `/oauth/meta/start`.

- [ ] **Step 3: Thêm routes vào `web/server.py`**

Thêm import ở đầu file, sau dòng `from ..core import attribution, jobs,
pipeline, scoring, storage`:

```python
from ..core import connections
```

Thêm route ngay sau block `# ---------------------------------------------- OAuth
Threads (công khai)` kết thúc (sau `oauth_delete_status`, trước
`@app.route("/api/funnel")`):

```python
    # ------------------------------------------------ OAuth Meta (Facebook/IG)

    @app.route("/oauth/meta/start")
    def oauth_meta_start():
        """Bắt đầu Facebook Login. Khác Threads: route này và callback đều bắt
        buộc đăng nhập ACP trước, dù nằm dưới prefix /oauth/ công khai --
        kiểm tra thủ công ở đây vì đây là hành động quản trị (thêm account có
        thể publish), không phải webhook/redirect không mang session như
        Threads deauthorize."""
        if admin_password and not session.get("uid"):
            return redirect(url_for("login", next="/oauth/meta/start"))
        state = secrets.token_urlsafe(24)
        session["meta_oauth_state"] = state
        redirect_uri = request.host_url.rstrip("/") + "/oauth/meta/callback"
        svc = factory.get_meta_connection_service()
        return redirect(svc.oauth_authorize_url(state, redirect_uri))

    @app.route("/oauth/meta/callback")
    def oauth_meta_callback():
        if admin_password and not session.get("uid"):
            return redirect(url_for("login", next="/oauth/meta/start"))
        err = request.args.get("error_description") or request.args.get("error")
        if err:
            return redirect(url_for("channels", err=err))
        code = request.args.get("code", "")
        state = request.args.get("state", "")
        expected = session.get("meta_oauth_state", "")
        if not code or not state or not expected or not hmac.compare_digest(state, expected):
            abort(400, "State OAuth không hợp lệ")
        session.pop("meta_oauth_state", None)

        redirect_uri = request.host_url.rstrip("/") + "/oauth/meta/callback"
        svc = factory.get_meta_connection_service()
        conn = connect()
        res = connections.connect_meta_account(conn, svc, code, redirect_uri, actor="operator")
        conn.close()
        if not res.get("ok"):
            return redirect(url_for("channels", err=res.get("error")))
        return redirect(url_for("channels",
                                summary=f"Đã import {res['imported']} account, cập nhật {res['updated']}"))

    @app.route("/kenh/meta/sync", methods=["POST"])
    def kenh_meta_sync():
        conn = connect()
        connection = conn.execute("SELECT id FROM meta_connection ORDER BY created_at DESC LIMIT 1").fetchone()
        if not connection:
            conn.close()
            return redirect(url_for("channels", err="Chưa kết nối Meta"))
        svc = factory.get_meta_connection_service()
        res = connections.sync_meta_accounts(conn, svc, connection["id"], actor="operator")
        conn.close()
        return redirect(url_for("channels", err=None if res.get("ok") else res.get("error")))
```

`admin_password` và `session`/`hmac`/`secrets` đã có sẵn trong scope của
`create_app()` (đọc ở đầu hàm và import ở đầu file) — không cần import
thêm.

- [ ] **Step 4: Chạy lại test, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: `0 hỏng`.

- [ ] **Step 5: Commit**

```bash
git add web/server.py tests/test_pilot.py
git commit -m "feat: add /oauth/meta and /kenh/meta/sync routes"
```

---

## Task 6: `/kenh` UI đa nền tảng + toggle enable/disable

**Files:**
- Modify: `web/server.py` (route `channels()`, route mới enable/disable)
- Modify: `web/templates/channels.html`
- Test: `tests/test_pilot.py`

**Interfaces:**
- Consumes: cột `channel.enabled`/`connection_id`/`username`/`last_sync_at`
  (Task 1).

- [ ] **Step 1: Viết test thất bại**

Thêm vào `tests/test_pilot.py`, ngay sau `test_oauth_meta_routes`:

```python
def test_channel_enable_disable_route():
    print("\nRoute bật/tắt kênh")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    ch = conn.execute("SELECT id FROM channel LIMIT 1").fetchone()
    conn.close()

    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    r = c.post(f"/kenh/{ch['id']}/disable", data={"_csrf": csrf})
    check("tắt kênh thành công", r.status_code == 302, r.status_code)
    conn = connect()
    row = conn.execute("SELECT enabled FROM channel WHERE id=?", (ch["id"],)).fetchone()
    check("kênh đã tắt (enabled=0)", row["enabled"] == 0, row["enabled"])
    conn.close()

    r2 = c.post(f"/kenh/{ch['id']}/enable", data={"_csrf": csrf})
    check("bật lại kênh thành công", r2.status_code == 302, r2.status_code)
    conn = connect()
    row2 = conn.execute("SELECT enabled FROM channel WHERE id=?", (ch["id"],)).fetchone()
    check("kênh đã bật lại (enabled=1)", row2["enabled"] == 1, row2["enabled"])
    conn.close()

    r3 = c.post("/kenh/khong-ton-tai/disable", data={"_csrf": csrf})
    check("tắt kênh không tồn tại vẫn redirect, không sập trang", r3.status_code == 302, r3.status_code)

    page = c.get("/kenh")
    check("trang /kenh vẫn render 200 sau các thao tác trên", page.status_code == 200, page.status_code)

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)
```

Gọi `test_channel_enable_disable_route()` trong khối `__main__`, ngay sau
`test_oauth_meta_routes()`.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: `404 NOT FOUND` cho `/kenh/<id>/disable`.

- [ ] **Step 3: Sửa route `channels()` trong `web/server.py`**

Thay toàn bộ route `channels()` hiện tại (từ `@app.route("/kenh", ...)` tới
hết hàm, trước comment `# ----------------------------------------------------------- duyệt bài`):

```python
    @app.route("/kenh", methods=["GET", "POST"])
    def channels():
        """Mỗi kênh một ngách. Đổi bất cứ lúc nào, không ảnh hưởng bài đã đăng."""
        from ..core import niche as niche_mod
        conn = connect()
        saved = None
        if request.method == "POST":
            cid = request.form.get("channel_id", "")
            applied = pipeline.set_channel_niches(conn, cid, request.form.getlist("niches"))
            row = conn.execute("SELECT handle FROM channel WHERE id=?", (cid,)).fetchone()
            saved = row["handle"] if row else cid

        rows = []
        for ch in conn.execute("SELECT * FROM channel ORDER BY platform, code").fetchall():
            nl = pipeline.channel_niches(conn, ch["id"])
            rows.append(dict(ch, niches=nl,
                             pool=len(scoring.score_candidates(conn, limit=9999, niches=nl)),
                             published=conn.execute(
                                 "SELECT COUNT(*) FROM post WHERE channel_id=? AND status='PUBLISHED'",
                                 (ch["id"],)).fetchone()[0]))
        by_platform = {}
        for row in rows:
            by_platform.setdefault(row["platform"], []).append(row)
        has_meta_connection = bool(conn.execute("SELECT 1 FROM meta_connection LIMIT 1").fetchone())
        pending = conn.execute(
            "SELECT COUNT(*) FROM post WHERE status IN ('PENDING_REVIEW','DRAFT')").fetchone()[0]
        conn.close()
        return render_template("channels.html", page="kenh", by_platform=by_platform,
                               all_niches=niche_mod.NICHES, saved=saved, pending_review=pending,
                               has_meta_connection=has_meta_connection,
                               summary=request.args.get("summary"))

    @app.route("/kenh/<channel_id>/enable", methods=["POST"])
    def channel_enable(channel_id):
        conn = connect()
        conn.execute("UPDATE channel SET enabled=1 WHERE id=?", (channel_id,))
        pipeline.audit(conn, "channel", channel_id, "enabled", actor="operator")
        conn.close()
        return redirect(url_for("channels"))

    @app.route("/kenh/<channel_id>/disable", methods=["POST"])
    def channel_disable(channel_id):
        conn = connect()
        conn.execute("UPDATE channel SET enabled=0 WHERE id=?", (channel_id,))
        pipeline.audit(conn, "channel", channel_id, "disabled", actor="operator")
        conn.close()
        return redirect(url_for("channels"))
```

(`pipeline.audit` — kiểm tra `core/pipeline.py` đã `from .db import audit,
now, ulid` ở đầu file nên `pipeline.audit` gọi được thẳng qua module; nếu
IDE/linter báo thiếu, dùng `from ..core.db import audit as db_audit` ở đầu
`web/server.py` thay vì `pipeline.audit` — cách nào cũng được, chọn cách
nhất quán với phần còn lại của file.)

- [ ] **Step 4: Sửa `web/templates/channels.html`**

Thay toàn bộ nội dung file bằng:

```html
{% extends "base.html" %}
{% block title %}Kênh — ACP{% endblock %}
{% block content %}
<div class="page-header">
  <div><div class="eyebrow">Channel routing</div><h1>Kênh</h1><p class="lede">Quản lý account theo nền tảng. Bật/tắt và ngách chỉ áp dụng cho lô nội dung sau.</p></div>
  <form method="get" action="/oauth/meta/start"><button class="btn btn--primary" type="submit">Kết nối Meta</button></form>
</div>
{% if request.args.get('err') %}<div class="alert alert--error"><strong>Không thực hiện được.</strong><span>{{ request.args.get('err') }}</span></div>{% endif %}
{% if summary %}<div class="alert alert--success"><strong>Đã đồng bộ.</strong><span>{{ summary }}</span></div>{% endif %}
{% if saved %}<div class="alert alert--success"><strong>Đã lưu.</strong><span>Đã cập nhật nhóm sản phẩm cho {{ saved }}.</span></div>{% endif %}

{% if has_meta_connection %}
<form method="post" action="/kenh/meta/sync"><input type="hidden" name="_csrf" value="{{ csrf_token }}"><button class="btn btn--ghost" type="submit">Đồng bộ lại</button></form>
{% endif %}

{% for platform, chs in by_platform.items() %}
<div class="section-heading section-heading--spaced"><div><h2>{{ platform|upper }}</h2></div></div>
<div class="channel-list">
{% for c in chs %}
<section class="card channel-card">
  <div class="channel-card__head">
    <div><div class="channel-card__title">{{ c.username and ('@' + c.username) or c.handle }}</div><span class="mono-sub">{{ c.code }}{% if c.last_sync_at %} · đồng bộ {{ c.last_sync_at[:16]|replace('T',' ') }}{% endif %}</span></div>
    <div class="channel-meta">
      <span class="tag {{ 'ok' if c.status=='ACTIVE' else 'bad' }}">{{ 'Cần kết nối lại' if c.status=='NEEDS_REAUTH' else c.status }}</span>
      <span class="tag {{ 'ok' if c.enabled else '' }}">{{ 'Đang bật' if c.enabled else 'Đã tắt' }}</span>
      <span class="tag">{{ c.pool|num }} hợp lệ</span><span class="tag">đã đăng {{ c.published|num }}</span>
      <form method="post" action="/kenh/{{ c.id }}/{{ 'disable' if c.enabled else 'enable' }}"><input type="hidden" name="_csrf" value="{{ csrf_token }}"><button class="btn btn--small" type="submit">{{ 'Tắt' if c.enabled else 'Bật' }}</button></form>
    </div>
  </div>
  <form method="post">
    <input type="hidden" name="_csrf" value="{{ csrf_token }}"><input type="hidden" name="channel_id" value="{{ c.id }}">
    <div class="niche-grid">
    {% for code, n in all_niches.items() %}
      <label class="niche-tile"><input type="checkbox" name="niches" value="{{ code }}" {{ 'checked' if code in c.niches }}><span>{{ n.name }}{% if n.extra_banned_phrases %}<small>nhóm có điều kiện · +{{ n.extra_banned_phrases|length }} cụm cấm</small>{% endif %}</span></label>
    {% endfor %}
    </div>
    <div class="channel-card__foot"><span class="note">Không chọn ô nào = kênh nhận mọi danh mục.</span><button class="btn btn--primary" type="submit">Lưu cho {{ c.handle }}</button></div>
  </form>
</section>
{% endfor %}
</div>
{% endfor %}
{% if not by_platform %}<div class="empty-state">Chưa có kênh nào.</div>{% endif %}
{% endblock %}
```

- [ ] **Step 5: Chạy lại test, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: `0 hỏng`.

- [ ] **Step 6: Commit**

```bash
git add web/server.py web/templates/channels.html tests/test_pilot.py
git commit -m "feat: multi-platform /kenh UI with enable/disable toggle"
```

---

## Task 7: Chặn thực thi khi `enabled=0` + vá dropdown `/sanpham`

**Files:**
- Modify: `core/pipeline.py` (`approve_post`, `_create_post_from_raw_product`)
- Modify: `web/server.py` (`_product_common_context`)
- Test: `tests/test_pipeline.py`, `tests/test_pilot.py`

**Interfaces:**
- Consumes: `channel.enabled` (Task 1).

- [ ] **Step 1: Viết test thất bại (enabled guard)**

Thêm vào `tests/test_pipeline.py`, sau `test_meta_connection_schema`:

```python
def test_disabled_channel_blocks_new_publish():
    print("\nKênh tắt (enabled=0) không tạo publish job mới")
    conn = connect()
    campaign = conn.execute("SELECT id FROM campaign LIMIT 1").fetchone()
    channel = conn.execute("SELECT id FROM channel LIMIT 1").fetchone()
    product = conn.execute("SELECT id FROM product LIMIT 1").fetchone()
    conn.execute("UPDATE channel SET enabled=0 WHERE id=?", (channel["id"],))

    # Tạo thẳng một post PENDING_REVIEW gắn với kênh đã tắt -- không phụ
    # thuộc vào chấm điểm/random để chắc chắn đúng kênh cần test.
    post_id = ulid()
    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, variant_code,
                    caption_body, disclosure_text, caption_final, status, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,'PENDING_REVIEW',?,?)""",
                 (post_id, product["id"], channel["id"], campaign["id"], "A",
                  "thân bài", "nhãn tiếp thị", "thân bài", now(), now()))

    res = pipeline.approve_post(conn, post_id)
    check("approve_post từ chối kênh đã tắt", res["ok"] is False, res)
    check("không tạo publish_target khi bị từ chối",
          conn.execute("SELECT COUNT(*) FROM publish_target WHERE post_id=?",
                       (post_id,)).fetchone()[0] == 0)
    check("post vẫn ở PENDING_REVIEW, chưa bị đổi sang SCHEDULED",
          conn.execute("SELECT status FROM post WHERE id=?", (post_id,)).fetchone()["status"]
          == "PENDING_REVIEW")

    conn.execute("UPDATE channel SET enabled=1 WHERE id=?", (channel["id"],))
    conn.close()
```

Gọi thêm `test_disabled_channel_blocks_new_publish()` trong khối `__main__`,
sau `test_meta_connection_schema()`.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `approve_post` hiện tại chưa có guard nên vẫn duyệt bình thường →
assertion `res["ok"] is False` thất bại (`res["ok"]` thực tế là `True`).

- [ ] **Step 3: Thêm guard vào `core/pipeline.py::approve_post`**

Thay dòng đầu hàm `approve_post` (`def approve_post(conn, post_id: str, actor:
str = "operator", caption_override: str = None) -> dict:` tới hết đoạn kiểm
tra `problems`):

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
```

(Phần còn lại của hàm — từ `scheduled = _next_slot(...)` tới cuối — giữ
nguyên, không đổi.)

- [ ] **Step 4: Chạy lại test, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `0 hỏng`.

- [ ] **Step 5: Viết test thất bại (dropdown lọc platform)**

Thêm vào `tests/test_pilot.py`, ngay sau `test_channel_enable_disable_route`:

```python
def test_product_dropdown_only_shows_threads():
    print("\nDropdown /sanpham chỉ hiện kênh Threads")
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
                 (ulid(), "fb_test_dropdown", "facebook", "Fake Page", "ACTIVE", 1, now()))
    conn.close()

    page = c.get("/sanpham")
    body = page.get_data(as_text=True)
    check("dropdown KHÔNG chứa kênh facebook mới tạo", "Fake Page" not in body, "leaked into dropdown")

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)
```

Gọi `test_product_dropdown_only_shows_threads()` trong khối `__main__`, ngay
sau `test_channel_enable_disable_route()`.

- [ ] **Step 6: Chạy test, xác nhận thất bại**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: thất bại vì "Fake Page" xuất hiện trong dropdown.

- [ ] **Step 7: Sửa `_product_common_context()` trong `web/server.py`**

Thay:

```python
        channels = [dict(r) for r in conn.execute(
            "SELECT code, handle FROM channel WHERE status='ACTIVE' ORDER BY code").fetchall()]
```

bằng:

```python
        channels = [dict(r) for r in conn.execute(
            "SELECT code, handle FROM channel WHERE status='ACTIVE' AND platform='threads' "
            "ORDER BY code").fetchall()]
```

- [ ] **Step 8: Chạy lại toàn bộ, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: `0 hỏng` cả hai.

- [ ] **Step 9: Commit**

```bash
git add core/pipeline.py web/server.py tests/test_pipeline.py tests/test_pilot.py
git commit -m "fix: block disabled-channel approval and filter /sanpham dropdown to Threads"
```

---

## Task 8: Hồi quy toàn bộ + hoàn tất

**Files:** không tạo file mới, chỉ chạy kiểm tra.

- [ ] **Step 1: Chạy toàn bộ 2 suite**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: cả hai in dòng cuối `... đạt, 0 hỏng`. Nếu FAIL, dừng và debug theo
`superpowers:systematic-debugging` trước khi qua bước sau.

- [ ] **Step 2: Kiểm tra không lọt secrets/runtime data**

```bash
git status --porcelain
git diff --check
```

Xác nhận không có file trong `var/`, `.env*`, hoặc DB thật trong danh sách
thay đổi. Xác nhận `META_APP_ID`/`META_APP_SECRET` không xuất hiện hardcode
trong bất kỳ file nào ngoài đọc qua `os.environ.get(...)`.

- [ ] **Step 3: Commit cuối (nếu còn thay đổi chưa commit)**

```bash
git add -A
git commit -m "chore: finalize MetaConnectionService + /kenh multi-platform (sub-project B)"
```
