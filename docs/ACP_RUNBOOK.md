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

Sau khi ACP 2.0 đã được xác nhận và `~/Downloads/ACP/acp` trỏ đúng release đang chạy:

```bash
cd ~/Downloads/ACP
ln -sfn "acp/manage.sh" manage.sh
chmod +x "$(readlink -f manage.sh)"
```

Kiểm tra:

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
