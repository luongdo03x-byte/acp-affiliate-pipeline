# FacebookPublisher & InstagramPublisher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cho ACP thực sự đăng bài lên Facebook Page và Instagram
Professional account — thêm `FacebookPublisher`/`InstagramPublisher` đúng
interface `Publisher` đã có, xử lý ảnh đơn/carousel, thử áp native Meta
label (best-effort, không chặn publish).

**Architecture:** `Publisher.publish()` và `ctx["publishers"][platform]`
dispatch (từ sub-project A) đã hoàn toàn generic — C chỉ thêm class mới
(mock + live) và đăng ký factory. Native label outcome trả qua trường mới
`PublishResult.native_label_status` (additive, có default) thay vì publisher
tự ghi DB — `core/pipeline.py::publish_post` đọc field này sau khi
`publish()` trả về và ghi đúng một audit call thêm; không đụng
routing/exception-handling hiện có, không sửa `core/jobs.py`.

**Tech Stack:** Python 3.14, `requests` (đã có sẵn từ Threads/AccessTrade/
MetaConnectionService), SQLite. Test runner tự viết (`check()`), **không
dùng pytest**.

**Spec:** `docs/superpowers/specs/2026-08-15-facebook-instagram-publisher-design.md`

## Global Constraints

- Không sửa `core/jobs.py`. Không sửa routing/exception-handling của
  `core/pipeline.py::publish_post` — chỉ thêm đúng một đoạn đọc
  `native_label_status` + ghi audit (Task 6).
- `PublishResult` thêm field `native_label_status: str = "not_attempted"`
  (additive, có default — không phá constructor `PublishResult(
  external_post_id=.., published_at=..)` hiện có của Threads).
- Media validation raise `ContentViolationError` (non-retryable, đúng
  taxonomy đã có ở `adapters/base.py`) — không raise loại lỗi mới.
- Mỗi Page/IG account có token riêng trong `channel_row["token_encrypted"]`
  (từ sub-project B) — publisher đọc qua `channel_row`, không giữ credential
  trong instance, đúng pattern `ThreadsChannel` đã có.
- Cơ chế native label thật của Meta (Partnership Ads/Branded Content) CHƯA
  được xác nhận chính xác — `_try_apply_native_label` ở bản live PHẢI trả
  `"unavailable"` một cách trung thực (không tự chế endpoint trông hợp lý
  nhưng chưa kiểm chứng) — xem Task 3/4 để biết lý do.
- Test chạy qua `ACP_ADAPTER=mock ACP_SOURCE=mock python -m acp.tests.test_pipeline`
  và `acp.tests.test_pilot`, từ thư mục **cha** của repo (repo là thư mục
  tên `acp/`). Không dùng pytest.
- Không commit secrets/runtime data.

---

## Task 1: `PublishResult` mở rộng `native_label_status`

**Files:**
- Modify: `adapters/base.py:31-34`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `PublishResult(external_post_id: str, published_at: str,
  native_label_status: str = "not_attempted")`. Task 2-5 dùng field này.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `tests/test_pipeline.py`, ngay sau hàm `test_publisher_media_list`
(trước `if __name__ == "__main__":`):

```python
def test_publish_result_native_label_field():
    print("\nPublishResult có native_label_status")
    from acp.adapters.base import PublishResult
    old_style = PublishResult(external_post_id="p1", published_at="2026-01-01T00:00:00")
    check("constructor cũ (không native_label_status) vẫn hợp lệ",
          old_style.native_label_status == "not_attempted", old_style.native_label_status)
    new_style = PublishResult(external_post_id="p2", published_at="2026-01-01T00:00:00",
                               native_label_status="applied")
    check("field mới nhận giá trị truyền vào", new_style.native_label_status == "applied")
```

Gọi thêm `test_publish_result_native_label_field()` vào khối `if __name__ ==
"__main__":`, ngay sau `test_publisher_media_list()`.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `TypeError: PublishResult.__init__() got an unexpected keyword argument 'native_label_status'`.

- [ ] **Step 3: Thêm field vào `adapters/base.py`**

Thay:

```python
@dataclass
class PublishResult:
    external_post_id: str
    published_at: str
```

bằng:

```python
@dataclass
class PublishResult:
    external_post_id: str
    published_at: str
    native_label_status: str = "not_attempted"  # applied / unavailable / failed
```

- [ ] **Step 4: Chạy lại test, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `0 hỏng`.

- [ ] **Step 5: Commit**

