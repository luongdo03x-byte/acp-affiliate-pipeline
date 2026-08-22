# Affiliate Content Pipeline (ACP)

Hệ thống tự động hoá tiếp thị liên kết Shopee → Threads, triển khai theo BRD v2.0.

Nguyên tắc nền: **chỉ tự động hoá những việc một publisher hợp pháp được phép làm** —
lấy dữ liệu qua datafeed chính thức, đăng bài qua Threads Graph API bằng tài khoản
mình sở hữu, và đo doanh thu thật thay vì đếm số bài đăng.

## Chuyển Account Factory giữa hai máy Ubuntu

Luồng được hỗ trợ là **một máy ACTIVE tại một thời điểm**. Trước khi rời máy đang chạy:

```bash
./manage.sh handoff-out
```

Lần đầu trên máy còn lại:

```bash
git clone -b feat/account-factory-android git@github.com:luongdo03x-byte/acp-affiliate-pipeline.git
cd acp-affiliate-pipeline
./setup.sh
```

Các lần chuyển máy sau, trong clone đã có sẵn:

```bash
git pull --ff-only
./setup.sh
```

State bền vững được chuyển bằng private GitHub Release tag `acp-portable-state`. Asset tar có chứa `.env.local` ở dạng **plaintext** bên trong archive, bao gồm `ACP_MASTER_KEY` và provider/app secrets; đây không phải lớp mã hoá thứ hai. Bất kỳ ai có quyền tải private release của repo đều có thể lấy các secret đó, vì vậy nên bảo vệ tài khoản GitHub bằng **2FA** và **passkey**. Không commit `.env.local` vào Git.

`./setup.sh` restore DB/env/avatar, kiểm tra generation, Git/GitHub auth, SQLite, credential decrypt, OAuth config, AVD và callback trước khi resume/start. AVD/browser profile không được copy giữa máy; **Chrome Terms**, **OAuth consent**, OTP/CAPTCHA hoặc challenge bảo mật vẫn có thể cần người vận hành thao tác thủ công.

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

## Quản lý app bằng một lệnh

### Máy mới (lần đầu, sau khi git clone)

```bash
git clone <repo-url> ~/Downloads/ACP/releases/2.0/acp
cd ~/Downloads/ACP/releases/2.0/acp
./manage.sh setup      # tạo venv, shared/.env.local, symlink, schema CSDL
```

`setup` tự làm: tạo virtualenv + cài `requirements.txt`, tạo
`~/Downloads/ACP/shared/.env.local` từ `.env.example` (tự sinh
`ACP_MASTER_KEY` bằng `run.py genkey`, tự điền `ACP_DB` đúng đường dẫn
máy này), symlink `acp/.env.local` + `acp/var` vào `shared/`, tạo schema
CSDL (không seed dữ liệu demo), và tạo hai symlink kích hoạt
`~/Downloads/ACP/acp` + `~/Downloads/ACP/manage.sh`. An toàn để chạy lại
nhiều lần — không bao giờ ghi đè `.env.local`/CSDL đã có sẵn.

Chạy xong, `setup` in ra danh sách biến **bắt buộc điền tay** trước khi
dùng thật (`ACP_ADMIN_PASSWORD`, `ACP_SECRET_KEY`, và tuỳ nhu cầu
`ACCESSTRADE_API_TOKEN`/`ACP_GEMINI_API_KEY`/`ACP_PUBLIC_BASE_URL`) —
những thứ không thể tự sinh an toàn được. Không cần điền gì thì
`./manage.sh start` vẫn chạy được ngay ở chế độ dev cục bộ (không đăng
nhập, `ACP_ADAPTER=mock`), đủ để xem dashboard/demo.

**Muốn giữ nguyên kết nối Threads + catalog đã có (chuyển máy, không phải
máy hoàn toàn mới)** — workflow Account Factory mới ở đầu README dùng private
Release `acp-portable-state`. Cơ chế GPG/copy tay dưới đây chỉ còn là luồng legacy
cho deployment ACP cũ, không phải workflow handoff Account Factory được khuyến nghị.

1. **Mã hoá rồi commit vào git (legacy):**

   ```bash
   ./manage.sh encrypt-secrets    # hỏi passphrase, tạo secrets/env.local.gpg
   git add secrets/env.local.gpg
   git commit -m "chore: cập nhật bản mã hoá shared/.env.local"
   git push
   ```

   `secrets/env.local.gpg` là bản mã hoá đối xứng AES-256 bằng `gpg`. Passphrase
   không truyền qua đối số dòng lệnh; tự lưu passphrase ở nơi khác git.

2. **Copy tay ngoài git (legacy):** copy nguyên thư mục `shared/` bằng `scp`/`rsync`
   trước khi chạy setup.

### Vòng đời hằng ngày

