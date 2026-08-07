"""Nguồn sản phẩm TikTok Shop qua ACCESSTRADE (product feed v2).

Bài vẫn đăng lên Threads -- TikTok Shop chỉ đóng vai trò nguồn sản phẩm và nơi
tạo tracking link. Dùng nguồn này khi campaign Shopee còn chờ duyệt.

Lưu ý về base URL: feed v2 nằm ở /v2/... còn AccessTradeSource (Shopee) dùng
AT_BASE đã bao gồm /v1. Ở đây dùng base KHÔNG có phiên bản và ghi full path, để
không lặp lại lỗi ghép thành /v1/v1/... như đã từng xảy ra.
"""
import os
from typing import Optional

import requests

from .base import ContentSource, RawProduct, PublishError, RateLimitError
from .live import TokenBucket

AT_ROOT = os.environ.get("AT_ROOT", "https://api.accesstrade.vn")

# Đường tạo link TikTok Shop. Người vận hành đã gọi thành công nhưng response
# chưa được lưu lại, nên để cấu hình được và có đường dự phòng chung.
TIKTOK_LINK_PATH = os.environ.get("AT_TIKTOK_LINK_PATH", "/v1/product_link/create")


def _amount(node, default=0) -> int:
    """Giá TikTok Shop trả về dạng object có minimum_amount, đôi khi là chuỗi."""
    if node is None:
        return default
    if isinstance(node, (int, float)):
        return int(node)
    if isinstance(node, str):
        try:
            return int(float(node))
        except ValueError:
            return default
    if isinstance(node, dict):
        for key in ("minimum_amount", "min_amount", "amount", "value"):
            if node.get(key) is not None:
                return _amount(node[key], default)
    return default


def _rate(node) -> Optional[float]:
    """Tỷ lệ hoa hồng có thể là 0.08 hoặc 8 (phần trăm). Chuẩn hoá về dạng thập phân."""
    if node is None:
        return None
    if isinstance(node, dict):
        node = node.get("rate") or node.get("commission_rate")
    try:
        val = float(node)
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    return val / 100 if val > 1 else val


