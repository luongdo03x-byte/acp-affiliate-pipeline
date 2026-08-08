# ACP 2.0 — Dark Premium UI Design

**Ngày:** 2026-08-07
**Trạng thái:** Thiết kế đã được người dùng chọn từ preview phương án C; chờ review bản spec viết trước khi implementation.

## 1. Mục tiêu

Nâng cấp giao diện ACP 2.0 từ dashboard sáng hiện tại sang **Dark Premium** đồng bộ, dễ đọc và hiện đại hơn, đồng thời tích hợp tự nhiên với luồng mới **Nhập link affiliate Shopee**.

Thay đổi này là **UI/UX + presentation layer**. Không được thay đổi nghiệp vụ publish, attribution, database live hoặc hành vi của các flow TikTok/ACCESSTRADE ngoài phần cần thiết cho feature Shopee đã duyệt.

## 2. Phạm vi giao diện

Áp dụng cho toàn bộ dashboard server-rendered hiện tại:

- `/` — Doanh thu
- `/sanpham` — Sản phẩm + nhập link affiliate Shopee
- `/duyet` — Chờ duyệt
- `/kenh` — Kênh
- `/vanhanh` — Vận hành
- `/chamdiem` — Chấm điểm
- `/dangnhap` — Đăng nhập

Không thêm SPA/framework frontend mới. Tiếp tục dùng Flask + Jinja2 + HTML/CSS hiện tại.

## 3. Hướng thị giác đã chốt

Phong cách: **Dark Premium / Developer Dashboard**.

### Bảng màu

```text
Background         #07111F
Sidebar            #081522
Surface            #0D1B2A
Surface elevated   #112236
Surface hover      #162A40
Border             rgba(148,163,184,.16)
Text primary       #F8FAFC
Text secondary     #94A3B8
Accent             #8B5CF6
Accent strong      #7C3AED
Success            #22C55E
Warning            #F59E0B
Danger             #EF4444
Info                #38BDF8
```

Accent tím chỉ dùng cho:

- navigation active;
- primary CTA;
- tab active;
- focus state;
- chart/progress highlight;
- selected form controls.

Không phủ tím toàn màn hình.

### Typography

- Body/UI: `Inter`, system-ui fallback.
- Numeric/technical values: `IBM Plex Mono` hoặc monospace fallback.
- Heading không dùng serif/display font quá trang trí; ưu tiên sạch, rõ, compact.

## 4. Layout tổng thể

Desktop:

```text
┌───────────────┬──────────────────────────────────────────────┐
│ ACP           │ Header / page title / system state          │
│               ├──────────────────────────────────────────────┤
│ Tổng quan     │                                              │
│ Sản phẩm      │ Main content                                 │
│ Kênh          │                                              │
│ Chờ duyệt     │ cards / tables / forms                       │
│ Vận hành      │                                              │
│ Chấm điểm     │                                              │
│               │                                              │
│ status        │                                              │
└───────────────┴──────────────────────────────────────────────┘
```

- Sidebar rộng khoảng `220–236px`, sticky.
- Main content có max-width rộng hơn hiện tại, khoảng `1360–1440px` để tận dụng desktop.
- Header trang dùng flex: title/description bên trái, action/status bên phải.
- Card radius `12–14px`.
- Khoảng cách theo scale 4/8/12/16/24/32.
- Shadow rất nhẹ; phân lớp chủ yếu bằng surface + border.

Responsive:

- `< 960px`: sidebar chuyển thành top navigation hoặc compact stacked nav.
- `< 720px`: KPI/grid/table chuyển sang một cột hoặc horizontal-scroll hợp lý.
- Form Shopee confirmation chuyển từ image + form hai cột thành một cột.

## 5. Navigation

Giữ nguyên route và nghiệp vụ.

Navigation labels:

```text
Tổng quan     -> /
Sản phẩm      -> /sanpham
Kênh          -> /kenh
Chờ duyệt     -> /duyet
Vận hành      -> /vanhanh
Chấm điểm     -> /chamdiem
```

Yêu cầu:

- active state: violet-tinted background + accent edge/glow nhẹ;
- pending review badge dễ thấy nhưng không quá chói;
- footer sidebar hiển thị adapter `LIVE` / `MOCK` bằng status chip;
- logout là secondary/ghost action;
- không thay route/path để tránh regression.

## 6. Design system dùng chung

CSS hiện đang tập trung trong `base.html` và các template có nhiều inline-style. Nâng cấp sẽ tạo một stylesheet dùng chung:

```text
web/static/acp.css
```

`base.html` chỉ giữ layout/template structure và load stylesheet.

Các utility/component class chính:

```text
.app-shell
.sidebar
.nav-item / .nav-item--active
.page-header
.card / .card--elevated
.kpi-grid / .kpi-card
.data-table
.status-badge
.tabs / .tab
.form-grid / .field
.alert / .alert--error / .alert--success
.btn / .btn--primary / .btn--ghost / .btn--danger
.product-preview
.review-card
.empty-state
```

Không cố xây một component framework tổng quát. Chỉ extract các pattern thực sự lặp lại trong ACP.

## 7. Trang `/sanpham`

Đây là trang ưu tiên cao nhất vì vừa redesign vừa thêm feature mới.

### 7.1 Tabs

```text
[ Tìm sản phẩm ]   [ Nhập link affiliate ]
```

- Tab active dùng accent violet.
- Tab inactive dùng surface tối + muted text.
- `Tìm sản phẩm` giữ flow hiện tại.
- `Nhập link affiliate` là flow Shopee direct mới.

### 7.2 Search mode

Search form nằm trong elevated card:

```text
[Từ khóa................................] [Nguồn ▼] [Tìm]
```

Kết quả desktop hiển thị data table premium:

- tên sản phẩm nổi bật;
- external ID bằng mono/muted;
- shop;
- giá;
- commission;
- sold count;
- CTA `Tạo bài`.

Row hover rõ nhưng nhẹ.

### 7.3 Shopee affiliate mode

Bước 1:

```text
┌──────────────────────────────────────────────────────────────┐
│ Nhập link affiliate Shopee                                  │
│ Link đã có sẵn, ACP không tạo link qua ACCESSTRADE.        │
│                                                              │
│ [ https://s.shopee.vn/............................ ] [Phân tích]
└──────────────────────────────────────────────────────────────┘
```

Bước 2 — confirmation luôn xuất hiện sau resolve thành công:

```text
┌──────────────┐  Tên         [..............................]
│              │  Giá         [..............................]
│   PRODUCT    │  Giá gốc     [..............................]
│    IMAGE     │  Shop        [..............................]
│              │  URL ảnh     [..............................]
└──────────────┘  Kênh        [ Threads channel ▼ ]

                  Link sản phẩm   readonly
                  Affiliate link  readonly

                            [ Tạo bài nháp ]
```

- Image preview 280–340px trên desktop.
- Form field labels rõ và compact.
- URL dài dùng truncated presentation nhưng full value vẫn có thể copy/select.
- Affiliate link hiển thị distinct badge `Shopee Direct`.
- Metadata thiếu được đánh dấu warning, không phải error fatal.
- CTA duy nhất ở bước này: `Tạo bài nháp`.
- Tuyệt đối không có `Đăng ngay`.

## 8. Trang Tổng quan `/`

Giữ dữ liệu hiện tại nhưng thay presentation:

- Funnel hiện tại đổi thành KPI grid 4–5 cards.
- Các chỉ số chính dùng số lớn, label nhỏ, mono cho tiền/số.
- Commission là KPI nhấn accent hoặc success.
- Bảng theo danh mục/template/kênh/variant nằm trong card riêng.
- Progress bar dùng violet gradient nhẹ, không dùng màu cam cũ.
- Empty state có icon/visual nhẹ bằng CSS/SVG inline, không cần asset ngoài.

Không thêm chart library ở P0. Dữ liệu hiện tại không đủ lý do để thêm dependency chỉ để vẽ chart.

## 9. Trang `/duyet`

Mỗi bài chờ duyệt thành một `review-card` rõ hierarchy:

```text
[Ảnh 220x220]   Product name                  Score
                badges: category/template/channel/commission

                [ caption textarea                        ]

                420 / 500                 [Bỏ qua] [Duyệt & lên lịch]
```

- Duyệt là primary violet.
- Bỏ qua là ghost/danger secondary.
- Validation failure dùng red surface, không chỉ red border.
- Affiliate/product link nếu hiện trong dữ liệu review phải dễ nhận biết.
- Không thay approval state machine.

## 10. Trang `/kenh`

- Mỗi channel là elevated card.
- Status/pool/published/cap thành status chips.
- Niche checkbox đổi thành selectable tile; checked state có violet border/background.
- `Lưu` là primary button cuối card.
- Không đổi semantics: không chọn niche vẫn có nghĩa nhận mọi danh mục.

## 11. Trang `/vanhanh`

