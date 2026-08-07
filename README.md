# Affiliate Content Pipeline (ACP)

Hệ thống tự động hoá tiếp thị liên kết Shopee → Threads, triển khai theo BRD v2.0.

Nguyên tắc nền: **chỉ tự động hoá những việc một publisher hợp pháp được phép làm** —
lấy dữ liệu qua datafeed chính thức, đăng bài qua Threads Graph API bằng tài khoản
mình sở hữu, và đo doanh thu thật thay vì đếm số bài đăng.

---

## Chạy trong 30 giây

```bash
cd acp
python3 run.py demo      # dựng CSDL, mô phỏng 14 ngày vận hành, in báo cáo
python3 run.py serve     # mở http://127.0.0.1:5000
```

Không cần cài gì thêm nếu môi trường đã có Flask, Pillow, cryptography.
Nếu thiếu: `pip install flask pillow cryptography`

Kết quả một lần chạy demo:

```
Bài đã đăng          39
Lượt xem         59.521
Click             1.035     CTR 1,74%
Đơn được duyệt       16     CR 1,55%
Hoa hồng      1.321.883đ
EPC               1.277đ
```

---

## Các lệnh

| Lệnh | Việc |
|---|---|
| `python3 run.py init` | Tạo CSDL, chiến dịch, template, kênh, cấu hình chấm điểm |
| `python3 run.py ingest` | Chặng 1 — nạp datafeed |
| `python3 run.py plan` | Chặng 2 — chấm điểm, tạo job sinh nội dung |
| `python3 run.py work` | Chạy hàng đợi tới khi hết việc |
| `python3 run.py niche` | Xem nhóm sản phẩm của từng kênh |
| `python3 run.py niche <kênh> <nhóm...>` | Đặt nhóm cho một kênh |
| `python3 run.py niche <kênh>` | Xoá nhóm (kênh nhận mọi danh mục) |
| `python3 run.py search [từ khoá]` | Tìm sản phẩm trong nguồn |
| `python3 run.py product <mã sp>` | **Một sản phẩm → một bài chờ duyệt** |
| `python3 run.py review` | Liệt kê bài chờ duyệt |
| `python3 run.py approve <post_id>` | Duyệt một bài |
| `python3 run.py report` | Báo cáo doanh thu ra terminal |
| `python3 run.py serve` | Dashboard web |
| `python3 run.py demo` | Chạy trọn bộ với dữ liệu mô phỏng |
| `python3 run.py genkey` | Sinh khoá `ACP_MASTER_KEY` |
| `python3 run.py reconcile` | Kéo dữ liệu chuyển đổi về |
| `python3 run.py trace` | Soi vì sao chuyển đổi không quy kết được |
| `python3 run.py doctor` | Kiểm tra cấu hình trước khi chạy thật |
| `python3 -m acp.tests.test_pipeline` | 37 test lõi |
| `python3 -m acp.tests.test_pilot` | 101 test nguồn, factory, nhóm sản phẩm, bảo mật |

---

## Kiến trúc

```
acp/
├── run.py                  CLI
├── core/
│   ├── db.py               Schema SQLite (port sang Postgres: đổi kiểu dữ liệu)
│   ├── crypto.py           Mã hoá token AES-256-GCM
│   ├── scoring.py          Chấm điểm sản phẩm, trọng số lưu trong DB
│   ├── niche.py            8 nhóm sản phẩm: lọc + rào chắn riêng từng nhóm
│   ├── content.py          Sinh caption + rào chắn nội dung
│   ├── imaging.py          Ghép ảnh bằng Pillow
│   ├── jobs.py             Hàng đợi, retry, idempotency
│   ├── attribution.py      sub_id, postback, đối soát, EPC
│   └── pipeline.py         Điều phối 7 chặng
├── adapters/
│   ├── base.py             Interface ContentSource / PublishingChannel
│   ├── factory.py          Nơi DUY NHẤT chọn adapter (web và CLI dùng chung)
│   ├── mock.py             Giả lập, chạy offline
│   ├── live.py             Accesstrade Shopee + Threads
│   └── tiktokshop.py       Nguồn TikTok Shop qua Accesstrade feed v2
├── web/                    Flask: 4 trang + webhook postback
├── seed/                   135 sản phẩm mẫu trải đủ các nhóm
└── tests/
```

### Luồng 7 chặng

