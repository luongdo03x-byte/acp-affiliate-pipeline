# ACP 2.0 — Thiết kế MetaConnectionService & `/kenh` đa nền tảng (Sub-project B)

**Ngày:** 2026-08-14
**Trạng thái:** Thiết kế đã được chốt trong hội thoại; chờ review bản spec trước khi lập implementation plan.
**Thuộc:** Sub-project B trong 4 phần (A → B → C → D) chia nhỏ từ
`PTYC_ACP_FACEBOOK_INSTAGRAM_MULTI_ACCOUNT.md`. B build trên nền
`publish_target`/`Publisher` registry của sub-project A (đã merge vào
`feat/shopee-affiliate-import`). B là nền cho C (FacebookPublisher/
InstagramPublisher thật) và D (multi-select, preset, caption per-platform).

## 1. Mục tiêu

Cho phép operator kết nối một Facebook App (Meta Login) **một lần**, tự động
lấy về toàn bộ Facebook Page và Instagram Professional account có quyền quản
lý, và quản lý các account đó tại `/kenh` — mà **không đổi hành vi Threads
hiện tại** và không cần `FacebookPublisher`/`InstagramPublisher` thật (đó là
sub-project C).

Kết thúc B:

- operator bấm "Kết nối Meta" → OAuth → tự động import Page/IG account, không
  cần thao tác CLI;
- `/kenh` hiển thị account theo 3 nhóm platform, bật/tắt được, thấy trạng
  thái kết nối;
- "Đồng bộ lại" chạy lại discovery mà không cần OAuth lại;
- account mất quyền được đánh dấu `NEEDS_REAUTH`, không bị xoá;
- Threads/Shopee/ACCESSTRADE không regression.

## 2. Phạm vi

### Trong phạm vi

- Bảng `meta_connection` mới (additive).
- Mở rộng bảng `channel` (additive: `connection_id`, `external_account_id`,
  `username`, `enabled`, `last_sync_at`).
- `MetaConnectionService` (mock + live), factory chọn theo `ACP_ADAPTER`,
  đúng pattern `ContentSource`/`Publisher` đã có.
- OAuth flow: `/oauth/meta/start`, `/oauth/meta/callback`, exchange code→token
  server-side (app_secret đọc từ env, không render ra client/log).
- Import tự động Page + IG Professional account sau OAuth thành công.
- `POST /kenh/meta/sync` — chạy lại discovery bằng token `meta_connection`
  hiện có, upsert không trùng, đánh dấu account mất quyền `NEEDS_REAUTH`.
- `/kenh` UI: nhóm theo platform, toggle `enabled`, hiển thị `last_sync_at`,
  nút Kết nối Meta / Đồng bộ lại.
- Guard `enabled=0` trong `approve_post`/`create_post_for_product` (chặn tạo
  publish job mới cho account đã tắt).
- Vá regression: lọc `platform='threads'` vào dropdown kênh hiện có ở
  `/sanpham` (tránh Page/IG mới import lọt vào luồng tạo bài Threads trước
  khi C có `FacebookPublisher`).
- Unit/integration/web tests cho toàn bộ luồng trên.

### Ngoài phạm vi

- `FacebookPublisher`/`InstagramPublisher` thật, publish ảnh/carousel (C).
- `AccountGroup`/preset, multi-select `/sanpham` + `/duyet`, caption theo
  platform, override caption từng account, media library (D).
- Partnership/native Meta label (C/E).
- Live pilot thật với Meta (cần App được duyệt đủ quyền
  `pages_show_list`/`instagram_basic` — nằm ngoài session phát triển này,
  giống cách `ThreadsChannel`/`AccessTradeSource` live hiện tại chưa live-test
  được trong container).

## 3. Bối cảnh hiện tại (đã khảo sát code)

