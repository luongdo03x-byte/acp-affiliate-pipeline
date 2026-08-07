# Hướng dẫn triển khai

Hai kiến trúc. Chọn theo giai đoạn bạn đang ở.

| | **A — Máy cá nhân** | **B — Máy chủ 24/7** |
|---|---|---|
| Dùng khi | Chạy thử, học hệ thống | Đã có doanh thu, muốn ổn định |
| Chi phí/tháng | 0đ | 130–250k |
| Cần tên miền | Không | Có |
| Cần VPS | Không | Có |
| Nhận postback | Không — đối soát 6h/lần | Có |
| Máy tắt thì | Bài dồn lại, đăng bù khi bật | Không sao |

**Phần A là nơi bắt đầu.** Nó bỏ được tên miền, VPS và tunnel bằng một quyết định
kiến trúc: không nhận postback, chỉ kéo dữ liệu về bằng lệnh `reconcile`.

Đánh đổi duy nhất là biết đơn sau tối đa 6 giờ thay vì tức thì. Vì hoa hồng chỉ
chốt được ở bước đối soát — postback chỉ báo trạng thái `pending` — khác biệt này
gần như bằng không trong thực tế.

---

# PHẦN A — Chạy thử ở máy cá nhân

## A1. Đăng ký Accesstrade

Làm trước vì phải chờ duyệt.

**Tạo tài khoản** tại `pub2.accesstrade.vn`. Khai đúng kênh quảng bá — chọn mạng
xã hội, ghi rõ tên tài khoản Threads. Khai sai thì đơn có thể bị từ chối sau, vì
Accesstrade đối chiếu nguồn traffic.

**Đăng ký chiến dịch Shopee.** Chờ tới khi nút đổi thành *Tạo Link*. Ghi lại
**Campaign ID** (chuỗi số dài trong URL trang chiến dịch).

**Lấy Access Key** trong phần công cụ lập trình viên. Thử ngay:

```bash
curl -H 'Authorization: Token <ACCESS_KEY>' \
     'https://api.accesstrade.vn/v1/campaigns'
```

Ra JSON danh sách chiến dịch là được.

> **Không cần khai postback URL.** Đó là điểm mấu chốt giúp bỏ được tên miền.

## A2. Kết nối Threads

### Tin tốt: nhiều khả năng bạn không cần App Review

Meta có hai mức truy cập:

- **Standard Access** — đăng lên tài khoản của chính bạn và tài khoản tester của app. Không cần duyệt.
- **Advanced Access** — đăng lên tài khoản của bất kỳ ai. Cần Tech Provider Verification (~1 tuần) rồi App Review 2–4 tuần mỗi permission.

Hệ thống này chỉ đăng lên kênh bạn sở hữu, nên Standard Access là đủ.

### A2.1. Tạo app

Vào `developers.facebook.com`, tạo app mới, chọn use case **Threads API**.

Thêm Redirect URI. Vì chạy ở máy cá nhân, dùng địa chỉ nội bộ:

```
https://localhost:5000/oauth/threads/callback
```

Ghi lại **App ID** và **App Secret**.

### A2.2. Thêm chính bạn làm tester

Vào phần vai trò của app, thêm tài khoản Threads của bạn với vai trò
**Threads Tester**. Sau đó mở `threads.net` bằng chính tài khoản đó, vào phần lời
mời và **bấm chấp nhận**.

Bước chấp nhận rất hay bị quên. Không chấp nhận thì OAuth báo lỗi quyền.

### A2.3. Lấy token

Mở trên trình duyệt:

```
https://threads.net/oauth/authorize
  ?client_id=<APP_ID>
  &redirect_uri=https://localhost:5000/oauth/threads/callback
  &scope=threads_basic,threads_content_publish,threads_manage_insights
  &response_type=code
```

Đồng ý. Trình duyệt chuyển hướng và **sẽ báo lỗi không kết nối được** — bình
thường, vì chưa có gì chạy ở localhost. Thứ bạn cần là đoạn `?code=...` trên
thanh địa chỉ. Sao chép nó (chỉ sống vài phút).

