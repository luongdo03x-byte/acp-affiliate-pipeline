"""Adapter thật. Chưa chạy được trong container này (không có mạng ra ngoài),
nhưng là mã sẵn sàng dùng: điền key vào biến môi trường rồi đổi ACP_ADAPTER=live.

Ba chi tiết dễ sai đã xử lý sẵn ở đây:
  1. Accesstrade giới hạn 30 request/phút -> token bucket phía client.
  2. Threads dùng container model 2 bước và KHÔNG có webhook -> phải tự poll.
  3. Threads yêu cầu image_url truy cập công khai -> ảnh phải nằm trên object
     storage có URL công khai, không phải đường dẫn local.
"""
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

import requests

from ..core.crypto import decrypt
from .base import (
    ContentSource, PublishingChannel, RawProduct, PublishResult,
    PublishError, RateLimitError, ContentViolationError, AuthError,
)

AT_BASE = "https://api.accesstrade.vn/v1"
THREADS_BASE = "https://graph.threads.net/v1.0"


class TokenBucket:
    """Giới hạn tần suất phía client. Accesstrade chặn ở 30 req/phút."""

    def __init__(self, rate: int, per_seconds: float):
        self.capacity = rate
        self.tokens = float(rate)
        self.rate = rate / per_seconds
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def take(self, n: int = 1) -> None:
        while True:
            with self.lock:
                nowm = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (nowm - self.updated) * self.rate)
                self.updated = nowm
                if self.tokens >= n:
                    self.tokens -= n
                    return
                wait = (n - self.tokens) / self.rate
            time.sleep(wait)


class AccessTradeSource(ContentSource):
    name = "accesstrade"

    def __init__(self, access_key: Optional[str] = None, campaign_id: Optional[str] = None):
        self.access_key = access_key or os.environ.get("AT_ACCESS_KEY", "")
        self.campaign_id = campaign_id or os.environ.get("AT_CAMPAIGN_ID", "")
        self.bucket = TokenBucket(rate=30, per_seconds=60)
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Token {self.access_key}"

    def _get(self, path: str, **params):
        self.bucket.take()
        r = self.session.get(f"{AT_BASE}{path}", params=params, timeout=30)
        if r.status_code == 429:
            raise RateLimitError("Accesstrade trả 429")
        r.raise_for_status()
        return r.json()

    def fetch_products(self, category=None, limit=200):
        page, out = 1, []
        while len(out) < limit:
            data = self._get("/datafeeds", domain="shopee.vn", cat=category, page=page, limit=50)
            rows = data.get("data") or []
            if not rows:
                break
            for r in rows:
                price = int(float(r.get("price") or 0))
                out.append(RawProduct(
                    external_product_id=str(r.get("product_id") or r.get("id")),
                    name=r.get("name", ""),
                    description=r.get("desc", "") or "",
                    current_price=price,
                    original_price=int(float(r.get("price_original") or price)),
                    # Trường hoa hồng khác nhau tuỳ chiến dịch -- kiểm tra bằng
                    # dữ liệu thật rồi chỉnh map ở đúng chỗ này.
                    commission_value=int(float(r.get("commission") or 0)),
                    commission_rate=float(r.get("commission_rate") or 0) or None,
                    category_code=r.get("cate") or "khac",
                    rating=float(r.get("rating") or 0) or None,
                    review_count=int(r.get("review_count") or 0),
                    sold_count=int(r.get("sold") or 0),
                    image_url_original=r.get("image"),
                    product_url=r.get("url") or r.get("aff_link", ""),
                    merchant="shopee.vn",
                ))
            page += 1
        return out[:limit]

    def _post(self, path: str, body: dict):
        self.bucket.take()
        r = self.session.post(f"{AT_BASE}{path}", json=body, timeout=30)
        if r.status_code == 429:
            raise RateLimitError("Accesstrade trả 429")
        r.raise_for_status()
        return r.json()

    def create_tracking_link(self, product_url: str, sub_ids: dict) -> str:
        """POST JSON theo developers.accesstrade.vn/api-publisher-vietnamese/tao-tracking-link

        post_id được gắn vào CẢ HAI chỗ -- utm_content và sub1 -- một cách có chủ ý.
        Lý do: với chiến dịch Shopee, Accesstrade có thể ghi đè utm_content bằng
        chuỗi tracking của họ; ngược lại sub1..sub4 chỉ xuất hiện trong
        _extra.parameters của API giao dịch chứ không phải trường cấp một. Gắn hai
        chỗ thì mất một vẫn còn một. Xem attribution.extract_post_id().
        """
        body = {
            "campaign_id": self.campaign_id,
            "urls": [product_url],
            "url_enc": True,
            "utm_source": sub_ids.get("sub4", ""),      # kênh đăng
            "utm_medium": "threads",
            "utm_campaign": sub_ids.get("sub2", ""),    # mã chiến dịch nội bộ
            "utm_content": sub_ids.get("sub1", ""),     # post_id
            "sub1": sub_ids.get("sub1", ""),
            "sub2": sub_ids.get("sub2", ""),
            "sub3": sub_ids.get("sub3", ""),
            "sub4": sub_ids.get("sub4", ""),
        }
        data = self._post("/product_link/create", body)
        links = ((data.get("data") or {}).get("success_link") or [])
        if not links:
            errs = (data.get("data") or {}).get("error_link")
            raise PublishError(f"Accesstrade không tạo được link: {errs}")
        # short_link đẹp hơn khi đăng, aff_link là bản đầy đủ để dự phòng.
        return links[0].get("short_link") or links[0]["aff_link"]

    def fetch_transactions(self, since: str, until: str = None):
        """GET /v1/transactions?since=...&until=...

        status trả về là SỐ, không phải chuỗi -- phải map trước khi đối soát.
        """
        params = {"since": since, "limit": 200}
        if until:
            params["until"] = until
        data = self._get("/transactions", **params)
        return [self._normalize_transaction(t) for t in (data.get("data") or [])]

    # Accesstrade dùng mã số cho trạng thái đơn. Xác minh lại bằng dữ liệu thật
    # trước khi tin tuyệt đối -- đối soát sai ở đây là báo cáo doanh thu sai.
    STATUS_MAP = {0: "pending", 1: "approved", 2: "rejected", -1: "rejected"}

    @classmethod
    def _normalize_transaction(cls, t: dict) -> dict:
        extra = (t.get("_extra") or {}).get("parameters") or {}
        return {
            "transaction_id": str(t.get("transaction_id") or ""),
            "external_product_id": str(t.get("product_id") or ""),
            "sale_amount": int(float(t.get("transaction_value") or 0)),
            "commission": int(float(t.get("commission") or 0)),
            "status": cls.STATUS_MAP.get(t.get("status"), "pending"),
            "converted_at": t.get("transaction_time"),
            "utm_content": t.get("utm_content") or extra.get("utm_content"),
            "sub1": extra.get("sub1"),
            "sub2": extra.get("sub2") or t.get("utm_campaign"),
            "sub3": extra.get("sub3"),
            "sub4": extra.get("sub4") or t.get("utm_source"),
        }


