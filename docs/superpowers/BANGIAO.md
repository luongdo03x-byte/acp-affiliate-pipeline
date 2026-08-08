# BÀN GIAO — merge vào repo đang có

Tài liệu này dành cho việc đưa các thay đổi vào **project git sẵn có**, không
phải thay thế toàn bộ. Đưa file này cho Claude Code trong terminal là đủ ngữ cảnh.

Trạng thái: **195 test đạt** (37 `test_pipeline` + 158 `test_pilot`).

---

## 1. Tình trạng hiện tại

Pipeline 7 chặng chạy đầy đủ với adapter giả lập, không cần mạng:

```
datafeed → chấm điểm → sinh nội dung → duyệt tay → đăng → đối soát → hiệu chỉnh
```

**Đã có**
- Nguồn: Accesstrade Shopee (v1) và TikTok Shop (feed v2), chọn bằng `ACP_SOURCE`
- Đăng qua Threads Graph API, container model 3 bước, idempotency chống đăng trùng
- Quy kết doanh thu: `post_id` gắn vào cả `utm_content` lẫn `sub1`
- 8 nhóm sản phẩm, gán **theo từng kênh**, đổi lúc nào cũng được
- 9 hook mở đầu + thư viện CTA, mỗi hook là một biến thể đo bằng `sub3`
- Bài không bán hàng (phương pháp 3 bài)
- Dashboard 6 trang, có đăng nhập và CSRF
- Lưu trữ ảnh: local hoặc S3/R2

**Chưa có**
- Job tự gia hạn token Threads 60 ngày (hiện làm tay)
- Cảnh báo Telegram
- Scheduler tự chèn bài giá trị khi tỷ lệ bán vượt ngưỡng
- OCR cuốn "công thức TikTok 1000 đơn" (bản scan 207 trang)

---

## 2. File cần thêm mới

| File | Dòng | Việc |
|---|---|---|
| `core/playbook.py` | 219 | 9 hook mở đầu + CTA, chắt lọc từ tài liệu bán hàng |
| `core/valuepost.py` | 166 | Bài không bán hàng: mặt bằng giá, món giảm thật, checklist |
| `core/niche.py` | 310 | 8 nhóm sản phẩm + rào chắn nội dung riêng từng nhóm |
| `core/storage.py` | 102 | Lưu ảnh local hoặc S3/R2, chọn bằng `ACP_STORAGE` |
| `adapters/factory.py` | 61 | Nơi DUY NHẤT chọn adapter — web và CLI dùng chung |
| `adapters/tiktokshop.py` | 225 | Nguồn TikTok Shop qua Accesstrade feed v2 |
| `web/templates/channels.html` | 49 | Trang gán nhóm sản phẩm cho từng kênh |
| `web/templates/products.html` | 54 | Tìm sản phẩm và tạo bài lẻ |
| `web/templates/login.html` | 14 | Đăng nhập |
| `web/templates/oauth.html` | 26 | Nhận mã uỷ quyền Threads |
| `tests/test_pilot.py` | 611 | 158 test |
| `tests/fixtures/*.json` | 71 | Response API đã ẩn thông tin nhạy cảm |

---

## 3. File cần sửa

### `core/db.py` — schema + migration

Ba thay đổi schema:

```sql
channel.niches   TEXT NOT NULL DEFAULT '[]'     -- mới
post.post_type   TEXT NOT NULL DEFAULT 'SALES'  -- mới
post.product_id  -- BỎ ràng buộc NOT NULL
```

Có sẵn `migrate(conn)` chạy tự động trong `init_db()`. Hai loại:

- `MIGRATIONS` — thêm cột bằng `ALTER TABLE`, idempotent
- `_rebuild_post_table()` — dựng lại bảng `post` để bỏ `NOT NULL` trên
  `product_id` (SQLite không bỏ được bằng `ALTER`). Giữ nguyên toàn bộ bản ghi cũ.

> **Sao lưu `var/acp.db` trước khi chạy lần đầu.** Migration đã có test dựng CSDL
> kiểu cũ rồi nâng cấp, nhưng dữ liệu thật thì không có bản thứ hai.

### `adapters/live.py` — **sửa lỗi nghiêm trọng**

`AT_BASE` đã chứa `/v1` nhưng hai lời gọi lại ghi `/v1/...` → URL thành
`/v1/v1/transactions`. **Mọi lời gọi live đều trả 404.**

```python
self._post("/product_link/create", body)   # KHÔNG phải /v1/product_link/create
self._get("/transactions", **params)       # KHÔNG phải /v1/transactions
```

Kèm theo:
- `create_tracking_link` đổi từ GET sang **POST với body JSON**, `urls` là mảng
- `fetch_transactions` đổi endpoint `/order-list` → `/transactions`
- `STATUS_MAP` — Accesstrade trả trạng thái dạng **số** (0/1/2), không phải chuỗi

`tests/test_pilot.py::test_no_double_version_prefix` đọc thẳng source để chặn tái diễn.

### `core/content.py`
- `generate()` dựng theo **HOOK → THÂN → MỘT CTA → DISCLOSURE**
- `validate()` nhận thêm `niches` và `post_type`
- Chặn nhiều hơn một CTA trong bài

