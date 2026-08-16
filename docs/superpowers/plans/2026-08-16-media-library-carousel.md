# Thư viện ảnh + carousel theo platform (Sub-project D3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cho phép operator upload/dán URL ảnh vào 1 thư viện dùng lại được
giữa nhiều bài, chọn thêm tối đa 9 ảnh cho 1 bài lúc tạo ở `/sanpham`, và
`publish_post()` gửi đúng số ảnh mỗi platform cho phép (Threads 1, Facebook/
Instagram tới 10 → carousel).

**Architecture:** 2 bảng mới (`media_asset` độc lập, `post_media` join có
thứ tự). Module mới `core/media_library.py` xử lý xác thực/lưu ảnh (file
upload hoặc URL ngoài, tái dùng `SafeHttpClient` đã có), tách biệt khỏi
`core/pipeline.py`. `post.image_url_composited` không đổi, luôn là ảnh đầu
tiên/bắt buộc — ảnh thêm chỉ tồn tại trong `post_media`. Chọn ảnh chỉ ở
`/sanpham` lúc tạo bài (không có ở `/duyệt`, khác caption/kênh của D1/D2).
Không gọi API sinh ảnh AI nào — chỉ gợi ý prompt cho operator tự dùng công
cụ ngoài.

**Tech Stack:** Python 3, Flask, SQLite, Jinja2, Pillow (đã có, dùng cho
`imaging.py`), `requests` (qua `SafeHttpClient`, đã có). Test bằng test
runner tự viết (`check()` + `PASS`/`FAIL`) — chạy `python3 -m
acp.tests.test_pipeline` / `acp/.venv/bin/python3 -m acp.tests.test_pilot`
(test_pilot.py cần venv riêng của repo vì python3 hệ thống không có Flask)
từ thư mục **cha** của repo (repo tên `acp/`).

**Spec:** `docs/superpowers/specs/2026-08-16-media-library-carousel-design.md`

## Global Constraints

- Toàn bộ code mới, comment, docstring, copy UI viết bằng tiếng Việt, đúng
  giọng văn hiện có trong file đang sửa.
- **Không gọi bất kỳ API sinh ảnh AI nào** — đã chốt cứng ở spec §1, không
  phải điểm mở. Chỉ gợi ý prompt (text tĩnh, `<textarea readonly>`) để
  operator tự dùng công cụ ngoài rồi tự upload kết quả.
- `post.image_url_composited` không đổi ý nghĩa — luôn là ảnh đầu
  tiên/bắt buộc của carousel, không lưu vào `post_media`.
- Chọn ảnh chỉ ở `/sanpham` lúc tạo bài — không có UI sửa ảnh ở `/duyệt`
  trong D3.
- Ảnh dán URL/upload đều tải/lưu file thật vào storage nội bộ (không lưu
  thẳng URL ngoài vào `media_asset.url`) — tái dùng `SafeHttpClient`
  (`adapters/safe_http.py`) và cùng mức xác thực PIL đã có ở
  `ShopeeAffiliateSource.materialize_image()`, không phát minh cơ chế mới.
- Trần 9 ảnh thêm/bài (tổng 10 kể cả ảnh ghép) — validate ở tầng
  `core/pipeline.py` (không chỉ ở route web).
- `MEDIA_MAX_COUNT = {"threads": 1, "facebook": 10, "instagram": 10}` —
  trùng đúng giới hạn đã hard-code trong `publish()` của từng `Publisher`
  (`adapters/mock.py`, `adapters/live.py`); cắt **trước khi** gọi
  `publisher.publish()`, không dựa publisher tự chặn.
- Mọi tham số mới đều optional, mặc định `None` = giữ nguyên hành vi trước
  D3 — không được phá bất kỳ test nào đang xanh (`test_pipeline.py` 262/0,
  `test_pilot.py` 295/0 tính đến khi bắt đầu D3).

---

## Task 1: Schema — `media_asset` + `post_media`