```bash
./manage.sh start
./manage.sh status
./manage.sh restart
./manage.sh stop
./manage.sh test
./manage.sh upgrade ~/Downloads/acp_2.1.zip 2.1
./manage.sh rollback
```

Runtime/secrets nằm ở `~/Downloads/ACP/shared`, log ở `~/Downloads/ACP/logs`, backup DB ở `~/Downloads/ACP/backups`. `manage.sh test` và bước xác minh upgrade luôn ép adapter/source về mock để không đăng bài thật. Xem `docs/ACP_RUNBOOK.md` cho quy trình đầy đủ.

---

## Các lệnh

| Lệnh | Việc |
|---|---|
| `python3 run.py init` | Tạo CSDL, chiến dịch, template, kênh, cấu hình chấm điểm |
| `python3 run.py ingest` | Chặng 1 — nạp datafeed |
| `python3 run.py plan` | Chặng 2 — chấm điểm, tạo job sinh nội dung |
| `python3 run.py work` | Chạy hàng đợi tới khi hết việc |
| `python3 run.py worker-once` | Chạy một lượt worker theo công tắc tự đăng |
| `python3 run.py worker-status` | Xem công tắc tự đăng và tổng số job an toàn |
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

---

## Nhập link affiliate Shopee có sẵn

ACP có một luồng riêng cho link affiliate Shopee mà operator đã tạo trước đó. Luồng này **không gọi ACCESSTRADE để tạo tracking link mới**.

Trên dashboard:

```text
/sanpham
→ Nhập link affiliate
→ dán link Shopee
→ Phân tích link
→ kiểm tra/chỉnh tên, giá, ảnh, shop
→ chọn kênh Threads
→ Tạo bài nháp
→ /duyet
```

ACP thử resolve link và đọc metadata công khai bằng JSON-LD/OpenGraph. Nếu Shopee không trả đủ metadata, màn hình xác nhận vẫn mở để nhập phần còn thiếu thủ công.

Các nguyên tắc bắt buộc của luồng này:

- link affiliate được lưu **nguyên đúng giá trị operator nhập** trong `post.affiliate_link`;
- `manual_shopee` và ACCESSTRADE là hai nguồn độc lập;
- không tự thêm `sub1=post_id` vào link Shopee có sẵn;
- attribution của bài manual ghi rõ `provider=shopee_direct`, `link_mode=prebuilt`;
- resolve/create chỉ tạo `PENDING_REVIEW` (hoặc `DRAFT` nếu caption chưa qua validator);
- không tạo `PUBLISH_POST` trước khi operator duyệt tại `/duyet`;
- outbound URL và ảnh đi qua kiểm tra scheme/host/DNS/IP, redirect, Content-Type và giới hạn kích thước để giảm rủi ro SSRF.

## Catalog sản phẩm ACCESSTRADE TikTok Shop

Đồng bộ catalog chạy độc lập với Flask để có thể gọi an toàn từ cron hoặc
systemd timer. Sao chép các biến catalog từ `.env.example` vào `.env.local` của
runtime, rồi đặt `ACCESSTRADE_API_TOKEN` ở file runtime đó. Token phải để trống
trong `.env.example` và không được commit.

```bash
/bin/bash -lc 'set -a; . /home/operator/Downloads/ACP/acp/.env.local; set +a; exec /home/operator/Downloads/ACP/acp/.venv/bin/python /home/operator/Downloads/ACP/acp/run.py product-sync'
```

`/home/operator/Downloads/ACP/acp` là symlink tới release đang active, vì vậy
lệnh luôn source đúng `.env.local` của release trước khi chạy đúng virtualenv.
Thay `/home/operator` bằng đường dẫn tuyệt đối nơi ACP được cài.

Lệnh lấy tối đa `ACP_PRODUCT_SYNC_MAX_PAGES` trang, upsert catalog cục bộ và
không tạo bản ghi trùng khi đồng bộ lại. Đặt lịch mỗi 60 phút (theo
`ACP_PRODUCT_SYNC_INTERVAL_MINUTES`), ví dụ cron:

```cron
0 * * * * /bin/bash -lc 'set -a; . /home/operator/Downloads/ACP/acp/.env.local; set +a; exec /home/operator/Downloads/ACP/acp/.venv/bin/python /home/operator/Downloads/ACP/acp/run.py product-sync'
```

Hoặc dùng systemd service có `ExecStart` là đúng command `/bin/bash -lc` ở trên,
và timer `OnUnitActiveSec=60min`. Không đặt scheduler trong Flask worker. Khóa
trong database sẽ từ chối một lượt đồng bộ đang chạy, nên không chạy chồng nhiều
job.

Để chạy thủ công, mở `/sanpham`, nhập từ khóa nếu cần và bấm **Đồng bộ**. Trang
này chỉ đọc catalog cục bộ sau khi sync; operator có thể tạo link affiliate hoặc
tạo bài nháp cho một sản phẩm. `--auto-prepare` chỉ có hiệu lực khi
`ACP_AUTO_PREPARE_CONTENT=true`; mặc định tắt. Ngay cả khi bật, ACP chỉ tạo bài
`PENDING_REVIEW` để người vận hành kiểm tra ở `/duyet`, không tự publish.