Đổi lấy token ngắn hạn:

```bash
curl -X POST https://graph.threads.net/oauth/access_token \
  -F client_id=<APP_ID> \
  -F client_secret=<APP_SECRET> \
  -F grant_type=authorization_code \
  -F redirect_uri=https://localhost:5000/oauth/threads/callback \
  -F code=<CODE>
```

Trả về `access_token` và `user_id`. **Ghi lại cả hai.**

Đổi sang token dài hạn (60 ngày):

```bash
curl -G https://graph.threads.net/access_token \
  -d grant_type=th_exchange_token \
  -d client_secret=<APP_SECRET> \
  -d access_token=<TOKEN_NGAN_HAN>
```

### A2.4. Kiểm tra quyết định — có cần App Review không

```bash
# tạo container
curl -X POST "https://graph.threads.net/v1.0/<USER_ID>/threads" \
  -F media_type=TEXT -F text="Thử kết nối API" \
  -F access_token=<TOKEN_DAI_HAN>

# publish, dùng id vừa nhận
curl -X POST "https://graph.threads.net/v1.0/<USER_ID>/threads_publish" \
  -F creation_id=<ID_BUOC_TREN> \
  -F access_token=<TOKEN_DAI_HAN>
```

**Đăng được → bỏ qua App Review hoàn toàn.**

**Báo lỗi thiếu quyền → cần Advanced Access.** Nộp Tech Provider Verification rồi
App Review từng permission, mỗi hồ sơ kèm video quay màn hình.

## A3. Cloudflare R2 — bắt buộc

Đây là ràng buộc duy nhất không lách được khi chạy ở máy cá nhân. Threads không
nhận file gửi thẳng: máy chủ Meta phải tự tải ảnh về từ một URL công khai. Máy
bạn không có địa chỉ công khai, nên ảnh phải nằm chỗ khác.

R2 miễn phí 10GB lưu trữ và không tính phí băng thông ra — thừa cho việc này.

**Tạo bucket.** Vào Cloudflare, mục R2, tạo bucket tên `acp-media`. Bật
**Public Access**, ghi lại URL công khai dạng `https://pub-xxxxx.r2.dev`.

**Tạo API token** với quyền đọc/ghi cho bucket đó. Ghi lại Access Key ID,
Secret Access Key, và **Account ID** (ở trang tổng quan R2).

Endpoint sẽ có dạng:

```
https://<ACCOUNT_ID>.r2.cloudflarestorage.com
```

> Meta tải ảnh về lúc tạo container rồi tự lưu trên CDN của họ. Nên bài **đã**
> đăng không chết dù nguồn ảnh sau đó hỏng. URL chỉ cần sống đúng lúc đăng.

## A4. Cài app

```bash
unzip acp-affiliate-pipeline.zip
cd acp

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Chạy thử offline để chắc môi trường ổn:

```bash
python3 run.py demo
```

Rồi chạy test — **chú ý phải đứng ở thư mục cha**:

```bash
cd ..                              # KHÔNG đứng trong thư mục acp
python3 -m acp.tests.test_pipeline
cd acp
```

Phải ra `37 đạt, 0 hỏng`.

## A5. Khai cấu hình

Sinh khoá mã hoá trước:

```bash
python3 run.py genkey
```

```bash
nano .env
```

```bash
export ACP_ENV=production
export ACP_ADAPTER=live
export ACP_MASTER_KEY='<dán kết quả genkey>'

export AT_ACCESS_KEY='<access key Accesstrade>'
export AT_CAMPAIGN_ID='<campaign id Shopee>'

export ACP_STORAGE=s3
export R2_BUCKET='acp-media'
export R2_ENDPOINT='https://<ACCOUNT_ID>.r2.cloudflarestorage.com'
export R2_ACCESS_KEY_ID='<key id>'
export R2_SECRET_ACCESS_KEY='<secret>'
export ACP_MEDIA_BASE_URL='https://pub-xxxxx.r2.dev'

