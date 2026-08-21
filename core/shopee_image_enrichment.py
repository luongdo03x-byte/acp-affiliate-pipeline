"""Shopee Affiliate image-enrichment primitives.

This module owns only the bounded post-import image-enrichment lifecycle.  It
never logs in to Shopee, reads browser credentials, solves CAPTCHA, calls
private Shopee endpoints, creates posts, or publishes content.
"""
from __future__ import annotations

import glob
import os
import tempfile
from datetime import datetime, timedelta, timezone
from io import BytesIO

from PIL import Image

from ..adapters.safe_http import SafeHttpClient, SafeHttpError
from ..adapters.shopee_affiliate import ProductMetadata
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

    # Missing-image re-enqueue never resets retry/error/helper state.  Explicit
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

    for existing in sorted(glob.glob(os.path.join(media_dir, stem + ".*"))):
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
        code = "IMAGE_TOO_LARGE" if "giới hạn kích thước" in message else "IMAGE_DOWNLOAD_FAILED"
        raise ShopeeImageEnrichmentError(
            code,
            "Không thể tải ảnh sản phẩm an toàn.",
        ) from exc
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
        # Verify the exact bytes written before replacing the deterministic target.
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
        raise ShopeeImageEnrichmentError("PRODUCT_IDENTITY_INVALID", "Không tìm thấy Product cần enrich.")
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
