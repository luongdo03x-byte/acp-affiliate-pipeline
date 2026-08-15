"""Ghép ảnh đăng bài (BRD FR4.1).

Cố ý KHÔNG dùng model sinh ảnh. Model sinh ảnh làm biến dạng logo và chữ trên
bao bì, nên người xem nhận về món khác với ảnh -> đơn hoàn và mất uy tín kênh.
Ghép layer thì deterministic, không tốn phí, và tái tạo được khi cần sửa.
"""
import os
import textwrap

from PIL import Image, ImageDraw, ImageFont

CANVAS = (1080, 1080)
PAD = 64

INK = (22, 40, 58)
PAPER = (245, 246, 244)
SIGNAL = (168, 87, 30)
MUTED = (90, 104, 117)
WHITE = (255, 255, 255)

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
_FONT_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def _font(size: int, bold: bool = False):
    for path in (_FONT_CANDIDATES if bold else _FONT_REGULAR):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _placeholder(product) -> Image.Image:
    """Khi chưa tải được ảnh gốc. Dùng cho demo và cho trường hợp ảnh sàn chết."""
    img = Image.new("RGB", (720, 720), (232, 235, 231))
    d = ImageDraw.Draw(img)
    seed = sum(ord(c) for c in product["external_product_id"])
    for i in range(6):
        r = 90 + (seed * (i + 3)) % 160
        x = 80 + (seed * (i + 1) * 37) % 520
        y = 80 + (seed * (i + 2) * 53) % 520
        tone = 200 - i * 14
        d.ellipse([x - r // 2, y - r // 2, x + r // 2, y + r // 2], fill=(tone, tone + 4, tone - 2))
    d.rectangle([0, 0, 719, 719], outline=(214, 218, 213), width=3)
    return img


def compose(product, out_dir: str, discount_pct: float = 0.0, handle: str = None) -> str:
    """Ghép và trả về đường dẫn file.

    LƯU Ý PRODUCTION: Threads yêu cầu image_url truy cập công khai được. File
    local này phải đẩy lên S3/R2/MinIO rồi lấy URL công khai truyền vào adapter.
    """
    os.makedirs(out_dir, exist_ok=True)
    canvas = Image.new("RGB", CANVAS, PAPER)
    draw = ImageDraw.Draw(canvas)

    # Layer 1 -- ảnh sản phẩm gốc, giữ nguyên tỷ lệ, không bóp méo.
    src = None
    local = product["image_path_local"]
    if local and os.path.exists(local):
        try:
            src = Image.open(local).convert("RGB")
        except Exception:
            src = None
    if src is None:
        src = _placeholder(product)

    box = 720
    src.thumbnail((box, box), Image.LANCZOS)
    frame = Image.new("RGB", (box, box), WHITE)
    frame.paste(src, ((box - src.width) // 2, (box - src.height) // 2))
    canvas.paste(frame, ((CANVAS[0] - box) // 2, PAD))

    # Layer 2 -- badge giảm giá, chỉ hiện khi giảm thật đủ sâu.
    if discount_pct >= 0.10:
        label = f"-{round(discount_pct * 100)}%"
        f = _font(52, bold=True)
        tw = draw.textlength(label, font=f)
        bx, by = PAD + 24, PAD + 24
        draw.rounded_rectangle([bx, by, bx + tw + 44, by + 78], radius=14, fill=SIGNAL)
        draw.text((bx + 22, by + 14), label, font=f, fill=WHITE)

    # Layer 3 -- tên sản phẩm và giá.
    y = PAD + box + 40
    name_font = _font(40, bold=True)
    for line in textwrap.wrap(product["name"], width=42)[:2]:
        draw.text((PAD, y), line, font=name_font, fill=INK)
        y += 52

    price = f"{product['current_price']:,}đ".replace(",", ".")
    pf = _font(58, bold=True)
    draw.text((PAD, y + 12), price, font=pf, fill=SIGNAL)

    if product["original_price"] and product["original_price"] > product["current_price"]:
        old = f"{product['original_price']:,}đ".replace(",", ".")
        of = _font(34)
        ox = PAD + draw.textlength(price, font=pf) + 24
        draw.text((ox, y + 32), old, font=of, fill=MUTED)
        w = draw.textlength(old, font=of)
        draw.line([ox, y + 50, ox + w, y + 50], fill=MUTED, width=3)

    # Layer 4 -- nhận diện kênh. handle=None khi ảnh dùng chung cho nhiều
    # kênh (sub-project D) -- không đóng dấu tên kênh nào để tránh trường hợp
    # đăng lên kênh A nhưng ảnh lại ghi tên kênh B. Đường kẻ phân cách vẫn giữ
    # để layer giá/tên sản phẩm phía trên không bị trống chân trang đột ngột.
    if handle:
        hf = _font(30)
        draw.text((PAD, CANVAS[1] - PAD - 12), handle, font=hf, fill=MUTED)
    draw.line([PAD, CANVAS[1] - PAD - 34, CANVAS[0] - PAD, CANVAS[1] - PAD - 34], fill=(224, 228, 223), width=2)

    path = os.path.join(out_dir, f"{product['id']}.jpg")
    canvas.save(path, "JPEG", quality=88, optimize=True)
    return path
