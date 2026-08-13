# ACP Runbook — chạy, nâng cấp và rollback

Mục tiêu: sau khi setup một lần, vận hành ACP chỉ qua `manage.sh`. Không lặp lại các bước `source`, `nohup`, backup DB, tạo symlink, test và migration bằng tay.

## 1. Cấu trúc cố định

```text
~/Downloads/ACP/
├── manage.sh -> acp/manage.sh
├── acp -> releases/<version>/acp
├── releases/
├── shared/
│   ├── .env.local
│   ├── run/
│   └── var/
│       ├── acp-live.db
│       └── media/
├── backups/
└── logs/
```

`shared/` chứa dữ liệu/secrets. Source mỗi phiên bản nằm trong `releases/`. Không commit `shared/`.

## 2. Setup một lần

**Máy mới (git clone lần đầu):**

```bash
git clone <repo-url> ~/Downloads/ACP/releases/2.0/acp
cd ~/Downloads/ACP/releases/2.0/acp
./manage.sh setup
```

`setup` tự tạo `.venv`, cài `requirements.txt`, dựng `shared/.env.local`
từ `.env.example` (tự sinh `ACP_MASTER_KEY` qua `run.py genkey`, tự điền
`ACP_DB` theo đường dẫn máy này), symlink `acp/.env.local`/`acp/var` vào
`shared/`, tạo schema CSDL (`init_db()` — chỉ schema, không seed demo
data), và tạo hai symlink `~/Downloads/ACP/acp` +
`~/Downloads/ACP/manage.sh`. Idempotent: chạy lại không ghi đè
`.env.local`/CSDL đã có.

Muốn giữ nguyên kết nối Threads + catalog của máy cũ thay vì bắt đầu
trắng: copy nguyên `shared/` (scp/rsync, **không** qua git vì có secret
thật) sang máy mới trước khi chạy `setup` — `setup` thấy `.env.local` đã
tồn tại thì bỏ qua bước tạo mới.

Sau `setup`, điền các biến bắt buộc mà script không tự sinh được vào
`shared/.env.local`: `ACP_ADMIN_PASSWORD`, `ACP_SECRET_KEY`, và tuỳ nhu
cầu `ACCESSTRADE_API_TOKEN`/`ACP_GEMINI_API_KEY`/`ACP_PUBLIC_BASE_URL`.
Không điền gì thì `./manage.sh start` vẫn chạy được ở chế độ dev cục bộ
(không đăng nhập, `ACP_ADAPTER=mock`).

**Máy đã có sẵn release cũ (đổi bằng ZIP qua `manage.sh upgrade` thay vì
git clone trực tiếp):** dùng lại `~/Downloads/ACP/acp` đang trỏ đúng, rồi:

```bash
cd ~/Downloads/ACP
ln -sfn "acp/manage.sh" manage.sh
chmod +x "$(readlink -f manage.sh)"
```

Kiểm tra cả hai đường:

```bash
./manage.sh status
./manage.sh test
```

Từ đây không cần `source .venv/bin/activate` hoặc `source .env.local` để start app.

## 3. Dùng hằng ngày

```bash
cd ~/Downloads/ACP
./manage.sh start
```

Lệnh này:
- nạp `.env.local` của release hiện tại;
- chạy Flask bằng đúng `.venv`;
- đợi HTTP local trả lời;
- chạy ngrok nếu có URL ngrok trong `ACP_NGROK_URL`, `ACP_PUBLIC_BASE_URL`, hoặc `ACP_MEDIA_BASE_URL`;
- ghi PID vào `shared/run/` và log vào `logs/`.

Các lệnh thường dùng:

```bash
./manage.sh status
./manage.sh restart
./manage.sh stop
```

Xem log:

```bash
tail -f ~/Downloads/ACP/logs/acp.log
tail -f ~/Downloads/ACP/logs/ngrok.log
```

## 4. Test release hiện tại

```bash
cd ~/Downloads/ACP
./manage.sh test
```

`test` chạy:
1. `acp.tests.test_pipeline`;
2. `acp.tests.test_pilot`;
3. `run.py doctor`.