# Bảo mật — BẮT BUỘC khi ACP_ENV=production, app từ chối chạy nếu thiếu
export ACP_ADMIN_PASSWORD='<mật khẩu vào dashboard>'
export ACP_SECRET_KEY='<chuỗi ngẫu nhiên dài, giữ phiên đăng nhập>'
export ACP_WEBHOOK_SECRET='<khoá gắn vào postback URL, chỉ cần ở Phần B>'

# Nguồn sản phẩm: tiktokshop | shopee | mock
export ACP_SOURCE='tiktokshop'
export AT_TIKTOK_CAMPAIGN_ID='<campaign id TikTok Shop>'
```

Sinh hai khoá ngẫu nhiên:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # ACP_SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # ACP_WEBHOOK_SECRET
```

```bash
chmod 600 .env
```

> Mất `ACP_MASTER_KEY` là mất toàn bộ token đã lưu, phải làm lại OAuth. Chép ra
> một chỗ khác ngoài máy này.

## A6. Nạp kênh và kiểm tra cấu hình

```bash
source .env
python3 run.py init
```

```bash
python3 - <<'PY'
from acp.core.db import connect, now, ulid
from acp.core import crypto
c = connect()
c.execute("""INSERT INTO channel (id, code, platform, handle, external_user_id, status,
             token_encrypted, daily_post_cap, min_gap_minutes, created_at)
             VALUES (?,?,'threads',?,?,'ACTIVE',?,?,?,?)""",
          (ulid(), "threads_main", "@handle_cua_ban", "<USER_ID>",
           crypto.encrypt("<TOKEN_DAI_HAN>"), 3, 180, now()))
c.close(); print("đã thêm kênh")
PY
```

Thêm kênh thứ hai, thứ ba thì lặp lại đoạn trên với `code` và `handle` khác, rồi
gán nhóm sản phẩm riêng cho từng kênh.

`daily_post_cap = 3` và `min_gap_minutes = 180` cho giai đoạn thử. Máy cá nhân
hay tắt, để trần thấp thì hàng đợi không tích quá nhiều.

**Chọn nhóm sản phẩm cho từng kênh:**

```bash
python3 run.py niche                                    # xem tất cả kênh
python3 run.py niche threads_nu thoi-trang-nu my-pham
python3 run.py niche threads_be me-va-be
python3 run.py niche threads_pet thu-cung
```

Hoặc tích ô trên trang `/kenh`. Có 8 nhóm: thời trang nữ, thời trang nam, mỹ
phẩm, mẹ & bé, thú cưng, gia dụng, công nghệ, thể thao.

Lọc chạy ở tầng lọc cứng — sản phẩm ngoài nhóm không bao giờ lên bài. Đổi được
bất cứ lúc nào, bài đã đăng không bị ảnh hưởng.

Một số nhóm tự thêm cụm cấm vào rào chắn caption vì là hàng quảng cáo có điều
kiện: `my-pham` cấm khẳng định điều trị ("trị mụn", "trắng da cấp tốc"),
`me-va-be` cấm khẳng định dinh dưỡng ("giúp bé ăn ngon", "tăng đề kháng"),
`thu-cung` cấm khẳng định chữa bệnh. Bộ dò bắt cả biến thể viết không dấu.

**Kiểm tra toàn bộ cấu hình:**

```bash
python3 run.py doctor
```

Phải thấy tất cả dấu `✓`. Còn `✗` thì xử lý trước khi đi tiếp — lệnh này ghi rõ
thiếu gì.

## A7. Lô đầu tiên

```bash
python3 run.py ingest
```

Lỗi ở đây thường là **map trường dữ liệu chưa khớp**. Xem dữ liệu thô:

```bash
curl -H "Authorization: Token $AT_ACCESS_KEY" \
     'https://api.accesstrade.vn/v1/datafeeds?domain=shopee.vn&limit=2' | python3 -m json.tool
```

Đối chiếu tên trường thật với hàm `fetch_products` trong `adapters/live.py` rồi sửa.

**Cách 1 — một sản phẩm cụ thể (khuyến nghị cho lần đầu).** Tìm rồi tạo bài:

```bash
python3 run.py search "máy xay"        # lấy mã sản phẩm
python3 run.py product <mã sản phẩm>   # tạo MỘT bài chờ duyệt
```

