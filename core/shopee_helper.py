"""Validation boundary for metadata submitted by ACP Shopee Helper.

The Chrome extension is intentionally user-assisted.  It may read only the
rendered product DOM after the operator clicks the extension, and this module
is the server-side boundary that proves the observed tab is the same Shopee
product that ACP paired with before accepting any metadata.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlsplit

from ..adapters.shopee_affiliate import canonical_product_url


ALLOWED_METADATA_FIELDS = ("name", "current_price", "original_price", "image_url", "shop")
MAX_NAME_LEN = 500
MAX_SHOP_LEN = 200
MAX_IMAGE_URL_LEN = 2048
MAX_PRODUCT_URL_LEN = 2048
MAX_PRICE_VND = 10_000_000_000
_DIRECT_SHOPEE_HOSTS = {"shopee.vn", "www.shopee.vn"}
_CANONICAL_PRODUCT_RE = re.compile(r"^/product/(\d+)/(\d+)$")


class ShopeeHelperError(ValueError):
    """Invalid or unsafe metadata submitted by the local browser helper."""


@dataclass(frozen=True)
class HelperSubmission:
    expected_product_url: str
    observed_product_url: str
    product_id: str
    metadata: dict


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def canonical_helper_product(url: str) -> tuple[str, str]:
    """Return ``(canonical_url, item_id)`` for a concrete direct Shopee product.

    Short affiliate hosts and non-HTTPS URLs are rejected.  Query strings,
    fragments and temporary ``credential_token`` values disappear when the
    product identity is canonicalized by ``canonical_product_url``.
    """
    if not isinstance(url, str):
        raise ShopeeHelperError("URL sản phẩm Shopee không hợp lệ.")
    value = url.strip()
    if not value or len(value) > MAX_PRODUCT_URL_LEN or _has_control_chars(value):
        raise ShopeeHelperError("URL sản phẩm Shopee không hợp lệ.")

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ShopeeHelperError("URL sản phẩm Shopee không hợp lệ.") from exc

    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or host not in _DIRECT_SHOPEE_HOSTS:
        raise ShopeeHelperError("Chỉ chấp nhận link sản phẩm trực tiếp trên shopee.vn.")
    if parsed.username is not None or parsed.password is not None:
        raise ShopeeHelperError("URL sản phẩm Shopee không được chứa thông tin đăng nhập.")
    if port not in (None, 443):
        raise ShopeeHelperError("Cổng URL sản phẩm Shopee không hợp lệ.")

    canonical = canonical_product_url(value)
    try:
        canonical_parsed = urlsplit(canonical)
    except ValueError as exc:
        raise ShopeeHelperError("Không nhận diện được sản phẩm Shopee.") from exc

    match = _CANONICAL_PRODUCT_RE.fullmatch(canonical_parsed.path or "")
    if not match:
        raise ShopeeHelperError("Link không chứa mã sản phẩm Shopee cụ thể.")

    shop_id, item_id = match.groups()
    canonical = f"https://shopee.vn/product/{shop_id}/{item_id}"
    return canonical, item_id


def _clean_text(value, *, field: str, max_len: int):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ShopeeHelperError(f"{field} không hợp lệ.")
    text = value.strip()
    if not text:
        return None
    if len(text) > max_len or _has_control_chars(text):
        raise ShopeeHelperError(f"{field} không hợp lệ.")
    return text


def _clean_price(value, *, field: str):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ShopeeHelperError(f"{field} không hợp lệ.")
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError, AttributeError) as exc:
        raise ShopeeHelperError(f"{field} không hợp lệ.") from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise ShopeeHelperError(f"{field} không hợp lệ.")
    amount = int(number)
    if amount < 0 or amount > MAX_PRICE_VND:
        raise ShopeeHelperError(f"{field} không hợp lệ.")
    return amount


def _clean_image_url(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ShopeeHelperError("URL ảnh sản phẩm không hợp lệ.")
    text = value.strip()
    if not text:
        return None
    if len(text) > MAX_IMAGE_URL_LEN or _has_control_chars(text):
        raise ShopeeHelperError("URL ảnh sản phẩm không hợp lệ.")
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ShopeeHelperError("URL ảnh sản phẩm không hợp lệ.") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ShopeeHelperError("URL ảnh sản phẩm phải dùng HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise ShopeeHelperError("URL ảnh sản phẩm không được chứa thông tin đăng nhập.")
    if port not in (None, 443):
        raise ShopeeHelperError("Cổng URL ảnh sản phẩm không hợp lệ.")
    return text


def sanitize_helper_metadata(value: object) -> dict:
    """Return only the five allowlisted helper fields with bounded values."""
    if not isinstance(value, dict):
        raise ShopeeHelperError("Metadata Shopee không hợp lệ.")

    return {
        "name": _clean_text(value.get("name"), field="Tên sản phẩm", max_len=MAX_NAME_LEN),
        "current_price": _clean_price(value.get("current_price"), field="Giá hiện tại"),
        "original_price": _clean_price(value.get("original_price"), field="Giá gốc"),
        "image_url": _clean_image_url(value.get("image_url")),
        "shop": _clean_text(value.get("shop"), field="Tên shop", max_len=MAX_SHOP_LEN),
    }


def validate_helper_submission(expected_url: str, observed_url: str, metadata: object) -> HelperSubmission:
    """Validate that helper metadata came from the product paired by ACP."""
    expected_product_url, expected_item_id = canonical_helper_product(expected_url)
    observed_product_url, observed_item_id = canonical_helper_product(observed_url)
    if expected_product_url != observed_product_url or expected_item_id != observed_item_id:
        raise ShopeeHelperError("Tab Shopee đang mở không đúng sản phẩm đã ghép với ACP.")

    return HelperSubmission(
        expected_product_url=expected_product_url,
        observed_product_url=observed_product_url,
        product_id=expected_item_id,
        metadata=sanitize_helper_metadata(metadata),
    )