Trong bước kiểm thử, script ép `ACP_ADAPTER=mock` và `ACP_SOURCE=mock`, không publish Threads thật.

## 5. Nâng cấp bằng ZIP đáng tin cậy

Chỉ chạy sau khi đã review release/ZIP và Git working tree hiện tại sạch:

```bash
cd ~/Downloads/ACP
./manage.sh upgrade ~/Downloads/acp_2.1.zip 2.1
```

Script tự thực hiện:

```text
stop
→ backup SQLite
→ giải nén release mới
→ gắn shared/var + shared/.env.local
→ tạo .venv
→ cài requirements
→ chạy 2 bộ test + doctor ở mock mode
→ chạy schema-only init_db()
→ ghi release trước để rollback
→ đổi symlink acp
→ start + health check
```

Nếu app mới không start được sau khi chuyển symlink, script tự quay về release trước và thử start lại.

### Git

Nếu release hiện tại là Git repo, `upgrade` sao chép metadata Git vào release mới và tạo branch `upgrade/<version>` khi có thể. Nó **không commit, push hoặc merge**. Agent/human phải review `git diff`, test và xử lý Git riêng.

## 6. Rollback

```bash
cd ~/Downloads/ACP
./manage.sh rollback
```

Script dừng runtime, chuyển về release trước đã ghi nhận và start lại. DB backup trước upgrade vẫn nằm trong `~/Downloads/ACP/backups/`.

## 7. Quy tắc DB

Không chạy thủ công:

```bash
python3 run.py init
```

trên DB live chỉ để nâng schema. `manage.sh upgrade` gọi:

```python
from acp.core.db import init_db
init_db()
```

để áp schema/migration mà không seed lại channel/template demo.

## 8. Quy trình với IDE + terminal agent

```text
main sạch
→ branch feature/upgrade
→ agent đọc AGENTS.md
→ sửa code
→ python3 tests/test_manage.py (nếu manager thay đổi)
→ ./manage.sh test
→ git diff / review
→ commit + push branch
→ merge main
→ tag
→ restart hoặc upgrade release
```

Agent không được tự publish Threads thật, tự đổi `ACP_ADAPTER=live`, hoặc force-push `main`.

## 9. Pilot một link affiliate Shopee có sẵn

Luồng operator chuẩn:

```text
./manage.sh start
→ mở /sanpham
→ chọn "Nhập link affiliate"
→ dán một link affiliate Shopee có sẵn
→ bấm "Phân tích link"
→ kiểm tra/chỉnh metadata
→ chọn đúng kênh Threads
→ bấm "Tạo bài nháp"
→ mở /duyet
```

Ở `/duyet`, kiểm tra:

- ảnh đúng sản phẩm;
- tên và giá đúng;
- caption có disclosure và không bịa trải nghiệm;
- affiliate link đúng link đã nhập;
- trạng thái là `PENDING_REVIEW` hoặc `DRAFT` nếu validator báo lỗi;
- chưa có `thread_id` và chưa có hành vi publish.

Luồng Shopee direct không tạo tracking link qua ACCESSTRADE và không tự gắn `sub1=post_id`. Vì vậy không được coi conversion là đã quy kết chính xác tới từng post nếu provider Shopee không trả một identifier tương ứng.

### Kiểm thử trước pilot thật

```bash
cd ~/Downloads/ACP
./manage.sh test
```

`manage.sh test` phải chạy ở mock mode. Automated verification không được publish Threads thật.

Sau khi test pass, chỉ tạo **một** bài từ link Shopee để xem ở `/duyet`. Việc duyệt/publish bài thật là một bước riêng do operator chủ động thực hiện.

## 10. Dark Premium UI

Giao diện quản trị dùng stylesheet chung `web/static/acp.css`. Khi kiểm tra sau nâng cấp, mở tối thiểu:

```text
/
/sanpham?mode=search
/sanpham?mode=affiliate
/duyet
/kenh
/vanhanh
/chamdiem
```

