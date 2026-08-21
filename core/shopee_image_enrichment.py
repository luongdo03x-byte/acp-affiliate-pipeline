"""Shopee Affiliate image-enrichment primitives.

This module owns only the bounded post-import image-enrichment lifecycle.  It
never logs in to Shopee, reads browser credentials, solves CAPTCHA, calls
private Shopee endpoints, creates posts, or publishes content.
"""
from __future__ import annotations

import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO

from PIL import Image

from ..adapters.safe_http import SafeHttpClient, SafeHttpError
from ..adapters.shopee_affiliate import (
    AffiliateImportError,
    ProductMetadata,
    ProductMetadataResolver,
)
from .db import now
from .shopee_helper import ShopeeHelperError, sanitize_helper_metadata
from .shopee_products import ShopeeProductError, identity_from_url


PROVIDER = "SHOPEE_AFFILIATE"
PENDING = "PENDING"
PUBLIC_FETCH = "PUBLIC_FETCH"
DOWNLOADING = "DOWNLOADING"
NEEDS_HELPER = "NEEDS_HELPER"
READY = "READY"
FAILED = "FAILED"
STATUSES = frozenset({PENDING, PUBLIC_FETCH, DOWNLOADING, NEEDS_HELPER, READY, FAILED})
TRANSIENT_STATUSES = frozenset({PUBLIC_FETCH, DOWNLOADING})
STALE_SECONDS = 10 * 60
MAX_PUBLIC_ATTEMPTS = 2
MAX_DOWNLOAD_ATTEMPTS = 2
MAX_BATCH_SIZE = 20
DEFAULT_DELAY_SECONDS = 1.5
MAX_IMAGE_BYTES = 8 * 1024 * 1024
_EXT_BY_FORMAT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}


class ShopeeImageEnrichmentError(ValueError):
    """Safe bounded enrichment error suitable for operator-facing handling."""

    def __init__(self, code: str, message: str):
        self.code = str(code)
        self.user_message = str(message)
        super().__init__(self.user_message)


def _product(conn, product_id: str):
    return conn.execute("SELECT * FROM product WHERE id=?", (str(product_id),)).fetchone()


def _has_product_image(product) -> bool:
    if product is None:
        return False
    if str(product["main_image_url"] or "").strip():
        return True
    local_path = str(product["image_path_local"] or "").strip()
    return bool(local_path and os.path.isfile(local_path))


def _eligible_identity(product):
    if product is None or str(product["provider"] or "") != PROVIDER:
        return None
    try:
        return identity_from_url(product["product_url"])
    except (ShopeeProductError, TypeError, ValueError) as exc:
        raise ShopeeImageEnrichmentError(
            "PRODUCT_IDENTITY_INVALID",
            "Link sản phẩm Shopee không có shop/item hợp lệ để enrich ảnh.",
        ) from exc


def get_job(conn, product_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM shopee_image_enrichment_job WHERE product_id=?",
        (str(product_id),),
    ).fetchone()
    return dict(row) if row is not None else None


