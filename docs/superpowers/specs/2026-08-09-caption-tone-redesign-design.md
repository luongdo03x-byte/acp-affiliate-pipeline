# Giọng văn caption "phát hiện & chia sẻ" — thiết kế

## Bối cảnh

Caption sinh ra hiện đọc như báo cáo số liệu ("Trang bán ghi nhận...", "là mẫu
có số liệu đáng chú ý", "Đang bán 100.250đ." đứng riêng một đoạn) thay vì
giọng một người bình thường thấy món hay và chia sẻ lại. Người dùng muốn giọng
văn tự nhiên, đời thường hơn.

Ràng buộc không thay đổi: `core/content.py` cấm bịa trải nghiệm sử dụng
(`FABRICATED_EXPERIENCE`) vì ACP lấy dữ liệu từ Shopee feed, không có ai thật
sự dùng sản phẩm. Đây là rào chắn pháp lý/đạo đức, giữ nguyên. Persona mục
tiêu là **người phát hiện & chia sẻ hộ** ("thấy hay nên để lại đây"), không
phải người đã trải nghiệm sản phẩm.

## Phạm vi

Trong phạm vi:
- 9 hàm render hook trong `core/playbook.py`.
- 4 template thân bài (`TEMPLATES`) trong `core/content.py`.
- Hàm `_social_proof()` (content.py) và `_social_bits()` (playbook.py).

Ngoài phạm vi (không đổi):
- `DISCLOSURE_DEFAULT`, `CTA_LIBRARY`.
- `BANNED_SUPERLATIVES`, `FABRICATED_EXPERIENCE`, `EFFICACY_CLAIMS` và mọi
  logic `validate()`.
- `core/valuepost.py` (bài giá trị / phương pháp 3 bài) — có thể làm ở lượt
  sau nếu cần, không phải một phần của yêu cầu này.
- Kiến trúc hook × template (giữ nguyên 2 trục biến thể, đo qua sub3).

## Root cause cụ thể của case trong ảnh

Template `comparison` có `\n\n` ngay trước câu giá:

```text
"Trong tầm giá {price_band} thì {name} là mẫu có số liệu đáng chú ý.\n\nĐang bán {price}. {social}"
```

Khi `social` rỗng (chưa có lượt mua/đánh giá), câu giá đứng trơ trọi thành
một đoạn riêng — chính là "Đang bán 100.250đ." mà người dùng thấy máy móc.
Fix: gộp câu giá vào cùng câu trước, không tách đoạn riêng cho một câu ngắn.

## Copy mới

### Hook (`core/playbook.py`)

| Mã | Cũ | Mới |
|---|---|---|
| H1_GIAGIAM | "Đang bán {price}, thấp hơn khoảng {pct}% so với mặt bằng gần đây." | "Giá đang treo {price}, mềm hơn tầm {pct}% so với bình thường — thấy hời nên để lại đây." |
| H2_SOSANH | "So với mấy món cùng tầm giá thì đây là cái có số liệu đáng chú ý hơn." | "So mấy món cùng tầm giá thì cái này có vẻ đáng tiền hơn hẳn." |
| H3_KHANHIEM | "Xem qua thì thấy số lượng không nhiều, nhóm nào cần thì cân nhắc sớm." | "Nhìn số lượng còn lại thì chắc không trụ lâu, ai cần thì cân nhắc sớm nhé." |
| H4_CAUHOI | "Có ai đang tìm món kiểu này không?" | giữ nguyên |
| H5_XAHOI | "Trang bán ghi nhận {bits} -- để ý thấy nên chia sẻ lại." / fallback "Lướt thấy món này, số liệu trên trang bán khá ổn nên chia sẻ lại." | "Thấy {bits}, để lại đây cho ai đang cần." / fallback "Lướt thấy món này trông ổn, để lại đây cho ai quan tâm." |
| H6_HANGMOI | "Mới để ý thấy món này trong danh mục, thông tin cơ bản như sau." | "Mới thấy món này, để lại thông tin cơ bản cho ai đang cần." |
| H7_TIETKIEM | "Tính ra mua đúng lúc này thì đỡ được một khoản, để lại thông tin cho ai cần." | "Mua đúng lúc này thì đỡ được một khoản, để lại thông tin cho ai cần." |
| H8_CANHBAO | "Giá đang thấp hơn mức thường thấy, sợ lên lại nên chia sẻ luôn." | giữ nguyên |
| H9_TRUCTIEP | "{name} -- {price}." | "{name} — đang có giá {price}." |

`_social_bits()`: đổi "đã bán {N} lượt" → "{N} người mua rồi"; "{r}/5 từ {N}
đánh giá" → "đánh giá {r}/5". Nối bằng ", ".

### Thân bài (`core/content.py`, `TEMPLATES`)

| Mã | Mới |
|---|---|
| price_drop | "{name}\n\nGiá hiện {price}, mềm hơn khoảng {discount}% so với 30 ngày qua. {social}" |
| spec_highlight | "{name}\n\nGiá {price}. {social} Bên bán mô tả: {highlight}." |
| deal_roundup | "Lướt nhóm {category} hôm nay thấy món này giá khá hời:\n\n{name} — {price}. {social}" |
| comparison | "Trong tầm giá {price_band}, {name} là món khá ổn — giá đang {price}. {social}" |

`_social_proof()`: trả về `"Cũng {bits}."` với bits dùng phrasing mới ("{N}
người mua rồi", "đánh giá {r}/5"), rỗng thì trả chuỗi rỗng như hiện tại.

Ghi chú giữ nguyên có chủ đích: `spec_highlight` vẫn gắn nhãn "Bên bán mô
tả:" trước phần trích mô tả sản phẩm — đây là điểm bắt buộc giữ để không đọc
như một nhận định cá nhân (vi phạm `FABRICATED_EXPERIENCE`); chỉ đổi câu chữ
từ "Thông tin từ trang bán:" cho đỡ hành chính, không bỏ phần gán nguồn.

## Kiểm tra không hồi quy

Test hiện có (`tests/test_playbook_hooks_and_cta`, `test_content_post_type`,
`tests/test_pipeline.py::test_content_guards`) kiểm cấu trúc (có hook, có
CTA, có link, có disclosure, độ dài ≤500) chứ không so khớp chuỗi chữ cụ thể
— an toàn để đổi copy. Cần soát copy mới không chứa cụm nằm trong
`BANNED_SUPERLATIVES` / `FABRICATED_EXPERIENCE` / `EFFICACY_CLAIMS`.

## Xác minh

1. `./manage.sh test` — phải vẫn `TEST_OK`.
2. Tạo 1 bài thật từ link Shopee affiliate đã dùng trước đó, xem lại ở
   `/duyet` — đọc caption bằng mắt, xác nhận không còn câu giá đứng riêng một
   đoạn, không còn "trang bán ghi nhận".
3. Không publish thật — dừng ở `PENDING_REVIEW`, người vận hành tự duyệt.