Kiểm tra thêm trên cửa sổ hẹp/mobile để chắc chắn sidebar, form Shopee, bảng và review card không vỡ layout. Thay đổi UI không phải lý do để bật adapter live hoặc publish thử tự động.

## 11. Catalog ACCESSTRADE TikTok Shop

### Cấu hình an toàn

Sao chép các biến catalog trong `.env.example` vào `.env.local` của runtime. Chỉ
điền token thật vào `.env.local`; file đó nằm trong `shared/` và không được
commit. Các giá trị vận hành mặc định là:

```dotenv
ACCESSTRADE_API_BASE_URL=https://api.accesstrade.vn
ACCESSTRADE_API_TOKEN=
ACP_PRODUCT_SYNC_ENABLED=true
ACP_PRODUCT_SYNC_INTERVAL_MINUTES=60
ACP_PRODUCT_SYNC_MAX_PAGES=10
ACP_PRODUCT_REPOST_COOLDOWN_DAYS=7
ACP_PRODUCT_RECOMMENDATION_LIMIT=20
ACP_AUTO_PREPARE_CONTENT=false
ACP_AUTO_PREPARE_CONTENT_COUNT=3
```

`ACP_AUTO_PREPARE_CONTENT` phải giữ `false` cho đến khi operator chủ động bật
nó. Bật cờ này vẫn không tự publish: nội dung tạo tự động dừng ở
`PENDING_REVIEW` để duyệt tay tại `/duyet`.

### Đồng bộ thủ công và theo lịch

Từ release đang chạy:

```bash
/bin/bash -lc 'set -a; . /home/operator/Downloads/ACP/acp/.env.local; set +a; exec /home/operator/Downloads/ACP/acp/.venv/bin/python /home/operator/Downloads/ACP/acp/run.py product-sync'
```

Thay `/home/operator` bằng đường dẫn cài đặt tuyệt đối. `acp` là symlink release
đang active, nên wrapper source `.env.local` của đúng release trước khi gọi
interpreter và `run.py` cùng release đó. Không dùng bare `python3 run.py` trong
cron hoặc systemd vì process scheduler không tự nạp `.env.local`.

Trên dashboard, mở `/sanpham`, có thể nhập từ khóa, rồi bấm **Đồng bộ**. Catalog
được tìm/lọc/sắp xếp tại database cục bộ; tạo link hoặc tạo bài chỉ áp dụng cho
sản phẩm operator chọn.

Tạo cron hoặc systemd timer ngoài Flask worker, chạy mỗi 60 phút. Ví dụ cron:

```cron
0 * * * * /bin/bash -lc 'set -a; . /home/operator/Downloads/ACP/acp/.env.local; set +a; exec /home/operator/Downloads/ACP/acp/.venv/bin/python /home/operator/Downloads/ACP/acp/run.py product-sync'
```

Ví dụ systemd (dùng một service để env không bị ghi trực tiếp trong unit):

```ini
# /etc/systemd/system/acp-product-sync.service
[Service]
Type=oneshot
ExecStart=/bin/bash -lc 'set -a; . /home/operator/Downloads/ACP/acp/.env.local; set +a; exec /home/operator/Downloads/ACP/acp/.venv/bin/python /home/operator/Downloads/ACP/acp/run.py product-sync'
```

```ini
# /etc/systemd/system/acp-product-sync.timer
[Timer]
OnBootSec=5min
OnUnitActiveSec=60min
Persistent=true
Unit=acp-product-sync.service

[Install]
WantedBy=timers.target
```

Sau khi tạo/sửa unit, chạy `systemctl daemon-reload` rồi `systemctl enable --now
acp-product-sync.timer`. Khóa database của catalog chặn sync chồng nhau; khi
nhận thông báo đồng bộ đang chạy, đợi job hiện tại hoàn tất thay vì chạy lại song
song.

### Thao tác hàng loạt trên catalog

`/sanpham` cho chọn nhiều sản phẩm cùng lúc (checkbox trên từng thẻ, có nút chọn
tất cả trên trang hiện tại — chỉ để tiện thao tác, server luôn tự kiểm tra lại
từng ID). Hai route:

- `POST /sanpham/batch/affiliate-link` — tạo hàng loạt link product-card
  (`sub1=product:<external_product_id>`).
- `POST /sanpham/batch/tao-bai` — tạo hàng loạt bài nháp, dùng lại nguyên luồng
  một-sản-phẩm-một-bài hiện có nên luôn dừng ở `PENDING_REVIEW`/`DRAFT`, không
  tự đăng, không tạo job `PUBLISH_POST`.

Cả hai giới hạn 10 sản phẩm/lần (`ProductService.create_product_links`/
`create_posts`, tham số `max_items`), khử trùng lặp ID, giữ nguyên thứ tự đã
chọn, và không dừng cả lô khi một sản phẩm lỗi — kết quả trả về dạng "N thành
công, N bỏ qua, N lỗi" không lộ lỗi provider thô. Sản phẩm đã có bài đang hoạt
động bị bỏ qua khi tạo bài hàng loạt để tránh trùng bài.

### Worker tự đăng bài theo lịch

Worker là process riêng chạy một lượt mỗi phút; Flask dashboard không tự quét
hàng đợi. Công tắc publish worker được lưu trong database và mặc định **tắt**.
Vì vậy cài hoặc bật timer không làm ACP tự đăng bài. Khi công tắc tắt, các job
`PUBLISH_POST` đến hạn vẫn giữ `READY`; các job khác vẫn có thể được xử lý.

Kiểm tra trạng thái trước khi cài:

```bash
/bin/bash -lc 'set -a; . "$HOME/Downloads/ACP/acp/.env.local"; set +a; exec "$HOME/Downloads/ACP/acp/.venv/bin/python" "$HOME/Downloads/ACP/acp/run.py" worker-status'
```

Từ source/release ACP, cài unit user đi kèm. Các unit dùng `%h/Downloads/ACP/acp`
(symlink release active), source `.env.local` ngay khi bắt đầu và không chứa
token/key trong source control:

```bash
mkdir -p ~/.config/systemd/user
cp ops/acp-worker.service ops/acp-worker.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now acp-worker.timer
systemctl --user status acp-worker.timer
```

Kiểm tra một lượt chạy và lỗi cục bộ an toàn:

```bash
systemctl --user status acp-worker.service
journalctl --user -u acp-worker.service -n 100
```

Timer gọi `run.py worker-once` mỗi phút và service sẽ thử lại khi lỗi vận hành.
Không dùng `run.py work` trong timer vì lệnh đó drain toàn bộ hàng đợi. Dừng lịch
nhưng không đổi công tắc bằng `systemctl --user disable --now acp-worker.timer`.

### Xử lý sự cố

- **401:** token ACCESSTRADE thiếu hoặc sai. Cập nhật chỉ `.env.local`, rồi chạy
  lại sync; không đưa token vào source control.
- **429:** ACCESSTRADE đang rate-limit. Client đã retry hữu hạn; chờ rồi chạy lại,
  không tạo thêm timer song song.
- **Không phản hồi/5xx:** dịch vụ hoặc mạng tạm thời không khả dụng. Thử lại sau
  và xem log vận hành, không dán response/provider token vào ticket.
- **Sản phẩm không được auto-prepare:** kiểm tra còn hàng, `detail_link`, trạng
  thái link và cooldown. Manual selection ở `/sanpham` được phép override
  cooldown nhưng bài vẫn phải duyệt tay.

### Acceptance mock end-to-end

Chỉ dùng mock (`ACP_ADAPTER=mock`, `ACP_SOURCE=mock`) khi chạy acceptance:

```text
sync
→ một catalog row
→ generate bài cho row
→ short link được lưu
→ PENDING_REVIEW
→ sync lại vẫn một row
→ simulated publish thành công cập nhật cooldown
```

Kiểm tra `last_posted_at` và `post_count` sau simulated publish; candidate đó sẽ
không được auto-prepare lại trong `ACP_PRODUCT_REPOST_COOLDOWN_DAYS`. Không
publish Threads thật trong quy trình xác minh này.
