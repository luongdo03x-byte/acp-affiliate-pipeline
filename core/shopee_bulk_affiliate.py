"""Bulk Shopee Affiliate link generation for ACP.

Uses Shopee's documented redirect shape:
    https://s.shopee.vn/an_redir?origin_link=...&affiliate_id=...&sub_id=...

The module deliberately does not log in to Shopee, scrape pages, bypass bot
protection, or call a private Shopee API. It only converts canonical Shopee
product URLs using an operator-configured Affiliate ID.
"""
from dataclasses import dataclass
import re
from typing import List, Optional, Tuple
from urllib.parse import urlencode, urlsplit

from ..adapters.shopee_affiliate import canonical_product_url, external_product_id
from .db import now

MAX_BULK_URLS = 500
SHOPEE_REDIRECT_URL = "https://s.shopee.vn/an_redir"
_ALLOWED_PRODUCT_HOSTS = {"shopee.vn", "www.shopee.vn"}


class BulkAffiliateError(ValueError):
    """Batch-level configuration or validation error safe to show to operator."""


@dataclass(frozen=True)
class BulkAffiliateResult:
    input_url: str
    status: str
    product_url: str = ""
    external_product_id: str = ""
    affiliate_url: str = ""
    sub_id: str = ""
    product_id: Optional[str] = None
    error: Optional[str] = None


def _validate_affiliate_id(value: str) -> str:
    affiliate_id = str(value or "").strip()
    if not re.fullmatch(r"[0-9]{1,32}", affiliate_id):
        raise BulkAffiliateError(
            "Thiếu hoặc sai SHOPEE_AFFILIATE_ID. Hãy cấu hình Affiliate ID dạng số ở backend."
        )
    return affiliate_id


def _segment(value: str, fallback: str, max_len: int = 24) -> str:
    text = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "").strip())
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or fallback)[:max_len]


def _normalize_product_url(value: str) -> Tuple[str, str]:
    raw = str(value or "").strip()
    if not raw:
        raise BulkAffiliateError("URL sản phẩm đang trống.")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise BulkAffiliateError("URL Shopee không hợp lệ.") from exc

    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or host not in _ALLOWED_PRODUCT_HOSTS:
        raise BulkAffiliateError("Chỉ nhận URL sản phẩm https://shopee.vn/...")
    if parsed.username is not None or parsed.password is not None or port not in (None, 443):
        raise BulkAffiliateError("URL Shopee chứa thành phần không được hỗ trợ.")
    if parsed.path.startswith("/an_redir"):
        raise BulkAffiliateError("Hãy nhập link sản phẩm gốc, không nhập link affiliate đã tạo.")

    product_url = canonical_product_url(raw)
    item_id = external_product_id(product_url)
    if not item_id or item_id.startswith("url_"):
        raise BulkAffiliateError(
            "Không nhận diện được mã sản phẩm. Dùng URL có dạng /product/<shop>/<item> hoặc -i.<shop>.<item>."
        )
    return product_url, item_id


def _build_sub_id(item_id: str, sub_tag: str) -> str:
    # Shopee documents five '-' separated values for sub_id. Keep every
    # generated segment hyphen-free so the result is always exactly 5 parts.
    return "-".join((
        "acp",
        "bulk",
        "web",
        _segment(item_id, "product", 24),
        _segment(sub_tag, "default", 24),
    ))


def build_affiliate_url(product_url: str, affiliate_id: str, sub_tag: str = "default") -> Tuple[str, str]:
    affiliate_id = _validate_affiliate_id(affiliate_id)
    canonical, item_id = _normalize_product_url(product_url)
    sub_id = _build_sub_id(item_id, sub_tag)
    query = urlencode({
        "origin_link": canonical,
        "affiliate_id": affiliate_id,
        "sub_id": sub_id,
    })
    return f"{SHOPEE_REDIRECT_URL}?{query}", sub_id


def _link_existing_product(conn, item_id: str, affiliate_url: str) -> Optional[str]:
    if conn is None:
        return None
    row = conn.execute(
        """SELECT id
             FROM product
            WHERE lower(merchant)='shopee.vn' AND external_product_id=?
            ORDER BY CASE WHEN source='manual_shopee' THEN 0 ELSE 1 END,
                     updated_at DESC
            LIMIT 1""",
        (item_id,),
    ).fetchone()
    if not row:
        return None
    product_id = row["id"] if hasattr(row, "keys") else row[0]
    timestamp = now()
    conn.execute(
        """UPDATE product
              SET affiliate_url=?, affiliate_short_url=NULL,
                  affiliate_link_status='READY', affiliate_link_error=NULL,
                  affiliate_link_created_at=?, updated_at=?
            WHERE id=?""",
        (affiliate_url, timestamp, timestamp, product_id),
    )
    return product_id


def generate_bulk_links(raw_text: str, affiliate_id: str, sub_tag: str = "default", conn=None) -> List[BulkAffiliateResult]:
    """Generate up to 500 links; invalid rows do not fail the whole batch."""
    affiliate_id = _validate_affiliate_id(affiliate_id)
    rows = [line.strip() for line in str(raw_text or "").splitlines() if line.strip()]
    if not rows:
        raise BulkAffiliateError("Dán ít nhất 1 URL sản phẩm Shopee.")
    if len(rows) > MAX_BULK_URLS:
        raise BulkAffiliateError(f"Mỗi lần chỉ xử lý tối đa {MAX_BULK_URLS} URL.")

    results: List[BulkAffiliateResult] = []
    seen = {}
    for input_url in rows:
        try:
            product_url, item_id = _normalize_product_url(input_url)
            previous = seen.get(product_url)
            if previous is not None:
                results.append(BulkAffiliateResult(
                    input_url=input_url,
                    status="DUPLICATE",
                    product_url=product_url,
                    external_product_id=item_id,
                    affiliate_url=previous.affiliate_url,
                    sub_id=previous.sub_id,
                    product_id=previous.product_id,
                ))
                continue

            affiliate_url, sub_id = build_affiliate_url(product_url, affiliate_id, sub_tag)
            product_id = _link_existing_product(conn, item_id, affiliate_url)
            result = BulkAffiliateResult(
                input_url=input_url,
                status="LINKED" if product_id else "CREATED",
                product_url=product_url,
                external_product_id=item_id,
                affiliate_url=affiliate_url,
                sub_id=sub_id,
                product_id=product_id,
            )
            seen[product_url] = result
            results.append(result)
        except BulkAffiliateError as exc:
            results.append(BulkAffiliateResult(input_url=input_url, status="ERROR", error=str(exc)))
    return results
