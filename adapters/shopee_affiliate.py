"""Manual import of an already-generated Shopee affiliate URL.

This module deliberately does not create tracking links.  It normalises an
operator-confirmed Shopee product and preserves the exact prebuilt affiliate
URL for the post pipeline.
"""
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from io import BytesIO
import hashlib
import json
import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from PIL import Image

from .base import RawProduct
from .safe_http import SafeHttpClient, SafeHttpError

SHOPEE_HOSTS = {"shopee.vn", "s.shopee.vn"}


class AffiliateImportError(Exception):
    """Safe, operator-facing import error."""


@dataclass(frozen=True)
class ResolvedAffiliateUrl:
    affiliate_url: str
    product_url: str


@dataclass(frozen=True)
class ProductMetadata:
    name: str = None
    current_price: int = None
    original_price: int = None
    image_url: str = None
    shop: str = None


@dataclass(frozen=True)
class ConfirmedProductInput:
    affiliate_url: str
    product_url: str
    name: str
    current_price: int
    original_price: int = None
    image_url: str = None
    shop: str = None


class AffiliateUrlResolver:
    def __init__(self, http=None):
        self.http = http or SafeHttpClient()

    def validate_shopee_url(self, url: str) -> str:
        try:
            return self.http.validate_url(url, SHOPEE_HOSTS)
        except (SafeHttpError, AttributeError) as exc:
            raise AffiliateImportError(
                "Link affiliate Shopee không hợp lệ hoặc host chưa được hỗ trợ."
            ) from exc

    def resolve(self, affiliate_url: str) -> ResolvedAffiliateUrl:
        self.validate_shopee_url(affiliate_url)
        try:
            response = self.http.get(
                affiliate_url,
                allowed_hosts=SHOPEE_HOSTS,
                expected_content_prefix="text/html",
            )
        except (SafeHttpError, OSError) as exc:
            raise AffiliateImportError("Không thể phân tích link affiliate Shopee.") from exc
        return ResolvedAffiliateUrl(affiliate_url=affiliate_url, product_url=canonical_product_url(response.final_url))


class _MetadataHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self._in_jsonld = False
        self._jsonld_parts = []
        self.jsonld = []

    def handle_starttag(self, tag, attrs):
        attrs = {str(k).lower(): v for k, v in attrs if k}
        if tag.lower() == "meta":
            key = attrs.get("property") or attrs.get("name")
            content = attrs.get("content")
            if key and content is not None:
                self.meta.setdefault(key.lower(), content.strip())
        elif tag.lower() == "script" and (attrs.get("type") or "").lower() == "application/ld+json":
            self._in_jsonld = True
            self._jsonld_parts = []

    def handle_data(self, data):
        if self._in_jsonld:
            self._jsonld_parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self._in_jsonld:
            raw = "".join(self._jsonld_parts).strip()
            if raw:
                self.jsonld.append(raw)
            self._in_jsonld = False
            self._jsonld_parts = []


def _walk_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _is_product(node) -> bool:
    typ = node.get("@type") if isinstance(node, dict) else None
    if isinstance(typ, str):
        return typ.lower() == "product"
    if isinstance(typ, list):
        return any(str(x).lower() == "product" for x in typ)
    return False