class AccessTradeTikTokShopSource(ContentSource):
    name = "accesstrade_tiktokshop"
    merchant_default = "tiktokshop"

    def __init__(self, access_key: str = None, campaign_id: str = None):
        self.access_key = access_key or os.environ.get("AT_ACCESS_KEY", "")
        self.campaign_id = campaign_id or os.environ.get("AT_TIKTOK_CAMPAIGN_ID") \
            or os.environ.get("AT_CAMPAIGN_ID", "")
        self.bucket = TokenBucket(rate=30, per_seconds=60)
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Token {self.access_key}"

    # ------------------------------------------------------------------ HTTP

    def _get(self, path: str, **params):
        self.bucket.take()
        r = self.session.get(f"{AT_ROOT}{path}", params=params, timeout=30)
        if r.status_code == 429:
            raise RateLimitError("Accesstrade trả 429")
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict):
        self.bucket.take()
        r = self.session.post(f"{AT_ROOT}{path}", json=body, timeout=30)
        if r.status_code == 429:
            raise RateLimitError("Accesstrade trả 429")
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------- chuẩn hoá dữ liệu

    @classmethod
    def normalize(cls, raw: dict) -> RawProduct:
        """Map một sản phẩm TikTok Shop sang model chuẩn của ACP.

        Tách thành classmethod để test được bằng fixture mà không cần mạng.
        """
        price = _amount(raw.get("sales_price"))
        original = _amount(raw.get("original_price"), price) or price
        commission_node = raw.get("commission") or {}
        commission_value = _amount(commission_node)
        rate = _rate(commission_node.get("rate") if isinstance(commission_node, dict) else None) \
            or _rate(raw.get("commission_rate"))
        if not commission_value and rate:
            commission_value = int(price * rate)
        if not rate and commission_value and price:
            rate = round(commission_value / price, 4)

        shop = raw.get("shop") or {}
        return RawProduct(
            external_product_id=str(raw.get("id") or raw.get("product_id") or ""),
            name=(raw.get("title") or raw.get("name") or "").strip(),
            description=(raw.get("description") or "").strip(),
            current_price=price,
            original_price=original,
            commission_value=commission_value,
            commission_rate=rate,
            category_code=cls._category(raw),
            rating=float(raw["rating"]) if raw.get("rating") else None,
            review_count=int(raw.get("review_count") or 0),
            sold_count=int(raw.get("units_sold") or raw.get("sold_count") or 0),
            image_url_original=raw.get("main_image_url") or raw.get("image_url"),
            product_url=raw.get("detail_link") or raw.get("product_url") or "",
            merchant=(shop.get("name") if isinstance(shop, dict) else None) or cls.merchant_default,
        )

    @staticmethod
    def _category(raw: dict) -> str:
        cat = raw.get("category") or raw.get("category_name") or raw.get("product_category")
        if isinstance(cat, dict):
            cat = cat.get("name") or cat.get("code")
        if isinstance(cat, list) and cat:
            cat = cat[-1] if isinstance(cat[-1], str) else (cat[-1] or {}).get("name")
        return str(cat).strip().lower().replace(" ", "-") if cat else "khac"

    # ------------------------------------------------------- ContentSource API

    def search_products(self, query: str = None, limit: int = 20, cursor: str = None):
        """Trả về (danh sách RawProduct, next_page_token)."""
        params = {"limit": min(limit, 50)}
        if query:
            params["keyword"] = query
        if cursor:
            params["page_token"] = cursor
        if self.campaign_id:
            params["campaign_id"] = self.campaign_id

        data = self._get("/v2/tiktokshop_product_feeds", **params)
        payload = data.get("data") or {}
        rows = payload.get("products") or []
        out = []
        for r in rows:
            try:
                p = self.normalize(r)
            except Exception:
                continue  # một sản phẩm hỏng không được làm gãy cả trang
            if p.external_product_id and p.name and p.current_price > 0:
                out.append(p)
        return out[:limit], payload.get("next_page_token")

    def get_product(self, external_product_id: str) -> Optional[RawProduct]:
        """Tìm đúng một sản phẩm. Feed v2 không có endpoint theo id nên duyệt trang.

        Giới hạn 10 trang để không quét vô hạn khi nhập nhầm mã.
        """
        cursor, pages = None, 0
        while pages < 10:
            items, cursor = self.search_products(limit=50, cursor=cursor)
            for p in items:
                if p.external_product_id == str(external_product_id):
                    return p
            pages += 1
            if not cursor:
                break
        return None

    def fetch_products(self, category=None, limit=200):
        """Nạp hàng loạt, dùng cho lệnh ingest."""
        out, cursor = [], None
        while len(out) < limit:
            items, cursor = self.search_products(limit=50, cursor=cursor)
            if not items:
                break
            out.extend(i for i in items if not category or i.category_code == category)
            if not cursor:
                break
        return out[:limit]

    def create_tracking_link(self, product_url: str, sub_ids: dict) -> str:
        """Gắn post_id vào cả utm_content lẫn sub1 -- xem attribution.extract_post_id."""
        body = {
            "campaign_id": self.campaign_id,
            "urls": [product_url],
            "url_enc": True,
            "utm_source": sub_ids.get("sub4", ""),
            "utm_medium": "threads",
            "utm_campaign": sub_ids.get("sub2", ""),
            "utm_content": sub_ids.get("sub1", ""),
            "sub1": sub_ids.get("sub1", ""),
            "sub2": sub_ids.get("sub2", ""),
            "sub3": sub_ids.get("sub3", ""),
            "sub4": sub_ids.get("sub4", ""),
        }
        data = self._post(TIKTOK_LINK_PATH, body)
        return self.parse_link_response(data)

    @staticmethod
    def parse_link_response(data: dict) -> str:
        """Tách riêng để test bằng fixture."""
        node = data.get("data") or {}
        links = node.get("success_link") or node.get("links") or []
        if isinstance(node, list):
            links = node
        if not links:
            raise PublishError(f"Accesstrade không tạo được link: {node.get('error_link') or data}")
        first = links[0]
        if isinstance(first, str):
            return first
        return first.get("short_link") or first.get("aff_link") or first.get("url") or ""

    def fetch_transactions(self, since: str, until: str = None):
        from .live import AccessTradeSource
        params = {"since": since, "limit": 200}
        if until:
            params["until"] = until
        data = self._get("/v1/transactions", **params)
        return [AccessTradeSource._normalize_transaction(t) for t in (data.get("data") or [])]