- Queue state thành 4 KPI cards: READY/RUNNING/DONE/FAILED.
- FAILED dùng danger accent.
- `Chạy hàng đợi ngay` nằm ở page header action.
- Tables sử dụng component table chung.
- Error message giữ nguyên nội dung nhưng presentation rõ hơn.
- Không đổi queue behavior.

## 12. Trang `/chamdiem`

- Hai card lớn: `Trọng số` và `Lọc cứng`.
- Range input accent violet.
- Numeric value hiển thị mono chip bên phải.
- Blocked categories dùng danger badge.
- Preview/rejected tables cùng table style mới.
- Không đổi scoring algorithm.

## 13. Login

Login là một centered premium panel:

```text
ACP
Affiliate Content Pipeline

[Mật khẩu........................]
[Đăng nhập]
```

- background có radial gradient rất nhẹ bằng CSS;
- không ảnh stock;
- không hiệu ứng nặng;
- password field/focus accessible;
- error state rõ.

## 14. Accessibility

- Contrast tối thiểu phù hợp dark UI; text chính không dùng gray quá tối.
- `:focus-visible` dùng accent ring rõ.
- Buttons/tabs có hover + focus + active state.
- Form labels dùng `<label>` thật.
- Tables vẫn giữ semantic `<table>`.
- Không dùng màu làm tín hiệu duy nhất: badge phải có text.
- Motion tối thiểu; tôn trọng `prefers-reduced-motion` nếu có transition.

## 15. Technical boundaries

Không được:

- thêm React/Vue/Tailwind/build pipeline;
- thêm chart library chỉ để trang đẹp;
- thay route;
- đổi schema vì redesign;
- đụng `.env.local`, DB live, `var/`;
- bật adapter live trong test;
- auto publish Threads;
- sửa business logic không liên quan.

Được phép:

- thêm `web/static/acp.css`;
- refactor inline style sang class;
- thêm small Jinja macros/partials nếu thực sự giảm lặp;
- dùng inline SVG nhỏ cho navigation/icon nếu không cần dependency ngoài;
- thay wording UI nhẹ để rõ hơn, không đổi semantics.

## 16. Testing

### Regression

Phải giữ:

```text
core pipeline suite: pass
pilot suite: pass
manager suite: pass
```

Không hardcode count cuối vì Shopee tests sẽ làm số lượng tăng.

### Web UI tests tối thiểu

- `/sanpham` vẫn render existing search mode;
- affiliate tab render;
- login/CSRF behavior không đổi;
- `/duyet`, `/kenh`, `/vanhanh`, `/chamdiem` render 200 sau login;
- template không lỗi khi dataset rỗng;
- navigation active class đúng page;
- không xuất hiện action `Đăng ngay` trên `/sanpham`;
- existing web security tests tiếp tục pass.

### Manual visual smoke test

Ở mock mode:

1. `/`
2. `/sanpham?mode=search`
3. `/sanpham?mode=affiliate`
4. resolve fake/mocked metadata trong test/dev
5. `/duyet`
6. `/kenh`
7. `/vanhanh`
8. `/chamdiem`
9. responsive khoảng 390px và 1024px

Không cần publish Threads để nghiệm thu UI.

## 17. Acceptance criteria

```text
✓ Giao diện toàn dashboard dùng Dark Premium nhất quán
✓ Sidebar + page hierarchy rõ hơn bản cũ
✓ Card/table/form/status có cùng design language
✓ /sanpham có tabs Search / Affiliate
✓ Confirmation Shopee khớp flow đã duyệt
✓ Không có Đăng ngay trên trang sản phẩm
✓ Mobile/tablet không vỡ layout
✓ Keyboard focus rõ
✓ Không thêm frontend framework
✓ Không đổi business logic ngoài Shopee feature đã duyệt
✓ Existing security/CSRF/auth vẫn hoạt động
✓ Existing core/pilot/manager tests pass
✓ Không publish Threads trong automated verification
```

## 18. Quan hệ với Shopee Affiliate Import spec

Spec này **bổ sung presentation layer** cho:

```text
docs/superpowers/specs/2026-08-07-shopee-affiliate-link-import-design.md
```

Nếu có xung đột:

- business/data/security behavior: Shopee Affiliate Import spec chi phối;
- visual/layout/component styling: Dark Premium UI spec chi phối.

Hai spec cùng yêu cầu `/sanpham` dừng ở `PENDING_REVIEW` và không có hành vi publish trong bước import/create.