### `core/pipeline.py`
- `channel_niches()` / `set_channel_niches()` — nhóm theo kênh
- `plan_content()` chấm điểm **riêng cho từng kênh**, xoay vòng hook làm biến thể
- `create_post_for_product()` — một sản phẩm → một bài chờ duyệt
- `create_value_post()` / `post_mix()` — bài không bán hàng
- `_median_30d()` — dữ liệu cho hook so sánh giá

### `core/scoring.py`
- `score_candidates(..., niches=...)` — ghi đè theo kênh
- Lọc theo nhóm ở tầng lọc cứng

### `web/server.py`
- **Sửa lỗi:** `/vanhanh/work` trước đây khởi tạo thẳng `MockThreads`, nên bấm
  nút trên web vẫn chạy giả lập dù `ACP_ADAPTER=live`. Giờ dùng `factory.build_context()`
- Đăng nhập + CSRF + webhook cần khoá `?k=`
- Trang mới: `/kenh`, `/sanpham`, `/dangnhap`, `/oauth/threads/callback`
- `ACP_ENV=production` mà thiếu `ACP_ADMIN_PASSWORD` hoặc `ACP_SECRET_KEY` →
  app **từ chối khởi động**

### `run.py`
Lệnh mới: `niche`, `search`, `product`, `valuepost`, `mix`, `reconcile`,
`trace`, `doctor`. `_adapters()` chuyển sang dùng factory.

### `seed/datafeed_sample.json`
135 sản phẩm (từ 78), trải đủ 8 nhóm để demo chạy có nghĩa.

---

## 4. Biến môi trường mới

```bash
# bảo mật — BẮT BUỘC khi ACP_ENV=production
export ACP_ADMIN_PASSWORD='...'
export ACP_SECRET_KEY='...'          # python3 -c "import secrets;print(secrets.token_urlsafe(32))"
export ACP_WEBHOOK_SECRET='...'      # chỉ cần khi bật postback

# nguồn sản phẩm
export ACP_SOURCE='tiktokshop'       # tiktokshop | shopee | mock
export AT_TIKTOK_CAMPAIGN_ID='...'

# lưu trữ ảnh
export ACP_STORAGE='s3'              # local | s3
export R2_BUCKET='acp-media'
export R2_ENDPOINT='https://<ACCOUNT_ID>.r2.cloudflarestorage.com'
export R2_ACCESS_KEY_ID='...'
export R2_SECRET_ACCESS_KEY='...'
export ACP_MEDIA_BASE_URL='https://pub-xxxxx.r2.dev'
```

`requirements.txt` thêm `boto3>=1.34`.

---

## 5. Thứ tự merge đề xuất

Làm từng bước, chạy test sau mỗi bước. Đừng gộp — nếu hỏng sẽ không biết ở đâu.

1. **`adapters/live.py`** — sửa lỗi `/v1/v1` trước tiên. Đây là lỗi chặn mọi thứ.
2. **`core/db.py`** — schema + migration. Sao lưu `var/acp.db` trước.
3. **`adapters/factory.py`** + sửa `web/server.py` dùng factory.
4. **`core/niche.py`** + nối vào `scoring.py`, `content.py`, `pipeline.py`.
5. **`core/storage.py`** + nối vào `pipeline.py`.
6. **`core/playbook.py`** + viết lại `content.generate()`.
7. **`core/valuepost.py`** + `pipeline.create_value_post()`.
8. **Bảo mật web** — đăng nhập, CSRF, khoá webhook.
9. **`run.py`** — các lệnh mới.
10. **Test + templates.**

Kiểm tra sau mỗi bước:

```bash
cd <thư mục cha của acp>    # KHÔNG đứng trong acp
python3 -m acp.tests.test_pipeline    # phải: 37 đạt, 0 hỏng
python3 -m acp.tests.test_pilot       # phải: 158 đạt, 0 hỏng
cd acp && python3 run.py demo && python3 run.py doctor
```

---

## 6. Bốn chỗ dễ sai khi merge

**Đứng sai thư mục khi chạy test.** `python3 -m acp.tests.*` phải chạy từ thư mục
**cha** của `acp`. Đứng trong `acp` sẽ ra `No module named 'acp'`.

**Test có phụ thuộc thứ tự.** `test_web_security` đặt mọi kênh về `NEEDS_REAUTH`;
`test_value_posts` bật lại trước khi chạy. Đổi thứ tự trong `__main__` sẽ vỡ.

**Migration bảng `post` không thể hoàn tác.** Nó `DROP TABLE post_old`. Sao lưu trước.

**Từ khoá tiếng Việt trong `niche.py` phải viết CÓ DẤU.** "đầm" khác "dặm",
"tóc" khác "tốc". Bỏ dấu rồi so chuỗi con là lỗi đã từng mắc.

---

## 7. Kiểm tra cuối trước khi commit

- [ ] `test_pipeline` → 37 đạt, 0 hỏng
- [ ] `test_pilot` → 158 đạt, 0 hỏng
- [ ] `run.py demo` chạy hết 7 chặng
- [ ] `run.py doctor` toàn dấu ✓
- [ ] `run.py niche` hiện đúng nhóm của từng kênh
- [ ] `run.py mix` chạy được
- [ ] Bốn trang web trả 200: `/`, `/kenh`, `/sanpham`, `/duyet`
- [ ] `git diff` không lẫn `var/acp.db`, `var/media/*`, `.env`
- [ ] `.gitignore` có `var/acp.db*`, `var/media/*.jpg`, `.env`, `.venv/`