Hoặc làm trên giao diện tại `/sanpham`. Trang này cố ý **không có nút đăng ngay** —
mọi bài đều phải qua màn hình duyệt.

**Cách 2 — hàng loạt theo chấm điểm.** Chỉ dùng sau khi cách 1 đã chạy đúng:

```bash
python3 run.py plan
python3 run.py work
```

```bash
python3 run.py serve      # mở http://127.0.0.1:5000/duyet
```

Đọc kỹ từng caption. Đây là lần duy nhất bạn thấy máy nghĩ gì trước khi nó nói ra
ngoài. Lần đầu chỉ duyệt một bài thôi.

## A8. Kiểm tra quy kết — **không được bỏ qua**

> **Đọc trước:** nhiều campaign cấm publisher tự mua qua link của chính mình
> (self-referral). Vi phạm có thể bị huỷ đơn, khoá tài khoản hoặc giữ hoa hồng.
> Kiểm tra điều khoản campaign trước. Hướng dẫn trước đây của tôi nói thẳng
> "mua một món rẻ" mà không cảnh báo — đó là thiếu sót.

Ba cách, xếp theo mức an toàn:

**Cách 1 — chỉ kiểm tra click, không mua.** Bấm vào link, xem có chuyển đúng sản
phẩm không, và kiểm tra tham số còn nguyên trên URL đích. Chứng minh được nửa
đầu của chuỗi quy kết mà không đụng gì tới điều khoản.

```bash
python3 run.py product <mã sản phẩm>   # tạo bài
# duyệt trên /duyet, chờ đăng, rồi bấm link trên Threads
```

Trên URL đích phải thấy `utm_content` hoặc `sub1` chứa mã bài.

**Cách 2 — nhờ người khác mua thật.** Gửi link cho một người quen, họ mua món họ
cần. Đây là chuyển đổi hợp lệ và kiểm được trọn chuỗi.

**Cách 3 — nếu điều khoản campaign cho phép self-referral**, mua một món rẻ.

Sau khi có đơn:

```bash
python3 run.py reconcile
python3 run.py trace
```

Phải thấy `✓ quy kết được` kèm tham số `sub1` chứa mã bài.

**Nếu thấy `✗ KHÔNG quy kết được`** mà tham số vẫn có giá trị: tên tham số chưa
được đọc. Thêm tên đó vào `core/attribution.py`, hàm `extract_post_id()`.

Hệ thống đã gắn mã bài vào **cả `utm_content` lẫn `sub1`** để phòng một trong hai
bị ghi đè.

> Chưa kiểm được nửa sau của chuỗi thì đừng nâng số bài lên. Hệ thống sẽ chạy
> trơn tru, tiền vẫn về ví Accesstrade, nhưng dashboard trống trơn — và bạn chỉ
> phát hiện sau vài tuần, khi dữ liệu giai đoạn đó đã mất.

## A9. Chạy tự động

```bash
crontab -e
```

```cron
0 3    * * * cd ~/acp && . .env && .venv/bin/python run.py ingest    >> var/cron.log 2>&1
0 6    * * * cd ~/acp && . .env && .venv/bin/python run.py plan      >> var/cron.log 2>&1
*/5    * * * * cd ~/acp && . .env && .venv/bin/python run.py work    >> var/cron.log 2>&1
0 */6  * * * cd ~/acp && . .env && .venv/bin/python run.py reconcile >> var/cron.log 2>&1
```

Cửa sổ đối soát mặc định 7 ngày — cố ý, để vớt lại đơn phát sinh lúc máy tắt.

**Không có dòng nào tự duyệt bài.** Mỗi sáng bạn mở `/duyet`, đọc, bấm duyệt.
Khoảng 5 phút.

## A10. Hai tuần đầu

| Ngày | Việc |
|---|---|
| 1–3 | 3 bài/ngày. Đọc kỹ từng caption, ghi lại phải sửa gì |
| 4–7 | Vẫn 3 bài. Xem danh mục nào có click |
| 8–14 | Kiểm tra đã có đơn nào quy kết đúng chưa |
| Sau đó | Nâng lên 5–8 bài, vào `/chamdiem` chỉnh trọng số theo dữ liệu thật |

