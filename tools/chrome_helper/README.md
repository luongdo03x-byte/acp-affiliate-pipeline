# ACP Shopee Helper (Chrome extension nội bộ)

Giải quyết vấn đề: Shopee chặn request server-side khi ACP tự lấy metadata sản
phẩm (CAPTCHA/HTTP 403 — xem `adapters/shopee_affiliate.py`). Extension này để
**bạn tự bấm** đọc lại thông tin từ trang Shopee đã hiển thị bình thường trong
Chrome của bạn rồi gửi về ACP đang chạy trên máy bạn — không bypass CAPTCHA,
không tự động hoá thao tác trên Shopee.

Extension này **không đăng lên Chrome Web Store** — chỉ dùng nội bộ, cài bằng
chế độ "Load unpacked".

## Extension đọc gì / gửi gì

Đọc (chỉ khi bạn chủ động bấm icon, đúng lúc đó, đúng tab đó):
- JSON-LD / thẻ `<meta>` OpenGraph đã render sẵn trên trang sản phẩm Shopee —
  tên, giá hiện tại, giá gốc, ảnh, shop.

**Không đọc:** cookie, localStorage, session token, thông tin đăng nhập
Shopee của bạn.

Gửi (chỉ về `127.0.0.1:5000` / `localhost:5000` — máy bạn, không ra Internet):
- `name`, `current_price`, `original_price`, `image_url`, `shop` — đúng 5
  trường, kèm token dùng-một-lần do trang ACP phát ra khi bạn bấm nút
  "Mở Shopee & lấy thông tin".

## Cài đặt

1. Mở Chrome, vào `chrome://extensions`.
2. Bật **Developer mode** (góc trên phải).
3. Bấm **Load unpacked**, chọn thư mục `tools/chrome_helper/` trong repo này.
4. Ghim icon "ACP Shopee Helper" lên thanh công cụ cho dễ bấm (tuỳ chọn).

## Dùng

1. Mở ACP → `/sanpham` → tab **Nhập link affiliate** → dán link, bấm
   **Phân tích link**.
2. Nếu ACP không tự lấy được đủ thông tin (Shopee chặn), màn hình xác nhận sẽ
   hiện nút **"Mở Shopee & lấy thông tin"**. Bấm nút đó.
3. Một tab Shopee mới mở ra. Chờ trang tải xong bình thường (không cần đăng
   nhập Shopee, không cần làm gì đặc biệt).
4. Bấm icon **ACP Shopee Helper** trên thanh công cụ Chrome.
   - Icon hiện `✓` (nền xanh): đã gửi thành công, quay lại tab ACP — form đã
     tự điền.
   - Icon hiện `?` (nền vàng): chưa có phiên đang chờ — quay lại tab ACP, bấm
     lại nút "Mở Shopee & lấy thông tin" trước.
   - Icon hiện `!` hoặc `×` (nền đỏ): không phải trang sản phẩm Shopee, hoặc
     gửi thất bại — thử lại hoặc nhập tay.
5. Trang ACP tự động nhận dữ liệu trong vài giây (đang poll), tự điền form.
   Kiểm tra lại rồi bấm **Tạo bài nháp** như bình thường — bài vẫn dừng ở
   `PENDING_REVIEW`, phải duyệt tay ở `/duyet` mới lên lịch đăng.

## Vì sao token hết hạn / bị từ chối

Token chỉ dùng được **một lần**, gắn với **đúng** link sản phẩm bạn vừa phân
tích, và hết hạn sau **5 phút**. Nếu quá thời gian hoặc dùng nhầm link khác,
bấm lại "Mở Shopee & lấy thông tin" ở ACP để lấy token mới.

## Gỡ cài đặt

`chrome://extensions` → tìm "ACP Shopee Helper" → **Remove**.