```bash
git add adapters/base.py tests/test_pipeline.py
git commit -m "feat: add native_label_status field to PublishResult"
```

---

## Task 2: `MockFacebookPublisher` + `MockInstagramPublisher`

**Files:**
- Modify: `adapters/mock.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Publisher`, `PublishResult` (Task 1), `PublishError`,
  `RateLimitError`, `ContentViolationError` (đã có từ A).
- Produces: `MockFacebookPublisher(fail_rate=0.0, rate_limited=False,
  seed=None, native_label_status="applied")`,
  `MockInstagramPublisher(...)` cùng chữ ký. Cả hai implement
  `publish(channel_row, caption, media) -> PublishResult`. Task 6's test
  dùng các class này.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `tests/test_pipeline.py`, ngay sau `test_publish_result_native_label_field`:

```python
def test_mock_facebook_publisher():
    print("\nMockFacebookPublisher")
    from acp.adapters.mock import MockFacebookPublisher
    from acp.adapters.base import Publisher, ContentViolationError as _CVE

    pub = MockFacebookPublisher(seed=1)
    check("là Publisher", isinstance(pub, Publisher))
    check("platform đúng", pub.platform == "facebook")

    result = pub.publish({}, "caption", media=["https://img.example/a.jpg"])
    check("publish 1 ảnh trả về PublishResult", bool(result.external_post_id))
    check("native_label_status mặc định applied", result.native_label_status == "applied")

    result2 = pub.publish({}, "caption", media=["https://img.example/a.jpg",
                                                  "https://img.example/b.jpg"])
    check("publish nhiều ảnh cũng trả về PublishResult", bool(result2.external_post_id))
    check("2 lần publish tạo 2 external_post_id khác nhau",
          result.external_post_id != result2.external_post_id)

    try:
        pub.publish({}, "caption", media=[])
        check("0 ảnh phải bị chặn", False, "không ném lỗi")
    except _CVE:
        check("0 ảnh phải bị chặn", True)

    try:
        pub.publish({}, "caption", media=["u"] * 11)
        check("quá 10 ảnh phải bị chặn", False, "không ném lỗi")
    except _CVE:
        check("quá 10 ảnh phải bị chặn", True)

    labeled = MockFacebookPublisher(seed=2, native_label_status="unavailable")
    r3 = labeled.publish({}, "caption", media=["https://img.example/a.jpg"])
    check("native_label_status tham số hoá được", r3.native_label_status == "unavailable")


def test_mock_instagram_publisher():
    print("\nMockInstagramPublisher")
    from acp.adapters.mock import MockInstagramPublisher
    from acp.adapters.base import Publisher, ContentViolationError as _CVE

    pub = MockInstagramPublisher(seed=1)
    check("là Publisher", isinstance(pub, Publisher))
    check("platform đúng", pub.platform == "instagram")

    single = pub.publish({}, "caption", media=["https://img.example/a.jpg"])
    check("publish 1 ảnh (single) trả về PublishResult", bool(single.external_post_id))

    carousel = pub.publish({}, "caption", media=["https://img.example/a.jpg",
                                                   "https://img.example/b.jpg",
                                                   "https://img.example/c.jpg"])
    check("publish carousel (2-10 ảnh) trả về PublishResult", bool(carousel.external_post_id))

    try:
        pub.publish({}, "caption", media=[])
        check("0 ảnh phải bị chặn", False, "không ném lỗi")
    except _CVE:
        check("0 ảnh phải bị chặn", True)

    try:
        pub.publish({}, "caption", media=["u"] * 11)
        check("quá 10 ảnh phải bị chặn", False, "không ném lỗi")
    except _CVE:
        check("quá 10 ảnh phải bị chặn", True)

    try:
        pub.publish({}, "x" * 2201, media=["https://img.example/a.jpg"])
        check("caption quá 2200 ký tự phải bị chặn", False, "không ném lỗi")
    except _CVE:
        check("caption quá 2200 ký tự phải bị chặn", True)
```

Gọi thêm cả hai hàm trong khối `__main__`, ngay sau
`test_publish_result_native_label_field()`.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `ImportError: cannot import name 'MockFacebookPublisher'`.

- [ ] **Step 3: Thêm hai class vào `adapters/mock.py`**

Thêm vào cuối `adapters/mock.py`, ngay sau class `MockMetaConnectionService`
(trước hàm `simulate_postbacks`):