```text
channel (core/db.py)
  - platform, handle, external_user_id, status, token_encrypted, ...
  - status hiện dùng đúng 2 giá trị: ACTIVE / NEEDS_REAUTH
  - pipeline.publish_post() gate publish bằng channel["status"] != 'ACTIVE'
    -> cơ chế này ĐÃ generic theo platform, không cần sửa

/oauth/threads/callback (web/server.py)
  - chỉ hiện `code`, KHÔNG tự đổi token -- cố ý giữ app_secret ngoài web,
    đổi tay qua CLI (run.py). Threads flow này giữ nguyên, không đổi.

/kenh (web/server.py, channels.html)
  - chỉ quản lý niches theo channel, không có OAuth/import/enable-disable

run.py cmd_init()
  - seed channel Threads trực tiếp bằng SQL/mock token, không qua OAuth thật

/sanpham (products.html)
  - dropdown "Kênh Threads" query `channel WHERE status='ACTIVE'`,
    KHÔNG lọc platform -- rủi ro regression sau khi B thêm Page/IG (mục 8.3)
```

## 4. Data model

```sql
CREATE TABLE IF NOT EXISTS meta_connection (
    id              TEXT PRIMARY KEY,
    provider        TEXT NOT NULL DEFAULT 'meta',
    token_encrypted BLOB NOT NULL,       -- user access token dài hạn
    meta_user_id    TEXT,
    status          TEXT NOT NULL DEFAULT 'ACTIVE',   -- ACTIVE / NEEDS_REAUTH
    expires_at      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
```

`channel` — thêm cột, additive, không đổi cột cũ:

```sql
ALTER TABLE channel ADD COLUMN connection_id       TEXT REFERENCES meta_connection(id);  -- NULL cho Threads
ALTER TABLE channel ADD COLUMN external_account_id TEXT;   -- Page ID / IG Business Account ID
ALTER TABLE channel ADD COLUMN username             TEXT;  -- @handle Instagram
ALTER TABLE channel ADD COLUMN enabled              INTEGER NOT NULL DEFAULT 1;
ALTER TABLE channel ADD COLUMN last_sync_at          TEXT;
```

(Thêm vào `MIGRATIONS` trong `core/db.py`, theo đúng cơ chế nâng cấp schema
đã có — additive, không phá dữ liệu cũ. Threads channel hiện có tự động nhận
`connection_id=NULL`, `enabled=1`.)

**Mỗi Page/IG account có token riêng**, lưu thẳng vào `channel.token_encrypted`
— đúng cách Graph API trả về (`/me/accounts` kèm Page Access Token theo từng
Page). `meta_connection.token_encrypted` (user token) chỉ dùng để chạy lại
discovery ở bước Đồng bộ, **không** dùng để publish.

**Status** dùng lại nguyên `ACTIVE`/`NEEDS_REAUTH` đã có trên `channel` —
không thêm chuỗi trạng thái mới. `pipeline.publish_post()`/`core/jobs.py`
(không sửa) đã gate đúng theo hai giá trị này cho mọi platform. UI hiển thị
`NEEDS_REAUTH` với nhãn "Cần kết nối lại" — khớp semantics `RECONNECT_REQUIRED`
của PTYC gốc mà không cần thêm giá trị enum.

## 5. `MetaConnectionService`

`adapters/base.py` thêm interface:

```python
@dataclass
class PageInfo:
    external_account_id: str
    name: str
    page_token: str

@dataclass
class InstagramInfo:
    external_account_id: str
    username: str
    page_token: str  # IG Graph API dùng chung Page token

@dataclass
class ExchangedToken:
    token: str
    expires_in: int
    meta_user_id: str

class MetaConnectionService:
    def oauth_authorize_url(self, state: str, redirect_uri: str) -> str: ...
    def exchange_code(self, code: str, redirect_uri: str) -> ExchangedToken: ...
    def list_pages(self, user_token: str) -> list: ...            # list[PageInfo]
    def instagram_for_page(self, page_id: str, page_token: str): ... # InstagramInfo | None
```

`adapters/mock.py` — `MockMetaConnectionService`: fixture cố định (2 Page giả
+ 1 IG account giả gắn với Page đầu), test được ngay không cần mạng.

`adapters/live.py` — `LiveMetaConnectionService`: gọi Graph API thật
(`GET /oauth/access_token`, `GET /me/accounts`, `GET /{page-id}?fields=
instagram_business_account{id,username}`). Đọc `META_APP_ID`/`META_APP_SECRET`
từ biến môi trường (giống `AT_ACCESS_KEY`). Viết đúng chuẩn Graph API nhưng
**chưa live-test được** trong session phát triển này — App thật đã có
(dùng chung với Threads) nhưng chưa bật sản phẩm Facebook Login/đủ quyền.