Đừng vội nâng số bài. Threads bóp reach của tài khoản đăng dày mà tương tác thấp,
và một khi bị bóp thì rất khó hồi.

**Gia hạn token sau 50 ngày** (hết hạn ở ngày 60):

```bash
curl -G https://graph.threads.net/refresh_access_token \
  -d grant_type=th_refresh_token -d access_token=<TOKEN_HIEN_TAI>
```

Rồi cập nhật vào CSDL (đổi `INSERT` thành `UPDATE` trong script ở A6). Token hết
hạn thì kênh tự chuyển `NEEDS_REAUTH` và dừng đăng — không mất dữ liệu.

---

# PHẦN B — Nâng lên máy chủ 24/7

Làm khi đã có doanh thu ổn định và thấy phiền vì bài đăng dồn cục.

## B1. VPS

1 vCPU, 1GB RAM là đủ. Chọn vùng Singapore. Ubuntu 24.04.

```bash
adduser acp && usermod -aG sudo acp && su - acp
sudo apt update && sudo apt install -y python3-pip python3-venv
```

Chép thư mục `acp` và file `.env` sang, cài lại venv như A4.

## B2. Cloudflare Tunnel — bỏ được nginx và certbot

Vẫn cần một tên miền đã nằm trong tài khoản Cloudflare. Chưa có thì mua trực tiếp
trên Cloudflare Registrar (bán giá gốc, `.com` khoảng 10–11 đô/năm) — domain nằm
sẵn trong tài khoản, không phải chờ đổi nameserver.

```bash
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cf.deb
sudo dpkg -i cf.deb

cloudflared tunnel login          # phải CHỌN một domain trong trình duyệt
cloudflared tunnel create acp
cloudflared tunnel route dns acp tenmien.com
```

Nếu `cloudflared tunnel login` treo ở `Waiting for login...`, gần như chắc chắn
tài khoản chưa có domain nào để chọn. Kiểm tra bằng `ls ~/.cloudflared/cert.pem` —
không có file nghĩa là chưa xong.

`~/.cloudflared/config.yml`:

```yaml
tunnel: <TUNNEL_ID>
credentials-file: /home/acp/.cloudflared/<TUNNEL_ID>.json
ingress:
  - hostname: tenmien.com
    service: http://localhost:5000
  - service: http_status:404
```

