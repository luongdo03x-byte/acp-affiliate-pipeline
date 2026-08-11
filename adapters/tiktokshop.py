"""Nguồn sản phẩm TikTok Shop qua ACCESSTRADE (product feed v2).

Bài vẫn đăng lên Threads -- TikTok Shop chỉ đóng vai trò nguồn sản phẩm và nơi
tạo tracking link. Dùng nguồn này khi campaign Shopee còn chờ duyệt.

Lưu ý về base URL: feed v2 nằm ở /v2/... còn AccessTradeSource (Shopee) dùng
AT_BASE đã bao gồm /v1. Ở đây dùng base KHÔNG có phiên bản và ghi full path, để
không lặp lại lỗi ghép thành /v1/v1/... như đã từng xảy ra.
"""
import os
from typing import Optional

from .accesstrade_client import AccessTradeClient, normalize_accesstrade_product
from .base import ContentSource, RawProduct, PublishError

AT_ROOT = os.environ.get("AT_ROOT", "https://api.accesstrade.vn")

# Đường tạo link TikTok Shop. Người vận hành đã gọi thành công nhưng response
# chưa được lưu lại, nên để cấu hình được và có đường dự phòng chung.
# Compatibility contract: the client owns calls to "/v2/tiktokshop_product_feeds".
PRODUCT_FEED_PATH = "/v2/tiktokshop_product_feeds"


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

    def __init__(self, access_key: str = None, campaign_id: str = None, client=None):
        self.access_key = access_key or os.environ.get("AT_ACCESS_KEY", "")
        self.campaign_id = campaign_id or os.environ.get("AT_TIKTOK_CAMPAIGN_ID") \
            or os.environ.get("AT_CAMPAIGN_ID", "")
        self.client = client or AccessTradeClient(
            base_url=os.environ.get("ACCESSTRADE_API_BASE_URL") or AT_ROOT,
            token=self.access_key or os.environ.get("ACCESSTRADE_API_TOKEN", ""),
        )

    # ------------------------------------------------------- chuẩn hoá dữ liệu

    @classmethod
    def normalize(cls, raw: dict) -> RawProduct:
        """Map một sản phẩm TikTok Shop sang model chuẩn của ACP.

        Tách thành classmethod để test được bằng fixture mà không cần mạng.
        """
        normalized = normalize_accesstrade_product(raw)
        price = normalized.price_min or 0
        original = normalized.original_price_min or price
        commission_value = normalized.commission_amount or 0
        commission_node = raw.get("commission") or {}
        rate = _rate(commission_node.get("rate") if isinstance(commission_node, dict) else None) \
            or _rate(raw.get("commission_rate"))
        if not commission_value and rate:
            commission_value = int(price * rate)
        if not rate and commission_value and price:
            rate = round(commission_value / price, 4)

        return RawProduct(
            external_product_id=normalized.external_product_id,
            name=normalized.title,
            description=(raw.get("description") or "").strip(),
            current_price=price,
            original_price=original,
            commission_value=commission_value,
            commission_rate=rate,
            category_code=cls._category(raw),
            rating=float(raw["rating"]) if raw.get("rating") else None,
            review_count=int(raw.get("review_count") or 0),
            sold_count=normalized.units_sold or 0,
            image_url_original=normalized.main_image_url,
            product_url=normalized.detail_link or "",
            merchant=normalized.shop_name or cls.merchant_default,
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
        rows, next_page_token = self.client.search_products(
            limit=limit, title_keywords=query, page_token=cursor)
        out = []
        for r in rows:
            try:
                p = self.normalize(r)
            except Exception:
                continue  # một sản phẩm hỏng không được làm gãy cả trang
            if p.external_product_id and p.name and p.current_price > 0:
                out.append(p)
        return out[:limit], next_page_token

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
        link = self.client.create_tracking_link(product_url, sub_ids)
        return link.short_url or link.full_url

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
        data = self.client._request("GET", "/v1/transactions", params=params)
        return [AccessTradeSource._normalize_transaction(t) for t in (data.get("data") or [])]
