# Account Factory trên GCP, điều khiển Android thật

Luồng: Android có Internet → HTTPS/ngrok → nginx localhost:8080 → Factory
localhost:5001. Các trang ACP vẫn chuyển tới localhost:5000. Callback Factory
chuyển tới 5001; hai service đọc **cùng** `shared/.env.local` và `ACP_DB`.
`ACP_FACTORY_LOCAL_ONLY=1` cho phép VM không cài Android SDK/ADB/emulator.

## Chuẩn bị

- Checkout bản chứa thay đổi này trên VM, có `.venv` và layout `~/Downloads/ACP/acp`.
- Các service `acp-web` và `acp-ngrok` đang được cấu hình như deployment hiện tại.
- `ACP_DB` trong shared env phải là đường dẫn tuyệt đối đến DB đã có kênh/sản phẩm.
- Giữ nguyên `ACP_MASTER_KEY`, Threads App ID/Secret. Cấu hình redirect trong Meta:
  `https://hardener-nearest-poser.ngrok-free.dev/oauth/account-factory/threads/callback`.
- Ngrok config hiện tại ở `/home/luongdo03x/.config/ngrok/ngrok.yml`.
- Cài nginx nếu chưa có: `sudo apt-get install nginx`.

## Sinh cấu hình để kiểm tra

Chạy trên VM, từ thư mục code:

```bash
cd ~/Downloads/ACP/acp
python3 scripts/render_factory_cloud.py --user luongdo03x --acp-root /home/luongdo03x/Downloads/ACP --public-url https://hardener-nearest-poser.ngrok-free.dev --output /tmp/acp-factory-cloud
```

Script chỉ tạo bốn file cấu hình, không đọc secrets/DB, không cài hoặc restart
service. Nếu thư mục output đã tồn tại, chọn tên khác. Đọc các file vừa tạo;
đảm bảo user và đường dẫn khớp với server.

## Cài controller rồi kiểm tra trước khi chuyển ngrok

Các lệnh dưới dành cho lần cài đầu; nếu đã tồn tại các file cùng tên trong
`/etc`, sao lưu cấu hình đó trước khi cập nhật. Không chép/generate lại DB/env.

```bash
sudo install -m 644 /tmp/acp-factory-cloud/acp-factory.service /etc/systemd/system/acp-factory.service
sudo install -m 644 /tmp/acp-factory-cloud/acp-factory-runtime.env /etc/acp-factory-runtime.env
sudo systemctl daemon-reload
sudo systemctl enable --now acp-factory
curl --fail http://127.0.0.1:5001/api/factory/healthz
```

Đợi vài giây nếu health trả 503 khi khởi động. Chỉ chuyển bước sau khi nhận
`ok: true, controller: running`. Service chỉ tạo schema Factory cần thiết,
không seed dữ liệu, không bật tự đăng. Mặc dù adapter publish là mock, thao tác
OAuth do người dùng thực hiện vẫn dùng nhà cung cấp Threads thật.

```bash
sudo install -m 644 /tmp/acp-factory-cloud/acp-factory-gateway.conf /etc/nginx/conf.d/acp-factory-gateway.conf
sudo nginx -t
sudo systemctl reload nginx
curl --fail http://127.0.0.1:8080/api/factory/healthz
```

Chỉ tiếp tục khi nginx test và health đều thành công. Gateway này chỉ nghe
localhost, không cần mở cổng 5001/8080 ra Internet. Nếu nginx có site mặc định
nghe cổng 80, bảo đảm firewall không vô tình mở thêm đường vào ngoài ngrok.

```bash
sudo install -d /etc/systemd/system/acp-ngrok.service.d
sudo install -m 644 /tmp/acp-factory-cloud/acp-ngrok-gateway.conf /etc/systemd/system/acp-ngrok.service.d/factory-gateway.conf
sudo systemctl daemon-reload
sudo systemctl restart acp-ngrok
curl --fail https://hardener-nearest-poser.ngrok-free.dev/api/factory/healthz
```

Kiểm tra lại trang ACP đang dùng. Nếu public health thất bại, xem trạng thái
`acp-ngrok`/`acp-factory` trước khi ghép điện thoại; không chạy thêm tunnel cùng
endpoint trên laptop.

## Ghép Android lần đầu

Trong SSH trên VM, tạo mã dùng một lần:

```bash
cd ~/Downloads/ACP/acp
set +x
set -a
. ../shared/.env.local
set +a
.venv/bin/python factory_pairing.py
```

Mã tồn tại 10 phút, chỉ lưu hash trong DB và dùng một lần. Nhập mã trực tiếp
trên điện thoại, không gửi vào chat hoặc commit. Nếu ghép đôi bị mất response,
tạo mã mới. Mã mới được redeem cho cùng thiết bị sẽ thay token cũ của thiết bị đó.

Cài APK mới, mở Settings → **Ghép đôi với máy chủ**:

- URL: `https://hardener-nearest-poser.ngrok-free.dev`
- Mã ghép đôi: mã vừa tạo trên VM.

Android lưu device token bằng Keystore. Mất mạng không xóa credential đã lưu.
Public LAN auto-enroll bị tắt ở cả controller và gateway. USB dùng để cài/debug
APK hoặc cấp nguồn; khi chạy, điện thoại kết nối server qua Internet.

## Pilot một thiết bị

1. Cài Instagram và Threads chính chủ, bật Accessibility cho Account Factory.
2. Kiểm tra thiết bị xuất hiện trong Workers và có heartbeat.
3. Create account → **This phone**. Chế độ server này không có AVD.
4. Làm các bước đăng ký/xác minh cần người dùng trong ứng dụng chính chủ;
   hoàn tất checkpoint Instagram rồi Threads.
5. Mở OAuth khi controller yêu cầu, xác nhận đúng tài khoản. Chờ `ACP_ACTIVE`
   và kiểm tra kênh tương ứng trong ACP. Không bấm publish để thử kết nối.
6. Ngắt mạng rồi kết nối lại điện thoại; xác nhận runner hồi phục. Dừng một
   account và kiểm tra job/checkpoint kết thúc. Khởi động lại service khi không
   có thao tác đang diễn ra để kiểm tra khả năng phục hồi.

Các test mock trong repo không chứng minh Meta App đã cấu hình đúng hay thao
tác foreground trên một model Android cụ thể hoạt động. Cần pilot này trước
khi coi deployment hoàn tất.

## Chẩn đoán và quay về routing cũ

```bash
sudo systemctl --no-pager status acp-factory acp-ngrok
sudo journalctl -u acp-factory -n 40 --no-pager
curl --fail http://127.0.0.1:5001/api/factory/healthz
```

Nếu cần trả ngrok về cấu hình trước lần cài đầu, chuyển riêng drop-in vừa thêm
ra ngoài thư mục `.d`, rồi reload/restart ngrok. Không dùng `systemctl revert`
vì có thể xóa các override khác của bạn.

```bash
sudo mv /etc/systemd/system/acp-ngrok.service.d/factory-gateway.conf /etc/systemd/system/acp-ngrok-factory-gateway.disabled
sudo systemctl daemon-reload
sudo systemctl restart acp-ngrok
sudo systemctl disable --now acp-factory
```

Nếu file đích `.disabled` đã có, dùng một tên backup mới. Không xóa các bản ghi
Factory hoặc device credentials khi rollback routing.
