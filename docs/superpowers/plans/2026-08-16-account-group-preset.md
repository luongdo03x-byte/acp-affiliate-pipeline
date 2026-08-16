# AccountGroup/preset chọn nhanh — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cho operator tạo/quản lý 1 bộ channel đặt tên trước (AccountGroup)
ở `/kenh`, rồi bấm 1 nút để tick nhanh cả nhóm ở checklist "Kênh đăng bài"
của `/sanpham`, thay vì tích từng channel một.

**Architecture:** 2 bảng mới hoàn toàn tách biệt khỏi `post`/
`publish_target` (`account_group`, `account_group_channel`), 4 hàm CRUD
mới trong `core/pipeline.py`, 3 route mới ở `/kenh` (tạo/sửa/xoá nhóm), và
1 khối JS thuần (vanilla, đầu tiên trong codebase) ở `/sanpham` tick sẵn
checkbox `channel_codes` đã có từ D1. Không đụng luồng tạo bài/publish nào.

**Tech Stack:** Python 3, Flask, Jinja2, SQLite (qua `core/db.py`), vanilla
JavaScript (không framework/build step).

**Spec:** `docs/superpowers/specs/2026-08-16-account-group-preset-design.md`

## Global Constraints

- Toàn bộ code mới, comment, docstring, copy UI viết bằng tiếng Việt, đúng
  giọng văn hiện có trong file đang sửa.
- AccountGroup thuần là tiện ích UI — **không** lưu vết nhóm đã dùng vào
  `post`/`publish_target`, **không** đổi bất kỳ hàm nào trong luồng tạo
  bài/publish hiện có (`_create_post_from_raw_product`,
  `create_post_for_product`, `create_post_from_manual_affiliate_product`,
  `publish_post`).
- Không có UI chọn nhóm ở `/duyệt` — chỉ ở `/sanpham` lúc tạo bài (cả 2 chế
  độ).
- 1 channel được thuộc nhiều nhóm; 1 nhóm được trộn platform thoải mái.
- Cơ chế chọn nhóm ở `/sanpham` **cộng dồn** (tick thêm), không bao giờ tự
  bỏ tick — operator vẫn tự tay điều chỉnh sau khi bấm nút nhóm.
- `update_account_group_channels()` **ghi đè toàn bộ** thành viên (không
  phải thêm/bớt từng cái).
- Mọi `channel_ids` truyền vào CRUD phải bỏ trùng (order-preserving,
  `list(dict.fromkeys(...))`) TRƯỚC khi validate/ghi — rút kinh nghiệm lỗi
  trùng `media_asset_ids` vỡ INSERT ở final review D3 (PK
  `(group_id, channel_id)` có cùng lớp rủi ro với PK
  `(post_id, media_asset_id)`).
- Route + template + 1 test end-to-end thật trong cùng 1 task; test phải
  GET (hoặc route xem-trước không mutate tương đương) TRƯỚC khi POST, xác
  nhận template render đúng field route sẽ đọc.
- Không dùng framework/build step cho JS — 1 khối `<script>` inline thuần
  vanilla, không CDN, không bundler.
- Mọi tham số/route mới không phá bất kỳ test nào đang xanh
  (`test_pipeline.py` 297/0, `test_pilot.py` 314/0 tính đến khi bắt đầu
  D4 phần A).

---

### Task 1: Schema — `account_group` + `account_group_channel`

