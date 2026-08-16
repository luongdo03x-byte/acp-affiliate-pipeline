"""Adapter giả lập -- cho phép chạy và test toàn bộ pipeline không cần mạng.

Cố ý mô phỏng cả các chế độ lỗi thật: rate limit, lỗi mạng tạm thời, nội dung bị
từ chối. Nếu logic retry chỉ đúng ở happy path thì nó sẽ lộ ra ở đây chứ không
phải lúc chạy production.
"""
import json
import os
import random
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

from .base import (
    ContentSource, Publisher, RawProduct, PublishResult,
    PublishError, RateLimitError, ContentViolationError,
    MetaConnectionService, ExchangedToken, PageInfo, InstagramInfo,
)

SEED_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seed", "datafeed_sample.json")
TXN_STORE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "var", "mock_transactions.json")


class MockAccessTrade(ContentSource):
    name = "accesstrade"

    def __init__(self, seed_file: str = SEED_FILE, txn_store: str = TXN_STORE):
        self.seed_file = seed_file
        self.txn_store = txn_store

    def fetch_products(self, category=None, limit=200):
        with open(self.seed_file, encoding="utf-8") as fh:
            rows = json.load(fh)
        out = []
        for r in rows[:limit]:
            if category and r["category_code"] != category:
                continue
            out.append(RawProduct(**r))
        return out

    def search_products(self, query: str = None, limit: int = 20, cursor: str = None):
        """Khớp theo TỪNG TỪ của truy vấn, không phải cả cụm.

        API thật của sàn cũng làm vậy: tìm "váy nữ" phải ra "Váy hoa nhí dáng
        suông". Khớp cả cụm thì kết quả rỗng và trông như hệ thống hỏng.
        """
        items = self.fetch_products(limit=500)
        if query:
            words = [w for w in query.lower().split() if len(w) > 1]
            scored = []
            for p in items:
                hay = f"{p.name} {p.category_code}".lower()
                hits = sum(1 for w in words if w in hay)
                if hits:
                    scored.append((hits, p))
            scored.sort(key=lambda x: (-x[0], -x[1].sold_count))
            items = [p for _, p in scored]
        return items[:limit], None

    def get_product(self, external_product_id: str):
        for p in self.fetch_products(limit=500):
            if p.external_product_id == str(external_product_id):
                return p
        return None

    def create_tracking_link(self, product_url: str, sub_ids: dict) -> str:
        params = {"url": product_url}
        params.update({k: v for k, v in sub_ids.items() if v})
        return "https://go.isclix.com/deep_link/v5/mock?" + urlencode(params)

    # -- phần giả lập API giao dịch ------------------------------------------
    # Ghi ra file để dữ liệu sống qua các lần gọi CLI, giống hệt API thật:
    # `run.py simulate` nạp vào, `run.py reconcile` đọc ra ở tiến trình khác.

    def seed_transactions(self, events) -> int:
        os.makedirs(os.path.dirname(self.txn_store), exist_ok=True)
        with open(self.txn_store, "w", encoding="utf-8") as fh:
            json.dump(events, fh, ensure_ascii=False)
        return len(events)

    def fetch_transactions(self, since: str, until: str = None):
        if not os.path.exists(self.txn_store):
            return []
        with open(self.txn_store, encoding="utf-8") as fh:
            rows = json.load(fh)
        return [r for r in rows if not since or (r.get("converted_at") or "") >= since]


