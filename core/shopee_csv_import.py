"""Official Shopee Affiliate bulk-link CSV parsing and Product Pool import.

Parsing is pure: no network calls and no database mutation. Import is an
explicit operator-confirmed step that preserves the already-created Shopee
short affiliate URL exactly as supplied by the CSV.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from io import StringIO
from urllib.parse import urlsplit

from .db import now, transaction, ulid
from .product_category import infer_category
from .shopee_image_enrichment import enqueue_product
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
PRODUCT_PROVIDER = "SHOPEE_AFFILIATE"
PRODUCT_SOURCE = "manual_shopee"
PRODUCT_MERCHANT = "shopee.vn"
PRICE_SOURCE = "affiliate_csv"


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
    source_filename: str | None = None
    source_row_number: int | None = None


def _required_text(value, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ShopeeCsvError(f"Thiếu {label}.")
    return text


def _decimal_display(value: str, *, label: str) -> Decimal:
    text = str(value or "").strip().lower().replace("\u00a0", "").replace(" ", "")
    if not text:
        raise ShopeeCsvError(f"Thiếu {label}.")
    normalized = text.replace(".", "").replace(",", ".")
    try:
        number = Decimal(normalized)
    except (InvalidOperation, ValueError) as exc:
        raise ShopeeCsvError(f"{label} không hợp lệ.") from exc
    if not number.is_finite():
        raise ShopeeCsvError(f"{label} không hợp lệ.")
    return number


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


def _row_result(*, row, error, status, filename, row_number):
    return ShopeeCsvRowResult(
        row=row,
        error=error,
        status=status,
        source_filename=filename,
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

    source_filename = str(filename or "").strip() or "upload.csv"
    results: list[ShopeeCsvRowResult] = []
    try:
        for row_number, values in enumerate(reader, start=2):
            if None in values:
                results.append(_row_result(
                    row=None,
                    error="Dòng CSV có số cột không hợp lệ.",
                    status="ERROR",
                    filename=source_filename,
                    row_number=row_number,
                ))
                continue
            try:
                row = _normalize_row(values, filename=source_filename, row_number=row_number)
            except (ShopeeCsvError, ShopeeProductError, TypeError, ValueError) as exc:
                message = str(exc).strip() or "Dòng CSV không hợp lệ."
                results.append(_row_result(
                    row=None,
                    error=message,
                    status="ERROR",
                    filename=source_filename,
                    row_number=row_number,
                ))
            else:
                results.append(_row_result(
                    row=row,
                    error=None,
                    status="VALID",
                    filename=source_filename,
                    row_number=row_number,
                ))
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


def _find_product(conn, item_id: str):
    return conn.execute(
        "SELECT * FROM product WHERE source=? AND merchant=? AND external_product_id=?",
        (PRODUCT_SOURCE, PRODUCT_MERCHANT, str(item_id)),
    ).fetchone()


def _find_matching_product(conn, row: ShopeeAffiliateCsvRow):
    existing = _find_product(conn, row.item_id)
    if existing is None:
        return None
    try:
        identity = identity_from_url(existing["product_url"])
    except (ShopeeProductError, TypeError, ValueError) as exc:
        raise ShopeeCsvError(
            f"Product hiện có cho item {row.item_id} không có canonical Shopee identity an toàn."
        ) from exc
    if identity.item_id != row.item_id or identity.shop_id != row.shop_id:
        raise ShopeeCsvError(
            f"Mã sản phẩm {row.item_id} đã tồn tại nhưng thuộc khác shop; không tự overwrite."
        )
    return existing


def _csv_owned_values(row: ShopeeAffiliateCsvRow) -> dict:
    values = {
        "provider": PRODUCT_PROVIDER,
        "name": row.name,
        "current_price": row.current_price,
        "price_min": row.current_price,
        "price_max": row.current_price,
        "product_url": row.product_url,
        "detail_link": row.product_url,
        "affiliate_url": row.affiliate_url,
        "affiliate_short_url": row.affiliate_url,
        "affiliate_link_status": "READY",
        "affiliate_link_error": None,
        "is_available": 1,
        "currency": "VND",
        "commission_currency": "VND",
    }
    if row.shop_name is not None:
        values["shop_name"] = row.shop_name
    if row.sold_count is not None:
        values["sold_count"] = row.sold_count
        values["units_sold"] = row.sold_count
    if row.commission_rate_percent is not None:
        values["commission_rate"] = row.commission_rate_percent
        values["commission_rate_percent"] = row.commission_rate_percent
    if row.commission_amount is not None:
        values["commission_value"] = row.commission_amount
        values["commission_amount"] = row.commission_amount
    return values


def classify_row_against_db(conn, row: ShopeeAffiliateCsvRow) -> str:
    existing = _find_matching_product(conn, row)
    if existing is None:
        return "NEW"
    for column, desired in _csv_owned_values(row).items():
        if existing[column] != desired:
            return "UPDATED"
    return "UNCHANGED"


def preview_rows_against_db(conn, row_results: list[ShopeeCsvRowResult]) -> list[ShopeeCsvRowResult]:
    previewed = []
    for result in row_results or []:
        if result.row is None or result.error is not None or result.status == "ERROR":
            previewed.append(result)
        elif result.status == "DUPLICATE_IN_UPLOAD":
            previewed.append(result)
        else:
            try:
                status = classify_row_against_db(conn, result.row)
            except ShopeeCsvError as exc:
                previewed.append(replace(result, status="ERROR", error=str(exc)))
            else:
                previewed.append(replace(result, status=status))
    return previewed


def _record_csv_price_observation(conn, product_id: str, price: int) -> bool:
    latest = conn.execute(
        "SELECT price FROM product_price_history WHERE product_id=? ORDER BY id DESC LIMIT 1",
        (product_id,),
    ).fetchone()
    if latest is not None and latest["price"] == int(price):
        return False
    conn.execute(
        "INSERT INTO product_price_history (product_id, price, observed_at, source) VALUES (?,?,?,?)",
        (product_id, int(price), now(), PRICE_SOURCE),
    )
    return True


def _insert_product(conn, row: ShopeeAffiliateCsvRow) -> str:
    timestamp = now()
    product_id = ulid()
    values = {
        "id": product_id,
        "source": PRODUCT_SOURCE,
        "merchant": PRODUCT_MERCHANT,
        "external_product_id": row.item_id,
        "name": row.name,
        "description": "",
        "current_price": row.current_price,
        "original_price": None,
        "commission_value": row.commission_amount or 0,
        "commission_rate": row.commission_rate_percent,
        "category_code": infer_category(row.name),
        "rating": None,
        "review_count": 0,
        "sold_count": row.sold_count or 0,
        "image_url_original": None,
        "image_path_local": None,
        "product_url": row.product_url,
        "is_available": 1,
        "last_seen_at": timestamp,
        "created_at": timestamp,
        "updated_at": timestamp,
        "provider": PRODUCT_PROVIDER,
        "shop_name": row.shop_name,
        "detail_link": row.product_url,
        "currency": "VND",
        "price_min": row.current_price,
        "price_max": row.current_price,
        "commission_rate_percent": row.commission_rate_percent,
        "commission_amount": row.commission_amount,
        "commission_currency": "VND",
        "units_sold": row.sold_count,
        "has_inventory": None,
        "first_seen_at": timestamp,
        "last_synced_at": timestamp,
        "affiliate_url": row.affiliate_url,
        "affiliate_short_url": row.affiliate_url,
        "affiliate_link_status": "READY",
        "affiliate_link_error": None,
        "affiliate_link_created_at": timestamp,
    }
    columns = list(values)
    conn.execute(
        f"INSERT INTO product ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )
    _record_csv_price_observation(conn, product_id, row.current_price)
    return product_id


def _update_product(conn, existing, row: ShopeeAffiliateCsvRow) -> str:
    timestamp = now()
    values = _csv_owned_values(row)
    # Danh mục KHÔNG thuộc nhóm CSV-owned: nếu nguồn khác (operator/topic
    # engine) đã đặt danh mục thật thì giữ nguyên; chỉ điền suy đoán vào chỗ
    # đang trống/'khac' -- đúng tinh thần "metadata giàu hơn phải sống sót".
    if str(existing["category_code"] or "khac") == "khac":
        values["category_code"] = infer_category(row.name)
    values.update({
        "last_seen_at": timestamp,
        "last_synced_at": timestamp,
        "updated_at": timestamp,
        "affiliate_link_created_at": timestamp,
    })
    columns = list(values)
    conn.execute(
        "UPDATE product SET " + ", ".join(f"{column}=?" for column in columns) + " WHERE id=?",
        tuple(values[column] for column in columns) + (existing["id"],),
    )
    _record_csv_price_observation(conn, existing["id"], row.current_price)
    return existing["id"]


def _touch_sync_stamps(conn, product_id: str) -> None:
    """Refresh freshness stamps for a row the CSV re-confirmed as still current.

    A stable product whose price never moves classifies as UNCHANGED forever.
    Auto publishing hard-blocks on `last_synced_at` age (SHOPEE_AUTO_FRESHNESS),
    so without this the product silently ages out of Auto and its publish jobs
    defer in a loop that never ends.
    """
    timestamp = now()
    conn.execute(
        "UPDATE product SET last_seen_at=?, last_synced_at=?, updated_at=? WHERE id=?",
        (timestamp, timestamp, timestamp, product_id),
    )


def import_rows(conn, row_results: list[ShopeeCsvRowResult]) -> dict:
    summary = {
        "total": len(row_results or []),
        "new": 0,
        "updated": 0,
        "unchanged": 0,
        "duplicate": 0,
        "error": 0,
    }
    with transaction(conn):
        for result in row_results or []:
            if result.row is None or result.error is not None or result.status == "ERROR":
                summary["error"] += 1
                continue
            if result.status == "DUPLICATE_IN_UPLOAD":
                summary["duplicate"] += 1
                continue

            try:
                state = classify_row_against_db(conn, result.row)
            except ShopeeCsvError:
                summary["error"] += 1
                continue
            if state == "NEW":
                product_id = _insert_product(conn, result.row)
                enqueue_product(conn, product_id)
                summary["new"] += 1
            elif state == "UPDATED":
                existing = _find_matching_product(conn, result.row)
                product_id = _update_product(conn, existing, result.row)
                enqueue_product(conn, product_id)
                summary["updated"] += 1
            else:
                existing = _find_matching_product(conn, result.row)
                if existing is not None:
                    _touch_sync_stamps(conn, existing["id"])
                    enqueue_product(conn, existing["id"])
                summary["unchanged"] += 1
    return summary