`adapters/factory.py` thêm `get_meta_connection_service()`, chọn mock/live
theo `ACP_ADAPTER`, cùng cơ chế `get_channel()`.

## 6. OAuth & Sync flow

```text
GET  /oauth/meta/start
  -> sinh state ngẫu nhiên, lưu session
  -> redirect tới oauth_authorize_url(state, redirect_uri)

GET  /oauth/meta/callback?code=...&state=...
  -> validate state khớp session (chống CSRF) -- lỗi thì báo rõ, không exchange
  -> exchange_code(code, redirect_uri) -> ExchangedToken
  -> upsert meta_connection (theo meta_user_id, tránh tạo trùng khi operator
     kết nối lại đúng tài khoản Meta đó)
  -> list_pages(user_token) -> với mỗi Page:
       upsert channel (platform='facebook', connection_id, external_account_id,
                        handle=Page name, token_encrypted=page_token,
                        status='ACTIVE', enabled=1 nếu tạo mới)
       instagram_for_page(page_id, page_token) -> nếu có:
         upsert channel (platform='instagram', connection_id, external_account_id,
                          username, token_encrypted=page_token, status='ACTIVE')
  -> redirect /kenh kèm tóm tắt "đã import N Page, M Instagram"

POST /kenh/meta/sync   (yêu cầu login + CSRF form, giống các route quản trị khác)
  -> lấy meta_connection hiện có (nếu chưa có -> lỗi "Chưa kết nối Meta")
  -> chạy lại list_pages()/instagram_for_page() bằng token đã lưu
  -> upsert theo external_account_id (không tạo trùng)
  -> account trước đây có mà lần này Meta không trả về nữa
     -> status='NEEDS_REAUTH' (KHÔNG xoá, KHÔNG xoá post/job liên quan)
  -> cập nhật last_sync_at cho toàn bộ account thuộc connection này
```

`app_secret` chỉ đọc server-side (env var), không bao giờ vào HTML/log —
khác Threads (đã xác nhận đổi posture chỉ riêng cho luồng Meta, vì tự động
hoá import bắt buộc web tự đổi code→token).

## 7. `/kenh` UI

```text
THREADS
● Threads Nữ          [Bật/Tắt]

FACEBOOK
● Fashion Page          ACTIVE      [Bật/Tắt]
○ Tech Deals Page       Cần kết nối lại   [Bật/Tắt]

INSTAGRAM
● @fashion              ACTIVE      [Bật/Tắt]

[ Kết nối Meta ]  [ Đồng bộ lại ]  (nút Đồng bộ chỉ hiện khi đã có meta_connection)
```

Mỗi account: platform, handle/username, tag trạng thái, toggle `enabled`,
`last_sync_at` (nếu có). Toggle POST tới route mới, yêu cầu login+CSRF như
`channels()` hiện tại.

## 8. Error handling & Regression safety

### 8.1. OAuth lỗi

`error`/`error_description` trên callback → hiển thị lỗi rõ, không tạo
`meta_connection`, không đổi trạng thái account đang có.

### 8.2. `enabled=0` guard

Thêm kiểm tra trong `pipeline.approve_post()` và
`create_post_for_product()`/`create_post_from_manual_affiliate_product()`:
nếu `channel.enabled=0` → từ chối rõ ràng, không tạo `publish_target`/job.
Đây là phòng thủ sớm cho §30 ("server phải xác minh account đang ENABLED"),
dù UI chọn-nhiều-account thật sự là việc của D.

### 8.3. Regression: dropdown `/sanpham` phải lọc platform

`_product_common_context()` (`web/server.py`) hiện query
`SELECT code, handle FROM channel WHERE status='ACTIVE'` không lọc platform.
Sau B, Page/IG mới import sẽ lọt vào dropdown "Kênh Threads" này — chọn nhầm
sẽ khiến `publish_post` gọi `ctx["publishers"]["facebook"]` (chưa tồn tại tới
khi có C) và job FAIL với lỗi khó hiểu. B sửa query này thêm
`AND platform='threads'` để giữ nguyên hành vi hiện tại cho tới khi D có UI
chọn đa nền tảng thật sự.