```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

## B3. Bật lại postback

Giờ đã có URL cố định, khai bên Accesstrade:

```
https://tenmien.com/webhook/at/postback?k=<ACP_WEBHOOK_SECRET>
```

Endpoint đã có sẵn trong `web/server.py` và **từ chối request không có đúng khoá**.
Không có khoá thì ai biết đường dẫn cũng bơm được doanh thu giả vào hệ thống. Thêm quy tắc WAF cho `/webhook/*` bỏ
qua bot protection, nếu không Cloudflare trả 403 và Accesstrade coi là thất bại.

**Vẫn giữ cron `reconcile`.** Postback chỉ báo `pending`; trạng thái `approved`
hay `rejected` chỉ chốt ở đối soát. Đối soát là nguồn chân lý, postback là bổ sung.

## B4. Chạy như dịch vụ

```bash
sudo nano /etc/systemd/system/acp-web.service
```

```ini
[Unit]
Description=ACP dashboard
After=network.target

[Service]
User=acp
WorkingDirectory=/home/acp/acp
EnvironmentFile=/home/acp/acp/.env.systemd
ExecStart=/home/acp/acp/.venv/bin/python run.py serve
Restart=always

[Install]
WantedBy=multi-user.target
```

`EnvironmentFile` của systemd không hiểu chữ `export`:

```bash
sed 's/^export //' .env > .env.systemd && chmod 600 .env.systemd
sudo systemctl enable --now acp-web
```

---

# Sự cố thường gặp

| Hiện tượng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `doctor` báo lưu trữ ảnh ✗ | Đang trỏ localhost | Đặt `ACP_STORAGE=s3` và các biến R2 |
| Bài đăng mất ảnh | URL R2 chưa công khai | Mở URL ảnh bằng cửa sổ ẩn danh. Không xem được thì Meta cũng không |
| Container mãi không `FINISHED` | Ảnh quá lớn hoặc URL chậm | Giảm `CANVAS` trong `core/imaging.py` |
| `AuthError` khi đăng | Token hết hạn (60 ngày) | Gia hạn theo A10 |
| `reconcile` báo chưa quy kết được | Tham số sub chưa khớp | Chạy `run.py trace`, sửa `extract_post_id()` |
| `No module named 'acp'` | Đang đứng trong thư mục `acp` | `cd ..` rồi chạy lại |
| Không có bài để duyệt | Cooldown 30 ngày, trần danh mục, hoặc lọc chủ đề | Vào `/chamdiem`, bảng "Bị loại" ghi rõ lý do từng món |
| Lọc nhóm ra quá ít sản phẩm | Nguồn không có hàng thuộc ngách đó | Đổi nguồn, hoặc thêm từ khoá vào `core/niche.py` |
| Nâng cấp từ bản cũ | Thiếu cột `channel.niches` | `run.py init` tự chạy migration, không mất dữ liệu |
| `ingest` lỗi | Map trường datafeed sai | So dữ liệu thô với `fetch_products` |
| Hoa hồng hiện rồi biến mất | Sàn từ chối đơn | Bình thường. Chỉ `approved` là tiền thật |
| `cloudflared` treo ở Waiting | Tài khoản chưa có domain | Thêm hoặc mua domain trên Cloudflare |
| Web chạy mock dù đã bật live | Bản cũ hardcode mock ở `/vanhanh/work` | Đã sửa — cả web và CLI dùng chung `adapters/factory.py` |
| App từ chối khởi động | `ACP_ENV=production` mà thiếu mật khẩu/khoá phiên | Đặt `ACP_ADMIN_PASSWORD` và `ACP_SECRET_KEY` |
| Postback trả 403 | Thiếu hoặc sai `?k=` | Khai lại postback URL kèm `ACP_WEBHOOK_SECRET` |
| Gọi API Accesstrade trả 404 | URL lặp `/v1/v1/...` | Đã sửa, có test hồi quy trong `test_pilot.py` |

---

# Checklist trước khi để chạy không người trông

- [ ] `python3 -m acp.tests.test_pipeline` cho `37 đạt, 0 hỏng`
- [ ] `python3 -m acp.tests.test_pilot` cho `101 đạt, 0 hỏng`
- [ ] Mỗi kênh đã được gán nhóm sản phẩm bằng `run.py niche`
- [ ] `ACP_ADMIN_PASSWORD` đã đặt (dashboard không được để công khai)
- [ ] Đã rotate Access Key Accesstrade nếu từng dán vào chat hoặc log
- [ ] `python3 run.py doctor` toàn dấu ✓
- [ ] Đã đăng thử thành công một bài lên Threads qua API
- [ ] **Đã có ít nhất một đơn thật quy kết đúng về bài đăng** (`run.py trace` thấy ✓)
- [ ] `ACP_MASTER_KEY` đã sao lưu ở nơi khác
- [ ] `chmod 600` cho `.env`
- [ ] URL ảnh R2 mở được bằng cửa sổ ẩn danh
- [ ] Cron `reconcile` chạy mỗi 6 giờ
- [ ] Đã đặt nhắc lịch gia hạn token sau 50 ngày
- [ ] `daily_post_cap` để 3 cho giai đoạn thử
- [ ] Đã đọc và duyệt tay ít nhất 30 bài trước khi nghĩ tới tự động duyệt

---

# Việc chưa làm trong code

1. **Job tự gia hạn token** — hiện làm tay mỗi 50 ngày (route OAuth callback đã có tại `/oauth/threads/callback`)
2. **Cảnh báo Telegram** khi publish lỗi liên tiếp hoặc 48 giờ không có chuyển đổi
4. **Lịch chạy trong app** — hiện dùng cron