**Files:**
- Modify: `core/db.py` (`SCHEMA`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: bảng `media_asset(id, url, source, created_at)`, bảng
  `post_media(post_id, media_asset_id, position)` PK `(post_id,
  media_asset_id)`, index `idx_post_media_post`.

- [ ] **Step 1: Viết test kiểm tra 2 bảng tồn tại**

Thêm vào `tests/test_pipeline.py`, ngay trước `if __name__ == "__main__":`:

```python
def test_media_asset_and_post_media_schema():
    print("\nbảng media_asset + post_media đã có trong schema")
    conn = connect()
    asset_cols = {r["name"] for r in conn.execute("PRAGMA table_info(media_asset)").fetchall()}
    check("media_asset có đủ cột", {"id", "url", "source", "created_at"} <= asset_cols, asset_cols)
    pm_cols = {r["name"] for r in conn.execute("PRAGMA table_info(post_media)").fetchall()}
    check("post_media có đủ cột", {"post_id", "media_asset_id", "position"} <= pm_cols, pm_cols)

    product = conn.execute("SELECT id FROM product LIMIT 1").fetchone()
    channel = conn.execute("SELECT id FROM channel LIMIT 1").fetchone()
    campaign = conn.execute("SELECT id FROM campaign LIMIT 1").fetchone()
    post_id = ulid()
    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, variant_code,
                    caption_body, disclosure_text, caption_final, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                 (post_id, product["id"], channel["id"], campaign["id"], "A",
                  "thân bài", "nhãn tiếp thị", "thân bài", now(), now()))
    asset_id = ulid()
    conn.execute("INSERT INTO media_asset (id, url, source, created_at) VALUES (?,?,?,?)",
                 (asset_id, "https://cdn.example/a.jpg", "upload", now()))
    conn.execute("INSERT INTO post_media (post_id, media_asset_id, position) VALUES (?,?,?)",
                 (post_id, asset_id, 1))
    row = conn.execute("SELECT position FROM post_media WHERE post_id=? AND media_asset_id=?",
                       (post_id, asset_id)).fetchone()
    check("post_media lưu đúng position", row["position"] == 1, dict(row))

    import sqlite3
    try:
        conn.execute("INSERT INTO post_media (post_id, media_asset_id, position) VALUES (?,?,?)",
                     (post_id, asset_id, 2))
        check("PK (post_id, media_asset_id) chặn trùng lặp", False, "insert trùng lọt qua")
    except sqlite3.IntegrityError as e:
        check("PK (post_id, media_asset_id) chặn trùng lặp", "UNIQUE constraint failed" in str(e), str(e))
    conn.close()
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | grep -B1 -A5 "media_asset + post_media"
```
Expected: FAIL — 2 bảng chưa tồn tại.

- [ ] **Step 3: Thêm 2 bảng vào `SCHEMA`**

Trong `core/db.py`, tìm khối kết thúc bằng:
```sql
CREATE TABLE IF NOT EXISTS post_channel_selection (
    post_id     TEXT NOT NULL REFERENCES post(id),
    channel_id  TEXT NOT NULL REFERENCES channel(id),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (post_id, channel_id)
);
```
Thêm ngay sau đó (trước `CREATE TABLE IF NOT EXISTS meta_connection`):
```sql
CREATE TABLE IF NOT EXISTS media_asset (
    id          TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    source      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS post_media (
    post_id         TEXT NOT NULL REFERENCES post(id),
    media_asset_id  TEXT NOT NULL REFERENCES media_asset(id),
    position        INTEGER NOT NULL,
    PRIMARY KEY (post_id, media_asset_id)
);
CREATE INDEX IF NOT EXISTS idx_post_media_post ON post_media(post_id, position);
```

- [ ] **Step 4: Chạy lại test, xác nhận PASS**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -10
```

- [ ] **Step 5: Thêm lời gọi test vào `__main__`**

Thêm `test_media_asset_and_post_media_schema()` vào danh sách trong
`if __name__ == "__main__":`, ngay trước dòng `print(f"\n{len(PASS)} đạt,
{len(FAIL)} hỏng")` cuối file.

- [ ] **Step 6: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add core/db.py tests/test_pipeline.py
git commit -m "feat: thêm bảng media_asset + post_media (D3)"
```

---

## Task 2: `core/media_library.py` — xác thực/lưu ảnh + CRUD asset

**Files:**
- Create: `core/media_library.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `core.storage.get_storage()` (đã có), `adapters.safe_http.
  SafeHttpClient`/`SafeHttpError` (đã có).
- Produces:
  - `MediaValidationError(Exception)`
  - `materialize_uploaded_file(file_storage, media_dir: str) -> str` (trả
    đường dẫn file cục bộ)
  - `materialize_external_image(url: str, media_dir: str, http_client=None) -> str`
  - `create_media_asset(conn, local_path: str, source: str, storage_backend=None) -> dict`
  - `list_media_assets(conn) -> list[dict]`
  - `delete_media_asset(conn, asset_id: str) -> dict` (`{"ok": bool,
    "error": str}` khi thất bại)

- [ ] **Step 1: Viết test cho xác thực ảnh + CRUD asset**

Thêm vào `tests/test_pipeline.py`, ngay trước `if __name__ == "__main__":`.
Trước tiên thêm import ở đầu file, cùng khối với các import `from acp.core
import ...`:
```python
from acp.core import media_library
```

```python
def test_media_library_validates_and_stores_uploaded_bytes():
    print("\nmedia_library.materialize_uploaded_file xác thực đúng ảnh thật, lưu file cục bộ")
    from io import BytesIO
    from PIL import Image

    class _FakeFileStorage:
        def __init__(self, data: bytes):
            self._data = data
        def read(self):
            return self._data

    img = Image.new("RGB", (10, 10), (200, 100, 50))
    buf = BytesIO()
    img.save(buf, format="PNG")
    tmp_dir = tempfile.mkdtemp()

    local_path = media_library.materialize_uploaded_file(_FakeFileStorage(buf.getvalue()), tmp_dir)
    check("file được lưu đúng thư mục", local_path.startswith(tmp_dir), local_path)
    check("file lưu đúng đuôi .png theo định dạng thật", local_path.endswith(".png"), local_path)
    check("file tồn tại thật trên đĩa", os.path.exists(local_path), local_path)

    try:
        media_library.materialize_uploaded_file(_FakeFileStorage(b"khong phai anh"), tmp_dir)
        check("dữ liệu không phải ảnh bị từ chối", False, "lọt qua xác thực")
    except media_library.MediaValidationError:
        check("dữ liệu không phải ảnh bị từ chối", True)


def test_media_library_create_list_delete_asset():
    print("\nmedia_library: tạo/liệt kê/xoá asset, chặn xoá khi còn post_media tham chiếu")
    from PIL import Image
    from io import BytesIO

    class _FakeStorage:
        def put(self, local_path):
            return f"https://fake-storage.example/{os.path.basename(local_path)}"

    img = Image.new("RGB", (10, 10), (10, 20, 30))
    buf = BytesIO()
    img.save(buf, format="JPEG")
    tmp_dir = tempfile.mkdtemp()
    local_path = os.path.join(tmp_dir, "test_asset.jpg")
    with open(local_path, "wb") as fh:
        fh.write(buf.getvalue())

    conn = connect()
    asset = media_library.create_media_asset(conn, local_path, "upload", _FakeStorage())
    check("create_media_asset trả đúng dict", asset["source"] == "upload" and asset["url"], asset)
    row = conn.execute("SELECT * FROM media_asset WHERE id=?", (asset["id"],)).fetchone()
    check("media_asset được ghi vào CSDL", row is not None and row["url"] == asset["url"], dict(row) if row else None)

    assets = media_library.list_media_assets(conn)
    check("list_media_assets thấy asset vừa tạo", any(a["id"] == asset["id"] for a in assets), len(assets))

    product = conn.execute("SELECT id FROM product LIMIT 1").fetchone()
    channel = conn.execute("SELECT id FROM channel LIMIT 1").fetchone()
    campaign = conn.execute("SELECT id FROM campaign LIMIT 1").fetchone()
    post_id = ulid()
    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, variant_code,
                    caption_body, disclosure_text, caption_final, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                 (post_id, product["id"], channel["id"], campaign["id"], "A",
                  "thân bài", "nhãn tiếp thị", "thân bài", now(), now()))
    conn.execute("INSERT INTO post_media (post_id, media_asset_id, position) VALUES (?,?,?)",
                 (post_id, asset["id"], 1))

    res = media_library.delete_media_asset(conn, asset["id"])
    check("xoá bị chặn khi còn post_media tham chiếu", res["ok"] is False and "1" in res["error"], res)
    still_there = conn.execute("SELECT 1 FROM media_asset WHERE id=?", (asset["id"],)).fetchone()
    check("asset vẫn còn trong CSDL sau khi xoá bị chặn", still_there is not None)

    conn.execute("DELETE FROM post_media WHERE post_id=? AND media_asset_id=?", (post_id, asset["id"]))
    res2 = media_library.delete_media_asset(conn, asset["id"])
    check("xoá thành công khi không còn ai dùng", res2["ok"], res2)
    gone = conn.execute("SELECT 1 FROM media_asset WHERE id=?", (asset["id"],)).fetchone()
    check("asset đã bị xoá khỏi CSDL", gone is None)
    conn.close()
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | grep -B1 -A5 "media_library"
```
Expected: FAIL — `ModuleNotFoundError: No module named 'acp.core.media_library'`.

- [ ] **Step 3: Tạo `core/media_library.py`**

```python
"""Thư viện ảnh dùng lại được giữa nhiều bài (sub-project D3).

ACP KHÔNG gọi bất kỳ API sinh ảnh AI nào (xem core/imaging.py -- nguyên tắc
đã chốt: model sinh ảnh làm biến dạng sản phẩm thật, gây hoàn đơn mất uy
tín kênh). Module này chỉ lưu trữ/xác thực ảnh operator tự upload hoặc tự
dán URL -- có thể là ảnh AI sinh ở công cụ ngoài (ChatGPT, DALL-E...),
nhưng operator tự quyết định dùng ảnh nào, ACP không tự động chèn ảnh nào
chưa qua mắt người.
"""
import os
from io import BytesIO

from PIL import Image

from .db import now, ulid
from ..adapters.safe_http import SafeHttpClient, SafeHttpError

_EXT_BY_FORMAT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}


class MediaValidationError(Exception):
    """Dữ liệu không phải ảnh thật/không đọc được."""


def _verify_image_bytes(data: bytes) -> str:
    """Xác thực đúng là ảnh hợp lệ, trả về đuôi file theo ĐỊNH DẠNG THẬT --
    không tin đuôi file/Content-Type người dùng khai báo."""
    try:
        probe = Image.open(BytesIO(data))
        fmt = (probe.format or "").upper()
        probe.verify()
    except Exception as exc:
        raise MediaValidationError("Dữ liệu ảnh không hợp lệ.") from exc
    return _EXT_BY_FORMAT.get(fmt, ".img")


def _write_local(data: bytes, media_dir: str, ext: str) -> str:
    os.makedirs(media_dir, exist_ok=True)
    path = os.path.join(media_dir, f"{ulid()}{ext}")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def materialize_uploaded_file(file_storage, media_dir: str) -> str:
    """file_storage: werkzeug FileStorage (request.files['image']). Xác
    thực đúng là ảnh thật rồi lưu cục bộ, trả về đường dẫn file."""
    data = file_storage.read()
    ext = _verify_image_bytes(data)
    return _write_local(data, media_dir, ext)


def materialize_external_image(url: str, media_dir: str, http_client=None) -> str:
    """Tải ảnh từ URL ngoài (cùng cơ chế an toàn với
    ShopeeAffiliateSource.materialize_image() -- SafeHttpClient chặn SSRF/
    redirect quá mức), xác thực đúng là ảnh thật rồi lưu cục bộ. KHÔNG lưu
    thẳng URL ngoài vào media_asset.url -- tránh carousel vỡ ảnh nếu link
    tạm (vd link ChatGPT sinh ra) hết hạn trước khi Meta kịp tải lúc
    publish."""
    client = http_client or SafeHttpClient(max_bytes=8 * 1024 * 1024)
    try:
        response = client.get(url, allowed_hosts=None, expected_content_prefix="image/")
    except SafeHttpError as exc:
        raise MediaValidationError(f"Không tải được ảnh từ URL: {exc}") from exc
    ext = _verify_image_bytes(response.content)
    return _write_local(response.content, media_dir, ext)


def create_media_asset(conn, local_path: str, source: str, storage_backend) -> dict:
    """Upload file cục bộ lên storage (S3/local, giống ảnh ghép ở
    imaging.compose()), ghi 1 dòng media_asset. source: 'upload' | 'url'."""
    url = storage_backend.put(local_path)
    asset_id = ulid()
    conn.execute("INSERT INTO media_asset (id, url, source, created_at) VALUES (?,?,?,?)",
                 (asset_id, url, source, now()))
    return {"id": asset_id, "url": url, "source": source}


def list_media_assets(conn) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM media_asset ORDER BY created_at DESC").fetchall()]


def delete_media_asset(conn, asset_id: str) -> dict:
    used = conn.execute(
        "SELECT COUNT(*) FROM post_media WHERE media_asset_id=?", (asset_id,)).fetchone()[0]
    if used:
        return {"ok": False, "error": f"Ảnh đang được dùng ở {used} bài, không xoá được"}
    row = conn.execute("SELECT id FROM media_asset WHERE id=?", (asset_id,)).fetchone()
    if not row:
        return {"ok": False, "error": "Không tìm thấy ảnh"}
    conn.execute("DELETE FROM media_asset WHERE id=?", (asset_id,))
    return {"ok": True}
```

- [ ] **Step 4: Chạy lại test, xác nhận PASS**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -20
```

- [ ] **Step 5: Thêm lời gọi test vào `__main__`**

Thêm `test_media_library_validates_and_stores_uploaded_bytes()` và
`test_media_library_create_list_delete_asset()` vào danh sách, ngay trước
`print(f"\n{len(PASS)} đạt, {len(FAIL)} hỏng")` cuối file.

- [ ] **Step 6: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add core/media_library.py tests/test_pipeline.py
git commit -m "feat: core/media_library.py -- xác thực/lưu ảnh + CRUD thư viện (D3)"
```

---

## Task 3: Web layer `/thuvien-anh` — route + template + test end-to-end

**Bài học từ D1 (đọc trước khi làm):** final review của D1 phát hiện 1 lỗi
Critical vì route và template bị tách thành 2 task riêng, không task nào
có test kiểm tra cả luồng thật. Task này **gộp route + template + 1 test
end-to-end thật** trong cùng 1 task, tránh lặp lại lỗ hổng đó.

**Files:**
- Modify: `web/server.py` (thêm import, 3 route mới)
- Create: `web/templates/media_library.html`
- Modify: `web/templates/base.html` (thêm nav item)
- Test: `tests/test_pilot.py`

**Interfaces:**
- Consumes: `core.media_library.materialize_uploaded_file`,
  `materialize_external_image`, `create_media_asset`, `list_media_assets`,
  `delete_media_asset`, `MediaValidationError` (Task 2).

- [ ] **Step 1: Đọc `web/server.py` để xác nhận đúng vị trí thêm route
      (ngay sau route `/media/<path:name>`, trước "chọn sản phẩm")**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
sed -n '1,35p' web/server.py
grep -n "def media(name)" -A 5 web/server.py
```

- [ ] **Step 2: Viết test end-to-end (RED trước khi thêm route/template)**

Thêm vào `tests/test_pilot.py`, ngay trước `if __name__ == "__main__":`:

```python
def test_thuvien_anh_upload_list_delete_end_to_end():
    print("\n/thuvien-anh: upload file + dán URL, hiện đúng trong grid, xoá đúng luồng")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    from io import BytesIO
    from PIL import Image
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    # Kiểm tra TEMPLATE thực sự render đúng field mà route sẽ đọc, trước
    # khi POST -- cùng lý do đã áp dụng ở D1/D2 (route/template lệch nhau
    # không test nào bắt được).
    page_before = c.get("/thuvien-anh")
    check("trang /thuvien-anh mở được", page_before.status_code == 200, page_before.status_code)
    body_before = page_before.get_data(as_text=True)
    check("form upload có field file 'image'", 'name="image"' in body_before, body_before[:1000])
    check("form upload có field 'image_url'", 'name="image_url"' in body_before, body_before[:1000])

    img = Image.new("RGB", (12, 12), (5, 6, 7))
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    r = c.post("/thuvien-anh/upload", data={
        "_csrf": csrf,
        "image": (buf, "test.png"),
    }, content_type="multipart/form-data")
    check("upload file thành công, redirect về /thuvien-anh",
          r.status_code == 302 and "err=" not in (r.location or ""), (r.status_code, r.location))

    page_after = c.get("/thuvien-anh")
    body_after = page_after.get_data(as_text=True)
    conn = connect()
    asset = conn.execute("SELECT * FROM media_asset WHERE source='upload' ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    check("asset vừa upload có mặt trong grid", asset["url"] in body_after, asset["url"] if asset else None)

    with c.session_transaction() as sess:
        csrf2 = sess["csrf"]
    r2 = c.post(f"/thuvien-anh/{asset['id']}/xoa", data={"_csrf": csrf2})
    check("xoá asset không ai dùng thành công",
          r2.status_code == 302 and "err=" not in (r2.location or ""), (r2.status_code, r2.location))
    conn = connect()
    gone = conn.execute("SELECT 1 FROM media_asset WHERE id=?", (asset["id"],)).fetchone()
    conn.close()
    check("asset đã bị xoá khỏi CSDL", gone is None)

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)
```

- [ ] **Step 3: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
acp/.venv/bin/python3 -m acp.tests.test_pilot 2>&1 | grep -B1 -A5 "upload file"
```
Expected: FAIL — route `/thuvien-anh` chưa tồn tại (404).

- [ ] **Step 4: Thêm import + 3 route vào `web/server.py`**

Tìm dòng import:
```python
from ..core import attribution, jobs, pipeline, scoring, storage
```
Thay bằng:
```python
from ..core import attribution, jobs, media_library, pipeline, scoring, storage
```

Tìm route `/media/<path:name>`:
```python
    @app.route("/media/<path:name>")
    def media(name):
        return send_from_directory(MEDIA_DIR, name)
```
Thêm ngay sau đó (trước khối comment `# ----------------------------------------------------------- doanh thu`):
```python
    # ---------------------------------------------------------- thư viện ảnh

    @app.route("/thuvien-anh")
    def media_library_page():
        conn = connect()
        assets = media_library.list_media_assets(conn)
        pending = conn.execute(
            "SELECT COUNT(*) FROM post WHERE status IN ('PENDING_REVIEW','DRAFT')").fetchone()[0]
        conn.close()
        return render_template("media_library.html", page="thu-vien-anh",
                               assets=assets, pending_review=pending)

    @app.route("/thuvien-anh/upload", methods=["POST"])
    def media_library_upload():
        file = request.files.get("image")
        url = request.form.get("image_url", "").strip()
        try:
            if file and file.filename:
                local_path = media_library.materialize_uploaded_file(file, MEDIA_DIR)
                source = "upload"
            elif url:
                local_path = media_library.materialize_external_image(url, MEDIA_DIR)
                source = "url"
            else:
                return redirect(url_for("media_library_page", err="Chọn file hoặc dán URL"))
        except media_library.MediaValidationError as exc:
            return redirect(url_for("media_library_page", err=str(exc)))
        conn = connect()
        media_library.create_media_asset(conn, local_path, source, storage.get_storage())
        conn.close()
        return redirect(url_for("media_library_page"))

    @app.route("/thuvien-anh/<asset_id>/xoa", methods=["POST"])
    def media_library_delete(asset_id):
        conn = connect()
        res = media_library.delete_media_asset(conn, asset_id)
        conn.close()
        return redirect(url_for("media_library_page", err=None if res["ok"] else res["error"]))
```

- [ ] **Step 5: Tạo `web/templates/media_library.html`**

```html
{% extends "base.html" %}
{% block title %}Thư viện ảnh — ACP{% endblock %}
{% block content %}
<div class="page-header">
  <div><div class="eyebrow">Media workspace</div><h1>Thư viện ảnh</h1><p class="lede">Ảnh dùng lại được cho nhiều bài — tải lên hoặc dán URL, gắn vào bài lúc tạo ở /sanpham.</p></div>
</div>
{% if request.args.get('err') %}<div class="alert alert--error"><strong>Không thực hiện được.</strong><span>{{ request.args.get('err') }}</span></div>{% endif %}
<form method="post" action="/thuvien-anh/upload" enctype="multipart/form-data" class="card inline-form">
  <input type="hidden" name="_csrf" value="{{ csrf_token }}">
  <div class="field"><label for="image">Tải ảnh từ máy</label><input type="file" id="image" name="image" accept="image/*"></div>
  <div class="field field--grow"><label for="image_url">Hoặc dán URL ảnh</label><input id="image_url" name="image_url" inputmode="url" placeholder="https://..."></div>
  <button class="btn btn--primary" type="submit">Thêm vào thư viện</button>
</form>
{% if assets %}
<div class="niche-grid">
{% for a in assets %}
  <div class="channel-caption-row">
    <img src="{{ a.url }}" alt="Ảnh thư viện" style="width:100%;border-radius:6px" loading="lazy">
    <form method="post" action="/thuvien-anh/{{ a.id }}/xoa"><input type="hidden" name="_csrf" value="{{ csrf_token }}"><button class="btn btn--small btn--danger" type="submit">Xoá</button></form>
  </div>
{% endfor %}
</div>
{% else %}
<div class="empty-state">Chưa có ảnh nào trong thư viện.</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 6: Thêm nav item vào `web/templates/base.html`**

Tìm:
```html
      <a href="/sanpham" class="nav-item {{ 'nav-item--active' if page=='san-pham' }}"><span class="nav-icon">◇</span><span class="nav-label">Sản phẩm</span></a>
```
Thêm ngay sau:
```html
      <a href="/sanpham" class="nav-item {{ 'nav-item--active' if page=='san-pham' }}"><span class="nav-icon">◇</span><span class="nav-label">Sản phẩm</span></a>
      <a href="/thuvien-anh" class="nav-item {{ 'nav-item--active' if page=='thu-vien-anh' }}"><span class="nav-icon">▤</span><span class="nav-label">Thư viện ảnh</span></a>
```

- [ ] **Step 7: Chạy lại test end-to-end, xác nhận PASS**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
acp/.venv/bin/python3 -m acp.tests.test_pilot 2>&1 | grep -B1 -A5 "upload file"
```

- [ ] **Step 8: Chạy toàn bộ 2 test suite, xác nhận không regression**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -5
acp/.venv/bin/python3 -m acp.tests.test_pilot 2>&1 | tail -5
```

- [ ] **Step 9: Thêm lời gọi test vào `__main__` của `test_pilot.py`**

Thêm `test_thuvien_anh_upload_list_delete_end_to_end()` vào danh sách,
ngay trước `print(f"\n{len(PASS)} đạt, {len(FAIL)} hỏng")` cuối file.

- [ ] **Step 10: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add web/server.py web/templates/media_library.html web/templates/base.html tests/test_pilot.py
git commit -m "feat: trang /thuvien-anh -- upload/xem/xoá ảnh thư viện (D3)"
```

---

## Task 4: Ghi `post_media` lúc tạo bài + `post_media_urls()`

**Files:**
- Modify: `core/pipeline.py` (`_create_post_from_raw_product`,
  `create_post_for_product`, `create_post_from_manual_affiliate_product`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `post_media_urls(conn, post_id: str) -> list[str]` (đúng thứ
  tự `position`). `_create_post_from_raw_product`/2 hàm gọi nó nhận thêm
  `media_asset_ids: list = None`.

- [ ] **Step 1: Viết test cho ghi `post_media` + validate trần/tồn tại**

Thêm vào `tests/test_pipeline.py`, ngay trước `if __name__ == "__main__":`:

```python
def test_create_post_with_media_asset_ids():
    print("\nTạo post với media_asset_ids -> đúng N dòng post_media, đúng thứ tự position")
    conn = connect()
    asset_ids = []
    for i in range(3):
        aid = ulid()
        conn.execute("INSERT INTO media_asset (id, url, source, created_at) VALUES (?,?,?,?)",
                     (aid, f"https://fake.example/{i}.jpg", "upload", now()))
        asset_ids.append(aid)
    try:
        src = MockAccessTrade()
        target = next(p for p in src.fetch_products(limit=50) if p.product_url)
        ctx = {"source": src, "publishers": {}}
        res = pipeline.create_post_for_product(
            conn, ctx, target.external_product_id, "test", media_asset_ids=asset_ids)
        check("tạo bài với media_asset_ids thành công", res.get("ok"), res.get("error"))
        rows = conn.execute(
            "SELECT media_asset_id, position FROM post_media WHERE post_id=? ORDER BY position",
            (res["post_id"],)).fetchall()
        check("đúng 3 dòng post_media", len(rows) == 3, len(rows))
        check("đúng thứ tự position khớp asset_ids đã submit",
              [r["media_asset_id"] for r in rows] == asset_ids, [dict(r) for r in rows])
    finally:
        conn.close()


def test_create_post_media_asset_ids_over_cap_rejected():
    print("\nTạo post với hơn 9 media_asset_ids -> lỗi rõ, không tạo post")
    conn = connect()
    asset_ids = []
    for i in range(10):
        aid = ulid()
        conn.execute("INSERT INTO media_asset (id, url, source, created_at) VALUES (?,?,?,?)",
                     (aid, f"https://fake.example/cap{i}.jpg", "upload", now()))
        asset_ids.append(aid)
    src = MockAccessTrade()
    target = next(p for p in src.fetch_products(limit=50) if p.product_url)
    ctx = {"source": src, "publishers": {}}
    before = conn.execute("SELECT COUNT(*) FROM post").fetchone()[0]
    res = pipeline.create_post_for_product(
        conn, ctx, target.external_product_id, "test", media_asset_ids=asset_ids)
    check("tạo bài thất bại vì vượt trần 9 ảnh thêm", res.get("ok") is False, res)
    check("thông báo lỗi nêu rõ số lượng", "10" in (res.get("error") or ""), res.get("error"))
    after = conn.execute("SELECT COUNT(*) FROM post").fetchone()[0]
    check("không tạo post nào", before == after, (before, after))
    conn.close()


def test_create_post_media_asset_id_not_found_rejected():
    print("\nTạo post với 1 media_asset_id không tồn tại -> lỗi rõ, không tạo post, không tạo post_media")
    conn = connect()
    src = MockAccessTrade()
    target = next(p for p in src.fetch_products(limit=50) if p.product_url)
    ctx = {"source": src, "publishers": {}}
    before = conn.execute("SELECT COUNT(*) FROM post").fetchone()[0]
    fake_id = ulid()
    res = pipeline.create_post_for_product(
        conn, ctx, target.external_product_id, "test", media_asset_ids=[fake_id])
    check("tạo bài thất bại vì asset không tồn tại", res.get("ok") is False, res)
    check("thông báo lỗi nêu rõ asset id", fake_id in (res.get("error") or ""), res.get("error"))
    after = conn.execute("SELECT COUNT(*) FROM post").fetchone()[0]
    check("không tạo post nào", before == after, (before, after))
    conn.close()


def test_post_media_urls_returns_ordered_urls():
    print("\npost_media_urls() trả đúng URL theo thứ tự position")
    conn = connect()
    asset_ids, urls = [], []
    for i in range(3):
        aid = ulid()
        url = f"https://fake.example/ordered{i}.jpg"
        conn.execute("INSERT INTO media_asset (id, url, source, created_at) VALUES (?,?,?,?)",
                     (aid, url, "upload", now()))
        asset_ids.append(aid)
        urls.append(url)
    src = MockAccessTrade()
    target = next(p for p in src.fetch_products(limit=50) if p.product_url)
    ctx = {"source": src, "publishers": {}}
    # Submit theo thứ tự ĐẢO NGƯỢC để chắc chắn kiểm tra đúng position, không
    # phải trùng hợp thứ tự insert.
    reversed_ids = list(reversed(asset_ids))
    res = pipeline.create_post_for_product(
        conn, ctx, target.external_product_id, "test", media_asset_ids=reversed_ids)
    check("tạo bài thành công", res.get("ok"), res.get("error"))
    result = pipeline.post_media_urls(conn, res["post_id"])
    check("post_media_urls trả đúng thứ tự theo submit (đảo ngược)",
          result == list(reversed(urls)), (result, urls))
    conn.close()
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | grep -B1 -A5 "media_asset_ids\|post_media_urls"
```
Expected: FAIL — `create_post_for_product()` chưa nhận `media_asset_ids`
(`TypeError`), `pipeline.post_media_urls` chưa tồn tại (`AttributeError`).

- [ ] **Step 3: Thêm `post_media_urls()`**

Trong `core/pipeline.py`, thêm ngay sau hàm `_save_channel_selection`
(trước `post_channel_selections`):
```python
def post_media_urls(conn, post_id: str) -> list:
    """Ảnh THÊM (không gồm ảnh ghép tự động ở post.image_url_composited),
    đúng thứ tự position. Dùng ở publish_post() để dựng carousel."""
    return [r["url"] for r in conn.execute("""
        SELECT ma.url FROM post_media pm JOIN media_asset ma ON ma.id = pm.media_asset_id
        WHERE pm.post_id=? ORDER BY pm.position
    """, (post_id,)).fetchall()]
```

- [ ] **Step 4: Sửa `_create_post_from_raw_product()`**

Thay chữ ký hàm:
```python
def _create_post_from_raw_product(conn, ctx, source, raw, campaign_code: str,
                                  channel_code: str = None, channel_codes: list = None,
                                  template_code: str = None,
                                  variant_code: str = "A", prebuilt_affiliate_link: str = None,
                                  attribution_payload: dict = None,
                                  audit_action: str = "created_single") -> dict:
    campaign = conn.execute("SELECT * FROM campaign WHERE code=?", (campaign_code,)).fetchone()
    if not campaign:
        return {"ok": False, "error": f"Chưa có chiến dịch {campaign_code}"}
```
bằng:
```python
def _create_post_from_raw_product(conn, ctx, source, raw, campaign_code: str,
                                  channel_code: str = None, channel_codes: list = None,
                                  template_code: str = None,
                                  variant_code: str = "A", prebuilt_affiliate_link: str = None,
                                  attribution_payload: dict = None,
                                  audit_action: str = "created_single",
                                  media_asset_ids: list = None) -> dict:
    campaign = conn.execute("SELECT * FROM campaign WHERE code=?", (campaign_code,)).fetchone()
    if not campaign:
        return {"ok": False, "error": f"Chưa có chiến dịch {campaign_code}"}
    if media_asset_ids:
        # Validate NGAY TRONG HÀM NÀY (không chỉ ở route web) -- pipeline là
        # nguồn sự thật duy nhất, web chỉ là 1 trong nhiều caller có thể có,
        # đúng khuôn channel_codes/_resolve_channels_by_code đã làm ở D1.
        if len(media_asset_ids) > 9:
            return {"ok": False, "error": f"Tối đa 9 ảnh thêm, nhận {len(media_asset_ids)}"}
        for aid in media_asset_ids:
            if not conn.execute("SELECT 1 FROM media_asset WHERE id=?", (aid,)).fetchone():
                return {"ok": False, "error": f"Không tìm thấy ảnh {aid} trong thư viện"}
```

Tìm dòng:
```python
    _save_channel_selection(conn, post_id, channel_ids)
    audit(conn, "post", post_id, audit_action, actor="operator",
```
Thay bằng:
```python
    _save_channel_selection(conn, post_id, channel_ids)
    if media_asset_ids:
        for i, aid in enumerate(media_asset_ids, start=1):
            conn.execute("INSERT INTO post_media (post_id, media_asset_id, position) VALUES (?,?,?)",
                         (post_id, aid, i))
    audit(conn, "post", post_id, audit_action, actor="operator",
```

- [ ] **Step 5: Sửa `create_post_for_product()` và
      `create_post_from_manual_affiliate_product()`**

Thay:
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
bằng:
```python
def create_post_for_product(conn, ctx, external_product_id: str, campaign_code: str,
                            channel_code: str = None, channel_codes: list = None,
                            template_code: str = None, variant_code: str = "A",
                            media_asset_ids: list = None) -> dict:
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
        template_code=template_code, variant_code=variant_code,
        media_asset_ids=media_asset_ids)
```

Thay:
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
```
bằng (chỉ đổi chữ ký + tham số truyền xuống, giữ nguyên phần thân còn
lại phía dưới `prebuilt_affiliate_link=affiliate_url,` chưa hiển thị ở
đây):
```python
def create_post_from_manual_affiliate_product(conn, ctx, source, raw, affiliate_url: str,
                                               campaign_code: str, channel_code: str = None,
                                               channel_codes: list = None,
                                               template_code: str = None,
                                               variant_code: str = "A",
                                               media_asset_ids: list = None) -> dict:
    """Tạo bài review từ sản phẩm Shopee + affiliate URL có sẵn; không publish."""
    if not affiliate_url or not affiliate_url.startswith(("http://", "https://")):
        return {"ok": False, "error": "Thiếu link affiliate hợp lệ"}
    if not raw.name or raw.current_price <= 0 or not raw.image_url_original:
        return {"ok": False, "error": "Thiếu tên, giá hoặc ảnh sản phẩm"}
    return _create_post_from_raw_product(
        conn, ctx, source, raw, campaign_code,
        channel_code=channel_code, channel_codes=channel_codes,
        template_code=template_code, variant_code=variant_code,
        media_asset_ids=media_asset_ids,
```
(Dòng cuối `media_asset_ids=media_asset_ids,` thêm vào NGAY TRƯỚC dòng
`prebuilt_affiliate_link=affiliate_url,` đã có sẵn trong lời gọi — đọc kỹ
phần thân hàm hiện tại trước khi sửa để không xoá nhầm tham số khác.)

- [ ] **Step 6: Chạy toàn bộ `test_pipeline.py`, xác nhận PASS, không
      regression**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -30
```
Expected: 4 test mới PASS. Mọi test cũ gọi `create_post_for_product()`/
`create_post_from_manual_affiliate_product()` không truyền
`media_asset_ids` vẫn PASS y hệt.

- [ ] **Step 7: Thêm lời gọi test vào `__main__`**

Thêm 4 hàm test mới vào danh sách, ngay trước `print(f"\n{len(PASS)} đạt,
{len(FAIL)} hỏng")` cuối file.

- [ ] **Step 8: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add core/pipeline.py tests/test_pipeline.py
git commit -m "feat: tạo post nhận media_asset_ids, ghi post_media (D3)"
```

---

## Task 5: `publish_post()` cắt ảnh đúng trần từng platform

**Files:**
- Modify: `core/pipeline.py` (`publish_post`, dòng ~696)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `post_media_urls()` (Task 4).
- Produces: hằng số `MEDIA_MAX_COUNT: dict`.

- [ ] **Step 1: Viết test — mỗi target nhận đúng số ảnh theo trần
      platform**

Thêm vào `tests/test_pipeline.py`, ngay trước `if __name__ == "__main__":`:

```python
def test_publish_post_clips_media_to_platform_limit():
    print("\npublish_post: cắt đúng số ảnh theo trần platform trước khi gọi publisher")
    conn = connect()
    fb_id, ig_id = ulid(), ulid()
    for cid, code, platform, handle in [
        (fb_id, "fb_media_clip_test", "facebook", "FB Media Clip"),
        (ig_id, "ig_media_clip_test", "instagram", "IG Media Clip"),
    ]:
        conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                        daily_post_cap, min_gap_minutes, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (cid, code, platform, handle, "ACTIVE", 1, 12, 0, now()))
    asset_ids = []
    for i in range(3):
        aid = ulid()
        conn.execute("INSERT INTO media_asset (id, url, source, created_at) VALUES (?,?,?,?)",
                     (aid, f"https://fake.example/clip{i}.jpg", "upload", now()))
        asset_ids.append(aid)
    try:
        src = MockAccessTrade()
        target_product = next(p for p in src.fetch_products(limit=50) if p.product_url)
        ctx = {"source": src, "publishers": {}}
        res = pipeline.create_post_for_product(
            conn, ctx, target_product.external_product_id, "test",
            channel_codes=["ch1", "fb_media_clip_test", "ig_media_clip_test"],
            media_asset_ids=asset_ids)
        check("tạo bài đa kênh với ảnh thêm thành công", res.get("ok"), res.get("error"))
        post = conn.execute("SELECT * FROM post WHERE id=?", (res["post_id"],)).fetchone()

        approve_res = pipeline.approve_post(
            conn, post["id"], channel_ids=[post["channel_id"], fb_id, ig_id])
        check("duyệt thành công", approve_res["ok"], approve_res)
        for t in approve_res["targets"]:
            conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?",
                         (now(), f"pub:{t['publish_target_id']}"))

        th_pub, fb_pub, ig_pub = MockThreads(seed=141), MockFacebookPublisher(seed=142), MockInstagramPublisher(seed=143)
        jobs.drain(conn, ctx={"source": MockAccessTrade(),
                              "publishers": {"threads": th_pub, "facebook": fb_pub, "instagram": ig_pub}})

        # MockThreads.published chỉ lưu (pid, caption), không lưu media -- kiểm
        # tra bằng publish_target SUCCESS (chứng minh không bị ValueError chặn
        # vì thừa ảnh, đúng thứ Task 5 cần chứng minh cho Threads) là đủ; FB/IG
        # thì .published lưu cả media nên kiểm tra được trực tiếp độ dài.
        target_th = conn.execute("SELECT status FROM publish_target WHERE post_id=? AND channel_id=?",
                                 (post["id"], post["channel_id"])).fetchone()
        check("target Threads SUCCESS (không bị ValueError vì thừa ảnh)",
              target_th["status"] == "SUCCESS", dict(target_th))

        fb_media = fb_pub.published[0][2]
        ig_media = ig_pub.published[0][2]
        check("target Facebook nhận đủ 4 ảnh (1 ghép + 3 thêm)", len(fb_media) == 4, fb_media)
        check("target Instagram nhận đủ 4 ảnh (1 ghép + 3 thêm)", len(ig_media) == 4, ig_media)
        check("ảnh ghép luôn là ảnh đầu tiên trong media Facebook",
              fb_media[0] == post["image_url_composited"], (fb_media[0], post["image_url_composited"]))
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id IN (?,?)", (fb_id, ig_id))
        conn.close()
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | grep -B1 -A8 "cắt đúng số ảnh"
```
Expected: FAIL — target Threads gặp `ValueError` (chưa cắt ảnh), test mất
`raise` giữa chừng (drain đã bắt exception thành FAILED, kỳ vọng vẫn không
khớp SUCCESS/độ dài media FB/IG chỉ có 1 ảnh vì `post_media_urls` chưa tồn
tại logic gộp).

- [ ] **Step 3: Sửa `publish_post()`**

Trong `core/pipeline.py`, thêm hằng số ngay trước `def publish_post(conn,
payload, ctx):`:
```python
# Trùng đúng giới hạn đã hard-code trong publish() của từng Publisher
# (adapters/mock.py, adapters/live.py: Threads len(media)>1 báo lỗi,
# Facebook/Instagram 1-10 ảnh) -- 2 nguồn cùng giá trị, sửa 1 chỗ nhớ sửa
# chỗ kia, cùng rủi ro/cách xử lý đã chốt ở content.PLATFORM_MAX_LEN (D2).
MEDIA_MAX_COUNT = {"threads": 1, "facebook": 10, "instagram": 10}


```

Tìm:
```python
        publisher = ctx["publishers"][channel["platform"]]
        media = [post["image_url_composited"]] if post["image_url_composited"] else []
        caption = _resolve_caption(post, target, channel)
        result = publisher.publish(channel, caption, media=media)
```
Thay bằng:
```python
        publisher = ctx["publishers"][channel["platform"]]
        media = [post["image_url_composited"]] if post["image_url_composited"] else []
        media += post_media_urls(conn, post["id"])
        media = media[:MEDIA_MAX_COUNT.get(channel["platform"], 1)]
        caption = _resolve_caption(post, target, channel)
        result = publisher.publish(channel, caption, media=media)
```

- [ ] **Step 4: Chạy lại test, xác nhận PASS**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -20
```
Expected: test mới PASS. Mọi test cũ (post không có ảnh thêm) vẫn PASS y
hệt vì `post_media_urls()` trả `[]`, `media` giữ nguyên đúng 1 phần tử như
trước D3.

- [ ] **Step 5: Thêm lời gọi test vào `__main__`**, và thêm import cần
      thiết

Đầu `tests/test_pipeline.py`, xác nhận dòng import đã có
`MockFacebookPublisher, MockInstagramPublisher, MockThreads` (đã thêm từ
D2 Task 5 — kiểm tra bằng `grep -n "from acp.adapters.mock import"
tests/test_pipeline.py` trước khi sửa, không thêm trùng nếu đã có).

Thêm `test_publish_post_clips_media_to_platform_limit()` vào danh sách
trong `if __name__ == "__main__":`.

- [ ] **Step 6: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add core/pipeline.py tests/test_pipeline.py
git commit -m "feat: publish_post cắt ảnh đúng trần từng platform (D3)"
```

---

## Task 6: `/sanpham` — chọn ảnh thêm + gợi ý prompt AI (route + template + test)

**Bài học từ D1/D2 (đọc trước khi làm):** gộp route + template + 1 test
end-to-end thật trong cùng 1 task — tránh lỗ hổng route/template lệch nhau
không test nào bắt được.

**Files:**
- Modify: `web/server.py` (`_product_common_context`, `_render_affiliate`,
  `products()`, `create_from_product()`, `create_affiliate_product()`)
- Modify: `web/templates/products.html`
- Test: `tests/test_pilot.py`

**Interfaces:**
- Consumes: `media_library.list_media_assets()` (Task 2/3),
  `pipeline.create_post_for_product(media_asset_ids=)`,
  `pipeline.create_post_from_manual_affiliate_product(media_asset_ids=)`
  (Task 4).

**Lưu ý quan trọng — khác biệt giữa 2 chế độ:** chế độ Affiliate chỉ có
ĐÚNG 1 sản phẩm (`metadata`) trong ngữ cảnh render, nên khối gợi ý prompt
AI đặt Ở CẤP FORM (1 lần). Chế độ Tìm kiếm dùng CHUNG 1 form cho cả bảng
kết quả (nhiều sản phẩm khác nhau, D1 đã thiết kế vậy cho checklist kênh)
— khối gợi ý prompt AI ở đây phải đặt TRONG TỪNG DÒNG sản phẩm (dùng đúng
`p.name`/`p.current_price` của dòng đó), KHÔNG đặt ở cấp form như checklist
kênh/ảnh (2 checklist đó không phụ thuộc sản phẩm cụ thể nên đặt chung được,
prompt thì có).

- [ ] **Step 1: Đọc `web/server.py` và `products.html` để xác nhận đúng
      vị trí sửa**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
grep -n "_product_common_context\|_render_affiliate\|def products\|def create_from_product\|def create_affiliate_product" web/server.py
```

- [ ] **Step 2: Viết test end-to-end (RED trước khi sửa)**

Thêm vào `tests/test_pilot.py`, ngay trước `if __name__ == "__main__":`:

```python
def test_sanpham_affiliate_create_with_media_asset_ids_end_to_end():
    print("\n/sanpham affiliate: chọn ảnh thêm từ thư viện lúc tạo bài, ghi đúng post_media")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    asset_id = ulid()
    conn.execute("INSERT INTO media_asset (id, url, source, created_at) VALUES (?,?,?,?)",
                 (asset_id, "https://fake-storage.example/sanpham-test.jpg", "upload", now()))
    conn.close()

    # Kiểm tra TEMPLATE thực sự render đúng field mà route sẽ đọc, trước
    # khi POST.
    page = c.get("/sanpham?mode=affiliate&affiliate_url=https://s.shopee.vn/abc")
    body = page.get_data(as_text=True)
    check("checklist ảnh thêm render đúng field media_asset_ids",
          'name="media_asset_ids"' in body, body[:500])
    check("checklist có ảnh vừa tạo trong thư viện",
          "sanpham-test.jpg" in body, "không thấy trong checklist")

    class _FakeManualShopeeMedia:
        name = "manual_shopee"
        def validate_confirmed_urls(self, affiliate_url, product_url):
            pass
        def prepare_product(self, confirmed, media_dir):
            from acp.adapters.base import RawProduct
            return RawProduct(
                external_product_id="media-test-1", name=confirmed.name,
                current_price=confirmed.current_price, original_price=confirmed.original_price,
                commission_value=0, commission_rate=None, category_code="khac",
                product_url=confirmed.product_url, merchant="shopee.vn",
                image_url_original=confirmed.image_url, image_path_local=None)
        def create_tracking_link(self, *args, **kwargs):
            raise AssertionError("manual Shopee không được gọi create_tracking_link")

    app.config["SHOPEE_SOURCE_FACTORY"] = lambda: _FakeManualShopeeMedia()

    ch1 = connect().execute("SELECT id FROM channel WHERE code='ch1'").fetchone()
    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    r = c.post("/sanpham/affiliate/create", data={
        "_csrf": csrf,
        "affiliate_url": "https://s.shopee.vn/abc",
        "product_url": "https://shopee.vn/vay-i.123.456",
        "name": "Váy hoa nữ test D3",
        "current_price": "289000",
        "image_url": "https://img.example/product.jpg",
        "channel_codes": ["ch1"],
        "media_asset_ids": [asset_id],
    })
    check("tạo bài với ảnh thêm thành công, redirect sang /duyet",
          r.status_code == 302 and "/duyet" in r.location, (r.status_code, getattr(r, "location", "")))

    conn = connect()
    post = conn.execute("""SELECT p.id FROM post p JOIN product pr ON pr.id = p.product_id
                           WHERE pr.external_product_id='media-test-1' ORDER BY p.id DESC LIMIT 1""").fetchone()
    check("tìm được post vừa tạo", post is not None, post)
    pm = conn.execute("SELECT media_asset_id, position FROM post_media WHERE post_id=?", (post["id"],)).fetchone()
    check("post_media ghi đúng asset đã chọn, position=1",
          pm is not None and pm["media_asset_id"] == asset_id and pm["position"] == 1,
          dict(pm) if pm else None)
    conn.close()

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_sanpham_search_mode_shows_media_checklist_and_per_row_prompt():
    print("\n/sanpham tìm kiếm: hiện checklist ảnh thêm + prompt AI riêng từng dòng sản phẩm")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    asset_id = ulid()
    conn.execute("INSERT INTO media_asset (id, url, source, created_at) VALUES (?,?,?,?)",
                 (asset_id, "https://fake-storage.example/search-mode-test.jpg", "upload", now()))
    conn.close()

    page = c.get("/sanpham?nguon=mock")
    body = page.get_data(as_text=True)
    check("checklist ảnh thêm render đúng field media_asset_ids ở chế độ tìm kiếm",
          'name="media_asset_ids"' in body, body[:500])
    check("checklist có ảnh vừa tạo trong thư viện",
          "search-mode-test.jpg" in body, "không thấy trong checklist")
    check("mỗi dòng sản phẩm có khối gợi ý prompt riêng (nhiều khối <details>)",
          body.count("Gợi ý prompt") >= 1, body.count("Gợi ý prompt"))

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)
```

- [ ] **Step 3: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
acp/.venv/bin/python3 -m acp.tests.test_pilot 2>&1 | grep -B1 -A5 "chọn ảnh thêm từ thư viện\|hiện checklist ảnh thêm"
```
Expected: FAIL — field `media_asset_ids` chưa được template render, route
chưa đọc.

- [ ] **Step 4: Sửa `_product_common_context()` và các hàm render**

Trong `web/server.py`, tìm:
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
        media_assets = media_library.list_media_assets(conn)
        conn.close()
        return pending, channels, media_assets
```

Tìm:
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
bằng:
```python
    def _render_affiliate(*, affiliate_url="", resolved=None, metadata=None,
                          err=None, warning=None, selected_channels=None, status=200):
        pending, channels, media_assets = _product_common_context()
        return render_template(
            "products.html", page="san-pham", mode="affiliate", items=[], q="", err=err,
            source_name="manual_shopee", pending_review=pending, channels=channels,
            affiliate_url=affiliate_url, resolved=resolved,
            metadata=metadata or ProductMetadata(), metadata_warning=warning,
            selected_channels=selected_channels or [], platform_labels=PLATFORM_LABELS,
            media_assets=media_assets,
        ), status
```

Tìm (trong `products()`):
```python
        pending, channels = _product_common_context()
        return render_template(
            "products.html", page="san-pham", mode="search", items=items, q=q, err=err,
            source_name=source_name or os.environ.get("ACP_SOURCE", "mock"),
            pending_review=pending, channels=channels, resolved=None,
            metadata=ProductMetadata(), affiliate_url="", platform_labels=PLATFORM_LABELS)
```
bằng:
```python
        pending, channels, media_assets = _product_common_context()
        return render_template(
            "products.html", page="san-pham", mode="search", items=items, q=q, err=err,
            source_name=source_name or os.environ.get("ACP_SOURCE", "mock"),
            pending_review=pending, channels=channels, resolved=None,
            metadata=ProductMetadata(), affiliate_url="", platform_labels=PLATFORM_LABELS,
            media_assets=media_assets)
```

- [ ] **Step 5: Sửa `create_from_product()` và `create_affiliate_product()`**

Tìm:
```python
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
```
bằng:
```python
        channel_codes = request.form.getlist("channel_codes")
        media_asset_ids = request.form.getlist("media_asset_ids")
        if not external_id:
            return redirect(url_for("products", q=q, err="Thiếu mã sản phẩm"))
        if not channel_codes:
            return redirect(url_for("products", q=q, err="Chọn ít nhất 1 kênh"))
        conn = connect()
        try:
            res = pipeline.create_post_for_product(
                conn, factory.build_context(source_name), external_id,
                campaign_code=os.environ.get("ACP_CAMPAIGN_CODE", "gd2026"),
                channel_codes=channel_codes, media_asset_ids=media_asset_ids or None)
```

Tìm (trong `create_affiliate_product()`):
```python
        channel_codes = request.form.getlist("channel_codes")
```
bằng:
```python
        channel_codes = request.form.getlist("channel_codes")
        media_asset_ids = request.form.getlist("media_asset_ids")
```

Tìm:
```python
            res = pipeline.create_post_from_manual_affiliate_product(
                conn, {"storage": storage.get_storage()}, source, raw,
                affiliate_url=affiliate_url,
                campaign_code=os.environ.get("ACP_CAMPAIGN_CODE", "gd2026"),
                channel_codes=channel_codes)
```
bằng:
```python
            res = pipeline.create_post_from_manual_affiliate_product(
                conn, {"storage": storage.get_storage()}, source, raw,
                affiliate_url=affiliate_url,
                campaign_code=os.environ.get("ACP_CAMPAIGN_CODE", "gd2026"),
                channel_codes=channel_codes, media_asset_ids=media_asset_ids or None)
```

- [ ] **Step 6: Sửa `web/templates/products.html` — chế độ Affiliate**

Tìm:
```html
        <div class="field field--full">
          <label>Kênh đăng bài</label>
          <div class="niche-grid">
          {% for ch in channels %}
            <label class="niche-tile"><input type="checkbox" name="channel_codes" value="{{ ch.code }}" {{ 'checked' if ch.code in selected_channels }}><span>[{{ platform_labels[ch.platform] }}] {{ ch.handle }}<small>{{ ch.code }}</small></span></label>
          {% endfor %}
          </div>
        </div>
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
        <div class="field field--full">
          <details><summary>💡 Gợi ý prompt tạo ảnh AI (dán vào ChatGPT/DALL-E ngoài, tự upload kết quả vào /thuvien-anh)</summary>
            <textarea readonly rows="4">Ảnh sản phẩm quảng cáo cho "{{ metadata.name or '' }}"{% if metadata.current_price %}, giá {{ metadata.current_price|vnd }}{% endif %}.
Phong cách: ảnh chụp sản phẩm studio chuyên nghiệp, nền sáng đơn sắc, ánh sáng tự nhiên, không có chữ/logo/watermark, tỷ lệ vuông 1:1. KHÔNG vẽ bao bì/nhãn hiệu cụ thể nào ngoài mô tả trên.</textarea>
          </details>
        </div>
        <div class="field field--full">
          <label>Ảnh thêm cho carousel (tối đa 9, ảnh ghép tự động luôn là ảnh đầu tiên)</label>
          <div class="niche-grid">
          {% for asset in media_assets %}
            <label class="niche-tile"><input type="checkbox" name="media_asset_ids" value="{{ asset.id }}">
              <img src="{{ asset.url }}" alt="Ảnh thư viện" style="width:100%;border-radius:6px" loading="lazy"></label>
          {% endfor %}
          </div>
        </div>
      </div>
```
(Chỉ có 1 chỗ khớp trong file — `</div>\n      </div>` đóng `.form-grid`
ngay sau checklist kênh. Xác nhận đúng vị trí bằng cách đọc nguyên khối
trước khi sửa.)

- [ ] **Step 7: Sửa `web/templates/products.html` — chế độ Tìm kiếm**

Tìm:
```html
    <div class="field field--full">
      <label>Kênh đăng bài (áp dụng cho nút "Tạo bài" ở bất kỳ dòng nào bên dưới)</label>
      <div class="niche-grid">
      {% for ch in channels %}
        <label class="niche-tile"><input type="checkbox" name="channel_codes" value="{{ ch.code }}"><span>[{{ platform_labels[ch.platform] }}] {{ ch.handle }}<small>{{ ch.code }}</small></span></label>
      {% endfor %}
      </div>
    </div>
    <div class="table-card">
```
bằng:
```html
    <div class="field field--full">
      <label>Kênh đăng bài (áp dụng cho nút "Tạo bài" ở bất kỳ dòng nào bên dưới)</label>
      <div class="niche-grid">
      {% for ch in channels %}
        <label class="niche-tile"><input type="checkbox" name="channel_codes" value="{{ ch.code }}"><span>[{{ platform_labels[ch.platform] }}] {{ ch.handle }}<small>{{ ch.code }}</small></span></label>
      {% endfor %}
      </div>
    </div>
    <div class="field field--full">
      <label>Ảnh thêm cho carousel (áp dụng cho nút "Tạo bài" ở bất kỳ dòng nào bên dưới, tối đa 9)</label>
      <div class="niche-grid">
      {% for asset in media_assets %}
        <label class="niche-tile"><input type="checkbox" name="media_asset_ids" value="{{ asset.id }}">
          <img src="{{ asset.url }}" alt="Ảnh thư viện" style="width:100%;border-radius:6px" loading="lazy"></label>
      {% endfor %}
      </div>
    </div>
    <div class="table-card">
```

Tìm:
```html
            <td><strong>{{ p.name }}</strong><span class="mono-sub">{{ p.external_product_id }}</span></td>
```
bằng (thêm khối `<details>` gợi ý prompt riêng cho từng dòng, dùng đúng
`p.name`/`p.current_price` của dòng đó — KHÔNG đặt ở cấp form như 2
checklist phía trên vì mỗi dòng là 1 sản phẩm khác nhau):
```html
            <td><strong>{{ p.name }}</strong><span class="mono-sub">{{ p.external_product_id }}</span>
              <details><summary>💡 Gợi ý prompt AI</summary>
                <textarea readonly rows="3">Ảnh sản phẩm quảng cáo cho "{{ p.name }}", giá {{ p.current_price|vnd }}.
Phong cách: ảnh chụp sản phẩm studio chuyên nghiệp, nền sáng đơn sắc, ánh sáng tự nhiên, không có chữ/logo/watermark, tỷ lệ vuông 1:1. KHÔNG vẽ bao bì/nhãn hiệu cụ thể nào ngoài mô tả trên.</textarea>
              </details>
            </td>
```

- [ ] **Step 8: Chạy lại test end-to-end, xác nhận PASS**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
acp/.venv/bin/python3 -m acp.tests.test_pilot 2>&1 | grep -B1 -A5 "chọn ảnh thêm từ thư viện\|hiện checklist ảnh thêm"
```

- [ ] **Step 9: Chạy toàn bộ 2 test suite, xác nhận không regression**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -5
acp/.venv/bin/python3 -m acp.tests.test_pilot 2>&1 | tail -5
```
Expected: cả 2 dòng cuối đều `0 hỏng`.

- [ ] **Step 10: Thêm lời gọi test vào `__main__` của `test_pilot.py`**

Thêm `test_sanpham_affiliate_create_with_media_asset_ids_end_to_end()` và
`test_sanpham_search_mode_shows_media_checklist_and_per_row_prompt()` vào
danh sách, ngay trước `print(f"\n{len(PASS)} đạt, {len(FAIL)} hỏng")` cuối
file.

- [ ] **Step 11: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add web/server.py web/templates/products.html tests/test_pilot.py
git commit -m "feat: /sanpham chọn ảnh thêm từ thư viện + gợi ý prompt AI (D3)"
```

---

## Sau khi cả 6 task hoàn tất

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -5
acp/.venv/bin/python3 -m acp.tests.test_pilot 2>&1 | tail -5
```

Baseline trước D3: `test_pipeline.py` 262/0, `test_pilot.py` 295/0. Sau D3
kỳ vọng: `test_pipeline.py` tăng thêm ~11 test mới (Task 1: 1, Task 2: 2,
Task 4: 4, Task 5: 1) = khoảng 273-280/0 (con số check() chính xác tuỳ
implementer đếm lại khi chạy thật); `test_pilot.py` +4 test (Task 3: 1,
Task 6: 2) = khoảng 300-310/0.
