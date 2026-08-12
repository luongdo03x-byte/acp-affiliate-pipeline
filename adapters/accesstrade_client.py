"""Resilient HTTP boundary for ACCESSTRADE TikTok Shop Product API V2."""
from dataclasses import dataclass
import os
import time
from typing import Any, Optional

import requests

from .base import PublishError, RateLimitError


DEFAULT_BASE_URL = "https://api.accesstrade.vn"


class UnsupportedSortError(PublishError):
    """The provider explicitly rejected a requested catalog sort field."""


@dataclass(frozen=True)
class LinkResult:
    """Both provider URLs; callers choose short URL only for presentation."""
    full_url: str
    short_url: Optional[str] = None


@dataclass(frozen=True)
class AccessTradeProduct:
    """Raw V2 product mapped to catalog field names without business decisions."""
    external_product_id: str
    title: str
    shop_name: Optional[str]
    detail_link: Optional[str]
    main_image_url: Optional[str]
    sale_region: Optional[str]
    currency: Optional[str]
    price_min: Optional[int]
    price_max: Optional[int]
    original_price_min: Optional[int]
    original_price_max: Optional[int]
    commission_rate_raw: Optional[float]
    commission_rate_percent: Optional[float]
    commission_amount: Optional[int]
    commission_currency: Optional[str]
    units_sold: Optional[int]
    has_inventory: Optional[bool]
    category_data: Any


