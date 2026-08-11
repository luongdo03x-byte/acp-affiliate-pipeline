# ACP ACCESSTRADE Product Automation — Design

## Mục tiêu

Đồng bộ catalog TikTok Shop từ ACCESSTRADE vào Product DB của ACP; chọn sản
phẩm trong ACP để tạo nội dung và dừng ở `PENDING_REVIEW`, không yêu cầu
operator sao chép TikTok URL hay affiliate link thủ công.

Không tự publish bài trong phạm vi này. Token ACCESSTRADE chỉ tồn tại phía
backend và không được ghi log.

## Kiến trúc

`product` hiện có được mở rộng thành Product DB trung tâm, thay vì thêm một
catalog song song. Dữ liệu catalog, trạng thái affiliate, score, thời điểm
sync và lịch sử đăng được lưu trên product. Các nguồn trong tương lai được
phân biệt bằng `provider`; TikTok Shop ACCESSTRADE dùng `ACCESSTRADE_TIKTOK`.

Luồng mới:

```text
ACCESSTRADE Product Search V2
  -> AccessTradeClient
  -> ProductService (parse, pagination, upsert, ranking)
  -> product DB
  -> /sanpham (local search/filter/sort)
  -> chọn Product
  -> affiliate link theo post
  -> content pipeline hiện có
  -> PENDING_REVIEW
```

`AccessTradeClient` là ranh giới HTTP duy nhất cho Product Search V2 và Create
Link V2. `ProductService` sở hữu nghiệp vụ sync, ranking, recommendation,
cooldown và trạng thái link; route/template không gọi API trực tiếp. Adapter
TikTok Shop hiện có sẽ chuyển tiếp qua client này, tránh hai cách gọi
ACCESSTRADE khác nhau.

## Attribution và affiliate link

ACP hiện quy kết doanh thu theo `post_id`; điều này được giữ nguyên. Khi tạo
nội dung, pipeline cấp `post_id` trước, tạo link với `sub1=<post_id>` và
`utm_content=<external_product_id>`, rồi chỉ truyền link trả về vào caption.
`post.affiliate_link` là link dùng để xuất bản và attribution.

Product lưu trạng thái/link gần nhất để vận hành và hiển thị, nhưng không được
tái sử dụng một link product-level theo cách làm mất `post_id` attribution.
Nếu Create Link thất bại, pipeline dừng và không bao giờ fallback sang
`detail_link` TikTok. `FAILED` có thể retry; `UNAVAILABLE` loại khỏi auto
recommendation.

## Schema và migration

Migration SQLite idempotent sẽ bổ sung các cột product về provider, shop,
detail/image URL, vùng bán, giá và commission có cấu trúc, sold/inventory,
category JSON, score, affiliate state/error/timestamps, cùng `first_seen_at`,
`last_synced_at`, `last_posted_at`, `post_count`.

Dữ liệu cũ được backfill không phá vỡ post/conversion hiện hữu. Một unique
index `provider + external_product_id` bảo đảm catalog TikTok không trùng; các
unique constraint cũ được giữ để tương thích. Tiền vẫn là VND integer theo
schema hiện hữu (không float); các trường không có từ API là `NULL`, không đổi
thành 0.

## Sync, ranking và vận hành

Manual sync và keyword sync sử dụng Product Search V2 với `page_token`, giới
hạn bởi `ACP_PRODUCT_SYNC_MAX_PAGES` (mặc định 10). Upsert không ghi đè
`first_seen_at`, luôn cập nhật `last_seen_at` và `last_synced_at`, sau đó tính
score 0–100 từ percentile: sales 45%, commission rate 35%, commission amount
20%.

Lệnh CLI độc lập chạy sync để cron/systemd timer gọi định kỳ; không đặt
scheduler trong Flask worker. DB-backed lock/idempotency chặn sync chồng nhau.
Auto-prepare mặc định tắt; khi được bật, chỉ chọn candidate hợp lệ, tạo content
và dừng tại `PENDING_REVIEW`.

Candidate tự động phải có hàng, `detail_link`, external ID, không
`UNAVAILABLE`, và chưa đăng trong `ACP_PRODUCT_REPOST_COOLDOWN_DAYS`. Manual
selection được phép override cooldown.

## UI và lỗi

`/sanpham` dùng catalog cục bộ cho search/filter/sort và hiển thị trạng thái
sync, thống kê, badge thực tế, product detail, xem TikTok, copy affiliate, tạo
link và tạo nội dung. Nút sync/search từ ACCESSTRADE chỉ gọi backend, kèm CSRF
theo pattern Flask hiện tại.

HTTP 401, 429, timeout, 5xx, JSON lỗi và payload `status: false` được map sang
thông báo tiếng Việt an toàn. Client retry tối đa hai lần với 1s/2s cho timeout,
429 và 5xx được phép; không retry 400/401/403/404. Log gồm loại request, status,
duration, page và product ID, không gồm Authorization/token/traceback UI.

## Kiểm thử và tài liệu

Tests mới phủ client và parser, pagination, migration/upsert, commission raw
1000 -> 10.00% và 3587 -> 35.87%, ranking/cooldown, link success/failure/retry,
pipeline affiliate-to-`PENDING_REVIEW`, routes và UI. Regression hiện có phải
chạy cùng `./manage.sh test` ở mock mode.

README và `.env.example` sẽ mô tả cấu hình, manual/automatic sync, tìm/chọn
product, affiliate generation, và xử lý 401/429/product unavailable. Các env
cần có: `ACCESSTRADE_API_BASE_URL`, `ACCESSTRADE_API_TOKEN`, sync settings,
cooldown/recommendation settings và auto-prepare settings.

## Ngoài phạm vi

Không quản lý seller/order/shipping, không expose token, không bulk-create link
cho toàn catalog, không bật live adapter hay publish bài thật.
