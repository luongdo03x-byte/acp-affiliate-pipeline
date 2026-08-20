"""Phase 3 Shopee metadata-cache and refresh web composition.

The legacy Shopee import routes stay in ``web.server``.  This module composes a
cache-aware source factory around them, exposes small cache/refresh endpoints,
and finalizes product cache/history only after a successful operator-confirmed
create action.  It never creates posts or publish jobs itself.
"""
from urllib.parse import urlsplit

from flask import jsonify, request

from ..adapters.shopee_affiliate import AffiliateImportError, ProductMetadata
from ..core.db import connect
from ..core.shopee_products import (
    CACHE_SOURCES,
    ShopeeProductError,
    finalize_confirmed_product,
    get_metadata_cache,
    put_metadata_cache,
)


CACHE_ROUTE = "/sanpham/affiliate/cache"
REFRESH_ROUTE = "/sanpham/affiliate/refresh-price"
CREATE_ROUTE = "/sanpham/affiliate/create"


def _usable(metadata) -> bool:
    return bool(metadata and any((
        getattr(metadata, "name", None),
        getattr(metadata, "current_price", None),
        getattr(metadata, "image_url", None),
        getattr(metadata, "shop", None),
    )))


def _metadata_dict(metadata) -> dict:
    return {
        "name": getattr(metadata, "name", None),
        "current_price": getattr(metadata, "current_price", None),
        "original_price": getattr(metadata, "original_price", None),
        "image_url": getattr(metadata, "image_url", None),
        "shop": getattr(metadata, "shop", None),
    }


def _cached_payload(cached, status: str) -> dict:
    payload = {
        "status": status,
        "source": cached.source,
        "observed_at": cached.observed_at,
        "is_fresh": cached.is_fresh,
    }
    if cached.is_fresh:
        payload["metadata"] = _metadata_dict(cached.as_product_metadata())
    return payload


class CacheAwareShopeeSource:
    """Delegate the manual Shopee source while caching/falling back metadata."""

    def __init__(self, base_source, connection_factory=connect):
        self._base = base_source
        self._connect = connection_factory
        self.name = getattr(base_source, "name", "manual_shopee")

    def __getattr__(self, name):
        return getattr(self._base, name)

    def _fresh_cache(self, product_url):
        conn = self._connect()
        try:
            cached = get_metadata_cache(conn, product_url)
            return cached if cached and cached.is_fresh else None
        finally:
            conn.close()

    def _cache_server_metadata(self, product_url, metadata):
        conn = self._connect()
        try:
            put_metadata_cache(conn, product_url, metadata, "server")
        finally:
            conn.close()

    def metadata(self, product_url):
        try:
            metadata = self._base.metadata(product_url)
        except AffiliateImportError:
            cached = self._fresh_cache(product_url)
            if cached:
                return cached.as_product_metadata()
            raise

        if _usable(metadata):
            try:
                self._cache_server_metadata(product_url, metadata)
            except ShopeeProductError:
                # Keep legacy resolve usable if a provider field is malformed;
                # the confirmation form/server validation still owns acceptance.
                pass
            return metadata

        cached = self._fresh_cache(product_url)
        return cached.as_product_metadata() if cached else metadata


def _safe_source(value) -> str:
    return value if value in CACHE_SOURCES else "manual"


def _form_metadata() -> ProductMetadata:
    try:
        current_price = int(request.form.get("current_price", ""))
    except (TypeError, ValueError):
        current_price = None
    try:
        original_raw = request.form.get("original_price", "")
        original_price = int(original_raw) if original_raw not in (None, "") else None
    except (TypeError, ValueError):
        original_price = None
    return ProductMetadata(
        name=(request.form.get("name", "") or "").strip() or None,
        current_price=current_price,
        original_price=original_price,
        image_url=(request.form.get("image_url", "") or "").strip() or None,
        shop=(request.form.get("shop", "") or "").strip() or None,
    )


def _redirects_to_review(response) -> bool:
    if response.status_code not in (301, 302, 303, 307, 308):
        return False
    location = response.headers.get("Location", "")
    try:
        return urlsplit(location).path == "/duyet"
    except ValueError:
        return False


def register_shopee_product_intel(app) -> None:
    if app.config.get("SHOPEE_PRODUCT_INTEL_REGISTERED"):
        return
    app.config["SHOPEE_PRODUCT_INTEL_REGISTERED"] = True

    base_factory = app.config["SHOPEE_SOURCE_FACTORY"]

    def cache_aware_source_factory():
        return CacheAwareShopeeSource(base_factory(), connect)

    app.config["SHOPEE_SOURCE_FACTORY"] = cache_aware_source_factory

    @app.get(CACHE_ROUTE)
    def shopee_metadata_cache_status():
        product_url = request.args.get("product_url", "").strip()
        conn = connect()
        try:
            cached = get_metadata_cache(conn, product_url)
        except ShopeeProductError:
            return jsonify(status="invalid"), 400
        finally:
            conn.close()
        if not cached:
            return jsonify(status="miss")
        return jsonify(_cached_payload(cached, "fresh" if cached.is_fresh else "stale"))

    @app.post(REFRESH_ROUTE)
    def shopee_refresh_price():
        product_url = request.form.get("product_url", "").strip()
        source = app.config["SHOPEE_SOURCE_FACTORY"]()
        metadata = None
        try:
            metadata = source.metadata(product_url)
        except (AffiliateImportError, ShopeeProductError):
            metadata = None

        conn = connect()
        try:
            cached = get_metadata_cache(conn, product_url)
        except ShopeeProductError:
            conn.close()
            return jsonify(status="invalid"), 400
        finally:
            if 'conn' in locals():
                try:
                    conn.close()
                except Exception:
                    pass

        if _usable(metadata) and getattr(metadata, "current_price", None):
            # CacheAwareShopeeSource already cached true server metadata. If it
            # returned a cache fallback, preserve that cache's original source.
            if cached and cached.is_fresh:
                state = "server" if cached.source == "server" else "cache"
                return jsonify({
                    **_cached_payload(cached, state),
                    "message": "Giá được lấy từ server." if state == "server"
                               else "Đang dùng dữ liệu cache gần nhất; không phải realtime.",
                })
            return jsonify(status="server", metadata=_metadata_dict(metadata))

        if cached and cached.is_fresh:
            return jsonify({
                **_cached_payload(cached, "cache"),
                "message": "Đang dùng dữ liệu cache gần nhất; không phải realtime.",
            })
        return jsonify(
            status="helper_required",
            message="Không lấy được giá mới từ server/cache. Dùng Chrome Helper hoặc nhập thủ công.",
        )

    @app.after_request
    def shopee_finalize_confirmed_cache(response):
        if request.method != "POST" or request.path != CREATE_ROUTE or not _redirects_to_review(response):
            return response
        product_url = request.form.get("product_url", "").strip()
        metadata_source = _safe_source(request.form.get("metadata_source", "manual"))
        metadata = _form_metadata()
        conn = connect()
        try:
            finalize_confirmed_product(
                conn, product_url, metadata=metadata, metadata_source=metadata_source)
        except Exception as error:
            # Never log form payload / affiliate URL / provider response.
            app.logger.warning("Shopee product-intel finalize failed: error_type=%s",
                               type(error).__name__)
        finally:
            conn.close()
        return response
