"""Prompt caption TÙY BIẾN của operator -- một câu prompt chung áp dụng cho
MỌI luồng sinh caption (v1 catalog + Shopee reviewer) khi ChatGPT được bật.

Nguồn ưu tiên:
  1. Bảng system_setting, khoá "caption_prompt_template" (sửa tại /chamdiem).
  2. File trỏ bằng env ACP_CAPTION_PROMPT_FILE (tiện đổi nhanh không vào web).
  3. Không có gì -> mỗi luồng dùng prompt mặc định riêng của nó (giữ nguyên
     toàn bộ rào chắn an toàn đi kèm).

Token thay thế trong template (viết hoa, {{ }}):
  {{DRAFT}}     bản nháp đầy đủ dữ liệu thật (hook + thân + CTA + LINK)
  {{TEN}}       tên sản phẩm (đã cắt hậu tố shop)
  {{GIA}}       giá hiện tại định dạng "123.000đ"
  {{GIAM}}      mức giảm thật theo giá gốc, số nguyên % (0 nếu không có)
  {{DANH_MUC}}  mã danh mục sản phẩm
  {{MOTA}}      mô tả ngắn từ nguồn (nếu có)
  {{LINK}}      đường link affiliate bắt buộc phải giữ trong output

An toàn KHÔNG phụ thuộc prompt: output vẫn bị chặn bởi validate() /
_safe_rewrite -- thiếu link, bịa trải nghiệm, cụm cấm, số liệu ngoài dữ liệu
đều khiến hệ thống rơi về bản nháp deterministic.
"""
import os

from .system_settings import get_system_setting

CAPTION_PROMPT_KEY = "caption_prompt_template"


def get_custom_template(conn=None):
    """Template đang đặt, hoặc None để các luồng dùng prompt mặc định.

    Chịu được CSDL chưa có bảng system_setting (DB mới/legacy chưa migrate):
    coi như chưa đặt -- không bao giờ làm vỡ luồng sinh caption vì lỗi schema.
    """
    import sqlite3

    def _read(c):
        try:
            row = c.execute("SELECT value FROM system_setting WHERE key=?",
                            (CAPTION_PROMPT_KEY,)).fetchone()
        except sqlite3.OperationalError:
            return None
        return _clean(row["value"]) if row else None

    if conn is not None:
        return _read(conn)
    from .db import connect
    conn = connect()
    try:
        return _read(conn)
    finally:
        conn.close()


def _clean(value):
    value = str(value or "").strip()
    return value or None


def get_custom_template_with_file(conn=None):
    """Ưu tiên DB; nếu không có thì đọc file ACP_CAPTION_PROMPT_FILE."""
    template = get_custom_template(conn)
    if template:
        return template
    path = os.environ.get("ACP_CAPTION_PROMPT_FILE", "").strip()
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return _clean(fh.read())
    except OSError:
        return None


def clear_custom_template(conn, actor: str = "operator"):
    conn.execute("DELETE FROM system_setting WHERE key=?", (CAPTION_PROMPT_KEY,))
    from .db import audit
    audit(conn, "system_setting", CAPTION_PROMPT_KEY, "cleared", actor=actor)


def render(template: str, product, draft: str, affiliate_link: str = "") -> str:
    """Thay token bằng dữ liệu thật; phần chữ khác giữ NGUYÊN văn operator."""
    name = str(product.get("name") or "")
    try:
        from .content import _strip_shop_suffix
        name = _strip_shop_suffix(name, product.get("shop") or product.get("shop_name"))
    except Exception:
        pass
    price = product.get("current_price") or 0
    orig = product.get("original_price") or 0
    discount = max(0, round((orig - price) / orig * 100)) if orig > price > 0 else 0
    link = str(affiliate_link or "").strip()
    if not link:
        import re
        match = re.search(r"https?://\S+", draft or "")
        link = match.group(0) if match else ""
    description = str(product.get("description") or "").strip()[:200]

    out = str(template)
    for token, value in (
        ("{{DRAFT}}", draft or ""),
        ("{{TEN}}", name),
        ("{{GIA}}", f"{price:,}đ".replace(",", ".")),
        ("{{GIAM}}", str(discount)),
        ("{{DANH_MUC}}", str(product.get("category_code") or "khac")),
        ("{{MOTA}}", description),
        ("{{LINK}}", link),
    ):
        out = out.replace(token, value)
    return out