```python
class MockFacebookPublisher(Publisher):
    """Fixture xác định, không cần mạng. Giới hạn 1-10 ảnh/bài -- con số cần
    xác nhận lại với giới hạn thật của Facebook lúc go-live."""

    platform = "facebook"
    max_caption_length = 63206

    def __init__(self, fail_rate: float = 0.0, rate_limited: bool = False,
                 seed: int = None, native_label_status: str = "applied"):
        self.fail_rate = fail_rate
        self.rate_limited = rate_limited
        self._rng = random.Random(seed)
        self.native_label_status = native_label_status
        self.published = []

    def publish(self, channel_row, caption: str, media: list = None) -> PublishResult:
        media = media or []
        if not (1 <= len(media) <= 10):
            raise ContentViolationError(f"Facebook cần 1-10 ảnh, nhận {len(media)}")
        if self.rate_limited:
            raise RateLimitError("Đã dùng hết hạn mức đăng bài Facebook")
        if self._rng.random() < self.fail_rate:
            raise PublishError("Lỗi tạm thời khi tạo bài Facebook")
        pid = f"mock_fb_{self._rng.randrange(10**12, 10**13)}"
        self.published.append((pid, caption, list(media)))
        return PublishResult(external_post_id=pid,
                              published_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              native_label_status=self.native_label_status)

    def remaining_quota(self, channel_row) -> int:
        return 0 if self.rate_limited else 999


class MockInstagramPublisher(Publisher):
    """1 ảnh -> nhánh single, 2-10 ảnh -> nhánh carousel (mock gộp làm một,
    bản live tách hai luồng khác nhau, xem adapters/live.py)."""

    platform = "instagram"
    max_caption_length = 2200

    def __init__(self, fail_rate: float = 0.0, rate_limited: bool = False,
                 seed: int = None, native_label_status: str = "applied"):
        self.fail_rate = fail_rate
        self.rate_limited = rate_limited
        self._rng = random.Random(seed)
        self.native_label_status = native_label_status
        self.published = []

    def publish(self, channel_row, caption: str, media: list = None) -> PublishResult:
        media = media or []
        if len(media) == 0 or len(media) > 10:
            raise ContentViolationError(
                f"Instagram cần 1 ảnh (single) hoặc 2-10 ảnh (carousel), nhận {len(media)}")
        if len(caption) > self.max_caption_length:
            raise ContentViolationError(
                f"Caption {len(caption)} ký tự, Instagram chỉ cho {self.max_caption_length}")
        if self.rate_limited:
            raise RateLimitError("Đã dùng hết hạn mức đăng bài Instagram")
        if self._rng.random() < self.fail_rate:
            raise PublishError("Lỗi tạm thời khi tạo media container Instagram")
        pid = f"mock_ig_{self._rng.randrange(10**12, 10**13)}"
        self.published.append((pid, caption, list(media)))
        return PublishResult(external_post_id=pid,
                              published_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              native_label_status=self.native_label_status)

    def remaining_quota(self, channel_row) -> int:
        return 0 if self.rate_limited else 999
```

- [ ] **Step 4: Chạy lại test, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `0 hỏng`.

- [ ] **Step 5: Commit**

```bash
git add adapters/mock.py tests/test_pipeline.py
git commit -m "feat: add MockFacebookPublisher and MockInstagramPublisher"
```

---

## Task 3: `FacebookPublisher` thật (Graph API)

**Files:**
- Modify: `adapters/live.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Publisher`, `PublishResult` (Task 1), `decrypt` (đã có),
  `AuthError`/`RateLimitError`/`ContentViolationError`/`PublishError` (đã có).
- Produces: `FacebookPublisher()` implement `Publisher`. Task 5 (factory)
  dùng class này.

