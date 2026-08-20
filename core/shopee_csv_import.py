"""Official Shopee Affiliate bulk-link CSV parsing and normalization.

This module treats the CSV as text input only. Parsing performs no network
requests and no database mutation. The already-created Shopee affiliate short
URL is preserved exactly as supplied by the operator's CSV.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from io import StringIO
from urllib.parse import urlsplit

from .shopee_products import ShopeeProductError, identity_from_url


REQUIRED_COLUMNS = (
    "Mã sản phẩm",
    "Tên sản phẩm",
    "Giá",
    "Doanh thu",
    "Tên cửa hàng",
    "Tỉ lệ hoa hồng",
    "Hoa hồng",
    "Link sản phẩm",
    "Link ưu đãi",
)
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES = 20
MAX_ROWS = 20_000
AFFILIATE_HOST = "s.shopee.vn"
PRODUCT_HOSTS = frozenset({"shopee.vn", "www.shopee.vn"})


class ShopeeCsvError(ValueError):
    """Safe operator-facing validation error for a CSV batch or row."""


@dataclass(frozen=True)
class ShopeeAffiliateCsvRow:
    item_id: str
    shop_id: str
    name: str
    current_price: int
    sold_count: int | None
    shop_name: str | None
    commission_rate_percent: float | None
    commission_amount: int | None
    product_url: str
    affiliate_url: str
    source_filename: str
    source_row_number: int


@dataclass(frozen=True)
class ShopeeCsvRowResult:
    row: ShopeeAffiliateCsvRow | None
    error: str | None
    status: str = "VALID"


def _required_text(value, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ShopeeCsvError(f"Thiếu {label}.")
    return text


def _decimal_display(value: str, *, label: str) -> Decimal:
    text = str(value or "").strip().lower().replace("\u00a0", "").replace(" ", "")
    if not text:
        raise ShopeeCsvError(f"Thiếu {label}.")
    # Shopee VN uses dot for thousands and comma for decimals in these exports.
    normalized = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(normalized)
    except (InvalidOperation, ValueError) as exc:
        raise ShopeeCsvError(f"{label} không hợp lệ.") from exc


def parse_price_vnd(value: str) -> int:
    text = str(value or "").strip().lower().replace("\u00a0", "").replace(" ", "")
    multiplier = 1
    if text.endswith("tr"):
        multiplier = 1_000_000
        text = text[:-2]
    elif text.endswith("k"):
        multiplier = 1_000
        text = text[:-1]
    number = _decimal_display(text, label="Giá sản phẩm")
    amount = int(number * multiplier)
    if amount <= 0:
        raise ShopeeCsvError("Giá sản phẩm phải lớn hơn 0.")
    return amount


def parse_commission_percent(value: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not text.endswith("%"):
        raise ShopeeCsvError("Tỉ lệ hoa hồng không hợp lệ.")
    number = _decimal_display(text[:-1], label="Tỉ lệ hoa hồng")
    if number < 0 or number > 100:
        raise ShopeeCsvError("Tỉ lệ hoa hồng phải nằm trong khoảng 0–100%.")
    return float(number)


def parse_commission_amount(value: str) -> int | None:
    text = str(value or "").strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        return None
    text = text.removeprefix("₫")
    if text.startswith("-"):
        raise ShopeeCsvError("Hoa hồng không được âm.")
    digits = text.replace(".", "")
    if not digits.isdigit():
        raise ShopeeCsvError("Hoa hồng không hợp lệ.")
    return int(digits)


def parse_sold_count(value: str) -> int | None:
    text = str(value or "").strip().lower().replace("\u00a0", "").replace(" ", "")
    if not text:
        return None
    if text.endswith("+"):
        text = text[:-1]
    multiplier = 1
    if text.endswith("tr"):
        multiplier = 1_000_000
        text = text[:-2]
    elif text.endswith("k"):
        multiplier = 1_000
        text = text[:-1]
    number = _decimal_display(text, label="Số lượt bán")
    if number < 0:
        raise ShopeeCsvError("Số lượt bán không được âm.")
    return int(number * multiplier)


def _split_safe_url(value: str, *, label: str):
    text = _required_text(value, label)
    if any(ord(character) < 32 for character in text):
        raise ShopeeCsvError(f"{label} chứa ký tự không hợp lệ.")
    try:
        parsed = urlsplit(text)
        # Accessing port deliberately validates malformed port syntax.
        port = parsed.port
    except ValueError as exc:
        raise ShopeeCsvError(f"{label} không hợp lệ.") from exc
    if parsed.username is not None or parsed.password is not None:
        raise ShopeeCsvError(f"{label} không được chứa thông tin đăng nhập.")
    return text, parsed, port


def _canonical_product_identity(value: str):
    text, parsed, port = _split_safe_url(value, label="Link sản phẩm")
    if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower().rstrip(".") not in PRODUCT_HOSTS:
        raise ShopeeCsvError("Link sản phẩm phải là HTTPS Shopee Việt Nam trực tiếp.")
    if port not in (None, 443):
        raise ShopeeCsvError("Link sản phẩm dùng cổng không được hỗ trợ.")
    try:
        identity = identity_from_url(text)
    except ShopeeProductError as exc:
        raise ShopeeCsvError(str(exc)) from exc
    return identity


def _validate_affiliate_url(value: str) -> str:
    text, parsed, port = _split_safe_url(value, label="Link ưu đãi")
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or host != AFFILIATE_HOST:
        raise ShopeeCsvError("Link ưu đãi phải là link HTTPS s.shopee.vn.")
    if port not in (None, 443):
        raise ShopeeCsvError("Link ưu đãi dùng cổng không được hỗ trợ.")
    if not parsed.path or parsed.path == "/":
        raise ShopeeCsvError("Link ưu đãi Shopee không có mã link.")
    return text


def _normalize_row(values: dict, *, filename: str, row_number: int) -> ShopeeAffiliateCsvRow:
    item_id = _required_text(values.get("Mã sản phẩm"), "Mã sản phẩm")
    if not item_id.isdigit():
        raise ShopeeCsvError("Mã sản phẩm phải là số.")
    name = _required_text(values.get("Tên sản phẩm"), "Tên sản phẩm")
    current_price = parse_price_vnd(values.get("Giá"))
    sold_count = parse_sold_count(values.get("Doanh thu"))
    shop_name = str(values.get("Tên cửa hàng") or "").strip() or None
    commission_rate = parse_commission_percent(values.get("Tỉ lệ hoa hồng"))
    commission_amount = parse_commission_amount(values.get("Hoa hồng"))
    identity = _canonical_product_identity(values.get("Link sản phẩm"))
    if identity.item_id != item_id:
        raise ShopeeCsvError(
            f"Mã sản phẩm {item_id} không khớp item_id {identity.item_id} trong Link sản phẩm."
        )
    affiliate_url = _validate_affiliate_url(values.get("Link ưu đãi"))
    return ShopeeAffiliateCsvRow(
        item_id=item_id,
        shop_id=identity.shop_id,
        name=name,
        current_price=current_price,
        sold_count=sold_count,
        shop_name=shop_name,
        commission_rate_percent=commission_rate,
        commission_amount=commission_amount,
        product_url=identity.canonical_url,
        affiliate_url=affiliate_url,
        source_filename=str(filename or "").strip() or "upload.csv",
        source_row_number=row_number,
    )


def parse_shopee_affiliate_csv(data: bytes, filename: str) -> list[ShopeeCsvRowResult]:
    if not isinstance(data, (bytes, bytearray)):
        raise ShopeeCsvError("Dữ liệu CSV không hợp lệ.")
    if len(data) > MAX_FILE_BYTES:
        raise ShopeeCsvError("File CSV vượt giới hạn 5 MiB.")
    try:
        text = bytes(data).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ShopeeCsvError("CSV phải dùng mã hóa UTF-8.") from exc

    try:
        reader = csv.DictReader(StringIO(text, newline=""), strict=True)
        fieldnames = tuple(reader.fieldnames or ())
    except csv.Error as exc:
        raise ShopeeCsvError("CSV không hợp lệ.") from exc
    if fieldnames != REQUIRED_COLUMNS:
        missing = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
        if missing:
            raise ShopeeCsvError("CSV thiếu cột bắt buộc: " + ", ".join(missing))
        raise ShopeeCsvError("CSV không đúng cấu trúc 9 cột Shopee Affiliate.")

    results: list[ShopeeCsvRowResult] = []
    try:
        for row_number, values in enumerate(reader, start=2):
            if None in values:
                results.append(ShopeeCsvRowResult(
                    row=None,
                    error="Dòng CSV có số cột không hợp lệ.",
                    status="ERROR",
                ))
                continue
            try:
                row = _normalize_row(values, filename=filename, row_number=row_number)
            except (ShopeeCsvError, ShopeeProductError, TypeError, ValueError) as exc:
                message = str(exc).strip() or "Dòng CSV không hợp lệ."
                results.append(ShopeeCsvRowResult(row=None, error=message, status="ERROR"))
            else:
                results.append(ShopeeCsvRowResult(row=row, error=None, status="VALID"))
    except csv.Error as exc:
        raise ShopeeCsvError("CSV không hợp lệ.") from exc
    return results


def dedupe_upload_rows(rows: list[ShopeeCsvRowResult]) -> list[ShopeeCsvRowResult]:
    rows = list(rows or [])
    last_valid_index: dict[tuple[str, str], int] = {}
    for index, result in enumerate(rows):
        if result.row is not None and result.error is None and result.status != "ERROR":
            last_valid_index[(result.row.shop_id, result.row.item_id)] = index

    normalized: list[ShopeeCsvRowResult] = []
    for index, result in enumerate(rows):
        if result.row is None or result.error is not None or result.status == "ERROR":
            normalized.append(result)
            continue
        key = (result.row.shop_id, result.row.item_id)
        status = "VALID" if last_valid_index.get(key) == index else "DUPLICATE_IN_UPLOAD"
        normalized.append(replace(result, status=status))
    return normalized