def enqueue_product(conn, product_id: str) -> str | None:
    """Idempotently enroll one eligible Shopee Affiliate Product."""
    product = _product(conn, product_id)
    if product is None or str(product["provider"] or "") != PROVIDER:
        return None
    _eligible_identity(product)

    desired = READY if _has_product_image(product) else PENDING
    timestamp = now()
    existing = get_job(conn, product_id)
    if existing is None:
        conn.execute(
            """INSERT INTO shopee_image_enrichment_job (
                 product_id, status, attempt_count, download_attempt_count,
                 last_error_code, last_error, last_attempt_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (product_id, desired, 0, 0, None, None, None, timestamp, timestamp),
        )
        return desired

    if desired == READY and existing["status"] != READY:
        conn.execute(
            """UPDATE shopee_image_enrichment_job
               SET status=?, last_error_code=NULL, last_error=NULL, updated_at=?
               WHERE product_id=?""",
            (READY, timestamp, product_id),
        )
        return READY

    # Missing-image re-enqueue never resets retry/error/helper state. Explicit
    # Retry owns that transition later; repeated CSV imports stay idempotent.
    return existing["status"]


def backfill_missing(conn, limit: int | None = None) -> int:
    """Enroll pre-feature Shopee Products that still have no usable image."""
    sql = "SELECT id FROM product WHERE provider=? ORDER BY created_at, id"
    params: list[object] = [PROVIDER]
    if limit is not None:
        safe_limit = max(0, int(limit))
        sql += " LIMIT ?"
        params.append(safe_limit)

    count = 0
    for row in conn.execute(sql, tuple(params)).fetchall():
        product = _product(conn, row["id"])
        if _has_product_image(product):
            continue
        status = enqueue_product(conn, row["id"])
        if status is not None:
            count += 1
    return count


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def recover_stale_jobs(conn, *, now_dt: datetime | None = None) -> int:
    """Recover transient work left behind by a process restart/crash."""
    current = now_dt or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    threshold = current - timedelta(seconds=STALE_SECONDS)
    recovered = 0

    rows = conn.execute(
        "SELECT * FROM shopee_image_enrichment_job WHERE status IN (?,?)",
        (PUBLIC_FETCH, DOWNLOADING),
    ).fetchall()
    for row in rows:
        updated = _parse_timestamp(row["updated_at"])
        if updated is not None and updated > threshold:
            continue
        product = _product(conn, row["product_id"])
        status = READY if _has_product_image(product) else PENDING
        conn.execute(
            """UPDATE shopee_image_enrichment_job
               SET status=?, last_error_code=NULL, last_error=NULL, updated_at=?
               WHERE product_id=?""",
            (status, now(), row["product_id"]),
        )
        recovered += 1
    return recovered


def _verified_extension(data: bytes) -> str:
    try:
        probe = Image.open(BytesIO(data))
        fmt = (probe.format or "").upper()
        probe.verify()
    except Exception as exc:
        raise ShopeeImageEnrichmentError(
            "IMAGE_DECODE_FAILED",
            "Dữ liệu tải về không phải ảnh sản phẩm hợp lệ.",
        ) from exc
    ext = _EXT_BY_FORMAT.get(fmt)
    if not ext:
        raise ShopeeImageEnrichmentError(
            "IMAGE_INVALID_CONTENT",
            "Định dạng ảnh sản phẩm chưa được hỗ trợ.",
        )
    return ext


def _valid_existing_image(path: str) -> bool:
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as fh:
            _verified_extension(fh.read())
    except (OSError, ShopeeImageEnrichmentError):
        return False
    return True


def _safe_image_url(image_url: str) -> str:
    try:
        value = sanitize_helper_metadata({"image_url": image_url}).get("image_url")
    except ShopeeHelperError as exc:
        raise ShopeeImageEnrichmentError(
            "IMAGE_INVALID_CONTENT",
            "URL ảnh sản phẩm không hợp lệ.",
        ) from exc
    if not value:
        raise ShopeeImageEnrichmentError(
            "IMAGE_INVALID_CONTENT",
            "Thiếu URL ảnh sản phẩm.",
        )
    return value


def _publish_local_image(storage_backend, local_path: str) -> str:
    try:
        public_url = storage_backend.put(local_path)
    except Exception as exc:
        raise ShopeeImageEnrichmentError(
            "STORAGE_FAILED",
            "Ảnh đã tải về nhưng chưa thể đưa lên vùng lưu trữ ACP.",
        ) from exc
    public_url = str(public_url or "").strip()
    if not public_url:
        raise ShopeeImageEnrichmentError(
            "STORAGE_FAILED",
            "Vùng lưu trữ ACP không trả URL ảnh công khai.",
        )
    return public_url


def materialize_product_image(
    product_url: str,
    image_url: str,
    media_dir: str,
    storage_backend,
    *,
    http_client=None,
) -> dict:
    """Download, decode, atomically store, and publish one Shopee product image."""
    try:
        identity = identity_from_url(product_url)
    except (ShopeeProductError, TypeError, ValueError) as exc:
        raise ShopeeImageEnrichmentError(
            "PRODUCT_IDENTITY_INVALID",
            "Link sản phẩm Shopee không hợp lệ để lưu ảnh.",
        ) from exc
    safe_url = _safe_image_url(image_url)
    media_dir = os.path.abspath(media_dir)
    os.makedirs(media_dir, exist_ok=True)
    stem = f"shopee_{identity.shop_id}_{identity.item_id}"

    # Reuse only the exact deterministic final names. A valid-looking stale
    # NamedTemporaryFile from a crashed process must never become Product media.
    for ext in _EXT_BY_FORMAT.values():
        existing = os.path.join(media_dir, stem + ext)
        if _valid_existing_image(existing):
            return {
                "image_url_original": safe_url,
                "image_path_local": existing,
                "main_image_url": _publish_local_image(storage_backend, existing),
            }

    client = http_client or SafeHttpClient(max_bytes=MAX_IMAGE_BYTES)
    try:
        response = client.get(
            safe_url,
            allowed_hosts=None,
            expected_content_prefix="image/",
        )
    except SafeHttpError as exc:
        message = str(exc)
        if "giới hạn kích thước" in message:
            code = "IMAGE_TOO_LARGE"
        elif "Content-Type" in message:
            code = "IMAGE_INVALID_CONTENT"
        else:
            code = "IMAGE_DOWNLOAD_FAILED"
        raise ShopeeImageEnrichmentError(code, "Không thể tải ảnh sản phẩm an toàn.") from exc
    except OSError as exc:
        raise ShopeeImageEnrichmentError(
            "IMAGE_DOWNLOAD_FAILED",
            "Không thể tải ảnh sản phẩm an toàn.",
        ) from exc

    if not str(response.content_type or "").lower().startswith("image/"):
        raise ShopeeImageEnrichmentError(
            "IMAGE_INVALID_CONTENT",
            "URL ảnh không trả nội dung hình ảnh.",
        )
    ext = _verified_extension(response.content)
    target = os.path.join(media_dir, stem + ext)

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=media_dir,
            prefix=stem + ".",
            suffix=".tmp",
            delete=False,
        ) as fh:
            temp_path = fh.name
            fh.write(response.content)
        if not _valid_existing_image(temp_path):
            raise ShopeeImageEnrichmentError(
                "IMAGE_DECODE_FAILED",
                "File ảnh tạm không thể xác thực sau khi ghi.",
            )
        os.replace(temp_path, target)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    return {
        "image_url_original": safe_url,
        "image_path_local": target,
        "main_image_url": _publish_local_image(storage_backend, target),
    }


def merge_metadata_into_product(
    conn,
    product_id: str,
    metadata: ProductMetadata,
    materialized: dict | None = None,
) -> None:
    """Fill only missing enrichment-owned Product fields."""
    product = _product(conn, product_id)
    if product is None:
        raise ShopeeImageEnrichmentError(
            "PRODUCT_IDENTITY_INVALID", "Không tìm thấy Product cần enrich."
        )
    _eligible_identity(product)

    values = {}
    metadata = metadata or ProductMetadata()
    materialized = materialized or {}

    if not str(product["name"] or "").strip() and str(metadata.name or "").strip():
        values["name"] = str(metadata.name).strip()
    if product["original_price"] is None and metadata.original_price:
        try:
            original = int(metadata.original_price)
        except (TypeError, ValueError):
            original = None
        if original and original > 0:
            values["original_price"] = original
    if not str(product["shop_name"] or "").strip() and str(metadata.shop or "").strip():
        values["shop_name"] = str(metadata.shop).strip()

    for column in ("image_url_original", "image_path_local", "main_image_url"):
        if not str(product[column] or "").strip() and str(materialized.get(column) or "").strip():
            values[column] = str(materialized[column]).strip()

    if not values:
        return
    values["updated_at"] = now()
    columns = list(values)
    conn.execute(
        "UPDATE product SET " + ", ".join(f"{column}=?" for column in columns) + " WHERE id=?",
        tuple(values[column] for column in columns) + (product_id,),
    )


def _set_job(
    conn,
    product_id: str,
    status: str,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    public_attempt_delta: int = 0,
    download_attempt_delta: int = 0,
) -> dict:
    if status not in STATUSES:
        raise ValueError("Invalid Shopee enrichment status")
    timestamp = now()
    conn.execute(
        """UPDATE shopee_image_enrichment_job
           SET status=?,
               attempt_count=attempt_count+?,
               download_attempt_count=download_attempt_count+?,
               last_error_code=?, last_error=?, last_attempt_at=?, updated_at=?
           WHERE product_id=?""",
        (
            status,
            int(public_attempt_delta),
            int(download_attempt_delta),
            error_code,
            error_message,
            timestamp,
            timestamp,
            product_id,
        ),
    )
    return get_job(conn, product_id)


def _result(conn, product_id: str) -> dict:
    job = get_job(conn, product_id) or {}
    return {
        "product_id": product_id,
        "status": job.get("status"),
        "attempt_count": int(job.get("attempt_count", 0) or 0),
        "download_attempt_count": int(job.get("download_attempt_count", 0) or 0),
        "error_code": job.get("last_error_code"),
        "error": job.get("last_error"),
    }


def reset_for_retry(conn, product_id: str) -> str | None:
    product = _product(conn, product_id)
    if product is None or str(product["provider"] or "") != PROVIDER:
        return None
    _eligible_identity(product)
    enqueue_product(conn, product_id)
    if _has_product_image(product):
        _set_job(conn, product_id, READY)
        return READY
    timestamp = now()
    conn.execute(
        """UPDATE shopee_image_enrichment_job
           SET status=?, attempt_count=0, download_attempt_count=0,
               last_error_code=NULL, last_error=NULL, last_attempt_at=NULL, updated_at=?
           WHERE product_id=?""",
        (PENDING, timestamp, product_id),
    )
    return PENDING


def _resolve_public(metadata_resolver, product_url: str) -> ProductMetadata:
    resolver = getattr(metadata_resolver, "resolve_public", None)
    if not callable(resolver):
        raise ShopeeImageEnrichmentError(
            "PUBLIC_BLOCKED",
            "Bộ đọc metadata Shopee không hỗ trợ public HTML.",
        )
    return resolver(product_url)


def _download_with_budget(
    conn,
    product,
    product_id: str,
    metadata: ProductMetadata,
    *,
    media_dir: str,
    storage_backend,
    image_http=None,
) -> dict:
    job = get_job(conn, product_id)
    remaining = max(0, MAX_DOWNLOAD_ATTEMPTS - int(job["download_attempt_count"] or 0))
    last_error = None
    for _ in range(remaining):
        _set_job(conn, product_id, DOWNLOADING, download_attempt_delta=1)
        try:
            materialized = materialize_product_image(
                product["product_url"],
                metadata.image_url,
                media_dir,
                storage_backend,
                http_client=image_http,
            )
        except ShopeeImageEnrichmentError as exc:
            last_error = exc
            continue
        merge_metadata_into_product(conn, product_id, metadata, materialized)
        _set_job(conn, product_id, READY)
        return _result(conn, product_id)

    if last_error is None:
        last_error = ShopeeImageEnrichmentError(
            "IMAGE_DOWNLOAD_FAILED",
            "Đã hết số lần thử tải ảnh tự động.",
        )
    _set_job(
        conn,
        product_id,
        FAILED,
        error_code=last_error.code,
        error_message=last_error.user_message,
    )
    return _result(conn, product_id)


def enrich_product(
    conn,
    product_id: str,
    *,
    metadata_resolver,
    media_dir: str,
    storage_backend,
    image_http=None,
    retry_delay_seconds: float = 0.0,
    sleep_fn=time.sleep,
) -> dict:
    """Run one bounded public-HTML enrichment cycle for a Product."""
    product = _product(conn, product_id)
    if product is None or str(product["provider"] or "") != PROVIDER:
        raise ShopeeImageEnrichmentError(
            "PRODUCT_IDENTITY_INVALID",
            "Product không thuộc Shopee Affiliate.",
        )
    _eligible_identity(product)
    enqueue_product(conn, product_id)
    if _has_product_image(product):
        _set_job(conn, product_id, READY)
        return _result(conn, product_id)

    job = get_job(conn, product_id)
    if job["status"] == FAILED:
        return _result(conn, product_id)
    if job["status"] == NEEDS_HELPER:
        return _result(conn, product_id)

    remaining = max(0, MAX_PUBLIC_ATTEMPTS - int(job["attempt_count"] or 0))
    metadata = None
    for attempt_index in range(remaining):
        _set_job(conn, product_id, PUBLIC_FETCH, public_attempt_delta=1)
        try:
            metadata = _resolve_public(metadata_resolver, product["product_url"])
        except (AffiliateImportError, ShopeeImageEnrichmentError, OSError):
            if attempt_index < remaining - 1 and float(retry_delay_seconds) > 0:
                sleep_fn(float(retry_delay_seconds))
            continue
        if not getattr(metadata, "image_url", None):
            _set_job(
                conn,
                product_id,
                NEEDS_HELPER,
                error_code="PUBLIC_NO_IMAGE",
                error_message="Trang Shopee công khai không cung cấp ảnh sản phẩm.",
            )
            return _result(conn, product_id)
        break

    if metadata is None or not getattr(metadata, "image_url", None):
        _set_job(
            conn,
            product_id,
            NEEDS_HELPER,
            error_code="PUBLIC_BLOCKED",
            error_message="Không đọc được ảnh từ trang Shopee công khai; cần Chrome Helper.",
        )
        return _result(conn, product_id)

    return _download_with_budget(
        conn,
        product,
        product_id,
        metadata,
        media_dir=media_dir,
        storage_backend=storage_backend,
        image_http=image_http,
    )


def complete_from_helper(
    conn,
    product_id: str,
    metadata: dict,
    *,
    media_dir: str,
    storage_backend,
    image_http=None,
) -> dict:
    """Complete a job after existing product-bound Helper validation succeeded."""
    product = _product(conn, product_id)
    if product is None or str(product["provider"] or "") != PROVIDER:
        raise ShopeeImageEnrichmentError(
            "PRODUCT_IDENTITY_INVALID",
            "Product không thuộc Shopee Affiliate.",
        )
    _eligible_identity(product)
    enqueue_product(conn, product_id)
    try:
        clean = sanitize_helper_metadata(metadata)
    except ShopeeHelperError as exc:
        _set_job(
            conn,
            product_id,
            NEEDS_HELPER,
            error_code="HELPER_REQUIRED",
            error_message="Metadata Chrome Helper không hợp lệ.",
        )
        raise ShopeeImageEnrichmentError(
            "HELPER_REQUIRED",
            "Metadata Chrome Helper không hợp lệ.",
        ) from exc
    if not clean.get("image_url"):
        _set_job(
            conn,
            product_id,
            NEEDS_HELPER,
            error_code="HELPER_REQUIRED",
            error_message="Chrome Helper chưa trả ảnh sản phẩm.",
        )
        return _result(conn, product_id)

    helper_metadata = ProductMetadata(
        name=clean.get("name"),
        current_price=clean.get("current_price"),
        original_price=clean.get("original_price"),
        image_url=clean.get("image_url"),
        shop=clean.get("shop"),
    )
    return _download_with_budget(
        conn,
        product,
        product_id,
        helper_metadata,
        media_dir=media_dir,
        storage_backend=storage_backend,
        image_http=image_http,
    )


def _default_media_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "var", "media")


def run_batch(
    connection_factory,
    *,
    limit: int = MAX_BATCH_SIZE,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    metadata_resolver_factory=None,
    image_http_factory=None,
    media_dir: str | None = None,
    storage_backend=None,
    sleep_fn=time.sleep,
) -> dict:
    """Process a deterministic operator-triggered batch, never more than 20."""
    from . import storage

    safe_limit = min(MAX_BATCH_SIZE, max(0, int(limit)))
    media_root = media_dir or _default_media_dir()
    backend = storage_backend or storage.get_storage()
    selector = connection_factory()
    try:
        recover_stale_jobs(selector)
        rows = selector.execute(
            """SELECT product_id FROM shopee_image_enrichment_job
               WHERE status=? ORDER BY created_at, product_id LIMIT ?""",
            (PENDING, safe_limit),
        ).fetchall()
        product_ids = [row["product_id"] for row in rows]
    finally:
        selector.close()

    summary = {
        "processed": 0,
        "ready": 0,
        "needs_helper": 0,
        "failed": 0,
        "pending": 0,
    }
    for index, product_id in enumerate(product_ids):
        conn = connection_factory()
        try:
            resolver = metadata_resolver_factory() if metadata_resolver_factory else ProductMetadataResolver()
            image_http = image_http_factory() if image_http_factory else SafeHttpClient(max_bytes=MAX_IMAGE_BYTES)
            try:
                result = enrich_product(
                    conn,
                    product_id,
                    metadata_resolver=resolver,
                    media_dir=media_root,
                    storage_backend=backend,
                    image_http=image_http,
                    retry_delay_seconds=max(0.0, float(delay_seconds)),
                    sleep_fn=sleep_fn,
                )
            except Exception:
                # Keep a provider/parser bug isolated to this Product. Never
                # persist exception text or stack trace because upstream errors
                # may contain request/provider details.
                _set_job(
                    conn,
                    product_id,
                    FAILED,
                    error_code="ENRICHMENT_FAILED",
                    error_message="Không thể enrich ảnh sản phẩm này.",
                )
                result = _result(conn, product_id)
        finally:
            conn.close()

        summary["processed"] += 1
        if result["status"] == READY:
            summary["ready"] += 1
        elif result["status"] == NEEDS_HELPER:
            summary["needs_helper"] += 1
        elif result["status"] == FAILED:
            summary["failed"] += 1
        else:
            summary["pending"] += 1
        if index < len(product_ids) - 1 and float(delay_seconds) > 0:
            sleep_fn(max(0.0, float(delay_seconds)))
    return summary


def list_products(conn, status: str | None = None, limit: int = 100) -> list[dict]:
    """Return Shopee Affiliate Products plus current enrichment status for UI."""
    safe_limit = min(500, max(1, int(limit)))
    rows = conn.execute(
        """SELECT p.*, j.status AS enrichment_status, j.last_error_code,
                  j.last_error, j.attempt_count, j.download_attempt_count
           FROM product p
           LEFT JOIN shopee_image_enrichment_job j ON j.product_id=p.id
           WHERE p.provider=?
           ORDER BY p.updated_at DESC, p.id DESC
           LIMIT ?""",
        (PROVIDER, safe_limit),
    ).fetchall()
    result = [dict(row) for row in rows]
    normalized = str(status or "all").strip().lower()
    if normalized in ("", "all"):
        return result
    if normalized == "missing":
        return [row for row in result if row.get("enrichment_status") != READY]
    mapping = {
        "ready": READY,
        "needs_helper": NEEDS_HELPER,
        "failed": FAILED,
        "pending": PENDING,
    }
    desired = mapping.get(normalized)
    return [row for row in result if row.get("enrichment_status") == desired] if desired else result