**Về native label:** cơ chế API chính xác để gắn nhãn native partnership
của Meta cho bài Facebook thông thường (không phải quảng cáo) CHƯA được xác
nhận — tài liệu Graph API công khai không có một endpoint đơn giản, ổn định
cho việc này (Partnership Ads/Branded Content thường đi qua Business
Manager/Marketing API, không phải một lệnh POST đơn lẻ vào post vừa tạo).
Thay vì viết một lệnh gọi API "trông hợp lý" nhưng chưa kiểm chứng — có thể
đánh lừa người đọc sau này tưởng đã đúng — `_try_apply_native_label` ở đây
**luôn trả về `"unavailable"`** một cách trung thực, kèm comment giải thích,
đúng tinh thần PTYC §29 dòng cuối ("Capability/permission chính xác phải
được kiểm tra lại với tài liệu API Meta tại thời điểm implementation").

- [ ] **Step 1: Viết test thất bại (phần không cần mạng)**

Thêm vào `tests/test_pipeline.py`, ngay sau `test_mock_instagram_publisher`:

```python
def test_facebook_publisher_validates_before_network():
    print("\nFacebookPublisher validate trước khi gọi mạng")
    from acp.adapters.live import FacebookPublisher
    from acp.adapters.base import Publisher, ContentViolationError as _CVE, AuthError as _AuthError

    pub = FacebookPublisher()
    check("là Publisher", isinstance(pub, Publisher))
    check("platform đúng", pub.platform == "facebook")

    # Validate media TRƯỚC khi chạm self.session -- test này chạy được không
    # cần mạng vì raise xảy ra trước bất kỳ lệnh gọi requests nào.
    try:
        pub.publish({"code": "fb1", "token_encrypted": None}, "caption", media=[])
        check("0 ảnh phải bị chặn trước khi gọi mạng", False, "không ném lỗi")
    except _CVE:
        check("0 ảnh phải bị chặn trước khi gọi mạng", True)

    try:
        pub.publish({"code": "fb1", "token_encrypted": None}, "caption", media=["u"] * 11)
        check("quá 10 ảnh phải bị chặn trước khi gọi mạng", False, "không ném lỗi")
    except _CVE:
        check("quá 10 ảnh phải bị chặn trước khi gọi mạng", True)

    # Token rỗng cũng phải chặn trước khi gọi mạng, đúng pattern ThreadsChannel.
    try:
        pub.publish({"code": "fb1", "token_encrypted": None}, "caption",
                     media=["https://img.example/a.jpg"])
        check("token rỗng phải bị chặn trước khi gọi mạng", False, "không ném lỗi")
    except _AuthError:
        check("token rỗng phải bị chặn trước khi gọi mạng", True)
```

Gọi thêm `test_facebook_publisher_validates_before_network()` trong khối
`__main__`, ngay sau `test_mock_instagram_publisher()`.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `ImportError: cannot import name 'FacebookPublisher'`.

- [ ] **Step 3: Thêm `import json` và hàm dùng chung `_raise_for_meta_api`**

Thêm `import json` vào đầu `adapters/live.py` (dòng 10, cùng nhóm với `import
os`):

```python
import json
import os
import threading
import time
```

Thêm hàm module-level ngay sau hằng số `META_OAUTH_SCOPES` (trước class
`TokenBucket`):

```python
def _raise_for_meta_api(r):
    """Dùng chung cho FacebookPublisher/InstagramPublisher -- cùng taxonomy lỗi
    Graph API mà ThreadsChannel._raise_for_api đã áp dụng cho Threads."""
    if r.status_code < 400:
        return
    try:
        err = r.json().get("error", {})
    except ValueError:
        err = {}
    code, msg = err.get("code"), err.get("message", r.text[:200])
    if r.status_code in (401, 403) or code in (190, 102):
        raise AuthError(msg)
    if r.status_code == 429 or code in (4, 17, 32, 613):
        raise RateLimitError(msg)
    if code in (1346003, 1346013, 36003) or "policy" in str(msg).lower():
        raise ContentViolationError(msg)
    raise PublishError(f"HTTP {r.status_code}: {msg}")
```

- [ ] **Step 4: Thêm class `FacebookPublisher`**

Thêm vào cuối `adapters/live.py`:

```python
class FacebookPublisher(Publisher):
    """Publish ảnh đơn hoặc nhiều ảnh lên Facebook Page. Giới hạn 1-10 ảnh --
    con số cần xác nhận lại với giới hạn thật của Facebook lúc go-live."""

    platform = "facebook"
    max_caption_length = 63206

    def __init__(self):
        self.session = requests.Session()

    def _token(self, channel_row) -> str:
        tok = decrypt(channel_row["token_encrypted"])
        if not tok:
            raise AuthError(f"Kênh {channel_row['code']} chưa có token hợp lệ")
        return tok

    def publish(self, channel_row, caption: str, media: list = None) -> PublishResult:
        media = media or []
        if not (1 <= len(media) <= 10):
            raise ContentViolationError(f"Facebook cần 1-10 ảnh, nhận {len(media)}")
        page_id = channel_row["external_account_id"]
        token = self._token(channel_row)

        if len(media) == 1:
            r = self.session.post(f"{GRAPH_BASE}/{page_id}/photos", data={
                "url": media[0], "caption": caption, "published": "true",
                "access_token": token,
            }, timeout=30)
            _raise_for_meta_api(r)
            body = r.json()
            post_id = body.get("post_id") or body["id"]
        else:
            media_fbids = []
            for url in media:
                pr = self.session.post(f"{GRAPH_BASE}/{page_id}/photos", data={
                    "url": url, "published": "false", "access_token": token,
                }, timeout=30)
                _raise_for_meta_api(pr)
                media_fbids.append(pr.json()["id"])
            attach = {f"attached_media[{i}]": json.dumps({"media_fbid": fbid})
                      for i, fbid in enumerate(media_fbids)}
            fr = self.session.post(f"{GRAPH_BASE}/{page_id}/feed", data={
                "message": caption, "access_token": token, **attach,
            }, timeout=30)
            _raise_for_meta_api(fr)
            post_id = fr.json()["id"]

        return PublishResult(
            external_post_id=post_id,
            published_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            native_label_status=self._try_apply_native_label(),
        )

    def _try_apply_native_label(self) -> str:
        """Cơ chế API chính xác chưa xác nhận được (xem ghi chú Task 3 trong
        plan) -- trả 'unavailable' trung thực, không tự chế endpoint."""
        return "unavailable"

    def remaining_quota(self, channel_row) -> int:
        # Không dùng ở đâu trong pipeline hiện tại (giống ThreadsChannel);
        # Facebook không có endpoint hạn mức đơn giản như Threads
        # threads_publishing_limit -- trả hằng số lớn cho tới khi cần thật.
        return 999
```

- [ ] **Step 5: Chạy lại test, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `0 hỏng`.

- [ ] **Step 6: Commit**

```bash
git add adapters/live.py tests/test_pipeline.py
git commit -m "feat: add FacebookPublisher (live Graph API)"
```

---

## Task 4: `InstagramPublisher` thật (Graph API)

**Files:**
- Modify: `adapters/live.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Publisher`, `PublishResult` (Task 1), `_raise_for_meta_api`
  (Task 3), `decrypt`, exception taxonomy (đã có).
- Produces: `InstagramPublisher()` implement `Publisher`. Task 5 (factory)
  dùng class này.

Cùng lý do với Task 3: `_try_apply_native_label` cho Instagram cũng luôn trả
`"unavailable"` — cơ chế Partnership/Branded Content của Instagram còn phức
tạp hơn Facebook (thường qua Business/Marketing API hoặc chính app di động,
không phải Graph API đơn giản), càng không nên tự chế endpoint.

- [ ] **Step 1: Viết test thất bại (phần không cần mạng)**

Thêm vào `tests/test_pipeline.py`, ngay sau
`test_facebook_publisher_validates_before_network`:

```python
def test_instagram_publisher_validates_before_network():
    print("\nInstagramPublisher validate trước khi gọi mạng")
    from acp.adapters.live import InstagramPublisher
    from acp.adapters.base import Publisher, ContentViolationError as _CVE, AuthError as _AuthError

    pub = InstagramPublisher()
    check("là Publisher", isinstance(pub, Publisher))
    check("platform đúng", pub.platform == "instagram")

    try:
        pub.publish({"code": "ig1", "token_encrypted": None}, "caption", media=[])
        check("0 ảnh phải bị chặn trước khi gọi mạng", False, "không ném lỗi")
    except _CVE:
        check("0 ảnh phải bị chặn trước khi gọi mạng", True)

    try:
        pub.publish({"code": "ig1", "token_encrypted": None}, "caption", media=["u"] * 11)
        check("quá 10 ảnh phải bị chặn trước khi gọi mạng", False, "không ném lỗi")
    except _CVE:
        check("quá 10 ảnh phải bị chặn trước khi gọi mạng", True)

    try:
        pub.publish({"code": "ig1", "token_encrypted": None}, "x" * 2201,
                     media=["https://img.example/a.jpg"])
        check("caption quá 2200 ký tự phải bị chặn trước khi gọi mạng", False, "không ném lỗi")
    except _CVE:
        check("caption quá 2200 ký tự phải bị chặn trước khi gọi mạng", True)

    try:
        pub.publish({"code": "ig1", "token_encrypted": None}, "caption",
                     media=["https://img.example/a.jpg"])
        check("token rỗng phải bị chặn trước khi gọi mạng", False, "không ném lỗi")
    except _AuthError:
        check("token rỗng phải bị chặn trước khi gọi mạng", True)
```

Gọi thêm `test_instagram_publisher_validates_before_network()` trong khối
`__main__`, ngay sau `test_facebook_publisher_validates_before_network()`.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `ImportError: cannot import name 'InstagramPublisher'`.

- [ ] **Step 3: Thêm class `InstagramPublisher` vào `adapters/live.py`**

Thêm vào cuối file:

```python
class InstagramPublisher(Publisher):
    """Container model giống Threads: tạo container -> poll -> publish. Nhiều
    ảnh thì tạo child container is_carousel_item=true trước, rồi container
    CAROUSEL tham chiếu tới children."""

    platform = "instagram"
    max_caption_length = 2200

    def __init__(self, poll_interval: float = 3.0, poll_timeout: float = 60.0):
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.session = requests.Session()

    def _token(self, channel_row) -> str:
        tok = decrypt(channel_row["token_encrypted"])
        if not tok:
            raise AuthError(f"Kênh {channel_row['code']} chưa có token hợp lệ")
        return tok

    def publish(self, channel_row, caption: str, media: list = None) -> PublishResult:
        media = media or []
        if len(media) == 0 or len(media) > 10:
            raise ContentViolationError(
                f"Instagram cần 1 ảnh (single) hoặc 2-10 ảnh (carousel), nhận {len(media)}")
        if len(caption) > self.max_caption_length:
            raise ContentViolationError(
                f"Caption {len(caption)} ký tự, Instagram chỉ cho {self.max_caption_length}")

        ig_id = channel_row["external_account_id"]
        token = self._token(channel_row)

        if len(media) == 1:
            creation_id = self._create_container(ig_id, token, {
                "image_url": media[0], "caption": caption})
        else:
            children = []
            for url in media:
                child_id = self._create_container(ig_id, token, {
                    "image_url": url, "is_carousel_item": "true"})
                children.append(child_id)
            creation_id = self._create_container(ig_id, token, {
                "media_type": "CAROUSEL", "children": ",".join(children), "caption": caption})

        self._poll_until_finished(creation_id, token)

        p = self.session.post(f"{GRAPH_BASE}/{ig_id}/media_publish", data={
            "creation_id": creation_id, "access_token": token,
        }, timeout=30)
        _raise_for_meta_api(p)
        media_id = p.json()["id"]

        return PublishResult(
            external_post_id=media_id,
            published_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            native_label_status=self._try_apply_native_label(),
        )

    def _create_container(self, ig_id: str, token: str, params: dict) -> str:
        r = self.session.post(f"{GRAPH_BASE}/{ig_id}/media",
                               data={**params, "access_token": token}, timeout=30)
        _raise_for_meta_api(r)
        return r.json()["id"]

    def _poll_until_finished(self, creation_id: str, token: str) -> None:
        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            s = self.session.get(f"{GRAPH_BASE}/{creation_id}",
                                  params={"fields": "status_code", "access_token": token},
                                  timeout=20)
            _raise_for_meta_api(s)
            status = s.json().get("status_code")
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise PublishError(f"Container {creation_id} lỗi khi xử lý")
            time.sleep(self.poll_interval)
        raise PublishError(f"Container {creation_id} chưa sẵn sàng sau {self.poll_timeout:.0f}s")

    def _try_apply_native_label(self) -> str:
        """Cơ chế API chính xác chưa xác nhận được (xem ghi chú Task 4 trong
        plan) -- trả 'unavailable' trung thực, không tự chế endpoint."""
        return "unavailable"

    def remaining_quota(self, channel_row) -> int:
        return 999
```

- [ ] **Step 4: Chạy lại test, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `0 hỏng`.

- [ ] **Step 5: Commit**

```bash
git add adapters/live.py tests/test_pipeline.py
git commit -m "feat: add InstagramPublisher (live Graph API, single + carousel)"
```

---

## Task 5: Đăng ký `factory.get_publishers()`

**Files:**
- Modify: `adapters/factory.py:49-52`
- Test: `tests/test_pilot.py`

**Interfaces:**
- Consumes: `MockFacebookPublisher`/`MockInstagramPublisher` (Task 2),
  `FacebookPublisher`/`InstagramPublisher` (Task 3/4).
- Produces: `factory.get_publishers()` trả dict đủ 3 key
  `{"threads", "facebook", "instagram"}`.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `tests/test_pilot.py`, ngay sau `test_factory_meta_connection_service_live_routing`
(trước `# --------------------------------------------------------- single product`):

```python
def test_factory_registers_facebook_instagram_publishers():
    print("\nFactory đăng ký đủ facebook/instagram publisher")
    from acp.adapters.base import Publisher
    from acp.adapters.mock import MockFacebookPublisher, MockInstagramPublisher
    factory.reset_cache()
    os.environ.pop("ACP_ADAPTER", None)

    publishers = factory.get_publishers()
    check("có đủ 3 platform", set(publishers) == {"threads", "facebook", "instagram"},
          set(publishers))
    check("facebook là MockFacebookPublisher (mặc định mock)",
          isinstance(publishers["facebook"], MockFacebookPublisher))
    check("instagram là MockInstagramPublisher (mặc định mock)",
          isinstance(publishers["instagram"], MockInstagramPublisher))
    check("cả hai đều là Publisher",
          isinstance(publishers["facebook"], Publisher) and isinstance(publishers["instagram"], Publisher))

    os.environ["ACP_ADAPTER"] = "live"
    os.environ["META_APP_ID"] = "test_app_id"
    os.environ["META_APP_SECRET"] = "test_app_secret"
    try:
        from acp.adapters.live import FacebookPublisher, InstagramPublisher
        live_publishers = factory.get_publishers()
        check("ACP_ADAPTER=live trả về FacebookPublisher thật",
              isinstance(live_publishers["facebook"], FacebookPublisher))
        check("ACP_ADAPTER=live trả về InstagramPublisher thật",
              isinstance(live_publishers["instagram"], InstagramPublisher))
    finally:
        os.environ.pop("ACP_ADAPTER", None)
        os.environ.pop("META_APP_ID", None)
        os.environ.pop("META_APP_SECRET", None)
```

Gọi thêm `test_factory_registers_facebook_instagram_publishers()` trong khối
`__main__`, ngay sau `test_factory_meta_connection_service_live_routing()`.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: assertion `set(publishers) == {"threads", "facebook", "instagram"}`
thất bại (`{'threads'}` hiện tại).

- [ ] **Step 3: Sửa `get_publishers()` trong `adapters/factory.py`**

Thay:

```python
def get_publishers() -> dict:
    """platform -> Publisher. Chỉ có 'threads' cho tới khi sub-project B/C
    đăng ký thêm 'facebook'/'instagram'."""
    return {"threads": get_channel()}
```

bằng:

```python
def get_publishers() -> dict:
    """platform -> Publisher. Đủ 3 platform kể từ sub-project C."""
    publishers = {"threads": get_channel()}
    if is_live():
        from .live import FacebookPublisher, InstagramPublisher
        publishers["facebook"] = FacebookPublisher()
        publishers["instagram"] = InstagramPublisher()
    else:
        from .mock import MockFacebookPublisher, MockInstagramPublisher
        publishers["facebook"] = MockFacebookPublisher()
        publishers["instagram"] = MockInstagramPublisher()
    return publishers
```

- [ ] **Step 4: Chạy lại test, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: `0 hỏng`.

- [ ] **Step 5: Commit**

```bash
git add adapters/factory.py tests/test_pilot.py
git commit -m "feat: register facebook/instagram publishers in factory.get_publishers()"
```

---

## Task 6: `publish_post` ghi audit native label + hồi quy toàn bộ

**Files:**
- Modify: `core/pipeline.py:518-524`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `result.native_label_status` (Task 1), `ctx["publishers"]` đủ 3
  platform (Task 5, dùng trong test bằng publisher tuỳ biến).

- [ ] **Step 1: Viết test thất bại**

Thêm vào `tests/test_pipeline.py`, ngay sau
`test_instagram_publisher_validates_before_network`:

```python
def test_publish_post_audits_native_label_status():
    print("\npublish_post ghi audit native_label_requested")
    from acp.adapters.base import Publisher, PublishResult

    class _LabelledPublisher(Publisher):
        platform = "facebook"

        def publish(self, channel_row, caption, media=None):
            return PublishResult(external_post_id="fb_post_1",
                                  published_at=now(), native_label_status="unavailable")

    conn = connect()
    campaign = conn.execute("SELECT id FROM campaign LIMIT 1").fetchone()
    product = conn.execute("SELECT id FROM product LIMIT 1").fetchone()
    channel_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,'ACTIVE',1,999,0,?)""",
                 (channel_id, "fb_audit_test", "facebook", "Audit Test Page", now()))

    post_id = ulid()
    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, variant_code,
                    caption_body, disclosure_text, caption_final, status, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,'PENDING_REVIEW',?,?)""",
                 (post_id, product["id"], channel_id, campaign["id"], "A",
                  "thân bài", "nhãn tiếp thị", "thân bài", now(), now()))

    res = pipeline.approve_post(conn, post_id)
    check("approve_post thành công", res["ok"], res)
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?",
                 (now(), f"pub:{res['publish_target_id']}"))
    jobs.drain(conn, ctx={"source": MockAccessTrade(),
                          "publishers": {"facebook": _LabelledPublisher()}})

    audit_row = conn.execute(
        "SELECT * FROM audit_log WHERE entity='publish_target' AND action='native_label_requested' "
        "AND entity_id=?", (res["publish_target_id"],)).fetchone()
    check("có audit native_label_requested", audit_row is not None)
    check("audit ghi đúng outcome", "unavailable" in (audit_row["detail"] or ""),
          audit_row["detail"] if audit_row else None)

    conn.close()


def test_publish_post_no_native_label_audit_for_threads():
    print("\npublish_post KHÔNG ghi audit native label cho Threads (not_attempted)")
    conn = connect()
    ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(51))
    check("có job sinh nội dung", len(ids) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=51)}})
    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
    res = pipeline.approve_post(conn, post["id"])
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?",
                 (now(), f"pub:{res['publish_target_id']}"))
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=51)}})

    audit_row = conn.execute(
        "SELECT * FROM audit_log WHERE entity='publish_target' AND action='native_label_requested' "
        "AND entity_id=?", (res["publish_target_id"],)).fetchone()
    check("Threads (native_label_status mặc định not_attempted) không tạo audit thừa",
          audit_row is None, dict(audit_row) if audit_row else None)
    conn.close()
```

Gọi thêm cả hai hàm trong khối `__main__`, ngay sau
`test_instagram_publisher_validates_before_network()`.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `test_publish_post_audits_native_label_status` thất bại ở check "có
audit native_label_requested" (`audit_row is None`, vì `publish_post` chưa
ghi audit này).

