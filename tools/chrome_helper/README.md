# ACP Shopee Helper (Chrome/Chromium extension nội bộ)

ACP Shopee Helper xử lý trường hợp Shopee trả CAPTCHA/HTTP 403 cho request
server-side. Extension **không vượt CAPTCHA** và không tự động hoá Shopee: operator
tự mở trang sản phẩm, tự xem trang bình thường, rồi tự bấm icon extension để
chuyển metadata DOM đang hiển thị về ACP trên chính máy đó.

Extension không cần Chrome Web Store. Cài nội bộ bằng **Load unpacked**.

## Dữ liệu được phép đọc/gửi

Chỉ sau khi operator bấm icon trên tab `https://shopee.vn/...`, helper đọc dữ
liệu sản phẩm đã render:

- `name`;
- `current_price`;
- `original_price` nếu có;
- `image_url`;
- `shop`;
- `observed_url` = URL thật của tab đang được bấm, dùng để ACP xác minh đúng
  canonical product.

`observed_url` chỉ dùng cho identity check và không được coi là metadata tin cậy
để lưu nguyên trạng.

Helper **không đọc/gửi**:

- cookie Shopee;
- session/access token;
- password;
- `credential_token` để tái sử dụng;
- `localStorage` / `sessionStorage` auth data;
- browser profile secret.

Manifest chỉ giữ `activeTab` + `scripting`, quyền host `shopee.vn` và ACP
`127.0.0.1:5000` / `localhost:5000`.

## Cài đặt

1. Mở `chrome://extensions` (Chrome/Chromium/Brave tương thích Manifest V3).
2. Bật **Developer mode**.
3. Bấm **Load unpacked**.
4. Chọn thư mục `tools/chrome_helper/`.
5. Ghim icon **ACP Shopee Helper** nếu muốn.

Sau khi pull code mới, nếu extension đang mở sẵn hãy bấm **Reload** tại
`chrome://extensions` để nạp version mới.

## Dùng hàng ngày

1. Chạy ACP local ở `http://127.0.0.1:5000` hoặc `http://localhost:5000`.
2. Mở `/sanpham` → **Nhập link affiliate** → **Phân tích link**.
3. Nếu metadata chưa đủ, bấm **Mở Shopee & lấy thông tin**.
4. ACP cấp token một lần, TTL 300 giây, gắn với canonical product đang xác nhận.
5. Tab Shopee mở. Chờ trang render bình thường.
6. Đảm bảo đang đứng **đúng sản phẩm** rồi bấm icon extension.
7. Extension gửi `observed_url` của chính tab đó cùng 5 field metadata về
   `/api/helper/shopee-product` trên loopback.
8. ACP canonicalize URL ghép và URL quan sát. Chỉ khi cùng product identity mới
   nhận metadata và consume token.
9. Quay lại ACP: form tự điền. Kiểm tra lại và sửa tay nếu cần.
10. **Tạo bài nháp** vẫn là action riêng; helper không tạo Post và không publish.

## Nếu bấm nhầm tab Shopee

Server từ chối metadata của product khác **mà không consume token**. Extension giữ
pairing để bạn chuyển sang tab sản phẩm đúng và bấm lại trong thời gian TTL.
Không cần tạo token mới chỉ vì click nhầm tab.

## Badge

- `✓` xanh: metadata được ACP nhận.
- `?` vàng: extension không có pairing đang chờ; quay lại ACP và bấm lại
  **Mở Shopee & lấy thông tin**.
- `!` đỏ: tab hiện tại không phải `https://shopee.vn/...`.
- `×` đỏ: metadata/identity/request bị từ chối hoặc ACP không kết nối được.
  Pairing vẫn được giữ để retry nếu token chưa hết hạn.

## Manual fallback

Manual input **luôn dùng được**. Nếu token hết hạn/helper không hoạt động, ACP
hiển thị trạng thái `MANUAL_REQUIRED`; nhập tên, giá và URL ảnh bằng tay rồi tiếp
tục confirmation flow. Không cần tắt helper hay sửa database.

## Vì sao endpoint chỉ dùng localhost

`POST /api/helper/shopee-product` không phải API public. ACP yêu cầu đồng thời:

- raw socket peer là loopback;
- địa chỉ sau ProxyFix cũng là loopback;
- payload JSON <= 16 KiB;
- one-time token còn hạn;
- canonical identity của `observed_url` khớp product ghép;
- metadata qua allowlist/size/type validation.

Do đó gọi helper qua ngrok/public URL bị từ chối là **đúng thiết kế**.

## Pilot trước merge/release

Xem checklist đầy đủ tại `docs/SHOPEE_METADATA_HELPER_RUNBOOK.md`. Pilot helper
không bao gồm approve/publish Threads.

## Gỡ cài đặt

`chrome://extensions` → **ACP Shopee Helper** → **Remove**.