**Files:**
- Modify: `core/db.py` (thêm 2 bảng vào `SCHEMA`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: bảng `account_group(id, code, name, created_at)`, bảng
  `account_group_channel(group_id, channel_id, created_at)` PK
  `(group_id, channel_id)`.

- [ ] **Step 1: Viết test schema (RED trước khi thêm bảng)**

Thêm vào `tests/test_pipeline.py`, ngay trước `if __name__ == "__main__":`:

```python
def test_account_group_schema():
    print("\nbảng account_group + account_group_channel đã có trong schema")
    conn = connect()
    ag_cols = {r["name"] for r in conn.execute("PRAGMA table_info(account_group)").fetchall()}
    check("account_group có đủ cột", {"id", "code", "name", "created_at"} <= ag_cols, ag_cols)
    agc_cols = {r["name"] for r in conn.execute("PRAGMA table_info(account_group_channel)").fetchall()}
    check("account_group_channel có đủ cột",
          {"group_id", "channel_id", "created_at"} <= agc_cols, agc_cols)

    channel = conn.execute("SELECT id FROM channel LIMIT 1").fetchone()
    group_id = ulid()
    conn.execute("INSERT INTO account_group (id, code, name, created_at) VALUES (?,?,?,?)",
                 (group_id, "nhom-test-abc123", "Nhóm test", now()))
    conn.execute("INSERT INTO account_group_channel (group_id, channel_id, created_at) VALUES (?,?,?)",
                 (group_id, channel["id"], now()))
    row = conn.execute("SELECT 1 FROM account_group_channel WHERE group_id=? AND channel_id=?",
                       (group_id, channel["id"])).fetchone()
    check("account_group_channel lưu đúng dòng", row is not None)

    import sqlite3
    try:
        conn.execute("INSERT INTO account_group_channel (group_id, channel_id, created_at) VALUES (?,?,?)",
                     (group_id, channel["id"], now()))
        check("PK (group_id, channel_id) chặn trùng lặp", False, "insert trùng lọt qua")
    except sqlite3.IntegrityError as e:
        check("PK (group_id, channel_id) chặn trùng lặp", "UNIQUE constraint failed" in str(e), str(e))
    conn.close()
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | grep -B1 -A8 "account_group"
```
Expected: FAIL — `no such table: account_group`.

- [ ] **Step 3: Thêm 2 bảng vào `core/db.py`**

Tìm (ngay sau khối `post_media`, trước `meta_connection`):
```python
CREATE TABLE IF NOT EXISTS post_media (
    post_id         TEXT NOT NULL REFERENCES post(id),
    media_asset_id  TEXT NOT NULL REFERENCES media_asset(id),
    position        INTEGER NOT NULL,
    PRIMARY KEY (post_id, media_asset_id)
);
CREATE INDEX IF NOT EXISTS idx_post_media_post ON post_media(post_id, position);

CREATE TABLE IF NOT EXISTS meta_connection (
```
Thay bằng:
```python
CREATE TABLE IF NOT EXISTS post_media (
    post_id         TEXT NOT NULL REFERENCES post(id),
    media_asset_id  TEXT NOT NULL REFERENCES media_asset(id),
    position        INTEGER NOT NULL,
    PRIMARY KEY (post_id, media_asset_id)
);
CREATE INDEX IF NOT EXISTS idx_post_media_post ON post_media(post_id, position);

CREATE TABLE IF NOT EXISTS account_group (
    id          TEXT PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_group_channel (
    group_id    TEXT NOT NULL REFERENCES account_group(id),
    channel_id  TEXT NOT NULL REFERENCES channel(id),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (group_id, channel_id)
);

CREATE TABLE IF NOT EXISTS meta_connection (
```

- [ ] **Step 4: Chạy lại test, xác nhận PASS**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -20
```
Expected: test mới PASS, mọi test cũ (297 trước đó) vẫn PASS y hệt.

- [ ] **Step 5: Thêm lời gọi test vào `__main__`**

Thêm `test_account_group_schema()` vào danh sách, ngay trước
`print(f"\n{len(PASS)} đạt, {len(FAIL)} hỏng")` cuối file.

- [ ] **Step 6: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add core/db.py tests/test_pipeline.py
git commit -m "feat: thêm bảng account_group + account_group_channel (D4-A)"
```

---

### Task 2: `core/pipeline.py` — CRUD AccountGroup

**Files:**
- Modify: `core/pipeline.py` (thêm `import re`, `import unicodedata`, 4 hàm
  CRUD mới + 1 helper `_slugify`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: bảng `account_group`/`account_group_channel` (Task 1).
- Produces: `create_account_group(conn, name: str, channel_ids: list) -> dict`
  (trả `{"ok": True, "group_id": str}` hoặc `{"ok": False, "error": str}`),
  `update_account_group_channels(conn, group_id: str, channel_ids: list) -> dict`,
  `delete_account_group(conn, group_id: str) -> dict`,
  `list_account_groups(conn) -> list[dict]` (mỗi dict có `id, code, name,
  created_at, channels: list[dict], channel_codes: list[str]`).

- [ ] **Step 1: Viết test cho toàn bộ 4 hàm CRUD (RED trước khi thêm hàm)**

Thêm vào `tests/test_pipeline.py`, ngay trước `if __name__ == "__main__":`:

```python
def test_create_account_group():
    print("\ncreate_account_group() -- đúng tên, đúng N dòng account_group_channel")
    conn = connect()
    ch1 = conn.execute("SELECT id FROM channel WHERE code='ch1'").fetchone()["id"]
    aux_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (aux_id, "ag_test_ch2", "facebook", "AG Test FB", "ACTIVE", 1, 12, 0, now()))
    res = pipeline.create_account_group(conn, "Nhóm test D4", [ch1, aux_id])
    check("tạo nhóm thành công", res.get("ok"), res.get("error"))
    rows = conn.execute("SELECT channel_id FROM account_group_channel WHERE group_id=?",
                        (res["group_id"],)).fetchall()
    check("đúng 2 dòng account_group_channel", len(rows) == 2, len(rows))
    grp = conn.execute("SELECT name, code FROM account_group WHERE id=?", (res["group_id"],)).fetchone()
    check("tên đúng", grp["name"] == "Nhóm test D4", dict(grp))
    check("code tự sinh không rỗng, khác id", bool(grp["code"]) and grp["code"] != res["group_id"], grp["code"])
    conn.close()


def test_create_account_group_channel_not_found_rejected():
    print("\ncreate_account_group() với channel không tồn tại -> lỗi rõ, không tạo nhóm")
    conn = connect()
    before = conn.execute("SELECT COUNT(*) FROM account_group").fetchone()[0]
    fake_id = ulid()
    res = pipeline.create_account_group(conn, "Nhóm lỗi", [fake_id])
    check("tạo nhóm thất bại vì kênh không tồn tại", res.get("ok") is False, res)
    check("thông báo lỗi nêu rõ channel id", fake_id in (res.get("error") or ""), res.get("error"))
    after = conn.execute("SELECT COUNT(*) FROM account_group").fetchone()[0]
    check("không tạo nhóm nào", before == after, (before, after))
    conn.close()


def test_create_account_group_duplicate_channel_ids_deduplicated():
    print("\ncreate_account_group() với channel_ids trùng -> tự bỏ trùng, không vỡ INSERT")
    conn = connect()
    ch1 = conn.execute("SELECT id FROM channel WHERE code='ch1'").fetchone()["id"]
    res = pipeline.create_account_group(conn, "Nhóm trùng", [ch1, ch1])
    check("tạo nhóm thành công dù channel_ids trùng", res.get("ok"), res.get("error"))
    n = conn.execute("SELECT COUNT(*) FROM account_group_channel WHERE group_id=?",
                     (res["group_id"],)).fetchone()[0]
    check("chỉ 1 dòng account_group_channel dù submit trùng 2 lần", n == 1, n)
    conn.close()


def test_update_account_group_channels_overwrites_membership():
    print("\nupdate_account_group_channels() ghi đè toàn bộ thành viên")
    conn = connect()
    ch1 = conn.execute("SELECT id FROM channel WHERE code='ch1'").fetchone()["id"]
    aux_a, aux_b = ulid(), ulid()
    for cid, code in [(aux_a, "ag_upd_a"), (aux_b, "ag_upd_b")]:
        conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                        daily_post_cap, min_gap_minutes, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (cid, code, "facebook", code, "ACTIVE", 1, 12, 0, now()))
    res = pipeline.create_account_group(conn, "Nhóm sửa", [ch1, aux_a])
    upd = pipeline.update_account_group_channels(conn, res["group_id"], [aux_a, aux_b])
    check("sửa thành công", upd.get("ok"), upd.get("error"))
    rows = {r["channel_id"] for r in conn.execute(
        "SELECT channel_id FROM account_group_channel WHERE group_id=?", (res["group_id"],)).fetchall()}
    check("thành viên đúng {aux_a, aux_b}, không còn ch1", rows == {aux_a, aux_b}, rows)
    conn.close()


def test_update_account_group_channels_not_found_rejected():
    print("\nupdate_account_group_channels() với group_id không tồn tại -> lỗi rõ")
    conn = connect()
    res = pipeline.update_account_group_channels(conn, ulid(), [])
    check("sửa thất bại vì nhóm không tồn tại", res.get("ok") is False, res)
    conn.close()


def test_delete_account_group_removes_group_and_members():
    print("\ndelete_account_group() xoá cả account_group lẫn account_group_channel liên quan")
    conn = connect()
    ch1 = conn.execute("SELECT id FROM channel WHERE code='ch1'").fetchone()["id"]
    res = pipeline.create_account_group(conn, "Nhóm xoá", [ch1])
    d = pipeline.delete_account_group(conn, res["group_id"])
    check("xoá thành công", d.get("ok"), d.get("error"))
    gone_group = conn.execute("SELECT 1 FROM account_group WHERE id=?", (res["group_id"],)).fetchone()
    check("account_group đã bị xoá", gone_group is None)
    gone_members = conn.execute("SELECT COUNT(*) FROM account_group_channel WHERE group_id=?",
                                (res["group_id"],)).fetchone()[0]
    check("account_group_channel liên quan đã bị xoá hết", gone_members == 0, gone_members)
    conn.close()


def test_delete_account_group_not_found_rejected():
    print("\ndelete_account_group() với group_id không tồn tại -> lỗi rõ")
    conn = connect()
    res = pipeline.delete_account_group(conn, ulid())
    check("xoá thất bại vì nhóm không tồn tại", res.get("ok") is False, res)
    conn.close()


def test_list_account_groups_returns_channels_and_codes():
    print("\nlist_account_groups() trả đúng nhóm + đúng channel_codes theo đúng nhóm")
    conn = connect()
    ch1 = conn.execute("SELECT id, code FROM channel WHERE code='ch1'").fetchone()
    res = pipeline.create_account_group(conn, "Nhóm list", [ch1["id"]])
    groups = pipeline.list_account_groups(conn)
    grp = next((g for g in groups if g["id"] == res["group_id"]), None)
    check("tìm được nhóm vừa tạo", grp is not None, res["group_id"])
    check("channel_codes chứa đúng ch1", grp["channel_codes"] == [ch1["code"]], grp["channel_codes"])
    check("channels có đủ object channel (không chỉ id)",
          grp["channels"] and grp["channels"][0]["code"] == ch1["code"], grp["channels"])
    conn.close()
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | grep -B1 -A5 "account_group\b" | grep -v "account_group_schema\|account_group_channel đã"
```
Expected: FAIL — `AttributeError: module 'acp.core.pipeline' has no attribute 'create_account_group'`.

- [ ] **Step 3: Thêm `import re`, `import unicodedata` vào đầu `core/pipeline.py`**

Tìm:
```python
import json
import os
import random
from datetime import datetime, timedelta, timezone
```
Thay bằng:
```python
import json
import os
import random
import re
import unicodedata
from datetime import datetime, timedelta, timezone
```

- [ ] **Step 4: Thêm 4 hàm CRUD + `_slugify` vào `core/pipeline.py`**

Tìm:
```python
def active_niches(conn, channel_id: str = None) -> list:
    """Tương thích ngược: có channel_id thì lấy của kênh, không thì lấy cấu hình chung."""
    if channel_id:
        return channel_niches(conn, channel_id)
    _, filters = scoring.active_config(conn)
    return filters.get("niches") or []


def upsert_one(conn, source, raw) -> str:
```
Thay bằng:
```python
def active_niches(conn, channel_id: str = None) -> list:
    """Tương thích ngược: có channel_id thì lấy của kênh, không thì lấy cấu hình chung."""
    if channel_id:
        return channel_niches(conn, channel_id)
    _, filters = scoring.active_config(conn)
    return filters.get("niches") or []


# --------------------------------------------------- AccountGroup/preset

def _slugify(name: str) -> str:
    """Bỏ dấu tiếng Việt, hạ chữ thường, thay ký tự không phải chữ/số bằng
    '-' -- dùng để tự sinh account_group.code từ name (operator không tự
    gõ code tay)."""
    s = unicodedata.normalize("NFD", name or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "D").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "nhom"


def create_account_group(conn, name: str, channel_ids: list) -> dict:
    """Tạo 1 AccountGroup mới -- preset chọn nhanh channel ở /sanpham, thuần
    tiện ích UI, KHÔNG ảnh hưởng logic tạo bài/publish. Tất-cả-hoặc-không-gì:
    có 1 channel không tồn tại thì không tạo nhóm nào."""
    channel_ids = list(dict.fromkeys(channel_ids or []))  # bỏ trùng, giữ thứ tự
    for cid in channel_ids:
        if not conn.execute("SELECT 1 FROM channel WHERE id=?", (cid,)).fetchone():
            return {"ok": False, "error": f"Không tìm thấy kênh {cid}"}
    group_id = ulid()
    code = f"{_slugify(name)}-{group_id[:6]}"
    conn.execute("INSERT INTO account_group (id, code, name, created_at) VALUES (?,?,?,?)",
                 (group_id, code, name, now()))
    for cid in channel_ids:
        conn.execute(
            "INSERT INTO account_group_channel (group_id, channel_id, created_at) VALUES (?,?,?)",
            (group_id, cid, now()))
    audit(conn, "account_group", group_id, "created", actor="operator",
          detail={"name": name, "channel_ids": channel_ids})
    return {"ok": True, "group_id": group_id}


def update_account_group_channels(conn, group_id: str, channel_ids: list) -> dict:
    """Ghi đè TOÀN BỘ thành viên của 1 nhóm (không thêm/bớt từng cái)."""
    if not conn.execute("SELECT 1 FROM account_group WHERE id=?", (group_id,)).fetchone():
        return {"ok": False, "error": f"Không tìm thấy nhóm {group_id}"}
    channel_ids = list(dict.fromkeys(channel_ids or []))  # bỏ trùng, giữ thứ tự
    for cid in channel_ids:
        if not conn.execute("SELECT 1 FROM channel WHERE id=?", (cid,)).fetchone():
            return {"ok": False, "error": f"Không tìm thấy kênh {cid}"}
    conn.execute("DELETE FROM account_group_channel WHERE group_id=?", (group_id,))
    for cid in channel_ids:
        conn.execute(
            "INSERT INTO account_group_channel (group_id, channel_id, created_at) VALUES (?,?,?)",
            (group_id, cid, now()))
    audit(conn, "account_group", group_id, "updated_channels", actor="operator",
          detail={"channel_ids": channel_ids})
    return {"ok": True}


def delete_account_group(conn, group_id: str) -> dict:
    """Xoá nhóm + mọi dòng account_group_channel liên quan. Không có bảng
    nào khác tham chiếu account_group.id nên không cần chặn kiểu
    media_asset phải chặn khi còn post_media tham chiếu."""
    if not conn.execute("SELECT 1 FROM account_group WHERE id=?", (group_id,)).fetchone():
        return {"ok": False, "error": f"Không tìm thấy nhóm {group_id}"}
    conn.execute("DELETE FROM account_group_channel WHERE group_id=?", (group_id,))
    conn.execute("DELETE FROM account_group WHERE id=?", (group_id,))
    audit(conn, "account_group", group_id, "deleted", actor="operator")
    return {"ok": True}


def list_account_groups(conn) -> list:
    """Mỗi nhóm kèm channels (đủ object channel, theo thứ tự tạo thành
    viên) + channel_codes (list code phẳng, rút từ channels) -- dùng ở
    /kenh hiển thị + /sanpham dựng nút chọn nhanh."""
    groups = [dict(r) for r in conn.execute(
        "SELECT * FROM account_group ORDER BY created_at").fetchall()]
    for g in groups:
        g["channels"] = [dict(r) for r in conn.execute(
            """SELECT ch.* FROM account_group_channel agc
               JOIN channel ch ON ch.id = agc.channel_id
               WHERE agc.group_id=? ORDER BY agc.created_at""", (g["id"],)).fetchall()]
        g["channel_codes"] = [c["code"] for c in g["channels"]]
    return groups


def upsert_one(conn, source, raw) -> str:
```

- [ ] **Step 5: Chạy lại test, xác nhận PASS**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -30
```
Expected: 8 test mới PASS, mọi test cũ vẫn PASS y hệt.

- [ ] **Step 6: Thêm lời gọi test vào `__main__`**

Thêm 8 hàm test mới vào danh sách, ngay trước `print(f"\n{len(PASS)} đạt,
{len(FAIL)} hỏng")` cuối file.

- [ ] **Step 7: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add core/pipeline.py tests/test_pipeline.py
git commit -m "feat: core/pipeline.py CRUD AccountGroup (D4-A)"
```

---

### Task 3: `/kenh` — quản lý nhóm (route + template + test)

**Bài học từ D1/D3 (đọc trước khi làm):** gộp route + template + 1 test
end-to-end thật trong cùng 1 task — tránh lỗ hổng route/template lệch
nhau không test nào bắt được.

**Files:**
- Modify: `web/server.py` (`channels()`, 3 route mới)
- Modify: `web/templates/channels.html`
- Test: `tests/test_pilot.py`

**Interfaces:**
- Consumes: `pipeline.create_account_group()`,
  `pipeline.update_account_group_channels()`,
  `pipeline.delete_account_group()`, `pipeline.list_account_groups()`
  (Task 2).

- [ ] **Step 1: Đọc `web/server.py` xác nhận đúng vị trí sửa**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
grep -n "def channels\|def channel_enable\|def channel_disable" web/server.py
```

- [ ] **Step 2: Viết test end-to-end (RED trước khi sửa)**

Thêm vào `tests/test_pilot.py`, ngay trước `if __name__ == "__main__":`:

```python
def test_kenh_account_group_crud_end_to_end():
    print("\n/kenh: tạo/sửa/xoá AccountGroup, checklist đúng field, ghi đúng thành viên")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    ch1 = conn.execute("SELECT id, code FROM channel WHERE code='ch1'").fetchone()
    aux_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (aux_id, "kenh_ag_test", "facebook", "Kenh AG Test", "ACTIVE", 1, 12, 0, now()))
    conn.close()

    # Kiểm tra TEMPLATE thực sự render đúng field mà route sẽ đọc, trước
    # khi POST -- cùng lý do đã áp dụng ở D1/D3.
    page = c.get("/kenh")
    check("trang /kenh mở được", page.status_code == 200, page.status_code)
    body = page.get_data(as_text=True)
    check("form tạo nhóm có field 'name'", 'name="name"' in body, body[:1000])
    check("form tạo nhóm có checklist 'channel_ids'", 'name="channel_ids"' in body, body[:1000])

    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    r = c.post("/kenh/nhom/tao", data={
        "_csrf": csrf, "name": "Nhóm test D4 kenh",
        "channel_ids": [ch1["id"], aux_id],
    })
    check("tạo nhóm thành công, redirect về /kenh",
          r.status_code == 302 and "err=" not in (r.location or ""), (r.status_code, r.location))

    page_after = c.get("/kenh")
    body_after = page_after.get_data(as_text=True)
    check("tên nhóm vừa tạo có mặt trên trang", "Nhóm test D4 kenh" in body_after, "không thấy")

    conn = connect()
    group = conn.execute(
        "SELECT id FROM account_group WHERE name='Nhóm test D4 kenh' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    check("tìm được nhóm vừa tạo", group is not None, group)
    n_members = conn.execute("SELECT COUNT(*) FROM account_group_channel WHERE group_id=?",
                             (group["id"],)).fetchone()[0]
    check("đúng 2 thành viên", n_members == 2, n_members)
    conn.close()

    with c.session_transaction() as sess:
        csrf2 = sess["csrf"]
    r2 = c.post(f"/kenh/nhom/{group['id']}/sua", data={"_csrf": csrf2, "channel_ids": [aux_id]})
    check("sửa nhóm thành công, redirect về /kenh",
          r2.status_code == 302 and "err=" not in (r2.location or ""), (r2.status_code, r2.location))
    conn = connect()
    members = {r["channel_id"] for r in conn.execute(
        "SELECT channel_id FROM account_group_channel WHERE group_id=?", (group["id"],)).fetchall()}
    check("sau khi sửa chỉ còn đúng 1 thành viên (aux_id)", members == {aux_id}, members)
    conn.close()

    with c.session_transaction() as sess:
        csrf3 = sess["csrf"]
    r3 = c.post(f"/kenh/nhom/{group['id']}/xoa", data={"_csrf": csrf3})
    check("xoá nhóm thành công, redirect về /kenh",
          r3.status_code == 302 and "err=" not in (r3.location or ""), (r3.status_code, r3.location))
    conn = connect()
    gone = conn.execute("SELECT 1 FROM account_group WHERE id=?", (group["id"],)).fetchone()
    check("nhóm đã bị xoá khỏi CSDL", gone is None)
    conn.close()

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)
```

- [ ] **Step 3: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
acp/.venv/bin/python3 -m acp.tests.test_pilot 2>&1 | grep -B1 -A10 "AccountGroup, checklist"
```
Expected: FAIL — field `name="name"`/`channel_ids` chưa được render (route
`/kenh` chưa có khối tạo nhóm), route `/kenh/nhom/tao` chưa tồn tại (404).

- [ ] **Step 4: Sửa route `channels()`, thêm 3 route mới trong `web/server.py`**

Tìm:
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
Thay bằng:
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
        # D4-A: preset chọn nhanh -- checklist tạo nhóm cần TOÀN BỘ channel
        # ACTIVE (không lọc thêm enabled=1, khác /sanpham -- nhóm là preset
        # lâu dài, channel tạm tắt vẫn nên giữ trong nhóm để bật lại là dùng
        # được ngay, không cần tạo lại nhóm).
        all_active_channels = [r for r in rows if r["status"] == "ACTIVE"]
        account_groups = pipeline.list_account_groups(conn)
        conn.close()
        return render_template("channels.html", page="kenh", by_platform=by_platform,
                               all_niches=niche_mod.NICHES, saved=saved, pending_review=pending,
                               has_meta_connection=has_meta_connection,
                               summary=request.args.get("summary"),
                               all_active_channels=all_active_channels,
                               account_groups=account_groups, platform_labels=PLATFORM_LABELS)

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

    # ------------------------------------------------- nhóm account (D4-A)

    @app.route("/kenh/nhom/tao", methods=["POST"])
    def account_group_create():
        name = request.form.get("name", "").strip()
        channel_ids = request.form.getlist("channel_ids")
        if not name:
            return redirect(url_for("channels", err="Thiếu tên nhóm"))
        conn = connect()
        res = pipeline.create_account_group(conn, name, channel_ids)
        conn.close()
        return redirect(url_for("channels", err=None if res.get("ok") else res.get("error")))

    @app.route("/kenh/nhom/<group_id>/sua", methods=["POST"])
    def account_group_update(group_id):
        channel_ids = request.form.getlist("channel_ids")
        conn = connect()
        res = pipeline.update_account_group_channels(conn, group_id, channel_ids)
        conn.close()
        return redirect(url_for("channels", err=None if res.get("ok") else res.get("error")))

    @app.route("/kenh/nhom/<group_id>/xoa", methods=["POST"])
    def account_group_delete(group_id):
        conn = connect()
        res = pipeline.delete_account_group(conn, group_id)
        conn.close()
        return redirect(url_for("channels", err=None if res.get("ok") else res.get("error")))
```

- [ ] **Step 5: Thêm khu vực quản lý nhóm vào `web/templates/channels.html`**

Tìm (cuối file):
```html
{% endfor %}
{% if not by_platform %}<div class="empty-state">Chưa có kênh nào.</div>{% endif %}
{% endblock %}
```
Thay bằng:
```html
{% endfor %}
{% if not by_platform %}<div class="empty-state">Chưa có kênh nào.</div>{% endif %}

<div class="section-heading section-heading--spaced"><div><h2>Nhóm account (preset chọn nhanh)</h2><p class="note">Bấm nút tên nhóm ở /sanpham để tick nhanh cả nhóm thay vì tích từng kênh. Không ảnh hưởng bài đã tạo/đã đăng.</p></div></div>

<form method="post" action="/kenh/nhom/tao" class="card">
  <input type="hidden" name="_csrf" value="{{ csrf_token }}">
  <div class="field"><label for="group-name">Tên nhóm</label>
    <input id="group-name" name="name" required placeholder="VD: FB Page chính"></div>
  <div class="niche-grid">
  {% for ch in all_active_channels %}
    <label class="niche-tile"><input type="checkbox" name="channel_ids" value="{{ ch.id }}">
      <span>[{{ platform_labels[ch.platform] }}] {{ ch.handle }}<small>{{ ch.code }}</small></span></label>
  {% endfor %}
  </div>
  <button class="btn btn--primary" type="submit">Tạo nhóm</button>
</form>

{% if account_groups %}
<div class="channel-list">
{% for g in account_groups %}
<section class="card channel-card">
  <div class="channel-card__head">
    <div><div class="channel-card__title">{{ g.name }}</div><span class="mono-sub">{{ g.code }} · {{ g.channels|length }} kênh</span></div>
  </div>
  <div class="channel-meta">
  {% for ch in g.channels %}
    <span class="tag">[{{ platform_labels[ch.platform] }}] {{ ch.handle }}</span>
  {% endfor %}
  </div>
  <form method="post" action="/kenh/nhom/{{ g.id }}/sua">
    <input type="hidden" name="_csrf" value="{{ csrf_token }}">
    <div class="niche-grid">
    {% for ch in all_active_channels %}
      <label class="niche-tile"><input type="checkbox" name="channel_ids" value="{{ ch.id }}" {{ 'checked' if ch.id in (g.channels|map(attribute='id')|list) }}>
        <span>[{{ platform_labels[ch.platform] }}] {{ ch.handle }}<small>{{ ch.code }}</small></span></label>
    {% endfor %}
    </div>
    <div class="channel-card__foot">
      <button class="btn btn--primary" type="submit">Lưu thành viên cho {{ g.name }}</button>
      <button class="btn btn--danger" type="submit" formaction="/kenh/nhom/{{ g.id }}/xoa">Xoá nhóm</button>
    </div>
  </form>
</section>
{% endfor %}
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 6: Chạy lại test end-to-end, xác nhận PASS**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
acp/.venv/bin/python3 -m acp.tests.test_pilot 2>&1 | grep -B1 -A15 "AccountGroup, checklist"
```

- [ ] **Step 7: Chạy toàn bộ 2 test suite, xác nhận không regression**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -5
acp/.venv/bin/python3 -m acp.tests.test_pilot 2>&1 | tail -5
```
Expected: cả 2 dòng cuối đều `0 hỏng`.

- [ ] **Step 8: Thêm lời gọi test vào `__main__` của `test_pilot.py`**

Thêm `test_kenh_account_group_crud_end_to_end()` vào danh sách, ngay trước
`print(f"\n{len(PASS)} đạt, {len(FAIL)} hỏng")` cuối file.

- [ ] **Step 9: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add web/server.py web/templates/channels.html tests/test_pilot.py
git commit -m "feat: /kenh tạo/sửa/xoá AccountGroup (D4-A)"
```

---

### Task 4: `/sanpham` — chọn nhanh theo nhóm (route + template + JS + test)

**Bài học từ D1/D3 (đọc trước khi làm):** gộp route + template + 1 test
end-to-end thật trong cùng 1 task — tránh lỗ hổng route/template lệch
nhau không test nào bắt được. Đây cũng là task thêm `<script>` **đầu
tiên** trong toàn bộ codebase — giữ tối thiểu, thuần vanilla JS, không
build step.

**Files:**
- Modify: `web/server.py` (`_product_common_context()`,
  `_render_affiliate()`, `products()`)
- Modify: `web/templates/products.html`
- Test: `tests/test_pilot.py`

**Interfaces:**
- Consumes: `pipeline.list_account_groups()` (Task 2).

- [ ] **Step 1: Đọc `web/server.py` xác nhận đúng vị trí sửa**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
grep -n "_product_common_context\|def _render_affiliate\|def products\b" web/server.py
```

- [ ] **Step 2: Viết test end-to-end (RED trước khi sửa)**

Thêm vào `tests/test_pilot.py`, ngay trước `if __name__ == "__main__":`:

```python
def test_sanpham_shows_account_group_quick_select_both_modes():
    print("\n/sanpham cả 2 chế độ: hiện nút chọn nhanh theo nhóm, đúng channel_codes nhúng vào onclick")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    ch1 = conn.execute("SELECT id, code FROM channel WHERE code='ch1'").fetchone()
    group_res = pipeline.create_account_group(conn, "Nhóm sanpham test", [ch1["id"]])
    conn.close()
    check("tạo nhóm test thành công", group_res.get("ok"), group_res.get("error"))

    # Chế độ Tìm kiếm
    page_search = c.get("/sanpham?nguon=mock")
    body_search = page_search.get_data(as_text=True)
    check("chế độ tìm kiếm: tên nhóm hiện trên trang", "Nhóm sanpham test" in body_search, "không thấy")
    check("chế độ tìm kiếm: đúng channel_codes của nhóm nhúng vào onclick",
          ('acpTickGroup(this, ["' + ch1["code"] + '"]') in body_search, body_search[:2000])

    # Chế độ Affiliate: nút nhóm nằm trong form xác nhận (product-confirm__form),
    # chỉ render sau khi có resolved/metadata (xem ghi chú route thật ở D3 --
    # GET /sanpham?mode=affiliate KHÔNG bao giờ tới được form đó, phải POST
    # /sanpham/affiliate/resolve, route không mutate DB, dùng làm bước xem
    # trước đúng khuôn D3 đã lập).
    from acp.adapters.shopee_affiliate import ResolvedAffiliateUrl, ProductMetadata

    class _FakeManualShopeeAG:
        name = "manual_shopee"
        def resolve(self, url):
            return ResolvedAffiliateUrl(affiliate_url=url, product_url="https://shopee.vn/vay-i.1.1")
        def metadata(self, product_url):
            return ProductMetadata(name="SP test", current_price=100000, image_url="https://img/x.jpg")

    app.config["SHOPEE_SOURCE_FACTORY"] = lambda: _FakeManualShopeeAG()
    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    resolved_page = c.post("/sanpham/affiliate/resolve", data={
        "_csrf": csrf, "affiliate_url": "https://s.shopee.vn/abc"})
    body_affiliate = resolved_page.get_data(as_text=True)
    check("chế độ affiliate: tên nhóm hiện trên trang", "Nhóm sanpham test" in body_affiliate, "không thấy")
    check("chế độ affiliate: đúng channel_codes của nhóm nhúng vào onclick",
          ('acpTickGroup(this, ["' + ch1["code"] + '"]') in body_affiliate, body_affiliate[:2000])

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)
```

- [ ] **Step 3: Chạy test, xác nhận FAIL**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
acp/.venv/bin/python3 -m acp.tests.test_pilot 2>&1 | grep -B1 -A10 "hiện nút chọn nhanh theo nhóm"
```
Expected: FAIL — `acpTickGroup` chưa tồn tại trong trang (nút nhóm chưa
được thêm vào template).

- [ ] **Step 4: Sửa `_product_common_context()`, `_render_affiliate()`,
      `products()` trong `web/server.py`**

Tìm:
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

    @app.route("/sanpham")
    def products():
        """Tìm sản phẩm hoặc nhập link affiliate. Không có hành vi publish."""
        mode = request.args.get("mode", "search")
        if mode == "affiliate":
            return _render_affiliate(affiliate_url=request.args.get("affiliate_url", ""))

        q = request.args.get("q", "").strip()
        source_name = request.args.get("nguon") or None
        items, err = [], request.args.get("err")
        try:
            src = factory.get_source(source_name)
            if hasattr(src, "search_products"):
                items, _ = src.search_products(query=q or None, limit=24)
            else:
                err = err or f"Nguồn {src.name} không hỗ trợ tìm kiếm."
        except Exception as e:
            err = err or str(e)
        pending, channels, media_assets = _product_common_context()
        return render_template(
            "products.html", page="san-pham", mode="search", items=items, q=q, err=err,
            source_name=source_name or os.environ.get("ACP_SOURCE", "mock"),
            pending_review=pending, channels=channels, resolved=None,
            metadata=ProductMetadata(), affiliate_url="", platform_labels=PLATFORM_LABELS,
            media_assets=media_assets)
```
Thay bằng:
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
        # D4-A: nhóm chỉ dùng channel_codes (đủ so khớp checkbox), không cần
        # object channel đầy đủ ở đây -- khác /kenh nơi cần hiển thị chi
        # tiết từng thành viên.
        account_groups = [{"id": g["id"], "name": g["name"], "channel_codes": g["channel_codes"]}
                          for g in pipeline.list_account_groups(conn)]
        conn.close()
        return pending, channels, media_assets, account_groups

    def _render_affiliate(*, affiliate_url="", resolved=None, metadata=None,
                          err=None, warning=None, selected_channels=None, status=200):
        pending, channels, media_assets, account_groups = _product_common_context()
        return render_template(
            "products.html", page="san-pham", mode="affiliate", items=[], q="", err=err,
            source_name="manual_shopee", pending_review=pending, channels=channels,
            affiliate_url=affiliate_url, resolved=resolved,
            metadata=metadata or ProductMetadata(), metadata_warning=warning,
            selected_channels=selected_channels or [], platform_labels=PLATFORM_LABELS,
            media_assets=media_assets, account_groups=account_groups,
        ), status

    @app.route("/sanpham")
    def products():
        """Tìm sản phẩm hoặc nhập link affiliate. Không có hành vi publish."""
        mode = request.args.get("mode", "search")
        if mode == "affiliate":
            return _render_affiliate(affiliate_url=request.args.get("affiliate_url", ""))

        q = request.args.get("q", "").strip()
        source_name = request.args.get("nguon") or None
        items, err = [], request.args.get("err")
        try:
            src = factory.get_source(source_name)
            if hasattr(src, "search_products"):
                items, _ = src.search_products(query=q or None, limit=24)
            else:
                err = err or f"Nguồn {src.name} không hỗ trợ tìm kiếm."
        except Exception as e:
            err = err or str(e)
        pending, channels, media_assets, account_groups = _product_common_context()
        return render_template(
            "products.html", page="san-pham", mode="search", items=items, q=q, err=err,
            source_name=source_name or os.environ.get("ACP_SOURCE", "mock"),
            pending_review=pending, channels=channels, resolved=None,
            metadata=ProductMetadata(), affiliate_url="", platform_labels=PLATFORM_LABELS,
            media_assets=media_assets, account_groups=account_groups)
```

- [ ] **Step 5: Sửa `web/templates/products.html` — chế độ Affiliate**

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
        <div class="field field--full">
          <details><summary>💡 Gợi ý prompt tạo ảnh AI (dán vào ChatGPT/DALL-E ngoài, tự upload kết quả vào /thuvien-anh)</summary>
```
Thay bằng:
```html
        {% if account_groups %}
        <div class="field field--full">
          <label>Chọn nhanh theo nhóm</label>
          <div class="quick-group-row">
          {% for g in account_groups %}
            <button type="button" class="btn btn--small" onclick="acpTickGroup(this, {{ g.channel_codes|tojson }})">{{ g.name }}</button>
          {% endfor %}
          </div>
        </div>
        {% endif %}
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
```
(Chỉ có 1 chỗ khớp trong file — xác nhận đúng vị trí bằng cách đọc nguyên
khối trước khi sửa.)

- [ ] **Step 6: Sửa `web/templates/products.html` — chế độ Tìm kiếm**

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
    <div class="field field--full">
      <label>Ảnh thêm cho carousel (áp dụng cho nút "Tạo bài" ở bất kỳ dòng nào bên dưới, tối đa 9)</label>
```
Thay bằng:
```html
    {% if account_groups %}
    <div class="field field--full">
      <label>Chọn nhanh theo nhóm (áp dụng cho checklist kênh ngay bên dưới)</label>
      <div class="quick-group-row">
      {% for g in account_groups %}
        <button type="button" class="btn btn--small" onclick="acpTickGroup(this, {{ g.channel_codes|tojson }})">{{ g.name }}</button>
      {% endfor %}
      </div>
    </div>
    {% endif %}
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
```

- [ ] **Step 7: Thêm khối `<script>` vào cuối `web/templates/products.html`**

Tìm (cuối file):
```html
{% elif not err %}
  <div class="empty-state">Không tìm thấy sản phẩm nào.</div>
  {% endif %}
{% endif %}
{% endblock %}
```
Thay bằng:
```html
{% elif not err %}
  <div class="empty-state">Không tìm thấy sản phẩm nào.</div>
  {% endif %}
{% endif %}
<script>
// D4-A: preset chọn nhanh theo nhóm -- tick THÊM checkbox channel_codes
// khớp nhóm được bấm, KHÔNG bỏ tick cái đang tick sẵn. Operator vẫn tự tay
// điều chỉnh trước khi submit. <script> đầu tiên trong codebase -- giữ
// thuần vanilla, không framework/build step.
function acpTickGroup(btn, codes) {
  const form = btn.closest('form') || document;
  codes.forEach(function (code) {
    const box = form.querySelector('input[name="channel_codes"][value="' + code + '"]');
    if (box) box.checked = true;
  });
}
</script>
{% endblock %}
```

- [ ] **Step 8: Chạy lại test end-to-end, xác nhận PASS**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
acp/.venv/bin/python3 -m acp.tests.test_pilot 2>&1 | grep -B1 -A10 "hiện nút chọn nhanh theo nhóm"
```

- [ ] **Step 9: Chạy toàn bộ 2 test suite, xác nhận không regression**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -5
acp/.venv/bin/python3 -m acp.tests.test_pilot 2>&1 | tail -5
```
Expected: cả 2 dòng cuối đều `0 hỏng`.

- [ ] **Step 10: Thêm lời gọi test vào `__main__` của `test_pilot.py`**

Thêm `test_sanpham_shows_account_group_quick_select_both_modes()` vào danh
sách, ngay trước `print(f"\n{len(PASS)} đạt, {len(FAIL)} hỏng")` cuối file.

- [ ] **Step 11: Commit**

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import/acp
git add web/server.py web/templates/products.html tests/test_pilot.py
git commit -m "feat: /sanpham nút chọn nhanh theo AccountGroup (D4-A)"
```

---

## Sau khi cả 4 task hoàn tất

```bash
cd /home/dluowng/Downloads/ACP/worktrees/shopee-affiliate-import
python3 -m acp.tests.test_pipeline 2>&1 | tail -5
acp/.venv/bin/python3 -m acp.tests.test_pilot 2>&1 | tail -5
```

Baseline trước D4 phần A: `test_pipeline.py` 297/0, `test_pilot.py` 314/0.
Sau D4 phần A kỳ vọng: `test_pipeline.py` tăng thêm ~9 test mới (Task 1: 1,
Task 2: 8) = khoảng 306-320/0 (con số check() chính xác tuỳ implementer
đếm lại khi chạy thật); `test_pilot.py` +2 test (Task 3: 1, Task 4: 1) =
khoảng 320-335/0.
