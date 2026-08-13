"""Local ACCESSTRADE TikTok product catalog synchronization and selection."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
import hashlib
import json
import os
import sqlite3
from typing import Optional

from PIL import Image

from ..adapters.accesstrade_client import UnsupportedSortError, normalize_accesstrade_product
from ..adapters.safe_http import SafeHttpClient, SafeHttpError
from .db import now, transaction, ulid


PROVIDER = "ACCESSTRADE_TIKTOK"
LOCK_NAME = "accesstrade_tiktok"


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class SyncAlreadyRunning(Exception):
    """A friendly operator-facing response for an in-progress catalog sync."""

    def __init__(self):
        super().__init__("Đồng bộ sản phẩm ACCESSTRADE đang chạy; hãy thử lại sau")


class CatalogImageError(Exception):
    """Safe, operator-facing error when a catalog product image cannot be used."""


def materialize_catalog_image(conn, product, media_dir: str, *, http=None) -> str:
    """Download and validate the catalog image once before composing a sales post."""
    existing = product["image_path_local"]
    if existing and os.path.isfile(existing):
        return existing
    image_url = product["main_image_url"] or product["image_url_original"]
    if not image_url:
        raise CatalogImageError("Sản phẩm không có ảnh để tạo bài")
    client = http or SafeHttpClient(max_bytes=8 * 1024 * 1024)
    try:
        response = client.get(image_url, allowed_hosts=None, expected_content_prefix="image/")
        probe = Image.open(BytesIO(response.content))
        image_format = (probe.format or "").upper()
        probe.verify()
    except (SafeHttpError, OSError, ValueError):
        raise CatalogImageError("Không thể tải ảnh sản phẩm; hãy thử lại sau") from None

    extension = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}.get(image_format)
    if not extension:
        raise CatalogImageError("Định dạng ảnh sản phẩm không được hỗ trợ")
    source_dir = os.path.join(media_dir, "source")
    os.makedirs(source_dir, exist_ok=True)
    digest = hashlib.sha256(image_url.encode("utf-8")).hexdigest()[:24]
    path = os.path.abspath(os.path.join(source_dir, f"accesstrade-{digest}{extension}"))
    with open(path, "wb") as image_file:
        image_file.write(response.content)
    conn.execute("UPDATE product SET image_path_local=?, updated_at=? WHERE id=? AND provider=?",
                 (path, now(), product["id"], PROVIDER))
    return path


@dataclass
class SyncResult:
    pages: int = 0
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    warning: Optional[str] = None

    def operator_summary(self):
        return (f"Fetched: {self.fetched} | New: {self.inserted} | Updated: {self.updated} | "
                f"Skipped: {self.skipped} | Failed: {self.failed}")


MAX_BATCH_ITEMS_DEFAULT = 10


@dataclass
class ProductBatchResult:
    """Per-product outcome of a bulk catalog action -- never a single pass/fail flag.

    ``successes``/``skipped``/``failures`` are lists of small dicts with a
    ``product_id`` key plus a safe, operator-facing ``message``/``reason`` --
    never a raw provider exception (see ``_redacted_batch_error``)."""

    successes: list
    skipped: list
    failures: list

    @property
    def summary(self) -> str:
        return (f"{len(self.successes)} thành công, {len(self.skipped)} bỏ qua, "
                f"{len(self.failures)} lỗi")


def _dedupe_and_cap(product_ids, max_items):
    """Preserve input order, drop repeats, then cap -- overflow becomes a skip, not silence."""
    seen = set()
    selected = []
    for product_id in product_ids:
        if not product_id or product_id in seen:
            continue
        seen.add(product_id)
        selected.append(product_id)
    if len(selected) <= max_items:
        return selected, []
    overflow = selected[max_items:]
    skipped = [{"product_id": pid, "reason": f"Vượt giới hạn {max_items} sản phẩm mỗi lần xử lý"}
               for pid in overflow]
    return selected[:max_items], skipped


@dataclass
class ProductFilters:
    """Safe, local catalog filters shared by the later CLI and web callers."""

    keyword: Optional[str] = None
    title_keyword: Optional[str] = None
    shop_keyword: Optional[str] = None
    has_inventory: Optional[bool] = None
    min_commission_amount: Optional[int] = None
    max_commission_amount: Optional[int] = None
    min_commission_rate_percent: Optional[float] = None
    max_commission_rate_percent: Optional[float] = None
    min_price: Optional[int] = None
    max_price: Optional[int] = None
    min_units_sold: Optional[int] = None
    max_units_sold: Optional[int] = None
    affiliate_link_status: Optional[str] = None
    post_state: Optional[str] = None
    sort: str = "recommended"
    limit: Optional[int] = None

    @classmethod
    def from_mapping(cls, values):
        """Accept only declared attributes; callers never pass SQL fragments through."""
        values = values or {}
        return cls(**{key: values[key] for key in cls.__dataclass_fields__ if key in values})

    @classmethod
    def from_request(cls, request_or_values):
        """Translate query-string values once, keeping route code free of SQL concerns."""
        values = getattr(request_or_values, "args", request_or_values) or {}

        def value(*names):
            for name in names:
                item = values.get(name)
                if item not in (None, ""):
                    return item
            return None

        def integer(*names):
            item = value(*names)
            try:
                return int(item) if item is not None else None
            except (TypeError, ValueError):
                return None

        def number(*names):
            item = value(*names)
            try:
                return float(item) if item is not None else None
            except (TypeError, ValueError):
                return None

        inventory = value("inventory", "has_inventory")
        return cls(
            keyword=value("q", "keyword"),
            title_keyword=value("title", "title_keyword"),
            shop_keyword=value("shop", "shop_keyword"),
            has_inventory=(str(inventory).strip().lower() in ("1", "true", "yes", "on")
                           if inventory is not None else None),
            min_commission_amount=integer("min_commission_amount", "min_commission"),
            max_commission_amount=integer("max_commission_amount", "max_commission"),
            min_commission_rate_percent=number("min_commission_rate_percent", "min_commission_rate"),
            max_commission_rate_percent=number("max_commission_rate_percent", "max_commission_rate"),
            min_price=integer("min_price"), max_price=integer("max_price"),
            min_units_sold=integer("min_units_sold"), max_units_sold=integer("max_units_sold"),
            affiliate_link_status=value("affiliate_status", "affiliate_link_status"),
            post_state=value("post_state"), sort=value("sort") or "recommended", limit=integer("limit"),
        )


class ProductService:
    def __init__(self, conn, client):
        self.conn = conn
        self.client = client
        self._lock_lease = None

    def sync(self, *, title_keywords=None, sort_field="RECOMMENDED", max_pages=None):
        """Fetch bounded V2 pages, upsert the provider catalog, then refresh ranking."""
        max_pages = max_pages or env_int("ACP_PRODUCT_SYNC_MAX_PAGES", 10)
        result = SyncResult()
        token = None
        active_sort = sort_field
        fallback_used = False
        phase_pages = 0
        self._acquire_lock()
        try:
            while phase_pages < max(0, int(max_pages)):
                try:
                    rows, token = self.client.search_products(
                        sort_field=active_sort, limit=50, title_keywords=title_keywords,
                        page_token=token)
                except UnsupportedSortError:
                    if active_sort.upper() != "COMMISSION" or fallback_used:
                        raise
                    active_sort = "RECOMMENDED"
                    fallback_used = True
                    token = None
                    phase_pages = 0
                    result.warning = "ACCESSTRADE không hỗ trợ sắp xếp hoa hồng; đã dùng đề xuất"
                    continue

                phase_pages += 1
                result.pages += 1
                result.fetched += len(rows)
                for raw in rows:
                    try:
                        self._upsert(normalize_accesstrade_product(raw), result)
                    except sqlite3.DatabaseError:
                        raise
                    except (AttributeError, TypeError, ValueError):
                        result.failed += 1
                if not token:
                    break
            self.recalculate_scores()
            return result
        finally:
            self._release_lock()

    def _acquire_lock(self):
        lock_timeout = env_int("ACP_PRODUCT_SYNC_LOCK_SECONDS", 600)
        lease = f"{now()}|{ulid()}"
        with transaction(self.conn):
            existing = self.conn.execute(
                "SELECT locked_at FROM product_sync_lock WHERE name=?", (LOCK_NAME,)).fetchone()
            if existing and self._lock_is_fresh(existing["locked_at"], lock_timeout):
                raise SyncAlreadyRunning()
            if existing:
                self.conn.execute("UPDATE product_sync_lock SET locked_at=? WHERE name=?", (lease, LOCK_NAME))
            else:
                self.conn.execute("INSERT INTO product_sync_lock (name, locked_at) VALUES (?, ?)",
                                  (LOCK_NAME, lease))
        self._lock_lease = lease

    @staticmethod
    def _lock_is_fresh(locked_at, timeout):
        try:
            locked = datetime.fromisoformat(str(locked_at).split("|", 1)[0])
            if locked.tzinfo is None:
                locked = locked.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return True
        return locked >= datetime.now(timezone.utc) - timedelta(seconds=max(0, timeout))

    def _release_lock(self):
        if not self._lock_lease:
            return
        with transaction(self.conn):
            self.conn.execute("DELETE FROM product_sync_lock WHERE name=? AND locked_at=?",
                              (LOCK_NAME, self._lock_lease))
        self._lock_lease = None

    def _upsert(self, product, result):
        if not product.external_product_id:
            result.skipped += 1
            return

        seen_at = now()
        category_data = json.dumps(product.category_data, ensure_ascii=False) \
            if product.category_data is not None else None
        category_code = self._category_code(product.category_data)
        row = self.conn.execute(
            "SELECT id, affiliate_link_status, affiliate_link_error FROM product "
            "WHERE provider=? AND external_product_id=?",
            (PROVIDER, product.external_product_id)).fetchone()
        values = (
            product.title, product.shop_name, product.detail_link, product.main_image_url,
            product.sale_region, product.currency, product.price_min, product.price_max,
            product.original_price_min, product.original_price_max, product.commission_rate_raw,
            product.commission_rate_percent, product.commission_amount, product.commission_currency,
            product.units_sold, int(bool(product.has_inventory)) if product.has_inventory is not None else None,
            category_data, product.price_min or 0, product.commission_amount or 0,
            category_code, product.detail_link or "", product.main_image_url, seen_at, seen_at,
        )
        if row:
            recovered = (row["affiliate_link_status"] == "UNAVAILABLE"
                         and product.has_inventory is True and bool(product.detail_link))
            link_status = "NOT_CREATED" if recovered else row["affiliate_link_status"]
            link_error = None if recovered else row["affiliate_link_error"]
            self.conn.execute("""UPDATE product SET
                    name=?, shop_name=?, detail_link=?, main_image_url=?, sale_region=?, currency=?,
                    price_min=?, price_max=?, original_price_min=?, original_price_max=?,
                    commission_rate_raw=?, commission_rate_percent=?, commission_amount=?,
                    commission_currency=?, units_sold=?, has_inventory=?, category_data=?,
                    current_price=?, commission_value=?, category_code=?, product_url=?, image_url_original=?,
                    is_available=1, last_seen_at=?, last_synced_at=?, affiliate_link_status=?,
                    affiliate_link_error=?, updated_at=?
                WHERE id=?""", values + (link_status, link_error, seen_at, row["id"]))
            result.updated += 1
            return

        self.conn.execute("""INSERT INTO product (
                id, source, merchant, external_product_id, name, description, current_price,
                original_price, commission_value, commission_rate, category_code, rating, review_count,
                sold_count, image_url_original, image_path_local, product_url, is_available,
                last_seen_at, created_at, updated_at, provider, shop_name, detail_link, main_image_url,
                sale_region, currency, price_min, price_max, original_price_min, original_price_max,
                commission_rate_raw, commission_rate_percent, commission_amount, commission_currency,
                units_sold, has_inventory, category_data, first_seen_at, last_synced_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ulid(), "accesstrade_tiktok", product.shop_name or "ACCESSTRADE TikTok",
             product.external_product_id, product.title or product.external_product_id, "", product.price_min or 0,
             product.original_price_min, product.commission_amount or 0, product.commission_rate_percent,
             category_code, None, 0, product.units_sold or 0, product.main_image_url, None,
             product.detail_link or "", 1, seen_at, seen_at, seen_at, PROVIDER,
             product.shop_name, product.detail_link, product.main_image_url, product.sale_region,
             product.currency, product.price_min, product.price_max, product.original_price_min,
             product.original_price_max, product.commission_rate_raw, product.commission_rate_percent,
             product.commission_amount, product.commission_currency, product.units_sold,
             int(bool(product.has_inventory)) if product.has_inventory is not None else None,
             category_data, seen_at, seen_at))
        result.inserted += 1

    @staticmethod
    def _category_code(category_data):
        if isinstance(category_data, dict):
            return str(category_data.get("code") or category_data.get("name") or "accesstrade-tiktok")
        return str(category_data or "accesstrade-tiktok")

    def recalculate_scores(self):
        """Persist 0–100 percent-rank weighted catalog scores for eligible products."""
        rows = self.conn.execute("""SELECT id, units_sold, commission_rate_percent, commission_amount
                FROM product
                WHERE provider=? AND has_inventory=1 AND detail_link IS NOT NULL AND detail_link <> ''
                  AND external_product_id <> '' AND COALESCE(affiliate_link_status, '') <> 'UNAVAILABLE'""",
                                 (PROVIDER,)).fetchall()
        percentiles = {
            field: self._percentiles(rows, field)
            for field in ("units_sold", "commission_rate_percent", "commission_amount")
        }
        for row in rows:
            score = round(percentiles["units_sold"][row["id"]] * 45 +
                          percentiles["commission_rate_percent"][row["id"]] * 35 +
                          percentiles["commission_amount"][row["id"]] * 20, 2)
            self.conn.execute("UPDATE product SET score=? WHERE id=?", (score, row["id"]))

    @staticmethod
    def _percentiles(rows, field):
        values = sorted((row[field] if row[field] is not None else 0) for row in rows)
        denominator = len(values) - 1
        out = {}
        for row in rows:
            value = row[field] if row[field] is not None else 0
            below = sum(candidate < value for candidate in values)
            out[row["id"]] = (below / denominator) if denominator else 0.0
        return out

    def recommended(self, limit=20):
        cooldown = (datetime.now(timezone.utc) - timedelta(
            days=env_int("ACP_PRODUCT_REPOST_COOLDOWN_DAYS", 7))).isoformat(timespec="seconds")
        return self.conn.execute("""SELECT * FROM product
            WHERE provider=? AND has_inventory=1 AND detail_link IS NOT NULL AND detail_link <> ''
              AND external_product_id <> '' AND COALESCE(affiliate_link_status, '') <> 'UNAVAILABLE'
              AND (last_posted_at IS NULL OR last_posted_at < ?)
              AND NOT EXISTS (
                  SELECT 1 FROM post
                  WHERE post.product_id=product.id
                    AND post.post_type='SALES'
                    AND post.status IN ('DRAFT','PENDING_REVIEW','APPROVED','SCHEDULED')
              )
            ORDER BY COALESCE(score, 0) DESC, last_seen_at DESC
            LIMIT ?""", (PROVIDER, cooldown, max(0, int(limit)))).fetchall()

    def search_local(self, filters=None):
        filters = filters if isinstance(filters, ProductFilters) else ProductFilters.from_mapping(filters)
        where = ["provider=?"]
        params = [PROVIDER]
        self._add_keyword_filter(where, params, "name", filters.title_keyword)
        self._add_keyword_filter(where, params, "shop_name", filters.shop_keyword)
        if filters.keyword:
            like = f"%{filters.keyword}%"
            where.append("(name LIKE ? COLLATE NOCASE OR shop_name LIKE ? COLLATE NOCASE)")
            params.extend((like, like))
        self._add_filter(where, params, "has_inventory", filters.has_inventory, bool_value=True)
        self._add_filter(where, params, "commission_amount", filters.min_commission_amount, operator=">=")
        self._add_filter(where, params, "commission_amount", filters.max_commission_amount, operator="<=")
        self._add_filter(where, params, "commission_rate_percent", filters.min_commission_rate_percent, operator=">=")
        self._add_filter(where, params, "commission_rate_percent", filters.max_commission_rate_percent, operator="<=")
        self._add_filter(where, params, "price_min", filters.min_price, operator=">=")
        self._add_filter(where, params, "price_min", filters.max_price, operator="<=")
        self._add_filter(where, params, "units_sold", filters.min_units_sold, operator=">=")
        self._add_filter(where, params, "units_sold", filters.max_units_sold, operator="<=")
        if filters.affiliate_link_status is not None:
            where.append("affiliate_link_status=?")
            params.append(filters.affiliate_link_status)
        if filters.post_state:
            state = filters.post_state.strip().lower()
            if state in ("posted", "has_posted"):
                where.append("last_posted_at IS NOT NULL")
            elif state in ("unposted", "not_posted"):
                where.append("last_posted_at IS NULL")
            elif state == "active":
                where.append("""EXISTS (SELECT 1 FROM post WHERE post.product_id=product.id
                              AND post.post_type='SALES'
                              AND post.status IN ('DRAFT','PENDING_REVIEW','APPROVED','SCHEDULED'))""")
            elif state == "no_active":
                where.append("""NOT EXISTS (SELECT 1 FROM post WHERE post.product_id=product.id
                              AND post.post_type='SALES'
                              AND post.status IN ('DRAFT','PENDING_REVIEW','APPROVED','SCHEDULED'))""")

        order_by = {
            "recommended": "COALESCE(score, 0) DESC, last_seen_at DESC",
            "score": "COALESCE(score, 0) DESC, last_seen_at DESC",
            "sold": "COALESCE(units_sold, 0) DESC, last_seen_at DESC",
            "commission": "COALESCE(commission_amount, 0) DESC, last_seen_at DESC",
            "price_asc": "price_min ASC, last_seen_at DESC",
            "price_desc": "price_min DESC, last_seen_at DESC",
            "newest": "COALESCE(first_seen_at, created_at) DESC, id DESC",
        }.get((filters.sort or "recommended").lower(), "COALESCE(score, 0) DESC, last_seen_at DESC")
        sql = f"SELECT * FROM product WHERE {' AND '.join(where)} ORDER BY {order_by}"
        if filters.limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(filters.limit)))
        return self.conn.execute(sql, params).fetchall()

    @staticmethod
    def _add_keyword_filter(where, params, column, value):
        if value:
            where.append(f"{column} LIKE ? COLLATE NOCASE")
            params.append(f"%{value}%")

    @staticmethod
    def _add_filter(where, params, column, value, operator="=", bool_value=False):
        if value is not None:
            where.append(f"{column}{operator}?")
            params.append(int(bool(value)) if bool_value else value)

    def get(self, product_id):
        return self.conn.execute("SELECT * FROM product WHERE id=? AND provider=?", (product_id, PROVIDER)).fetchone()

    def _has_active_sales_post(self, product_id):
        return bool(self.conn.execute(
            """SELECT 1 FROM post WHERE product_id=? AND post_type='SALES'
               AND status IN ('DRAFT','PENDING_REVIEW','APPROVED','SCHEDULED') LIMIT 1""",
            (product_id,)).fetchone())

    def create_product_only_link(self, product_id: str, on_link_error=None) -> dict:
        """Create/refresh the copy-paste product-card link (sub1=``product:<external_id>``).

        Deliberately separate from post creation -- see ``create_posts()`` and
        ``core.pipeline.create_post_for_catalog_product`` -- so this link can never be
        reused for a Threads post's attribution."""
        product = self.get(product_id)
        if not product:
            return {"ok": False, "product_id": product_id, "error": "Không tìm thấy sản phẩm trong catalog"}
        if not product["has_inventory"] or not product["detail_link"]:
            return {"ok": False, "product_id": product_id,
                    "error": "Sản phẩm không đủ điều kiện tạo link affiliate"}
        try:
            link = self.client.create_product_link(
                product["detail_link"], post_id=f"product:{product['external_product_id']}",
                external_product_id=product["external_product_id"])
            full_url = getattr(link, "full_url", None)
            short_url = getattr(link, "short_url", None)
            if not (full_url or short_url):
                raise ValueError("empty product link")
        except Exception as error:
            if on_link_error is not None:
                on_link_error(error)
            return {"ok": False, "product_id": product_id,
                    "error": "Không thể tạo link affiliate cho sản phẩm"}
        linked_at = now()
        self.conn.execute("""UPDATE product
                        SET affiliate_url=?, affiliate_short_url=?, affiliate_link_status='PRODUCT_ONLY',
                            affiliate_link_error=NULL, affiliate_link_created_at=?, updated_at=?
                        WHERE id=? AND provider=?""",
                          (full_url or short_url, short_url, linked_at, linked_at, product_id, PROVIDER))
        return {"ok": True, "product_id": product_id, "url": full_url or short_url}

    def create_product_links(self, product_ids, max_items=MAX_BATCH_ITEMS_DEFAULT) -> ProductBatchResult:
        """Bulk product-card links -- one provider call per selected, eligible product."""
        selected, skipped = _dedupe_and_cap(product_ids, max_items)
        successes, failures = [], []
        for product_id in selected:
            result = self.create_product_only_link(product_id)
            if result["ok"]:
                successes.append({"product_id": product_id, "message": "Đã tạo link affiliate"})
            else:
                failures.append({"product_id": product_id, "reason": result["error"]})
        return ProductBatchResult(successes, skipped, failures)

    def create_posts(self, product_ids, ctx, campaign_code, channel_code=None,
                     max_items=MAX_BATCH_ITEMS_DEFAULT, on_link_error=None) -> ProductBatchResult:
        """Bulk review-post drafts -- each reuses the single-item pipeline op untouched, so
        attribution, image handling, caption generation, and the PENDING_REVIEW/DRAFT stop
        stay identical to a manual one-at-a-time click."""
        from . import pipeline  # deferred: pipeline.py imports this module at load time

        selected, skipped = _dedupe_and_cap(product_ids, max_items)
        successes, failures = [], []
        for product_id in selected:
            if self._has_active_sales_post(product_id):
                skipped.append({"product_id": product_id,
                                "reason": "Sản phẩm đã có bài đang chờ duyệt hoặc đã lên lịch"})
                continue
            result = pipeline.create_post_for_catalog_product(
                self.conn, ctx, product_id, campaign_code, channel_code,
                on_link_error=lambda error, pid=product_id: on_link_error(pid, error) if on_link_error else None)
            if result.get("ok"):
                successes.append({"product_id": product_id, "post_id": result.get("post_id"),
                                  "message": "Đã tạo bài chờ duyệt"})
            else:
                failures.append({"product_id": product_id,
                                 "reason": result.get("error") or "Không thể tạo bài"})
        return ProductBatchResult(successes, skipped, failures)
