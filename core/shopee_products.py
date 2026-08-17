"""Shopee product cache and confirmed-product primitives.

This module owns only Shopee-specific product intelligence.  It does not crawl,
create posts, create affiliate links, or publish.  Cache rows are keyed by the
canonical Shopee `(shop_id, item_id)` identity and always carry an observation
source/timestamp so callers can distinguish cached data from realtime data.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from ..adapters.shopee_affiliate import ProductMetadata
from .db import now
from .shopee_helper import ShopeeHelperError, canonical_helper_product, sanitize_helper_metadata


CACHE_TTL_SECONDS = 24 * 60 * 60
CACHE_SOURCES = frozenset({"server", "helper", "manual"})


class ShopeeProductError(ValueError):
    """Safe validation error for Shopee product-intelligence operations."""


@dataclass(frozen=True)
class ShopeeIdentity:
    shop_id: str
    item_id: str
    canonical_url: str


@dataclass(frozen=True)
class CachedShopeeMetadata:
    shop_id: str
    item_id: str
    product_id: str | None
    name: str | None
    current_price: int | None
    original_price: int | None
    image_url: str | None
    shop: str | None
    source: str
    observed_at: str
    updated_at: str
    is_fresh: bool

    def as_product_metadata(self) -> ProductMetadata:
        return ProductMetadata(
            name=self.name,
            current_price=self.current_price,
            original_price=self.original_price,
            image_url=self.image_url,
            shop=self.shop,
        )


def identity_from_url(url: str) -> ShopeeIdentity:
    try:
        canonical, item_id = canonical_helper_product(url)
    except ShopeeHelperError as exc:
        raise ShopeeProductError(str(exc)) from exc
    parts = canonical.rstrip("/").split("/")
    if len(parts) < 2 or not parts[-2].isdigit() or not parts[-1].isdigit():
        raise ShopeeProductError("Không nhận diện được mã shop/item Shopee.")
    return ShopeeIdentity(shop_id=parts[-2], item_id=item_id, canonical_url=canonical)


def _parse_observed_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ShopeeProductError("Thời điểm metadata Shopee không hợp lệ.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_metadata(metadata: ProductMetadata) -> dict:
    values = {
        "name": getattr(metadata, "name", None),
        "current_price": getattr(metadata, "current_price", None),
        "original_price": getattr(metadata, "original_price", None),
        "image_url": getattr(metadata, "image_url", None),
        "shop": getattr(metadata, "shop", None),
    }
    try:
        clean = sanitize_helper_metadata(values)
    except ShopeeHelperError as exc:
        raise ShopeeProductError(str(exc)) from exc
    if not any(clean.get(field) not in (None, "")
               for field in ("name", "current_price", "image_url", "shop")):
        raise ShopeeProductError("Metadata Shopee không có dữ liệu hữu ích để cache.")
    return clean


def _cached_from_row(row, *, now_dt: datetime, max_age_seconds: int) -> CachedShopeeMetadata:
    observed = _parse_observed_at(row["observed_at"])
    current = now_dt
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    age_seconds = (current - observed).total_seconds()
    fresh = 0 <= age_seconds <= max(0, int(max_age_seconds))
    return CachedShopeeMetadata(
        shop_id=row["shop_id"],
        item_id=row["item_id"],
        product_id=row["product_id"],
        name=row["name"],
        current_price=row["current_price"],
        original_price=row["original_price"],
        image_url=row["image_url"],
        shop=row["shop_name"],
        source=row["source"],
        observed_at=row["observed_at"],
        updated_at=row["updated_at"],
        is_fresh=fresh,
    )


def put_metadata_cache(conn, product_url: str, metadata: ProductMetadata, source: str, *,
                       product_id: str | None = None, observed_at: str | None = None) -> CachedShopeeMetadata:
    if source not in CACHE_SOURCES:
        raise ShopeeProductError("Nguồn metadata Shopee không hợp lệ.")
    identity = identity_from_url(product_url)
    clean = _normalize_metadata(metadata)
    observed = observed_at or now()
    _parse_observed_at(observed)
    updated = now()

    conn.execute("""INSERT INTO shopee_metadata_cache (
                    shop_id, item_id, product_id, name, current_price, original_price,
                    image_url, shop_name, source, observed_at, updated_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(shop_id, item_id) DO UPDATE SET
                    product_id=COALESCE(excluded.product_id, shopee_metadata_cache.product_id),
                    name=excluded.name,
                    current_price=excluded.current_price,
                    original_price=excluded.original_price,
                    image_url=excluded.image_url,
                    shop_name=excluded.shop_name,
                    source=excluded.source,
                    observed_at=excluded.observed_at,
                    updated_at=excluded.updated_at""",
                 (identity.shop_id, identity.item_id, product_id,
                  clean["name"], clean["current_price"], clean["original_price"],
                  clean["image_url"], clean["shop"], source, observed, updated))
    return get_metadata_cache(conn, identity.canonical_url)


def get_metadata_cache(conn, product_url: str, *, max_age_seconds: int = CACHE_TTL_SECONDS,
                       now_dt: datetime | None = None) -> CachedShopeeMetadata | None:
    identity = identity_from_url(product_url)
    row = conn.execute("""SELECT shop_id, item_id, product_id, name, current_price,
                                 original_price, image_url, shop_name, source,
                                 observed_at, updated_at
                          FROM shopee_metadata_cache
                          WHERE shop_id=? AND item_id=?""",
                       (identity.shop_id, identity.item_id)).fetchone()
    if not row:
        return None
    current = now_dt or datetime.now(timezone.utc)
    return _cached_from_row(row, now_dt=current, max_age_seconds=max_age_seconds)
