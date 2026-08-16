"""Hai interface cho phép cắm thêm nguồn dữ liệu và kênh đăng bài (NFR6).

Thêm Lazada/TikTok Shop = viết một ContentSource mới.
Thêm X/Instagram = viết một PublishingChannel mới.
Phần lõi (scoring, content, jobs, attribution) không phải sửa dòng nào.
"""
from dataclasses import dataclass, field
from typing import Iterable, Optional


@dataclass
class RawProduct:
    """Bản ghi sản phẩm đã chuẩn hoá, độc lập với nguồn."""
    external_product_id: str
    name: str
    current_price: int
    commission_value: int
    category_code: str
    product_url: str
    merchant: str
    description: str = ""
    original_price: Optional[int] = None
    commission_rate: Optional[float] = None
    rating: Optional[float] = None
    review_count: int = 0
    sold_count: int = 0
    image_url_original: Optional[str] = None
    image_path_local: Optional[str] = None


@dataclass
class PublishResult:
    external_post_id: str
    published_at: str
    native_label_status: str = "not_attempted"  # applied / unavailable / failed


class PublishError(Exception):
    """Lỗi có thể retry (mạng, 5xx)."""


class RateLimitError(PublishError):
    """Chạm hạn mức. Không tính vào số lần retry -- hoãn tới cửa sổ sau."""


class ContentViolationError(Exception):
    """Nền tảng từ chối vì nội dung. TUYỆT ĐỐI không retry (BRD FR7)."""


class AuthError(Exception):
    """Token hỏng/hết hạn. Refresh rồi thử lại đúng một lần."""


class ContentSource:
    """Nguồn cấp dữ liệu sản phẩm."""

    name: str = "base"

    def fetch_products(self, category: Optional[str] = None, limit: int = 200) -> Iterable[RawProduct]:
        raise NotImplementedError

    def create_tracking_link(self, product_url: str, sub_ids: dict) -> str:
        raise NotImplementedError

    def fetch_transactions(self, since: str) -> Iterable[dict]:
        """Dùng cho job đối soát 6 giờ/lần."""
        raise NotImplementedError


class Publisher:
    """Kênh đăng bài theo platform. `media` luôn là list, kể cả khi chỉ 1 ảnh --
    chuẩn bị cho carousel Facebook/Instagram mà không phải đổi chữ ký lần hai."""

    platform: str = "base"
    max_caption_length: int = 500

    def publish(self, channel_row, caption: str, media: list) -> PublishResult:
        raise NotImplementedError

    def remaining_quota(self, channel_row) -> int:
        raise NotImplementedError

    def fetch_insights(self, channel_row, external_post_id: str) -> dict:
        return {}


@dataclass
class ExchangedToken:
    """Kết quả đổi authorization code lấy user access token."""
    token: str
    expires_in: int
    meta_user_id: str


@dataclass
class PageInfo:
    """Một Facebook Page mà user đang đăng nhập quản lý."""
    external_account_id: str
    name: str
    page_token: str


@dataclass
class InstagramInfo:
    """Instagram Professional account gắn với một Page."""
    external_account_id: str
    username: str
    page_token: str  # IG Graph API dùng chung Page token, không có token riêng


class MetaConnectionService:
    """OAuth + account discovery cho Facebook Page / Instagram Professional.

    Tách khỏi Publisher vì đây là bước KẾT NỐI (một lần, ra danh sách account),
    không phải bước ĐĂNG BÀI (mỗi account một Publisher riêng, xem
    FacebookPublisher/InstagramPublisher ở sub-project C).
    """

    def oauth_authorize_url(self, state: str, redirect_uri: str) -> str:
        raise NotImplementedError

    def exchange_code(self, code: str, redirect_uri: str) -> ExchangedToken:
        raise NotImplementedError

    def list_pages(self, user_token: str) -> list:
        raise NotImplementedError

    def instagram_for_page(self, page_id: str, page_token: str):
        raise NotImplementedError