### 8.4. Sync không xoá dữ liệu

`POST /kenh/meta/sync` không bao giờ `DELETE` channel hay post/job liên quan
— chỉ upsert và đổi status.

## 9. Security

```text
OAuth state validation (session-based, không phải _csrf form token)
CSRF cho mọi form POST tại /kenh (dùng cơ chế _csrf hiện có)
app_secret chỉ server-side, không log, không render
token_encrypted không bao giờ xuất hiện trong template/log
server-side xác minh account thuộc đúng connection trước khi toggle/sync
```

## 10. Test plan

### Unit tests

```text
MetaConnectionService mock: list_pages/instagram_for_page trả đúng fixture
exchange_code: tạo/khớp meta_connection theo meta_user_id, không tạo trùng
upsert channel theo external_account_id: không duplicate khi sync lặp lại
sync đánh dấu NEEDS_REAUTH cho account Meta không còn trả về
enabled=0 chặn approve_post/create_post_for_product tạo job mới
migration: channel cũ (Threads) tự nhận connection_id=NULL, enabled=1
```

### Web tests

```text
/oauth/meta/start yêu cầu login, redirect đúng URL
/oauth/meta/callback thiếu code/state -> lỗi rõ, không tạo connection
/oauth/meta/callback state sai -> 400, không exchange
/oauth/meta/callback hợp lệ (mock) -> tạo connection + import đúng số Page/IG
POST /kenh/meta/sync yêu cầu login + CSRF
POST /kenh/<channel_id>/enable|disable yêu cầu login + CSRF
/sanpham dropdown chỉ còn kênh Threads sau khi có Page/IG đã import
```

### Regression

```text
python -m acp.tests.test_pipeline
python -m acp.tests.test_pilot
```
Phải tiếp tục pass nguyên trạng — không sửa assertion cũ ngoài phần cần thiết
để phản ánh cột mới trên `channel`.

## 11. Acceptance criteria

```text
[ ] Meta Login thành công, import nhiều Facebook Page
[ ] import nhiều Instagram Professional account
[ ] account hiển thị đúng nhóm platform tại /kenh
[ ] bật/tắt account hoạt động, chặn tạo job mới khi tắt
[ ] Đồng bộ lại không duplicate account
[ ] account mất quyền -> NEEDS_REAUTH, không mất lịch sử
[ ] app_secret/token không xuất hiện trong log/UI
[ ] /sanpham dropdown không lẫn Page/IG chưa có publisher
[ ] Threads/Shopee/ACCESSTRADE không regression
[ ] tests đạt, git diff --check sạch, không commit secrets
```

## 12. Quyết định đã chốt

1. Mock-first (giống Threads/AccessTrade): `MockMetaConnectionService` test
   được ngay; `LiveMetaConnectionService` viết đúng chuẩn Graph API, live-test
   sau khi App được duyệt đủ quyền — ngoài phạm vi session này.
2. App ID/Secret thật đã có (dùng chung với Threads), đọc qua biến môi trường
   `META_APP_ID`/`META_APP_SECRET`.
3. Đổi posture so với Threads: web process **tự động đổi code→token**
   server-side để import tự động đúng yêu cầu PTYC §5.1 — app_secret đọc từ
   env, không bao giờ ra client/log.
4. Mở rộng bảng `channel` hiện có (additive) thay vì tạo `channel_account`
   song song — tận dụng toàn bộ pipeline/query đã có, rủi ro thấp hơn.
5. Mỗi Page/IG account có token riêng lưu trong `channel.token_encrypted`,
   `meta_connection.token_encrypted` chỉ dùng cho discovery/sync.
6. Không thêm giá trị status mới — dùng lại `ACTIVE`/`NEEDS_REAUTH` đã có,
   khớp semantics `RECONNECT_REQUIRED` của PTYC qua nhãn hiển thị.
7. `enabled` là cột mới, độc lập với `status` — toggle tay của operator.
8. Vá regression dropdown `/sanpham` (lọc `platform='threads'`) như một phần
   của B, vì B là nguyên nhân trực tiếp gây ra rủi ro này.