def _int_or_none(value) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _number_or_none(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _amounts(value) -> tuple[Optional[int], Optional[int], Optional[str]]:
    if isinstance(value, dict):
        return (
            _int_or_none(value.get("minimum_amount", value.get("min_amount", value.get("amount")))),
            _int_or_none(value.get("maximum_amount", value.get("max_amount", value.get("amount")))),
            value.get("currency"),
        )
    amount = _int_or_none(value)
    return amount, amount, None


def normalize_commission_rate_percent(raw_rate) -> Optional[float]:
    """Convert ACCESSTRADE's basis-point commission rate to a percentage."""
    rate = _number_or_none(raw_rate)
    return round(rate / 100, 2) if rate is not None else None


def normalize_accesstrade_product(raw: dict) -> AccessTradeProduct:
    """Preserve absent provider values as ``None`` for catalog ranking decisions."""
    raw = raw or {}
    sales_min, sales_max, currency = _amounts(raw.get("sales_price"))
    original_min, original_max, original_currency = _amounts(raw.get("original_price"))
    commission = raw.get("commission") or {}
    if not isinstance(commission, dict):
        commission = {"amount": commission}
    shop = raw.get("shop") or {}
    if not isinstance(shop, dict):
        shop = {}
    category = raw.get("category", raw.get("category_data"))
    raw_rate = commission.get("rate", raw.get("commission_rate"))
    commission_amount = _int_or_none(commission.get("amount", raw.get("commission_amount")))

    return AccessTradeProduct(
        external_product_id=str(raw.get("id") or raw.get("product_id") or ""),
        title=str(raw.get("title") or raw.get("name") or "").strip(),
        shop_name=(shop.get("name") or None),
        detail_link=(raw.get("detail_link") or raw.get("product_url") or None),
        main_image_url=(raw.get("main_image_url") or raw.get("image_url") or None),
        sale_region=(raw.get("sale_region") or raw.get("region") or None),
        currency=currency or original_currency,
        price_min=sales_min,
        price_max=sales_max,
        original_price_min=original_min,
        original_price_max=original_max,
        commission_rate_raw=_number_or_none(raw_rate),
        commission_rate_percent=normalize_commission_rate_percent(raw_rate),
        commission_amount=commission_amount,
        commission_currency=commission.get("currency") or currency,
        units_sold=_int_or_none(raw.get("units_sold", raw.get("sold_count"))),
        has_inventory=raw.get("has_inventory") if raw.get("has_inventory") is not None else None,
        category_data=category,
    )


class AccessTradeClient:
    PRODUCT_FEED_PATH = "/v2/tiktokshop_product_feeds"
    CREATE_LINK_PATH = "/v2/tiktokshop_product_feeds/create_link"
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

    def __init__(self, *, base_url: str = None, token: str = None, session=None, sleep=None):
        self.base_url = (base_url or os.environ.get("ACCESSTRADE_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.session = session or requests.Session()
        self.session.headers["Authorization"] = f"Token {token or os.environ.get('ACCESSTRADE_API_TOKEN', '')}"
        self._sleep = sleep or time.sleep

    @classmethod
    def from_env(cls):
        return cls()

    def search_products(self, *, sort_field="RECOMMENDED", limit=50,
                        title_keywords=None, page_token=None, product_ids=None,
                        campaign_id=None):
        params = {"sort_field": sort_field, "limit": min(int(limit), 50)}
        if title_keywords:
            params["title_keywords"] = title_keywords
        if page_token:
            params["page_token"] = page_token
        if product_ids:
            params["product_ids"] = ",".join(map(str, product_ids))
        if campaign_id:
            params["campaign_id"] = campaign_id
        payload = self._request(
            "GET", self.PRODUCT_FEED_PATH, params=params,
            requested_sort=sort_field)
        data = payload.get("data") or {}
        return data.get("products") or [], data.get("next_page_token")

    def create_product_link(self, detail_link: str, *, post_id: str, external_product_id: str) -> LinkResult:
        return self._create_link(detail_link, {
            "utm_source": "threads",
            "utm_medium": "social",
            "utm_campaign": "acp",
            "utm_content": str(external_product_id),
            "sub_1": str(post_id),
        }, product_id=external_product_id)

    def create_tracking_link(self, detail_link: str, sub_ids: dict, *,
                             campaign_id=None, link_path=None) -> LinkResult:
        """Compatibility path for the existing ContentSource contract."""
        attribution = {
            "utm_source": sub_ids.get("sub4", ""),
            "utm_medium": "threads",
            "utm_campaign": sub_ids.get("sub2", ""),
            "utm_content": sub_ids.get("sub1", ""),
            "sub_1": sub_ids.get("sub1", ""),
            "sub_2": sub_ids.get("sub2", ""),
            "sub_3": sub_ids.get("sub3", ""),
            "sub_4": sub_ids.get("sub4", ""),
        }
        if campaign_id:
            attribution["campaign_id"] = campaign_id
        return self._create_link(detail_link, attribution, link_path=link_path)

    def _create_link(self, detail_link: str, attribution: dict, *, product_id=None, link_path=None) -> LinkResult:
        body = {"product_url": detail_link, **attribution}
        if product_id:
            body["product_id"] = str(product_id)
        payload = self._request("POST", link_path or self.CREATE_LINK_PATH, json=body)
        node = payload.get("data") or {}
        full_url = short_url = None
        links = []
        if isinstance(node, dict):
            full_url = node.get("aff_url")
            short_url = node.get("aff_short_url")
            links = node.get("success_link") or node.get("links") or []
        elif isinstance(node, list):
            links = node
        if not (full_url or short_url) and not links:
            raise PublishError("ACCESSTRADE không tạo được link")
        if not (full_url or short_url):
            first = links[0]
            if isinstance(first, str):
                return LinkResult(full_url=first)
            full_url = first.get("aff_link") or first.get("full_url") or first.get("url")
            short_url = first.get("short_link") or first.get("short_url")
        if not full_url and not short_url:
            raise PublishError("ACCESSTRADE không tạo được link")
        return LinkResult(full_url=full_url or short_url, short_url=short_url)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        requested_sort = str(kwargs.pop("requested_sort", "") or "").upper()
        url = f"{self.base_url}{path}"
        for attempt in range(3):
            try:
                response = self.session.get(url, timeout=(5, 20), **kwargs) if method == "GET" else \
                    self.session.post(url, timeout=(5, 20), **kwargs)
            except requests.Timeout:
                if attempt < 2:
                    self._sleep(1 if attempt == 0 else 2)
                    continue
                raise PublishError("Không thể kết nối ACCESSTRADE; hãy thử lại sau")
            except requests.RequestException:
                raise PublishError("Không thể kết nối ACCESSTRADE; hãy thử lại sau")

            if response.status_code in self.RETRYABLE_STATUS_CODES:
                if attempt < 2:
                    self._sleep(1 if attempt == 0 else 2)
                    continue
                if response.status_code == 429:
                    raise RateLimitError("ACCESSTRADE đang giới hạn yêu cầu; hãy thử lại sau")
                raise PublishError("ACCESSTRADE tạm thời không phản hồi; hãy thử lại sau")
            if response.status_code == 401:
                raise PublishError("Token ACCESSTRADE không hợp lệ")
            if response.status_code < 200 or response.status_code >= 300:
                if requested_sort == "COMMISSION" and response.status_code == 400:
                    try:
                        error_payload = response.json()
                    except (TypeError, ValueError):
                        error_payload = None
                    if isinstance(error_payload, dict) and self._is_unsupported_sort(error_payload):
                        raise UnsupportedSortError("ACCESSTRADE không hỗ trợ sắp xếp hoa hồng")
                raise PublishError(f"ACCESSTRADE không phản hồi thành công (HTTP {response.status_code})")
            try:
                payload = response.json()
            except (TypeError, ValueError):
                raise PublishError("ACCESSTRADE trả dữ liệu không hợp lệ")
            if not isinstance(payload, dict):
                raise PublishError("ACCESSTRADE trả dữ liệu không hợp lệ")
            if payload.get("status") is False or payload.get("success") is False:
                if requested_sort == "COMMISSION" and self._is_unsupported_sort(payload):
                    raise UnsupportedSortError("ACCESSTRADE không hỗ trợ sắp xếp hoa hồng")
                raise PublishError("ACCESSTRADE từ chối yêu cầu")
            return payload
        raise AssertionError("unreachable")

    @staticmethod
    def _is_unsupported_sort(payload: dict) -> bool:
        code = str(payload.get("code") or payload.get("error_code") or "").upper()
        message = str(payload.get("message") or payload.get("error") or "").lower()
        return (code in {"UNSUPPORTED_SORT", "UNSUPPORTED_SORT_FIELD", "INVALID_SORT_FIELD"}
                or ("sort" in message and any(word in message for word in (
                    "unsupported", "not supported", "không hỗ trợ"))))