```
1. Nạp datafeed        ingest_datafeed()      03:00, Accesstrade API
2. Chấm điểm           plan_content()         lọc cứng → score → top-K
3. Sinh nội dung       job GENERATE_CONTENT   link + sub_id, ghép ảnh, caption
4. Duyệt thủ công      approve_post()         ~5 phút/ngày
5. Đăng bài            job PUBLISH_POST       container → poll → publish
6. Thu chuyển đổi      ingest_postback()      + đối soát mỗi 6 giờ
7. Hiệu chỉnh          category_conversion_rates() → quay lại chặng 2
```

---

## Nhóm sản phẩm theo kênh

Mỗi kênh chọn nhóm riêng. Kênh chuyên một ngách gần như luôn thắng kênh tạp về
EPC: người theo dõi biết họ đang theo dõi cái gì nên click có chủ đích hơn.

```bash
python3 run.py niche                                    # xem tất cả kênh
python3 run.py niche threads_nu thoi-trang-nu my-pham
python3 run.py niche threads_be me-va-be
python3 run.py niche threads_pet thu-cung
```

Hoặc tích ô trên trang `/kenh`. Đổi bất cứ lúc nào — bài đã đăng không bị ảnh
hưởng, chỉ lô sinh nội dung tiếp theo dùng cấu hình mới.

| Mã | Nhóm | Luật riêng |
|---|---|---|
| `thoi-trang-nu` | Thời trang nữ | tự loại hàng nam và trẻ em |
| `thoi-trang-nam` | Thời trang nam | tự loại hàng nữ và trẻ em |
| `my-pham` | Mỹ phẩm & chăm sóc da | +22 cụm cấm khẳng định điều trị |
| `me-va-be` | Mẹ & bé | +8 cụm cấm về dinh dưỡng; loại sữa công thức |
| `thu-cung` | Thú cưng | +5 cụm cấm về chữa bệnh; loại thuốc thú y |
| `gia-dung` | Nhà cửa & gia dụng | — |
| `cong-nghe` | Phụ kiện công nghệ | — |
| `the-thao` | Thể thao & dã ngoại | +4 cụm cấm về giảm cân |

Lọc chạy ở **tầng lọc cứng**: sản phẩm ngoài nhóm không bao giờ lên bài, dù điểm
cao đến đâu. Không tích ô nào = kênh nhận mọi danh mục.

Thêm nhóm mới: sửa `NICHES` trong `core/niche.py`. Từ khoá tiếng Việt phải viết
**có dấu** — "đầm" khác "dặm", "tóc" khác "tốc".

## Bốn chi tiết dễ sai đã xử lý sẵn

**Đăng trùng.** Timeout mạng rồi retry trong khi bài đã lên thành công là lỗi nguy
hiểm nhất của loại hệ thống này. Ba lớp chặn: `idempotency_key` khi tạo job, kiểm
`thread_id IS NULL` trước khi gọi API, và kiểm trạng thái bài. Có test riêng.

**Retry sai loại lỗi.** Lỗi vi phạm nội dung **không bao giờ** được thử lại — bài
quay về hàng đợi duyệt. Rate limit thì hoãn mà không tiêu lượt retry. Chỉ lỗi mạng
mới retry, tối đa 3 lần, backoff 1 → 5 → 25 phút.

**Khử trùng lặp postback.** Khách mua nhiều món trong một đơn thì Accesstrade gửi
**mỗi món một postback riêng**. Khoá khử trùng lặp là `(transaction_id, product_id)`,
không phải `transaction_id` đơn lẻ. Nhầm chỗ này là mất doanh thu trên báo cáo.

**Giảm giá thật.** Tính theo trung vị 30 ngày từ `product_price_history`, không tin
"giá gốc" sàn công bố — đó là cách chặn chiêu nâng giá rồi giảm.

---

## Chuyển sang chạy thật

> Hướng dẫn từng bước chi tiết: xem **DEPLOYMENT.md**. Phần dưới chỉ là tóm tắt.

### 1. Khoá mã hoá

```bash
export ACP_MASTER_KEY=$(python3 run.py genkey)
export ACP_ENV=production      # thiếu khoá thì fail sớm thay vì dùng khoá dev
```

### 2. Accesstrade

```bash
export AT_ACCESS_KEY=...
export AT_CAMPAIGN_ID=...
export ACP_ADAPTER=live
```

Trỏ postback URL của Accesstrade về `https://<domain>/webhook/at/postback`.