- [ ] **Step 3: Sửa `core/pipeline.py::publish_post`**

Thay (dòng 518-524):

```python
    conn.execute("""UPDATE publish_target SET status='SUCCESS', external_post_id=?, updated_at=?
                    WHERE id=?""", (result.external_post_id, now(), target["id"]))
    conn.execute("UPDATE post SET status='PUBLISHED', thread_id=?, published_at=?, updated_at=? WHERE id=?",
                 (result.external_post_id, result.published_at, now(), post["id"]))
    audit(conn, "post", post["id"], "published",
          detail={"thread_id": result.external_post_id, "publish_target_id": target["id"]})
    enqueue(conn, "FETCH_INSIGHTS", {"post_id": post["id"], "channel_id": channel["id"]},
```

bằng:

```python
    conn.execute("""UPDATE publish_target SET status='SUCCESS', external_post_id=?, updated_at=?
                    WHERE id=?""", (result.external_post_id, now(), target["id"]))
    conn.execute("UPDATE post SET status='PUBLISHED', thread_id=?, published_at=?, updated_at=? WHERE id=?",
                 (result.external_post_id, result.published_at, now(), post["id"]))
    audit(conn, "post", post["id"], "published",
          detail={"thread_id": result.external_post_id, "publish_target_id": target["id"]})
    if result.native_label_status != "not_attempted":
        audit(conn, "publish_target", target["id"], "native_label_requested",
              detail={"status": result.native_label_status, "platform": channel["platform"]})
    enqueue(conn, "FETCH_INSIGHTS", {"post_id": post["id"], "channel_id": channel["id"]},
```

(Chỉ thêm 3 dòng `if result.native_label_status != "not_attempted": ...` —
không đổi gì khác trong hàm.)

- [ ] **Step 4: Chạy lại test, xác nhận qua**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
```

Kỳ vọng: `0 hỏng`.

- [ ] **Step 5: Hồi quy toàn bộ**

```bash
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pipeline
cd .. && ACP_ADAPTER=mock ACP_SOURCE=mock acp/.venv/bin/python -m acp.tests.test_pilot
```

Kỳ vọng: `0 hỏng` cả hai. Nếu FAIL, dừng và debug theo
`superpowers:systematic-debugging` trước khi qua bước sau.

- [ ] **Step 6: Kiểm tra không lọt secrets/runtime data**

```bash
git status --porcelain
git diff --check
```

- [ ] **Step 7: Commit**

```bash
git add core/pipeline.py tests/test_pipeline.py
git commit -m "feat: audit native_label_status outcome after successful publish"
```