def _vnd_int(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(round(value)) if value >= 0 else None

    text = str(value).strip().replace("\u00a0", " ")
    text = re.sub(r"[^0-9,.-]", "", text)
    if not text or text.startswith("-"):
        return None

    # If exactly two trailing digits follow the final separator and the integer
    # part is already long, treat it as a decimal suffix (289000.00).
    last_dot, last_comma = text.rfind("."), text.rfind(",")
    last = max(last_dot, last_comma)
    if last >= 0 and len(text) - last - 1 in (1, 2):
        prefix_digits = re.sub(r"\D", "", text[:last])
        if len(prefix_digits) >= 4:
            try:
                fraction = re.sub(r"\D", "", text[last + 1:])
                return int(Decimal(prefix_digits + "." + fraction))
            except InvalidOperation:
                return None

    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def _first_name(value):
    if isinstance(value, dict):
        value = value.get("name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _first_image(value):
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        for item in value:
            result = _first_image(item)
            if result:
                return result
    if isinstance(value, dict):
        return _first_image(value.get("url") or value.get("contentUrl"))
    return None


def _offers(node):
    offers = node.get("offers") or {}
    if isinstance(offers, list):
        offers = next((x for x in offers if isinstance(x, dict)), {})
    return offers if isinstance(offers, dict) else {}


def _api_price(value):
    """Convert Shopee API fixed-point price (1 VND == 100000 units) to VND."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dict):
        value = value.get("single_value")
        if value in (None, -1):
            return None
    try:
        raw = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if raw < 0:
        return None
    return int(raw / Decimal("100000"))


def _api_image(value):
    if isinstance(value, list):
        value = next((x for x in value if x), None)
    if isinstance(value, dict):
        value = value.get("url") or value.get("image")
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.startswith(("http://", "https://")):
        return value
    return "https://down-vn.img.susercontent.com/file/" + value


def _merge_metadata(primary: ProductMetadata, fallback: ProductMetadata) -> ProductMetadata:
    return ProductMetadata(
        name=primary.name or fallback.name,
        current_price=primary.current_price or fallback.current_price,
        original_price=primary.original_price or fallback.original_price,
        image_url=primary.image_url or fallback.image_url,
        shop=primary.shop or fallback.shop,
    )


def _has_core_metadata(meta: ProductMetadata) -> bool:
    return bool(meta.name and meta.current_price and meta.image_url)


# Bốn cột mốc bàn giao ở docs/... roadmap. BROWSER_HELPER_REQUIRED và
# MANUAL_REQUIRED không tách bằng lý do lỗi (CAPTCHA vs mạng) vì UI xử lý như
# nhau ở cả hai trường hợp -- người vận hành luôn có thể bấm nút mở Chrome
# Helper HOẶC tự gõ tay, form không khoá theo trạng thái.
AUTO_COMPLETE = "AUTO_COMPLETE"
AUTO_PARTIAL = "AUTO_PARTIAL"
BROWSER_HELPER_REQUIRED = "BROWSER_HELPER_REQUIRED"


def metadata_state(meta: ProductMetadata) -> str:
    """Trạng thái đọc metadata tự động, quyết định UI hiển thị badge nào."""
    have = sum(1 for v in (meta.name, meta.current_price, meta.image_url) if v)
    if have == 3:
        return AUTO_COMPLETE
    if have > 0:
        return AUTO_PARTIAL
    return BROWSER_HELPER_REQUIRED


class ProductMetadataResolver:
    def __init__(self, http=None):
        self.http = http or SafeHttpClient()

    def _html_metadata(self, product_url: str) -> ProductMetadata:
        response = self.http.get(
            product_url,
            allowed_hosts=SHOPEE_HOSTS,
            expected_content_prefix="text/html",
        )
        if response.content_type and not response.content_type.startswith("text/html"):
            raise SafeHttpError("Trang sản phẩm không trả HTML")
        html = response.content.decode("utf-8", errors="replace")
        parser = _MetadataHTMLParser()
        try:
            parser.feed(html)
        except Exception:
            pass

        product = None
        for raw in parser.jsonld:
            try:
                decoded = json.loads(raw)
            except (ValueError, TypeError):
                continue
            product = next((node for node in _walk_json(decoded) if _is_product(node)), None)
            if product:
                break

        name = current_price = original_price = image_url = shop = None
        if product:
            offers = _offers(product)
            name = _first_name(product.get("name"))
            current_price = _vnd_int(offers.get("price") or offers.get("lowPrice"))
            high = _vnd_int(offers.get("highPrice"))
            if high and current_price and high > current_price:
                original_price = high
            image_url = _first_image(product.get("image"))
            shop = _first_name(product.get("brand")) or _first_name(product.get("seller"))

        meta = parser.meta
        name = name or meta.get("og:title")
        image_url = image_url or meta.get("og:image")
        current_price = current_price or _vnd_int(
            meta.get("product:price:amount") or meta.get("og:price:amount"))
        original_price = original_price or _vnd_int(meta.get("product:original_price:amount"))
        return ProductMetadata(
            name=name or None,
            current_price=current_price,
            original_price=original_price,
            image_url=image_url or None,
            shop=shop,
        )

    def _api_metadata(self, product_url: str) -> ProductMetadata:
        shop_id, item_id = _product_ids_from_url(product_url)
        if not shop_id or not item_id:
            return ProductMetadata()

        candidates = [
            "https://shopee.vn/api/v4/pdp/get_pc?" + urlencode({
                "shop_id": shop_id,
                "item_id": item_id,
            }),
            "https://shopee.vn/api/v4/item/get?" + urlencode({
                "shopid": shop_id,
                "itemid": item_id,
            }),
        ]

        for api_url in candidates:
            try:
                response = self.http.get(
                    api_url,
                    allowed_hosts=SHOPEE_HOSTS,
                    expected_content_prefix="application/json",
                )
            except (SafeHttpError, OSError):
                continue
            if not (response.content_type or "").startswith("application/json"):
                continue
            try:
                payload = json.loads(response.content.decode("utf-8", errors="replace"))
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
                continue

            data = payload["data"]
            item = data.get("item") if isinstance(data.get("item"), dict) else data
            product_price = data.get("product_price") if isinstance(data.get("product_price"), dict) else {}
            shop_detail = data.get("shop_detailed") if isinstance(data.get("shop_detailed"), dict) else {}

            name = item.get("title") or item.get("name")
            image = item.get("image") or data.get("image")

            current_raw = None
            original_raw = None
            price_block = product_price.get("price")
            if isinstance(price_block, dict):
                current_raw = price_block.get("single_value")
                if current_raw in (None, -1):
                    candidates_price = [price_block.get("range_min"), price_block.get("range_max")]
                    current_raw = next((x for x in candidates_price if x not in (None, -1)), None)
            else:
                current_raw = item.get("price") or item.get("price_min")

            before = product_price.get("price_before_discount")
            if isinstance(before, dict):
                original_raw = before.get("single_value")
                if original_raw in (None, -1):
                    original_raw = next((x for x in (before.get("range_min"), before.get("range_max"))
                                         if x not in (None, -1)), None)
            else:
                original_raw = item.get("price_before_discount") or item.get("price_min_before_discount")

            current = _api_price(current_raw)
            original = _api_price(original_raw)
            if original and current and original <= current:
                original = None

            shop = (shop_detail.get("name") or item.get("shop_name") or data.get("shop_name"))
            meta = ProductMetadata(
                name=str(name).strip() if isinstance(name, str) and name.strip() else None,
                current_price=current,
                original_price=original,
                image_url=_api_image(image),
                shop=str(shop).strip() if isinstance(shop, str) and shop.strip() else None,
            )
            if any((meta.name, meta.current_price, meta.image_url, meta.shop)):
                return meta
        return ProductMetadata()

    def resolve(self, product_url: str) -> ProductMetadata:
        product_url = canonical_product_url(product_url)
        html_meta = ProductMetadata()
        html_error = None
        try:
            html_meta = self._html_metadata(product_url)
        except (SafeHttpError, OSError) as exc:
            html_error = exc

        if _has_core_metadata(html_meta):
            return html_meta

        api_meta = self._api_metadata(product_url)
        merged = _merge_metadata(html_meta, api_meta)
        if any((merged.name, merged.current_price, merged.image_url, merged.shop)):
            return merged

        if html_error is not None:
            raise AffiliateImportError("Không thể tải thông tin sản phẩm Shopee.") from html_error
        return merged


def _product_ids_from_url(url: str):
    parsed = urlsplit(url)
    path = parsed.path or ""

    patterns = (
        r"-i\.(\d+)\.(\d+)(?:$|[/?])",
        r"/product/(\d+)/(\d+)(?:$|[/?])",
        r"/opaapi/lp/(\d+)/(\d+)(?:$|[/?])",
        r"/opaanlp/(\d+)/(\d+)(?:$|[/?])",
    )
    for pattern in patterns:
        match = re.search(pattern, path)
        if match:
            return match.group(1), match.group(2)

    params = {k.lower(): v for k, v in parse_qsl(parsed.query, keep_blank_values=False)}
    shop = params.get("shopid") or params.get("shop_id")
    item = params.get("itemid") or params.get("item_id")
    if shop and item and str(shop).isdigit() and str(item).isdigit():
        return str(shop), str(item)
    return None, None


def canonical_product_url(url: str) -> str:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")

    shop_id, item_id = _product_ids_from_url(url)
    if shop_id and item_id:
        # Shopee short links can land on /opaapi/lp/... with a short-lived
        # credential_token.  Never persist that URL or its query string.
        return f"https://shopee.vn/product/{shop_id}/{item_id}"

    netloc = host
    if parsed.port and not ((scheme == "https" and parsed.port == 443) or (scheme == "http" and parsed.port == 80)):
        netloc = f"{host}:{parsed.port}"

    path = parsed.path or "/"
    keep = {"itemid", "item_id", "shopid", "shop_id"}
    params = sorted((k.lower(), v) for k, v in parse_qsl(parsed.query, keep_blank_values=False)
                    if k.lower() in keep)
    query = urlencode(params)
    return urlunsplit((scheme, netloc, path, query, ""))


def _item_id_from_path(path: str):
    match = re.search(r"-i\.(\d+)\.(\d+)(?:$|[/?])", path)
    if match:
        return match.group(2)
    match = re.search(r"/product/(\d+)/(\d+)(?:$|[/?])", path)
    if match:
        return match.group(2)
    match = re.search(r"/opaapi/lp/(\d+)/(\d+)(?:$|[/?])", path)
    if match:
        return match.group(2)
    match = re.search(r"/opaanlp/(\d+)/(\d+)(?:$|[/?])", path)
    if match:
        return match.group(2)
    return None


def external_product_id(url: str) -> str:
    _shop, item = _product_ids_from_url(url)
    if item and str(item).isdigit():
        return str(item)
    digest = hashlib.sha256(canonical_product_url(url).encode("utf-8")).hexdigest()[:24]
    return "url_" + digest


class ManualShopeeSource:
    name = "manual_shopee"

    def __init__(self, http=None, image_http=None):
        self.http = http or SafeHttpClient()
        self.image_http = image_http or (http if http is not None else SafeHttpClient(max_bytes=8 * 1024 * 1024))
        self.url_resolver = AffiliateUrlResolver(self.http)
        self.metadata_resolver = ProductMetadataResolver(self.http)

    def resolve(self, affiliate_url: str) -> ResolvedAffiliateUrl:
        return self.url_resolver.resolve(affiliate_url)

    def metadata(self, product_url: str) -> ProductMetadata:
        return self.metadata_resolver.resolve(product_url)

    def validate_confirmed_urls(self, affiliate_url: str, product_url: str):
        self.url_resolver.validate_shopee_url(affiliate_url)
        self.url_resolver.validate_shopee_url(product_url)

    @staticmethod
    def normalize_confirmed(data: ConfirmedProductInput) -> RawProduct:
        name = (data.name or "").strip()
        image_url = (data.image_url or "").strip()
        try:
            price = int(data.current_price)
        except (TypeError, ValueError) as exc:
            raise AffiliateImportError("Giá sản phẩm không hợp lệ.") from exc
        if not name:
            raise AffiliateImportError("Tên sản phẩm là bắt buộc.")
        if price <= 0:
            raise AffiliateImportError("Giá sản phẩm phải lớn hơn 0.")
        if not image_url:
            raise AffiliateImportError("Ảnh sản phẩm là bắt buộc.")
        original = None
        if data.original_price not in (None, ""):
            try:
                original = int(data.original_price)
            except (TypeError, ValueError) as exc:
                raise AffiliateImportError("Giá gốc không hợp lệ.") from exc
            if original <= 0:
                original = None

        return RawProduct(
            external_product_id=external_product_id(data.product_url),
            name=name,
            current_price=price,
            original_price=original,
            commission_value=0,
            commission_rate=None,
            category_code="khac",
            product_url=canonical_product_url(data.product_url),
            merchant="shopee.vn",
            description="",
            rating=None,
            review_count=0,
            sold_count=0,
            image_url_original=image_url,
        )

    def materialize_image(self, image_url: str, media_dir: str) -> str:
        try:
            response = self.image_http.get(
                image_url,
                allowed_hosts=None,
                expected_content_prefix="image/",
            )
        except SafeHttpError as exc:
            raise AffiliateImportError("Không thể tải ảnh sản phẩm an toàn.") from exc
        if not (response.content_type or "").startswith("image/"):
            raise AffiliateImportError("URL ảnh không trả nội dung hình ảnh.")
        try:
            probe = Image.open(BytesIO(response.content))
            fmt = (probe.format or "").upper()
            probe.verify()
        except Exception as exc:
            raise AffiliateImportError("Dữ liệu ảnh sản phẩm không hợp lệ.") from exc

        ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}.get(fmt, ".img")
        digest = hashlib.sha256(image_url.encode("utf-8")).hexdigest()[:24]
        source_dir = os.path.join(media_dir, "source")
        os.makedirs(source_dir, exist_ok=True)
        path = os.path.abspath(os.path.join(source_dir, digest + ext))
        with open(path, "wb") as fh:
            fh.write(response.content)
        return path

    def prepare_product(self, data: ConfirmedProductInput, media_dir: str) -> RawProduct:
        self.validate_confirmed_urls(data.affiliate_url, data.product_url)
        raw = self.normalize_confirmed(data)
        local = self.materialize_image(raw.image_url_original, media_dir)
        return replace(raw, image_path_local=local)

    def create_tracking_link(self, product_url: str, sub_ids: dict) -> str:
        raise AffiliateImportError("Nguồn manual Shopee dùng link affiliate có sẵn.")