> **Cần xác minh trước khi chạy thật:** tên tham số sub chính xác của Accesstrade VN
> (`sub1..sub4` hay `utm_content`) — tra tại `developers.accesstrade.vn`. Chỉnh trong
> `adapters/live.py::create_tracking_link`. Trường hoa hồng trong datafeed cũng khác
> nhau tuỳ chiến dịch, kiểm bằng dữ liệu thật rồi chỉnh map trong `fetch_products`.

### 3. Threads

Cần **Meta App Review** cho ba permission: `threads_basic`,
`threads_content_publish`, `threads_manage_insights`. Mỗi permission submit riêng
kèm screencast, khoảng một tuần.

Đây là đường găng — nộp hồ sơ ngày đầu tiên, đừng đợi code xong.

### 4. Object storage (bắt buộc)

Threads yêu cầu `image_url` **truy cập công khai được**. File ảnh trong `var/media/`
phải đẩy lên S3/R2/MinIO rồi lấy URL công khai:

```bash
export ACP_MEDIA_BASE_URL=https://cdn.domain.com/acp
```

Chưa làm bước này thì đăng bài kèm ảnh sẽ hỏng.

### 5. Chuyển sang PostgreSQL

Schema trong `core/db.py` viết bám sát Postgres. Đổi kiểu dữ liệu:

```
TEXT (ULID)     → UUID / TEXT
TEXT (ISO8601)  → TIMESTAMPTZ
TEXT (JSON)     → JSONB
INTEGER (0/1)   → BOOLEAN
```

Và đổi `claim()` trong `jobs.py` sang:

```sql
SELECT * FROM job_queue
WHERE status='READY' AND run_after <= now()
ORDER BY priority DESC, run_after
FOR UPDATE SKIP LOCKED LIMIT ?
```

`FOR UPDATE SKIP LOCKED` cho nhiều worker chạy song song thật. SQLite dùng
`BEGIN IMMEDIATE` nên chỉ một worker ghi tại một thời điểm — đủ cho prototype.

---

## Dùng LLM cho caption

Bộ sinh mặc định là template: deterministic, miễn phí, test được. Muốn dùng LLM:

```python
from acp.core import content
content.set_llm(lambda prompt: goi_api_cua_ban(prompt))
```

Kết quả LLM vẫn phải qua `validate()` y hệt — rào chắn nội dung không có ngoại lệ.

---

## Rào chắn nội dung

Cưỡng chế bằng mã, không bằng ý thức:

- **Disclosure bắt buộc** — ràng buộc `CHECK (length(disclosure_text) > 0)` ở tầng CSDL
- **Tối đa 500 ký tự** — `CHECK` ở CSDL, kiểm trước khi vào hàng đợi duyệt
- **Cấm bịa trải nghiệm** — chặn "mình đã dùng", "da mình", "sau khi dùng"…
- **Cấm từ tuyệt đối hoá** — "tốt nhất", "số 1", "duy nhất" (Luật Quảng cáo)
- **Cấm cam kết công dụng** — "chữa khỏi", "trị dứt điểm"…
- **Chặn danh mục cần giấy phép riêng** — thực phẩm chức năng, thiết bị y tế, tài chính
- **Rào chắn theo nhóm** — mỗi nhóm có luật riêng. `my-pham` cấm thêm 22 cụm
  khẳng định điều trị, `me-va-be` cấm 8 cụm về dinh dưỡng, `thu-cung` cấm 5 cụm
  về chữa bệnh. Bắt cả biến thể viết không dấu

Sửa danh sách trong `core/content.py` và `core/scoring.py::DEFAULT_FILTERS`.

---

## Test

```bash
python3 -m acp.tests.test_pipeline
```

37 test phủ 9 nhóm bất biến: mã hoá token, rào chắn nội dung, lọc chấm điểm, quy kết
sub_id, khử trùng lặp postback, ngữ nghĩa retry, chống đăng trùng, trần đăng theo
ngày, ràng buộc CSDL.

---

## Chưa có (cố ý)

- Lịch chạy tự động — hiện gọi bằng CLI. Thêm cron hoặc `APScheduler` khi lên production.
- Job gia hạn token 60 ngày — `live.py` có sẵn chỗ, chưa nối vào hàng đợi.
- Cảnh báo Telegram/email (FR10).
- Auto-approve có điều kiện — chỉ nên bật sau >200 bài duyệt tay với tỷ lệ reject <3%.