class MockThreads(Publisher):
    platform = "threads"
    max_caption_length = 500

    def __init__(self, fail_rate: float = 0.0, rate_limited: bool = False, seed: int = None):
        self.fail_rate = fail_rate
        self.rate_limited = rate_limited
        self._rng = random.Random(seed)
        self.published = []

    def publish(self, channel_row, caption: str, media: list = None) -> PublishResult:
        media = media or []
        if len(media) > 1:
            raise ValueError(f"Threads chưa hỗ trợ nhiều ảnh, nhận {len(media)}")
        if self.rate_limited:
            raise RateLimitError("Đã dùng hết 250 bài trong cửa sổ 24 giờ")
        if len(caption) > self.max_caption_length:
            raise ContentViolationError(
                f"Caption {len(caption)} ký tự, vượt giới hạn {self.max_caption_length} của Threads"
            )
        if self._rng.random() < self.fail_rate:
            raise PublishError("Hết thời gian chờ khi tạo media container")
        # Threads dùng container model 2 bước: tạo container -> poll status -> publish.
        # Bản mock gộp lại, bản live tách đúng như thật.
        pid = f"mock_{self._rng.randrange(10**12, 10**13)}"
        self.published.append((pid, caption))
        return PublishResult(external_post_id=pid, published_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def remaining_quota(self, channel_row) -> int:
        return 0 if self.rate_limited else 250

    def fetch_insights(self, channel_row, external_post_id: str) -> dict:
        r = self._rng
        views = r.randrange(300, 4000)
        return {
            "views": views,
            "likes": int(views * r.uniform(0.01, 0.06)),
            "replies": int(views * r.uniform(0.001, 0.01)),
            "reposts": int(views * r.uniform(0.0005, 0.005)),
        }


class MockMetaConnectionService(MetaConnectionService):
    """Fixture cố định: 2 Page giả, Page đầu có 1 Instagram gắn kèm. Không cần
    mạng, dùng cho dev/test giống MockThreads/MockAccessTrade."""

    def oauth_authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"https://mock.meta.example/oauth/authorize?state={state}&redirect_uri={redirect_uri}"

    def exchange_code(self, code: str, redirect_uri: str) -> ExchangedToken:
        return ExchangedToken(token=f"mock_user_token_{code}", expires_in=5184000,
                               meta_user_id="mock_meta_user_1")

    def list_pages(self, user_token: str) -> list:
        return [
            PageInfo(external_account_id="1000000000001", name="Fashion Page Test",
                     page_token="mock_page_token_1"),
            PageInfo(external_account_id="1000000000002", name="Tech Deals Test",
                     page_token="mock_page_token_2"),
        ]

    def instagram_for_page(self, page_id: str, page_token: str):
        if page_id == "1000000000001":
            return InstagramInfo(external_account_id="1700000000001",
                                  username="test.fashion", page_token=page_token)
        return None


class MockFacebookPublisher(Publisher):
    """Fixture xác định, không cần mạng. Giới hạn 1-10 ảnh/bài -- con số cần
    xác nhận lại với giới hạn thật của Facebook lúc go-live."""

    platform = "facebook"
    max_caption_length = 63206

    def __init__(self, fail_rate: float = 0.0, rate_limited: bool = False,
                 seed: int = None, native_label_status: str = "applied"):
        self.fail_rate = fail_rate
        self.rate_limited = rate_limited
        self._rng = random.Random(seed)
        self.native_label_status = native_label_status
        self.published = []

    def publish(self, channel_row, caption: str, media: list = None) -> PublishResult:
        media = media or []
        if not (1 <= len(media) <= 10):
            raise ContentViolationError(f"Facebook cần 1-10 ảnh, nhận {len(media)}")
        if self.rate_limited:
            raise RateLimitError("Đã dùng hết hạn mức đăng bài Facebook")
        if self._rng.random() < self.fail_rate:
            raise PublishError("Lỗi tạm thời khi tạo bài Facebook")
        pid = f"mock_fb_{self._rng.randrange(10**12, 10**13)}"
        self.published.append((pid, caption, list(media)))
        return PublishResult(external_post_id=pid,
                              published_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              native_label_status=self.native_label_status)

    def remaining_quota(self, channel_row) -> int:
        return 0 if self.rate_limited else 999


class MockInstagramPublisher(Publisher):
    """1 ảnh -> nhánh single, 2-10 ảnh -> nhánh carousel (mock gộp làm một,
    bản live tách hai luồng khác nhau, xem adapters/live.py)."""

    platform = "instagram"
    max_caption_length = 2200

    def __init__(self, fail_rate: float = 0.0, rate_limited: bool = False,
                 seed: int = None, native_label_status: str = "applied"):
        self.fail_rate = fail_rate
        self.rate_limited = rate_limited
        self._rng = random.Random(seed)
        self.native_label_status = native_label_status
        self.published = []

    def publish(self, channel_row, caption: str, media: list = None) -> PublishResult:
        media = media or []
        if len(media) == 0 or len(media) > 10:
            raise ContentViolationError(
                f"Instagram cần 1 ảnh (single) hoặc 2-10 ảnh (carousel), nhận {len(media)}")
        if len(caption) > self.max_caption_length:
            raise ContentViolationError(
                f"Caption {len(caption)} ký tự, Instagram chỉ cho {self.max_caption_length}")
        if self.rate_limited:
            raise RateLimitError("Đã dùng hết hạn mức đăng bài Instagram")
        if self._rng.random() < self.fail_rate:
            raise PublishError("Lỗi tạm thời khi tạo media container Instagram")
        pid = f"mock_ig_{self._rng.randrange(10**12, 10**13)}"
        self.published.append((pid, caption, list(media)))
        return PublishResult(external_post_id=pid,
                              published_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              native_label_status=self.native_label_status)

    def remaining_quota(self, channel_row) -> int:
        return 0 if self.rate_limited else 999


def simulate_postbacks(posts, rng: random.Random):
    """Sinh postback giả lập theo phân phối hợp lý để kiểm chứng luồng quy kết.

    Không phải mọi bài đều ra đơn -- đó chính là điểm cần đo. Danh mục khác nhau
    có tỷ lệ chuyển đổi khác nhau, để dashboard có thứ đáng nhìn.
    """
    cr_by_category = {
        "gia-dung": 0.028, "cham-soc-ca-nhan": 0.021, "phu-kien-cong-nghe": 0.014,
        "thoi-trang": 0.009, "the-thao": 0.017, "me-va-be": 0.024,
    }
    events = []
    for p in posts:
        clicks = max(0, int(rng.gauss(26, 15)))
        cr = cr_by_category.get(p["category_code"], 0.012)
        orders = sum(1 for _ in range(clicks) if rng.random() < cr)
        for i in range(orders):
            # Giỏ hàng thường lớn hơn món được nhắc tới, nhưng không gấp mấy lần.
            sale = int(p["current_price"] * rng.uniform(0.95, 1.7))
            events.append({
                "transaction_id": f"TX{rng.randrange(10**9, 10**10)}",
                "external_product_id": p["external_product_id"],
                "sale_amount": sale,
                "commission": int(sale * (p["commission_rate"] or 0.05)),
                "sub1": p["id"],
                "sub2": p["campaign_code"],
                "sub3": p["variant_code"],
                "sub4": p["channel_code"],
                "converted_at": (datetime.now(timezone.utc) - timedelta(hours=rng.randrange(1, 72))).isoformat(timespec="seconds"),
            })
        p["_clicks"] = clicks
    return events