class ThreadsChannel(PublishingChannel):
    platform = "threads"
    max_caption_length = 500

    def __init__(self, poll_interval: float = 3.0, poll_timeout: float = 60.0):
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.session = requests.Session()

    def _token(self, channel_row) -> str:
        tok = decrypt(channel_row["token_encrypted"])
        if not tok:
            raise AuthError(f"Kênh {channel_row['code']} chưa có token hợp lệ")
        return tok

    def remaining_quota(self, channel_row) -> int:
        uid = channel_row["external_user_id"]
        r = self.session.get(
            f"{THREADS_BASE}/{uid}/threads_publishing_limit",
            params={"fields": "quota_usage,config", "access_token": self._token(channel_row)},
            timeout=20,
        )
        if r.status_code in (401, 403):
            raise AuthError("Token bị từ chối khi đọc hạn mức")
        r.raise_for_status()
        d = (r.json().get("data") or [{}])[0]
        return int(d.get("config", {}).get("quota_total", 250)) - int(d.get("quota_usage", 0))

    def publish(self, channel_row, caption: str, image_url=None) -> PublishResult:
        if len(caption) > self.max_caption_length:
            raise ContentViolationError(f"Caption {len(caption)} ký tự, Threads chỉ cho {self.max_caption_length}")
        uid, token = channel_row["external_user_id"], self._token(channel_row)

        # Bước 1 -- tạo media container.
        payload = {"media_type": "IMAGE" if image_url else "TEXT", "text": caption, "access_token": token}
        if image_url:
            payload["image_url"] = image_url  # bắt buộc là URL công khai
        r = self.session.post(f"{THREADS_BASE}/{uid}/threads", data=payload, timeout=30)
        self._raise_for_api(r)
        creation_id = r.json()["id"]

        # Bước 2 -- poll tới khi container xử lý xong. Threads không có webhook.
        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            s = self.session.get(
                f"{THREADS_BASE}/{creation_id}",
                params={"fields": "status,error_message", "access_token": token}, timeout=20,
            )
            self._raise_for_api(s)
            body = s.json()
            status = body.get("status")
            if status == "FINISHED":
                break
            if status == "ERROR":
                raise PublishError(f"Container lỗi: {body.get('error_message')}")
            time.sleep(self.poll_interval)
        else:
            raise PublishError(f"Container {creation_id} chưa sẵn sàng sau {self.poll_timeout:.0f}s")

        # Bước 3 -- publish.
        p = self.session.post(
            f"{THREADS_BASE}/{uid}/threads_publish",
            data={"creation_id": creation_id, "access_token": token}, timeout=30,
        )
        self._raise_for_api(p)
        return PublishResult(
            external_post_id=p.json()["id"],
            published_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def fetch_insights(self, channel_row, external_post_id: str) -> dict:
        r = self.session.get(
            f"{THREADS_BASE}/{external_post_id}/insights",
            params={"metric": "views,likes,replies,reposts", "access_token": self._token(channel_row)},
            timeout=20,
        )
        if r.status_code >= 400:
            return {}
        out = {}
        for m in r.json().get("data", []):
            vals = m.get("values") or [{}]
            out[m.get("name")] = vals[0].get("value", 0)
        return out

    @staticmethod
    def _raise_for_api(r):
        if r.status_code < 400:
            return
        try:
            err = r.json().get("error", {})
        except ValueError:
            err = {}
        code, msg = err.get("code"), err.get("message", r.text[:200])
        if r.status_code in (401, 403) or code in (190, 102):
            raise AuthError(msg)
        if r.status_code == 429 or code in (4, 17, 32, 613):
            raise RateLimitError(msg)
        # Vi phạm nội dung -> không bao giờ retry, đẩy về hàng đợi duyệt.
        if code in (1346003, 1346013, 36003) or "policy" in str(msg).lower():
            raise ContentViolationError(msg)
        raise PublishError(f"HTTP {r.status_code}: {msg}")