Khi gặp lỗi: HTTP 401 nghĩa là kiểm tra lại token ACCESSTRADE trong `.env.local`;
429 là giới hạn tốc độ, chờ rồi chạy lại; timeout/5xx hoặc dịch vụ không phản hồi
thì thử lại sau. Sản phẩm hết hàng, không có `detail_link`, hoặc còn trong
`ACP_PRODUCT_REPOST_COOLDOWN_DAYS` sẽ không được auto-prepare. Manual selection
trên `/sanpham` có thể override cooldown, nhưng vẫn dừng ở bước duyệt tay.

### Thao tác hàng loạt trên catalog

Mỗi thẻ sản phẩm ở `/sanpham` có ô chọn; nút **Chọn tất cả trên trang này** chỉ
tiện thao tác, server luôn tự kiểm tra lại từng sản phẩm được gửi lên (tồn tại,
đúng provider, còn hàng, có `detail_link`) chứ không tin trạng thái checkbox.
Có hai nút thao tác hàng loạt, tối đa 10 sản phẩm mỗi lần (`max_items` mặc định
của `ProductService.create_product_links`/`create_posts` trong `core/products.py`):

- **Tạo link hàng loạt** — tạo/làm mới link product-card
  (`sub1=product:<external_product_id>`) cho từng sản phẩm đã chọn, y hệt nút
  "Tạo link" của một sản phẩm đơn lẻ, chỉ khác là chạy nhiều lần liên tiếp.
- **Tạo bài hàng loạt** — tạo bài nháp cho từng sản phẩm, gọi lại đúng luồng
  một-sản-phẩm-một-bài hiện có (link riêng cho từng bài, không tái dùng link
  product-card) nên vẫn dừng ở `PENDING_REVIEW`/`DRAFT`, **không** tự đăng và
  **không** tạo job `PUBLISH_POST`. Sản phẩm đã có bài đang hoạt động
  (DRAFT/PENDING_REVIEW/APPROVED/SCHEDULED) bị bỏ qua để tránh tạo bài trùng.

Một sản phẩm lỗi (hết hàng, thiếu link, provider từ chối...) không chặn các sản
phẩm còn lại trong lô; kết quả hiển thị dạng "N thành công, N bỏ qua, N lỗi",
không bao giờ lộ nội dung lỗi thô từ provider.

### Worker tự đăng theo lịch

Worker đăng bài chạy ngoài Flask, một lượt mỗi phút. Nó chỉ xử lý job
`PUBLISH_POST` khi công tắc toàn hệ thống đã được operator bật; cài timer không
tự bật công tắc này. Xem trạng thái bằng:

```bash
/bin/bash -lc 'set -a; . "$HOME/Downloads/ACP/acp/.env.local"; set +a; exec "$HOME/Downloads/ACP/acp/.venv/bin/python" "$HOME/Downloads/ACP/acp/run.py" worker-status'
```

Để cài timer cấp user (không cần root), sao chép các mẫu unit trong `ops/`. Mẫu
dùng symlink `~/Downloads/ACP/acp` để luôn gọi release active và chỉ source
`.env.local` khi chạy, nên không có token trong unit:

```bash
mkdir -p ~/.config/systemd/user
cp ops/acp-worker.service ops/acp-worker.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now acp-worker.timer
systemctl --user status acp-worker.timer
```

Xem lượt chạy gần nhất bằng `journalctl --user -u acp-worker.service -n 100`.
Sau upgrade, giữ timer đang bật; unit sẽ theo symlink release active. Khi cần
dừng lịch worker, chạy `systemctl --user disable --now acp-worker.timer`.

### Kiểm tra end-to-end bằng mock

Giữ `ACP_ADAPTER=mock` và `ACP_SOURCE=mock`, rồi chạy command trên với hai biến
đó được export trong shell hiện tại, để sync → có một catalog row → tạo bài cho
row đó → xác nhận short link được lưu và post ở `PENDING_REVIEW`. Sync lại cùng
catalog phải vẫn chỉ có một row. Cuối cùng, mô phỏng publish thành công và kiểm
tra `last_posted_at`/`post_count` được cập nhật; lần auto-prepare sau phải tôn
trọng cooldown. Quy trình mock này không được publish Threads thật.

## Dark Premium dashboard

Dashboard server-rendered dùng design system chung tại:

```text
web/static/acp.css
```

Giao diện mới áp dụng cho Tổng quan, Sản phẩm, Kênh, Chờ duyệt, Vận hành, Chấm điểm và Đăng nhập. Không thêm React/Vue/Tailwind hoặc frontend build pipeline; route, CSRF, auth và business logic hiện có vẫn giữ nguyên.
