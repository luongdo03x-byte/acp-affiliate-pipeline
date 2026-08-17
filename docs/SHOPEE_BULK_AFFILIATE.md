# Shopee Bulk Affiliate — Phase 1

Phase 1 bổ sung một workspace để chuyển tối đa 500 URL sản phẩm Shopee thành Affiliate URL trong một lần, dùng đúng Affiliate ID của tài khoản Shopee Affiliate.

## Cấu hình

Thêm vào `~/Downloads/ACP/shared/.env.local`:

```env
SHOPEE_AFFILIATE_ID=14354840000
```

Không commit giá trị thật vào Git. ACP chỉ đọc cấu hình này ở backend. Affiliate URL đầu ra đương nhiên chứa `affiliate_id` theo định dạng link Shopee công bố.

## Sử dụng

1. Mở `/sanpham/shopee-bulk`.
2. Dán mỗi dòng một URL sản phẩm Shopee, tối đa 500 URL.
3. Điền nhãn tracking nếu cần, ví dụ `threads`, `facebook`, `campaign01`.
4. Bấm **Tạo affiliate link**.

ACP chuẩn hoá URL về dạng `https://shopee.vn/product/<shop>/<item>`, tạo `sub_id` 5 phần dạng `acp-bulk-web-<item>-<tag>`, rồi tạo URL:

```text
https://s.shopee.vn/an_redir?origin_link=...&affiliate_id=...&sub_id=...
```

Nếu Product DB đã có một sản phẩm Shopee trùng `external_product_id`, ACP cập nhật các field affiliate hiện có của row đó (`affiliate_url`, `affiliate_link_status`, `affiliate_link_created_at`). URL chưa có Product DB vẫn được trả về giao diện để copy, nhưng Phase 1 không tạo Product giả chỉ từ URL.

## Giới hạn chủ đích

- Không đăng nhập Shopee.
- Không browser/headless automation.
- Không bypass CAPTCHA/anti-bot.
- Không gọi private Shopee API.
- Không tự publish nội dung.
- Không nhận short link `s.shopee.vn/...` làm đầu vào vì cần network redirect để xác định product ID.
