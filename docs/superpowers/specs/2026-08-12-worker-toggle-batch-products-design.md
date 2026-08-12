# Worker tự đăng và thao tác sản phẩm hàng loạt

## Mục tiêu

Cho phép operator bật/tắt việc tự đăng bài trên toàn hệ thống, đồng thời tạo
link affiliate hoặc tạo bài nháp cho nhiều sản phẩm catalog trong một thao tác.
Tính năng không bỏ qua bước duyệt nội dung và không làm thay đổi luồng
ACCESSTRADE hiện có.

## Phạm vi

### Công tắc worker toàn hệ thống

- Trạng thái mặc định là tắt để bảo toàn hành vi an toàn hiện tại.
- Trạng thái được lưu bền trong SQLite, không phụ thuộc biến môi trường và không
  mất khi ACP restart.
- Trang Vận hành hiển thị trạng thái hiện tại, thời điểm cập nhật và nút bật/tắt.
- Worker kiểm tra công tắc trước khi xử lý job `PUBLISH_POST`.
- Khi tắt, các job đăng bài đã đến hạn vẫn ở trạng thái `READY` hoặc được trả về
  `READY` nếu worker đã nhận job; không đánh dấu bài là thất bại.
- Các job khác như đồng bộ catalog, insights hoặc tác vụ không phải publish vẫn
  có thể chạy.
- Khi bật, worker định kỳ xử lý job `PUBLISH_POST` có `run_after <= now()`.

### Tạo hàng loạt từ catalog

- Mỗi card sản phẩm có checkbox; có nút chọn/bỏ chọn tất cả sản phẩm đang hiển
  thị trong trang hiện tại.
- Server nhận danh sách ID sản phẩm đã chọn, kiểm tra lại toàn bộ điều kiện ở
  phía server, không tin vào dữ liệu ẩn từ trình duyệt.
- Giới hạn mặc định mỗi lần là 10 sản phẩm; giá trị vượt giới hạn bị từ chối.
- Sản phẩm phải thuộc provider `ACCESSTRADE_TIKTOK`, còn hàng, có detail link
  và có ảnh nguồn hoặc ảnh local hợp lệ.
- Sản phẩm đã có bài bán hàng ở trạng thái `DRAFT`, `PENDING_REVIEW`,
  `APPROVED`, hoặc `SCHEDULED` bị bỏ qua.

#### Tạo link hàng loạt

- Tạo một product-only affiliate link cho mỗi sản phẩm đủ điều kiện.
- Attribution dùng marker `sub_1=product:<external_product_id>`.
- Ghi URL đầy đủ, URL rút gọn, trạng thái và lỗi an toàn vào bảng `product`.
- Không tạo bài, không tạo job publish.

#### Tạo bài hàng loạt

- Mỗi sản phẩm nhận một post-specific affiliate link mới với `sub_1=<post_id>`.
- Tải và xác thực ảnh trước khi gọi API tạo link; lỗi ảnh dừng riêng sản phẩm đó.
- Tạo caption và ảnh composited, sau đó tạo bài ở `PENDING_REVIEW` hoặc `DRAFT`
  theo kết quả validation.
- Không tự duyệt, không tự lên lịch và không tự đăng.
- Một sản phẩm lỗi không làm rollback các sản phẩm đã thành công trước đó.
- Phản hồi hiển thị tổng số thành công, bỏ qua và thất bại; không hiển thị token,
  response body hoặc URL nhạy cảm của provider.

## Luồng dữ liệu

```text
UI chọn sản phẩm
  -> POST có CSRF + danh sách ID
  -> Server giới hạn/kiểm tra điều kiện
  -> ProductService xử lý tuần tự, có trạng thái từng sản phẩm
  -> UI nhận summary
```

Worker:

```text
timer mỗi phút -> run.py work
  -> kiểm tra system worker_enabled
  -> claim job đến hạn
  -> bỏ qua PUBLISH_POST nếu tắt
  -> publish nếu bật
```

## Lưu trữ và API nội bộ

- Thêm một bản ghi cấu hình hệ thống, ví dụ khóa `publish_worker_enabled`, vào
  database hiện hữu; migration phải idempotent.
- Thêm endpoint POST bật/tắt có CSRF và audit log actor `operator`.
- Thêm endpoint POST cho hai thao tác batch, dùng cùng service/pipeline hiện có
  để giữ attribution và điều kiện chống đăng trùng.
- Timer worker chạy ngoài Flask, dùng active release và `.env.local`, lặp mỗi
  phút. Timer không tự bật công tắc; operator phải bật trong UI.

## Lỗi, giới hạn và an toàn

- Batch rỗng, ID không tồn tại, vượt giới hạn hoặc sai provider trả lỗi rõ ràng.
- Lỗi ACCESSTRADE được chuyển thành thông báo an toàn; chi tiết chỉ ghi log
  dạng loại lỗi và ID sản phẩm.
- Không gọi API tạo link cho sản phẩm đã bị loại bởi kiểm tra server.
- Không dùng product-only link cho bài; bài luôn tạo link mới theo post ID.
- Worker tắt là trạng thái fail-safe: không có publish ngoài ý muốn.

## Kiểm thử và nghiệm thu

- Migration cấu hình idempotent, mặc định worker tắt.
- Toggle bật/tắt yêu cầu CSRF, ghi audit và ảnh hưởng đúng đến job publish.
- Worker tắt giữ job publish chưa xử lý; worker bật xử lý job đến hạn.
- Batch link tạo đúng marker product và cô lập lỗi từng sản phẩm.
- Batch post tạo link post-specific, tải ảnh thật, tạo `PENDING_REVIEW`, không
  tạo job publish.
- Kiểm tra giới hạn, sản phẩm trùng, sản phẩm đã có bài active, hết hàng và
  thiếu ảnh/link.
- Chạy toàn bộ nhóm test product automation, pipeline, web và kiểm tra cú pháp.
