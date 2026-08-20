"""Giao diện quản trị + endpoint công khai.

Ba thay đổi bảo mật so với bản trước:
  1. Dashboard yêu cầu đăng nhập (trước đây public hoàn toàn qua ngrok).
  2. Form POST có CSRF token.
  3. Webhook cần khoá bí mật trên URL.

Và một sửa lỗi: /vanhanh/work trước đây khởi tạo thẳng MockThreads, nên bấm nút
trên web vẫn chạy giả lập dù ACP_ADAPTER=live. Giờ dùng chung factory với CLI.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix

from ..adapters import factory
from ..adapters.accesstrade_client import AccessTradeClient
from ..adapters.base import AuthError
from ..adapters.shopee_affiliate import (
    AffiliateImportError, ConfirmedProductInput, ManualShopeeSource,
    ProductMetadata, ResolvedAffiliateUrl, metadata_state,
)
from ..core import attribution, auto_scheduler, content, helper_pairing, jobs, media_library, pipeline, scoring, storage
from ..core import connections
from ..core import content_checker, content_engine, content_facts, content_hook, content_platform, content_scoring, content_variant
from ..core.db import connect, now
from ..core.system_settings import PUBLISH_WORKER_ENABLED, publish_worker_enabled, set_system_setting
from ..core.products import ProductFilters, ProductService, SyncAlreadyRunning
from .threads_oauth import register_threads_channel_oauth_routes

MEDIA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "var", "media")

# Đường không cần đăng nhập. Media phải mở vì Meta tự tải ảnh về khi publish.
# /api/helper/ cũng public VÌ SAO: ACP Shopee Helper (Chrome extension) không
# mang session cookie của người vận hành -- nó tự bảo vệ bằng token một lần
# dùng + chỉ nhận request từ loopback (xem core/helper_pairing.py và route
# helper_submit() bên dưới), không phải bằng đăng nhập.
# /api/seeding/ tương tự: Chrome extension không mang dashboard session nhưng
# mọi endpoint tự bắt buộc ACP_SEEDING_EXTENSION_TOKEN trong Blueprint riêng.
PUBLIC_PREFIXES = ("/media/", "/webhook/", "/oauth/", "/dangnhap", "/static/", "/healthz", "/api/helper/", "/api/seeding/")


class ProductUserError(Exception):
    """A catalog message deliberately approved for display to an operator."""

    def __init__(self, user_message):
        self.user_message = user_message
        super().__init__(user_message)

PLATFORM_LABELS = {"threads": "Threads", "facebook": "Facebook", "instagram": "Instagram"}
_SLOT_RE = re.compile(r"^\d{2}:\d{2}$")
_AUTO_LIVE_STATUSES = ("SCHEDULED", "PENDING", "RUNNING")


def _fmt_vnd(v):
    try:
        return f"{int(v):,}".replace(",", ".") + "đ"
    except (TypeError, ValueError):
        return "0đ"


def _fmt_int(v):
    try:
        return f"{int(v):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "0"


def _safe_external_url(value):
    """Return only a browser-safe absolute HTTP(S) URL for catalog rendering."""
    text = str(value or "").strip()
    if not text or any(ord(character) < 32 for character in text):
        return None
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None
    if (parsed.scheme.lower() not in ("http", "https") or not parsed.netloc or
            parsed.username is not None or parsed.password is not None):
        return None
    return text


def validate_channel_automation_config(payload) -> dict:
    auto_schedule_enabled = 1 if str(payload.get("auto_schedule_enabled", "")).strip() in ("1", "true", "on") else 0

    try:
        daily_post_target = int(str(payload.get("daily_post_target", "")).strip())
        daily_post_cap = int(str(payload.get("daily_post_cap", "")).strip())
    except ValueError:
        return {"ok": False, "error": "Target và cap phải là số nguyên"}

    existing_cap = None
    try:
        if str(payload.get("existing_daily_post_cap", "")).strip():
            existing_cap = int(str(payload.get("existing_daily_post_cap", "")).strip())
    except ValueError:
        existing_cap = None
    cap_is_existing_legacy = existing_cap is not None and existing_cap > 3 and daily_post_cap == existing_cap

    if not (1 <= daily_post_target <= min(daily_post_cap, 3)):
        return {"ok": False, "error": "Cấu hình Auto phải thỏa 1 <= target <= min(cap, 3)"}
    if daily_post_cap > 3 and not cap_is_existing_legacy:
        return {"ok": False, "error": "Cap Auto mới tối đa là 3; cap legacy hiện có được giữ nguyên"}

    posting_timezone = str(payload.get("posting_timezone", "")).strip() or "Asia/Bangkok"
    try:
        ZoneInfo(posting_timezone)
    except ZoneInfoNotFoundError:
        return {"ok": False, "error": "Múi giờ không hợp lệ"}

    raw_slots = payload.get("posting_slots", [])
    if isinstance(raw_slots, str):
        raw_slots = [raw_slots]
    expanded_slots = []
    for raw_slot in raw_slots:
        for part in str(raw_slot).replace(",", "\n").splitlines():
            slot = part.strip()
            if slot:
                expanded_slots.append(slot)
    posting_slots = []
    seen = set()
    for slot in expanded_slots:
        if not _SLOT_RE.match(slot):
            return {"ok": False, "error": "Slot phải theo định dạng HH:MM"}
        hour, minute = (int(part) for part in slot.split(":", 1))
        if hour > 23 or minute > 59:
            return {"ok": False, "error": "Slot phải theo định dạng HH:MM"}
        if slot in seen:
            return {"ok": False, "error": "Slot bị trùng"}
        seen.add(slot)
        posting_slots.append(slot)

    if not (2 <= len(posting_slots) <= 3):
        return {"ok": False, "error": "Cần từ 2 đến 3 slot mỗi ngày"}

    return {
        "ok": True,
        "values": {
            "auto_schedule_enabled": auto_schedule_enabled,
            "daily_post_target": daily_post_target,
            "daily_post_cap": daily_post_cap,
            "posting_timezone": posting_timezone,
            "posting_slots": json.dumps(posting_slots, ensure_ascii=False),
        },
    }


def _parse_ops_now_utc(raw_value: str | None):
    text = str(raw_value or "").strip().replace(" ", "+")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _build_auto_ops_summary(conn, now_utc: datetime) -> dict:
    horizon_utc = now_utc + timedelta(hours=48)
    auto_channels = [dict(row) for row in conn.execute(
        """
        SELECT *
        FROM channel
        WHERE platform='threads'
          AND status='ACTIVE'
          AND COALESCE(enabled, 1)=1
          AND COALESCE(auto_schedule_enabled, 0)=1
        ORDER BY code
        """
    ).fetchall()]
    open_slots = sum(len(auto_scheduler.available_slots(conn, channel, now_utc)) for channel in auto_channels)

    placeholders = ",".join("?" for _ in _AUTO_LIVE_STATUSES)
    upcoming = []
    for row in conn.execute(
        f"""
        SELECT pt.channel_id, pt.status, pt.scheduled_at, ch.handle AS channel_handle, ch.code AS channel_code
        FROM publish_target pt
        JOIN channel ch ON ch.id = pt.channel_id
        WHERE pt.auto_scheduled = 1
          AND pt.scheduled_at IS NOT NULL
          AND pt.status IN ({placeholders})
        ORDER BY pt.scheduled_at ASC, ch.code ASC
        """,
        _AUTO_LIVE_STATUSES,
    ).fetchall():
        try:
            scheduled_at = datetime.fromisoformat(row["scheduled_at"]).astimezone(timezone.utc)
        except ValueError:
            continue
        if not (now_utc <= scheduled_at < horizon_utc):
            continue
        item = dict(row)
        item.pop("channel_code", None)
        upcoming.append(item)
        if len(upcoming) >= 12:
            break

    stale_reason_counts = {}
    for row in conn.execute(
        """
        SELECT detail
        FROM audit_log
        WHERE action='auto_stale_cancelled'
        ORDER BY id DESC
        LIMIT 50
        """
    ).fetchall():
        try:
            detail = json.loads(row["detail"] or "{}")
        except (TypeError, ValueError):
            detail = {}
        reason = str(detail.get("reason") or "unknown").strip() or "unknown"
        stale_reason_counts[reason] = stale_reason_counts.get(reason, 0) + 1

    return {
        "enabled_channels": len(auto_channels),
        "open_slots": open_slots,
        "upcoming_count": len(upcoming),
        "upcoming": upcoming,
        "stale_reasons": [
            {"reason": reason, "count": stale_reason_counts[reason]}
            for reason in sorted(stale_reason_counts)
        ],
    }


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    # Chạy sau ngrok (TLS kết thúc ở ngrok, chuyển tiếp về Flask bằng HTTP thô)
    # -- không có ProxyFix thì request.host_url/request.scheme luôn báo "http"
    # dù người dùng vào bằng URL https thật, khiến redirect_uri gửi cho OAuth
    # Meta/Threads sai lược đồ (Facebook từ chối thẳng "không dùng kết nối bảo
    # mật"). Chỉ tin đúng 1 lớp proxy (ngrok) cho từng header.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.jinja_env.filters["vnd"] = _fmt_vnd
    app.jinja_env.filters["num"] = _fmt_int

    app.secret_key = os.environ.get("ACP_SECRET_KEY") or secrets.token_hex(32)
    admin_password = os.environ.get("ACP_ADMIN_PASSWORD", "")
    webhook_secret = os.environ.get("ACP_WEBHOOK_SECRET", "")

    if os.environ.get("ACP_ENV") == "production":
        if not admin_password:
            raise RuntimeError("ACP_ADMIN_PASSWORD bắt buộc khi ACP_ENV=production — "
                               "dashboard sẽ bị lộ nếu chạy công khai mà không có mật khẩu")
        if not os.environ.get("ACP_SECRET_KEY"):
            raise RuntimeError("ACP_SECRET_KEY bắt buộc khi ACP_ENV=production — "
                               "thiếu nó thì mọi phiên đăng nhập mất khi khởi động lại")

    app.config["AUTH_ENABLED"] = bool(admin_password)
    app.config["LIVE"] = factory.is_live()
    # Test seam + provider boundary: manual Shopee must never be obtained through
    # the ACCESSTRADE source factory.
    app.config["SHOPEE_SOURCE_FACTORY"] = ManualShopeeSource

    # Bật LLM viết lại caption (nếu ACP_CAPTION_LLM có set) ngay ở đây, KHÔNG
    # chỉ trong factory.build_context() -- luồng nhập Shopee affiliate thủ
    # công (create_affiliate_product bên dưới) cố ý không gọi build_context()
    # để tránh khởi tạo nguồn ACCESSTRADE thật, nên nếu chỉ bật trong đó thì
    # luồng operator dùng hàng ngày sẽ không bao giờ thấy caption được viết
    # lại. content._llm_fn là biến module-level, set một lần ở đây là đủ cho
    # mọi route.
    content.set_llm(factory.get_caption_llm())
    register_threads_channel_oauth_routes(app, admin_password=admin_password)

    # Gắn LLM thật cho Content Engine v2 (G1) -- cùng lý do đặt ở
    # create_app() như dòng content.set_llm() ở trên: luồng nhập Shopee
    # affiliate thủ công không gọi build_context(), đặt ở đây đảm bảo
    # mọi route đều thấy. ACP_CONTENT_ENGINE_LLM=gemini bật, mặc định
    # tắt (None) -- toàn bộ E1-E6 giữ nguyên hành vi rule-based/template
    # khi không bật, không đổi baseline test hiện có.
    content_engine_llm = factory.get_content_engine_llm()
    content_facts.set_extractor(content_engine_llm)
    content_hook.set_hook_generator(content_engine_llm)
    content_hook.set_hook_judge(content_engine_llm)
    content_variant.set_body_generator(content_engine_llm)
    content_checker.set_variant_judge(content_engine_llm)
    content_scoring.set_hybrid_judge(content_engine_llm)

    # ------------------------------------------------------------ xác thực

    @app.before_request
    def guard():
        if any(request.path.startswith(p) for p in PUBLIC_PREFIXES):
            return None
        if not admin_password:
            return None  # dev cục bộ, không bật xác thực
        if not session.get("uid"):
            return redirect(url_for("login", next=request.path))
        if request.method == "POST":
            sent = request.form.get("_csrf", "")
            if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
                abort(400, "CSRF token không hợp lệ")
        return None

    @app.context_processor
    def inject_csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return {"csrf_token": session["csrf"], "auth_enabled": app.config["AUTH_ENABLED"]}

    @app.route("/dangnhap", methods=["GET", "POST"])
    def login():
        if not admin_password:
            return redirect(url_for("dashboard"))
        err = None
        if request.method == "POST":
            given = request.form.get("password", "")
            if hmac.compare_digest(hashlib.sha256(given.encode()).hexdigest(),
                                   hashlib.sha256(admin_password.encode()).hexdigest()):
                session["uid"] = "operator"
                session["csrf"] = secrets.token_urlsafe(32)
                return redirect(request.args.get("next") or url_for("dashboard"))
            err = "Mật khẩu không đúng."
        return render_template("login.html", page="dang-nhap", err=err)

    @app.route("/dangxuat", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/healthz")
    def healthz():
        return jsonify(ok=True, live=app.config["LIVE"])

    @app.route("/media/<path:name>")
    def media(name):
        return send_from_directory(MEDIA_DIR, name)

    # ---------------------------------------------------------- thư viện ảnh

    @app.route("/thuvien-anh")
    def media_library_page():
        conn = connect()
        assets = media_library.list_media_assets(conn)
        pending = conn.execute(
            "SELECT COUNT(*) FROM post WHERE status IN ('PENDING_REVIEW','DRAFT')").fetchone()[0]
        conn.close()
        return render_template("media_library.html", page="thu-vien-anh",
                               assets=assets, pending_review=pending)

    @app.route("/thuvien-anh/upload", methods=["POST"])
    def media_library_upload():
        file = request.files.get("image")
        url = request.form.get("image_url", "").strip()
        try:
            if file and file.filename:
                local_path = media_library.materialize_uploaded_file(file, MEDIA_DIR)
                source = "upload"
            elif url:
                local_path = media_library.materialize_external_image(url, MEDIA_DIR)
                source = "url"
            else:
                return redirect(url_for("media_library_page", err="Chọn file hoặc dán URL"))
        except media_library.MediaValidationError as exc:
            return redirect(url_for("media_library_page", err=str(exc)))
        conn = connect()
        media_library.create_media_asset(conn, local_path, source, storage.get_storage())
        conn.close()
        return redirect(url_for("media_library_page"))

    @app.route("/thuvien-anh/<asset_id>/xoa", methods=["POST"])
    def media_library_delete(asset_id):
        conn = connect()
        res = media_library.delete_media_asset(conn, asset_id)
        conn.close()
        return redirect(url_for("media_library_page", err=None if res["ok"] else res["error"]))

    # ----------------------------------------------------------- doanh thu

    @app.route("/")
    def dashboard():
        conn = connect()
        data = dict(
            funnel=attribution.funnel(conn),
            by_category=attribution.epc_by(conn, "category"),
            by_template=attribution.epc_by(conn, "template"),
            by_channel=attribution.epc_by(conn, "channel"),
            by_variant=attribution.epc_by(conn, "variant"),
            top=attribution.top_posts(conn, 12),
            pending_review=conn.execute(
                "SELECT COUNT(*) FROM post WHERE status IN ('PENDING_REVIEW','DRAFT')").fetchone()[0],
        )
        conn.close()
        return render_template("dashboard.html", page="doanh-thu", **data)

    # -------------------------------------------------------- chọn sản phẩm

    def _product_common_context():
        conn = connect()
        pending = conn.execute(
            "SELECT COUNT(*) FROM post WHERE status IN ('PENDING_REVIEW','DRAFT')").fetchone()[0]
        # D1: đa nền tảng -- bỏ lọc platform='threads', chỉ còn lọc kênh đang
        # dùng được (ACTIVE + enabled). Thêm enabled=1 (thiếu ở bản cũ) vì kênh
        # bị tắt ở /kenh thì không nên chọn được để tạo bài mới.
        channels = [dict(r) for r in conn.execute(
            "SELECT code, platform, handle FROM channel WHERE status='ACTIVE' AND enabled=1 "
            "ORDER BY platform, code").fetchall()]
        media_assets = media_library.list_media_assets(conn)
        # D4-A: nhóm chỉ dùng channel_codes (đủ so khớp checkbox), không cần
        # object channel đầy đủ ở đây -- khác /kenh nơi cần hiển thị chi
        # tiết từng thành viên.
        account_groups = [{"id": g["id"], "name": g["name"], "channel_codes": g["channel_codes"]}
                          for g in pipeline.list_account_groups(conn)]
        conn.close()
        return pending, channels, media_assets, account_groups

    def _render_affiliate(*, affiliate_url="", resolved=None, metadata=None,
                          err=None, warning=None, selected_channels=None, status=200):
        pending, channels, media_assets, account_groups = _product_common_context()
        meta = metadata or ProductMetadata()
        return render_template(
            "products.html", page="san-pham", mode="affiliate", items=[], q="", err=err,
            source_name="manual_shopee", pending_review=pending, channels=channels,
            affiliate_url=affiliate_url, resolved=resolved,
            metadata=meta, metadata_warning=warning,
            metadata_state=metadata_state(meta) if resolved else None,
            selected_channels=selected_channels or [], platform_labels=PLATFORM_LABELS,
            media_assets=media_assets, account_groups=account_groups,
        ), status

    def _catalog_summary(conn):
        row = conn.execute("""SELECT COUNT(*) AS count,
                                   COALESCE(SUM(CASE WHEN has_inventory=1 THEN 1 ELSE 0 END), 0) AS in_stock,
                                   COALESCE(SUM(CASE WHEN affiliate_link_status='READY' THEN 1 ELSE 0 END), 0) AS ready,
                                   MAX(last_synced_at) AS last_synced_at
                            FROM product
                            WHERE provider='ACCESSTRADE_TIKTOK'""").fetchone()
        return dict(row)

    @staticmethod
    def _sync_summary(result):
        return (f"Đã đồng bộ {result.fetched} sản phẩm "
                f"({result.inserted} mới, {result.updated} cập nhật, "
                f"{getattr(result, 'failed', 0)} lỗi).")

    def _log_catalog_failure(operation, error, *, product_id=None, status=None):
        """Log allowlisted diagnostics only; provider exception text may contain credentials."""
        fields = [f"operation={operation}", f"error_type={type(error).__name__}"]
        if product_id:
            fields.append(f"product_id={product_id}")
        if status:
            fields.append(f"status={status}")
        app.logger.error("Catalog operation failed: %s", " ".join(fields))

    def _catalog_error(error):
        if isinstance(error, ProductUserError):
            return error.user_message
        if isinstance(error, SyncAlreadyRunning):
            return "Đồng bộ sản phẩm ACCESSTRADE đang chạy; hãy thử lại sau"
        return "Không thể tiếp tục. Vui lòng thử lại."

    def _safe_catalog_items(rows):
        """Drop unsafe external URLs before catalog records reach the template."""
        items = []
        for row in rows:
            item = dict(row)
            for field in ("detail_link", "main_image_url", "affiliate_url", "affiliate_short_url"):
                item[field] = _safe_external_url(item.get(field))
            items.append(item)
        return items

    def _catalog_redirect(*, err=None, synced=None):
        values = {"q": request.form.get("q", "").strip()}
        if err:
            values["err"] = err
        if synced:
            values["synced"] = synced
        return redirect(url_for("products", **values))

    # Tên trùng với các field GET của form lọc trong products.html --
    # ProductFilters.from_request() đọc lại đúng các tên này.
    _CATALOG_FILTER_FIELDS = ("q", "shop", "inventory", "min_commission_rate", "min_commission_amount",
                              "min_price", "max_price", "min_units_sold", "affiliate_status",
                              "post_state", "sort")

    def _batch_redirect(*, summary=None, err=None):
        values = {name: request.form.get(name, "") for name in _CATALOG_FILTER_FIELDS
                  if request.form.get(name)}
        if summary:
            values["batch"] = summary
        if err:
            values["err"] = err
        return redirect(url_for("products", **values))

    def _batch_product_ids():
        return [pid.strip() for pid in request.form.getlist("product_id") if pid.strip()]

    @app.route("/sanpham")
    def products():
        """Mặc định (mode=catalog): catalog ACCESSTRADE cục bộ, đồng bộ trước, lọc
        nhanh không gọi mạng mỗi lần tìm -- nhưng CHƯA nối đa kênh/ảnh thêm/nhóm
        nhanh (D1/D3/D4-A), chỉ chọn được đúng 1 kênh mỗi lần tạo bài.
        mode=search: tìm trực tiếp qua nguồn (ACCESSTRADE/TikTokShop/mock), giữ lại
        từ trước khi có catalog cục bộ -- ĐÃ nối đủ đa kênh/ảnh thêm/nhóm nhanh.
        mode=affiliate: dán link Shopee thủ công, chung cho cả 2 chế độ trên."""
        mode = request.args.get("mode", "catalog")
        if mode == "affiliate":
            return _render_affiliate(affiliate_url=request.args.get("affiliate_url", ""))

        if mode == "search":
            q = request.args.get("q", "").strip()
            source_name = request.args.get("nguon") or None
            items, err = [], request.args.get("err")
            try:
                src = factory.get_source(source_name)
                if hasattr(src, "search_products"):
                    items, _ = src.search_products(query=q or None, limit=24)
                else:
                    err = err or f"Nguồn {src.name} không hỗ trợ tìm kiếm."
            except Exception as e:
                err = err or str(e)
            pending, channels, media_assets, account_groups = _product_common_context()
            return render_template(
                "products.html", page="san-pham", mode="search", items=items, q=q, err=err,
                source_name=source_name or os.environ.get("ACP_SOURCE", "mock"),
                pending_review=pending, channels=channels, resolved=None,
                metadata=ProductMetadata(), affiliate_url="", platform_labels=PLATFORM_LABELS,
                media_assets=media_assets, account_groups=account_groups)

        filters = ProductFilters.from_request(request)
        items, err = [], request.args.get("err")
        catalog = {"count": 0, "in_stock": 0, "ready": 0, "last_synced_at": None}
        conn = connect()
        try:
            service = ProductService(conn, AccessTradeClient.from_env())
            items = _safe_catalog_items(service.search_local(filters))
            catalog = _catalog_summary(conn)
        except Exception as error:
            _log_catalog_failure("query", error)
            err = err or "Không thể tiếp tục. Vui lòng thử lại."
        finally:
            conn.close()
        pending, channels, media_assets, account_groups = _product_common_context()
        return render_template(
            "products.html", page="san-pham", mode="catalog", items=items, filters=filters,
            catalog=catalog, synced=request.args.get("synced"), err=err,
            batch_summary=request.args.get("batch"),
            pending_review=pending, channels=channels, resolved=None,
            metadata=ProductMetadata(), affiliate_url="", platform_labels=PLATFORM_LABELS,
            media_assets=media_assets, account_groups=account_groups)

    @app.route("/sanpham/sync", methods=["POST"])
    def sync_products():
        conn = connect()
        try:
            result = ProductService(conn, AccessTradeClient.from_env()).sync(
                title_keywords=request.form.get("q") or None)
            return _catalog_redirect(synced=_sync_summary(result))
        except Exception as error:
            if not isinstance(error, ProductUserError):
                _log_catalog_failure("sync", error)
            return _catalog_redirect(err=_catalog_error(error))
        finally:
            conn.close()

    @app.route("/sanpham/<product_id>/affiliate-link", methods=["POST"])
    def create_catalog_affiliate_link(product_id):
        conn = connect()
        try:
            client = AccessTradeClient.from_env()
            result = ProductService(conn, client).create_product_only_link(
                product_id, on_link_error=lambda error: _log_catalog_failure(
                    "create_product_link", error, product_id=product_id))
            if not result["ok"]:
                raise ProductUserError(result["error"])
            return _catalog_redirect(synced="Đã tạo link affiliate để sao chép.")
        except Exception as error:
            if not isinstance(error, ProductUserError):
                _log_catalog_failure("create_product_link", error, product_id=product_id)
            return _catalog_redirect(err=_catalog_error(error))
        finally:
            conn.close()

    @app.route("/sanpham/<product_id>/tao-bai", methods=["POST"])
    def create_from_catalog_product(product_id):
        conn = connect()
        try:
            result = pipeline.create_post_for_catalog_product(
                conn, factory.build_context(), product_id,
                campaign_code=os.environ.get("ACP_CAMPAIGN_CODE", "gd2026"),
                channel_code=request.form.get("channel_code") or None,
                on_link_error=lambda error: _log_catalog_failure(
                    "create_post_link", error, product_id=product_id, status="FAILED"))
            if not result.get("ok"):
                raise ProductUserError(result.get("error") or "Không thể tạo bài nháp.")
            return redirect(url_for("review"))
        except Exception as error:
            if not isinstance(error, ProductUserError):
                _log_catalog_failure("create_post", error, product_id=product_id)
            return _catalog_redirect(err=_catalog_error(error))
        finally:
            conn.close()

    @app.route("/sanpham/batch/affiliate-link", methods=["POST"])
    def batch_create_affiliate_links():
        ids = _batch_product_ids()
        if not ids:
            return _batch_redirect(err="Chưa chọn sản phẩm nào")
        conn = connect()
        try:
            client = AccessTradeClient.from_env()
            result = ProductService(conn, client).create_product_links(ids)
            return _batch_redirect(summary=result.summary)
        except Exception as error:
            _log_catalog_failure("batch_create_product_links", error)
            return _batch_redirect(err=_catalog_error(error))
        finally:
            conn.close()

    @app.route("/sanpham/batch/tao-bai", methods=["POST"])
    def batch_create_posts():
        ids = _batch_product_ids()
        if not ids:
            return _batch_redirect(err="Chưa chọn sản phẩm nào")
        conn = connect()
        try:
            service = ProductService(conn, AccessTradeClient.from_env())
            result = service.create_posts(
                ids, factory.build_context(),
                campaign_code=os.environ.get("ACP_CAMPAIGN_CODE", "gd2026"),
                channel_code=request.form.get("channel_code") or None,
                on_link_error=lambda product_id, error: _log_catalog_failure(
                    "batch_create_post_link", error, product_id=product_id, status="FAILED"))
            return _batch_redirect(summary=result.summary)
        except Exception as error:
            _log_catalog_failure("batch_create_posts", error)
            return _batch_redirect(err=_catalog_error(error))
        finally:
            conn.close()

    @app.route("/sanpham/tao-bai", methods=["POST"])
    def create_from_product():
        external_id = request.form.get("external_product_id", "").strip()
        source_name = request.form.get("nguon") or None
        q = request.form.get("q", "")
        channel_codes = request.form.getlist("channel_codes")
        media_asset_ids = request.form.getlist("media_asset_ids")
        if not external_id:
            return redirect(url_for("products", q=q, err="Thiếu mã sản phẩm"))
        if not channel_codes:
            return redirect(url_for("products", q=q, err="Chọn ít nhất 1 kênh"))
        conn = connect()
        try:
            res = pipeline.create_post_for_product(
                conn, factory.build_context(source_name), external_id,
                campaign_code=os.environ.get("ACP_CAMPAIGN_CODE", "gd2026"),
                channel_codes=channel_codes, media_asset_ids=media_asset_ids or None)
        except Exception as e:
            conn.close()
            return redirect(url_for("products", q=q, err=str(e)))
        conn.close()
        if not res.get("ok"):
            return redirect(url_for("products", q=q, err=res.get("error")))
        return redirect(url_for("review"))

    @app.route("/sanpham/affiliate/resolve", methods=["POST"])
    def resolve_affiliate_product():
        affiliate_url = request.form.get("affiliate_url", "").strip()
        source = app.config["SHOPEE_SOURCE_FACTORY"]()
        try:
            resolved = source.resolve(affiliate_url)
        except AffiliateImportError as exc:
            return _render_affiliate(affiliate_url=affiliate_url, err=str(exc), status=400)

        warning = None
        try:
            metadata = source.metadata(resolved.product_url)
        except AffiliateImportError:
            metadata = ProductMetadata()
            warning = "Không tự lấy đủ thông tin sản phẩm. Hãy kiểm tra và nhập bổ sung trước khi tạo bài."
        if not any((metadata.name, metadata.current_price, metadata.image_url, metadata.shop)):
            warning = warning or "Trang Shopee không cung cấp metadata đọc được. Hãy nhập thông tin sản phẩm thủ công."
        return _render_affiliate(
            affiliate_url=affiliate_url, resolved=resolved, metadata=metadata, warning=warning)

    @app.route("/sanpham/affiliate/create", methods=["POST"])
    def create_affiliate_product():
        affiliate_url = request.form.get("affiliate_url", "").strip()
        product_url = request.form.get("product_url", "").strip()
        name = request.form.get("name", "").strip()
        image_url = request.form.get("image_url", "").strip()
        shop = request.form.get("shop", "").strip() or None
        channel_codes = request.form.getlist("channel_codes")
        media_asset_ids = request.form.getlist("media_asset_ids")

        def _positive_int(value):
            text = str(value or "").strip().replace(" ", "")
            if not text:
                return None
            # Form is VND integer. Dots/commas are accepted as grouping separators.
            text = text.replace(".", "").replace(",", "")
            try:
                value = int(text)
            except ValueError:
                return None
            return value if value > 0 else None

        price = _positive_int(request.form.get("current_price"))
        original_price = _positive_int(request.form.get("original_price"))
        source = app.config["SHOPEE_SOURCE_FACTORY"]()
        resolved = ResolvedAffiliateUrl(affiliate_url=affiliate_url, product_url=product_url)
        metadata = ProductMetadata(
            name=name or None, current_price=price, original_price=original_price,
            image_url=image_url or None, shop=shop)

        missing = []
        if not name:
            missing.append("tên sản phẩm")
        if not price:
            missing.append("giá lớn hơn 0")
        if not image_url:
            missing.append("URL ảnh")
        if not channel_codes:
            missing.append("ít nhất 1 kênh")
        if missing:
            return _render_affiliate(
                affiliate_url=affiliate_url, resolved=resolved, metadata=metadata,
                selected_channels=channel_codes,
                err="Thiếu hoặc không hợp lệ: " + ", ".join(missing), status=400)

        conn = connect()
        # Validate từng kênh nằm ở _create_post_from_raw_product (chung cho
        # web lẫn mọi caller khác) -- không lặp lại logic ở đây nữa.

        try:
            source.validate_confirmed_urls(affiliate_url, product_url)
            confirmed = ConfirmedProductInput(
                affiliate_url=affiliate_url, product_url=product_url, name=name,
                current_price=price, original_price=original_price,
                image_url=image_url, shop=shop)
            raw = source.prepare_product(confirmed, pipeline.MEDIA_DIR)
            # Important provider boundary: do not call factory.build_context() here.
            res = pipeline.create_post_from_manual_affiliate_product(
                conn, {"storage": storage.get_storage()}, source, raw,
                affiliate_url=affiliate_url,
                campaign_code=os.environ.get("ACP_CAMPAIGN_CODE", "gd2026"),
                channel_codes=channel_codes, media_asset_ids=media_asset_ids or None)
        except AffiliateImportError as exc:
            conn.close()
            return _render_affiliate(
                affiliate_url=affiliate_url, resolved=resolved, metadata=metadata,
                selected_channels=channel_codes, err=str(exc), status=400)
        except Exception:
            conn.close()
            return _render_affiliate(
                affiliate_url=affiliate_url, resolved=resolved, metadata=metadata,
                selected_channels=channel_codes,
                err="Không thể tạo bài nháp. Kiểm tra dữ liệu và thử lại.", status=500)
        conn.close()
        if not res.get("ok"):
            return _render_affiliate(
                affiliate_url=affiliate_url, resolved=resolved, metadata=metadata,
                selected_channels=channel_codes, err=res.get("error") or "Không thể tạo bài nháp.", status=400)
        return redirect(url_for("review"))

    # --------------------------------------------- ACP Shopee Helper (Chrome)

    @app.route("/sanpham/affiliate/helper/token", methods=["POST"])
    def helper_issue_token():
        """Gọi từ trang /sanpham (đã đăng nhập, có CSRF) khi bấm
        'Mở Shopee & lấy thông tin'. Token gắn với ĐÚNG product_url đang xác
        nhận -- xem core/helper_pairing.py."""
        product_url = request.form.get("product_url", "").strip()
        if not product_url:
            abort(400)
        return jsonify(helper_pairing.issue(product_url))

    @app.route("/sanpham/affiliate/helper/status")
    def helper_status():
        """Trang /sanpham poll định kỳ để lấy dữ liệu extension vừa gửi."""
        token = request.args.get("token", "")
        result = helper_pairing.poll(token)
        if result is None:
            return jsonify(status="expired"), 404
        return jsonify(result)

    @app.route("/api/helper/shopee-product", methods=["POST"])
    def helper_submit():
        """ACP Shopee Helper (Chrome extension) gọi sau khi đọc DOM trang Shopee
        đã render trong tab của người dùng. KHÔNG đăng nhập (extension không có
        session của người vận hành) -- tự bảo vệ bằng:
          1. Chỉ nhận request từ loopback (127.0.0.1/::1).
          2. Token một lần dùng, gắn đúng product_url, hết hạn sau 5 phút.
          3. Route này KHÔNG bật CORS nên một trang web bất kỳ không tự POST
             tới đây được qua fetch() của trình duyệt.
        """
        if request.remote_addr not in ("127.0.0.1", "::1"):
            abort(403)
        payload = request.get_json(silent=True) or {}
        token = str(payload.get("token") or "")
        product_url = str(payload.get("product_url") or "")
        metadata = payload.get("metadata") or {}
        if not token or not product_url or not isinstance(metadata, dict):
            abort(400)
        # Chỉ giữ lại đúng 5 trường được phép -- extension không được nhồi
        # thêm trường lạ (vd cookie/token) vào payload.
        clean = {k: metadata.get(k) for k in ("name", "current_price", "original_price",
                                               "image_url", "shop")}
        ok = helper_pairing.submit(token, product_url, clean)
        if not ok:
            abort(410)  # token sai/hết hạn/đã dùng/product_url không khớp
        return jsonify(ok=True)

    # ------------------------------------------------------------- kênh

    @app.route("/kenh", methods=["GET", "POST"])
    def channels():
        """Mỗi kênh một ngách. Đổi bất cứ lúc nào, không ảnh hưởng bài đã đăng."""
        from ..core import niche as niche_mod
        conn = connect()
        saved = None
        err = None
        if request.method == "POST":
            cid = request.form.get("channel_id", "")
            channel = conn.execute("SELECT * FROM channel WHERE id=?", (cid,)).fetchone()
            if not channel:
                err = "Kênh không tồn tại"
            else:
                values = None
                if channel["platform"] == "threads":
                    automation = validate_channel_automation_config({
                        "auto_schedule_enabled": request.form.get("auto_schedule_enabled", ""),
                        "daily_post_target": request.form.get("daily_post_target", ""),
                        "daily_post_cap": request.form.get("daily_post_cap", ""),
                        "existing_daily_post_cap": channel["daily_post_cap"],
                        "posting_timezone": request.form.get("posting_timezone", ""),
                        "posting_slots": request.form.getlist("posting_slots"),
                    })
                    if not automation["ok"]:
                        err = automation["error"]
                    else:
                        values = automation["values"]
                if not err:
                    applied = pipeline.set_channel_niches(conn, cid, request.form.getlist("niches"))
                    row = conn.execute("SELECT handle FROM channel WHERE id=?", (cid,)).fetchone()
                    saved = row["handle"] if row else cid
                    if values:
                        try:
                            current_slots = json.loads(channel["posting_slots"] or "[]")
                        except (TypeError, ValueError):
                            current_slots = []
                        new_slots = json.loads(values["posting_slots"])
                        automation_changed = any([
                            channel["auto_schedule_enabled"] != values["auto_schedule_enabled"],
                            channel["daily_post_target"] != values["daily_post_target"],
                            channel["daily_post_cap"] != values["daily_post_cap"],
                            channel["posting_timezone"] != values["posting_timezone"],
                            current_slots != new_slots,
                        ])
                        if not automation_changed:
                            values = None
                    if values:
                        audit_detail = dict(values)
                        audit_detail["posting_slots"] = json.loads(values["posting_slots"])
                        conn.execute("""
                            UPDATE channel
                            SET auto_schedule_enabled=?, daily_post_target=?, daily_post_cap=?,
                                posting_timezone=?, posting_slots=?
                            WHERE id=?
                        """, (values["auto_schedule_enabled"], values["daily_post_target"],
                              values["daily_post_cap"], values["posting_timezone"],
                              values["posting_slots"], cid))
                        pipeline.audit(conn, "channel", cid, "updated_automation",
                                       actor="operator", detail=audit_detail)
        rows = []
        for ch in conn.execute("SELECT * FROM channel ORDER BY platform, code").fetchall():
            nl = pipeline.channel_niches(conn, ch["id"])
            posting_slots = []
            if ch["platform"] == "threads":
                try:
                    posting_slots = json.loads(ch["posting_slots"] or "[]")
                except (TypeError, ValueError):
                    posting_slots = []
            rows.append(dict(ch, niches=nl, posting_slots_list=posting_slots,
                             posting_slots_text="\n".join(posting_slots),
                             daily_post_cap_input_max=max(3, int(ch["daily_post_cap"] or 3)),
                             pool=len(scoring.score_candidates(conn, limit=9999, niches=nl)),
                             published=conn.execute(
                                 "SELECT COUNT(*) FROM post WHERE channel_id=? AND status='PUBLISHED'",
                                 (ch["id"],)).fetchone()[0]))
        by_platform = {}
        for row in rows:
            by_platform.setdefault(row["platform"], []).append(row)
        has_meta_connection = bool(conn.execute("SELECT 1 FROM meta_connection LIMIT 1").fetchone())
        pending = conn.execute(
            "SELECT COUNT(*) FROM post WHERE status IN ('PENDING_REVIEW','DRAFT')").fetchone()[0]
        # D4-A: preset chọn nhanh -- checklist tạo nhóm cần TOÀN BỘ channel
        # ACTIVE (không lọc thêm enabled=1, khác /sanpham -- nhóm là preset
        # lâu dài, channel tạm tắt vẫn nên giữ trong nhóm để bật lại là dùng
        # được ngay, không cần tạo lại nhóm).
        all_active_channels = [r for r in rows if r["status"] == "ACTIVE"]
        account_groups = pipeline.list_account_groups(conn)
        conn.close()
        return render_template("channels.html", page="kenh", by_platform=by_platform,
                               all_niches=niche_mod.NICHES, saved=saved, pending_review=pending,
                               has_meta_connection=has_meta_connection,
                               summary=request.args.get("summary"), err=err or request.args.get("err"),
                               all_active_channels=all_active_channels,
                               account_groups=account_groups, platform_labels=PLATFORM_LABELS)

    @app.route("/kenh/<channel_id>/enable", methods=["POST"])
    def channel_enable(channel_id):
        conn = connect()
        conn.execute("UPDATE channel SET enabled=1 WHERE id=?", (channel_id,))
        pipeline.audit(conn, "channel", channel_id, "enabled", actor="operator")
        conn.close()
        return redirect(url_for("channels"))

    @app.route("/kenh/<channel_id>/disable", methods=["POST"])
    def channel_disable(channel_id):
        conn = connect()
        conn.execute("UPDATE channel SET enabled=0 WHERE id=?", (channel_id,))
        pipeline.audit(conn, "channel", channel_id, "disabled", actor="operator")
        conn.close()
        return redirect(url_for("channels"))

    # ------------------------------------------------- nhóm account (D4-A)

    @app.route("/kenh/nhom/tao", methods=["POST"])
    def account_group_create():
        name = request.form.get("name", "").strip()
        channel_ids = request.form.getlist("channel_ids")
        if not name:
            return redirect(url_for("channels", err="Thiếu tên nhóm"))
        if not channel_ids:
            return redirect(url_for("channels", err="Chọn ít nhất 1 kênh cho nhóm"))
        conn = connect()
        try:
            res = pipeline.create_account_group(conn, name, channel_ids)
        finally:
            conn.close()
        return redirect(url_for("channels", err=None if res.get("ok") else res.get("error")))

    @app.route("/kenh/nhom/<group_id>/sua", methods=["POST"])
    def account_group_update(group_id):
        channel_ids = request.form.getlist("channel_ids")
        conn = connect()
        try:
            res = pipeline.update_account_group_channels(conn, group_id, channel_ids)
        finally:
            conn.close()
        return redirect(url_for("channels", err=None if res.get("ok") else res.get("error")))

    @app.route("/kenh/nhom/<group_id>/xoa", methods=["POST"])
    def account_group_delete(group_id):
        conn = connect()
        try:
            res = pipeline.delete_account_group(conn, group_id)
        finally:
            conn.close()
        return redirect(url_for("channels", err=None if res.get("ok") else res.get("error")))

    # ----------------------------------------------------------- duyệt bài

    @app.route("/duyet")
    def review():
        conn = connect()
        # LEFT JOIN -- bài không bán hàng (post_type='VALUE') không có product_id,
        # INNER JOIN sẽ âm thầm giấu chúng khỏi màn hình duyệt.
        rows = [dict(r) for r in conn.execute("""
            SELECT p.*, pr.name AS product_name, pr.category_code, pr.current_price,
                   pr.commission_value, pr.rating, pr.review_count, pr.sold_count,
                   ch.handle AS channel_handle, ch.code AS channel_code,
                   ch.platform AS channel_platform, t.name AS template_name
            FROM post p
            LEFT JOIN product pr ON pr.id = p.product_id
            JOIN channel ch ON ch.id = p.channel_id
            LEFT JOIN caption_template t ON t.id = p.caption_template_id
            WHERE p.status IN ('PENDING_REVIEW', 'DRAFT')
            ORDER BY p.created_at DESC""").fetchall()]
        selections = pipeline.post_channel_selections(conn, [r["id"] for r in rows])
        for r in rows:
            # Bài KHÔNG có dòng post_channel_selection nào vẫn phải duyệt được:
            # bài cũ tạo từ trước khi có bảng này, và bài do một writer tương lai
            # quên gọi _save_channel_selection(). Checklist rỗng sẽ vướng rào
            # "chọn ít nhất 1 kênh" ở review_action() -> bài kẹt vĩnh viễn. Rơi
            # về đúng 1 kênh gốc của bài (post.channel_id) là hành vi cũ, an toàn.
            r["selected_channels"] = selections.get(r["id"]) or [
                {"id": r["channel_id"], "code": r["channel_code"],
                 "platform": r["channel_platform"], "handle": r["channel_handle"]}
            ]
        # Điền lại override theo account đã nhập ở lần duyệt trước: bài bị
        # bounce về PENDING_REVIEW (ContentViolationError ở MỘT kênh khác, xem
        # core/jobs.py) rồi duyệt lại là đường DUY NHẤT để duyệt lại một bài,
        # mà template cố ý render ô override rỗng (spec §8) -- không đọc lại
        # thì chữ operator đã gõ mất im lặng, kênh đó đăng caption gốc.
        overrides_by_post = pipeline.latest_channel_caption_overrides(conn, [r["id"] for r in rows])
        for r in rows:
            channel_overrides = overrides_by_post.get(r["id"], {})
            for sel in r["selected_channels"]:
                sel["prior_override"] = channel_overrides.get(sel["id"], "")
        try:
            _attach_content_variants(conn, rows)
        except Exception:
            # Bảng content_generation_run/content_variant_row có thể chưa tồn
            # tại (CSDL cũ chưa migrate qua E6). /duyet là trang vận hành chính
            # -- phần hiển thị Content Engine v2 hỏng thì bỏ trống khối variant,
            # tuyệt đối không được làm cả trang 500.
            for r in rows:
                r["variants"] = []
        recent = [dict(r) for r in conn.execute("""
            SELECT p.id, p.status, p.scheduled_at, p.published_at, pr.name AS product_name
            FROM post p LEFT JOIN product pr ON pr.id = p.product_id
            WHERE p.status IN ('SCHEDULED','PUBLISHED','REJECTED')
            ORDER BY p.updated_at DESC LIMIT 8""").fetchall()]
        conn.close()
        return render_template("review.html", page="duyet", posts=rows, recent=recent,
                               platform_labels=PLATFORM_LABELS)

    def _attach_content_variants(conn, rows):
        """Gắn rows[i]["variants"] từ content_generation_run/content_variant_row
        (Content Engine v2, E6). Tách hàm riêng để caller bọc try/except gọn --
        lỗi ở đây không được làm hỏng cả trang /duyet.
        """
        run_by_post = {r["post_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM content_generation_run WHERE post_id IN ({}) AND status='READY'".format(
                ",".join("?" * len(rows))), [r["id"] for r in rows]).fetchall()} if rows else {}
        for r in rows:
            run = run_by_post.get(r["id"])
            r["variants"] = []
            if not run:
                continue
            variant_rows = conn.execute(
                "SELECT * FROM content_variant_row WHERE run_id=? ORDER BY label", (run["id"],)).fetchall()
            platforms = sorted({sel["platform"] for sel in r["selected_channels"]} & {"threads", "facebook", "instagram"})
            for vr in variant_rows:
                # persist_run() ghi NULL cả 3 cột điểm cho đúng những variant bị
                # select_best_variant() (E4) loại vì KHÔNG đạt fact safety -- đó
                # là dấu hiệu tin cậy duy nhất phân biệt variant bị loại với
                # variant hợp lệ. Không render thành card chọn được, để operator
                # không thể chọn/duyệt nhầm nội dung đã bị chặn.
                if vr["rule_score"] is None and vr["hybrid_score"] is None and vr["final_score"] is None:
                    continue
                variant_obj = content_variant.ContentVariant(
                    angle=vr["angle"], hook=vr["hook"], main_message=vr["main_message"],
                    body=json.loads(vr["body_json"]), cta=vr["cta"], structure=vr["structure"])
                r["variants"].append({
                    "id": vr["id"], "label": vr["label"], "angle": vr["angle"], "hook": vr["hook"],
                    "is_best": bool(vr["is_best"]), "final_score": vr["final_score"],
                    "caption_by_platform": content_platform.adapt_for_platforms(
                        variant_obj, platforms, r["affiliate_link"]) if platforms else {},
                    "violations": [v["message"] for v in content_checker.check_variant_rules(variant_obj)],
                })

    @app.route("/duyet/<post_id>/<action>", methods=["POST"])
    def review_action(post_id, action):
        conn = connect()
        if action == "approve":
            scheduled_at = None
            raw_time = request.form.get("scheduled_at", "").strip()
            if raw_time:
                # <input type="datetime-local"> trả về giờ theo múi giờ TRÌNH
                # DUYỆT của operator, không có offset. ACP vận hành cho thị
                # trường VN nên quy ước đó là giờ Việt Nam (UTC+7, không có
                # DST) rồi quy đổi sang UTC trước khi lưu -- mọi giờ khác
                # trong hệ thống (scheduled_at, published_at) đều là UTC.
                try:
                    local_dt = datetime.fromisoformat(raw_time)
                    scheduled_at = (local_dt - timedelta(hours=7)).replace(
                        tzinfo=timezone.utc).isoformat(timespec="seconds")
                except ValueError:
                    conn.close()
                    return redirect(url_for("review", err="Giờ đăng không hợp lệ"))
            channel_ids = request.form.getlist("channel_ids")
            if not channel_ids:
                # Checklist rỗng nghĩa là operator bỏ tích hết -- CHẶN, không
                # được âm thầm rơi về fallback 1-kênh của approve_post() (đó
                # là dành cho caller cũ gọi trực tiếp, không phải cho form này).
                res = {"ok": False, "error": "Chọn ít nhất 1 kênh trước khi duyệt"}
            else:
                # request.form.get(...) không kèm default: None nếu field
                # không có trong form (giữ nguyên giá trị cũ), chuỗi rỗng nếu
                # có mặt nhưng để trống (xoá override) -- đúng ngữ nghĩa
                # approve_post() cần, xem D2 spec §8.
                caption_overrides = {}
                for cid in channel_ids:
                    val = request.form.get(f"caption_override_{cid}", "").strip()
                    if val:
                        caption_overrides[cid] = val
                res = pipeline.approve_post(conn, post_id, actor="operator",
                                            caption_override=request.form.get("caption") or None,
                                            channel_ids=channel_ids,
                                            caption_facebook=request.form.get("caption_facebook"),
                                            caption_instagram=request.form.get("caption_instagram"),
                                            caption_overrides=caption_overrides or None,
                                            scheduled_at=scheduled_at)
        elif action == "reject":
            res = pipeline.reject_post(conn, post_id, request.form.get("reason") or "Không phù hợp", "operator")
        elif action in ("doi-hook", "lam-lai", "doi-angle"):
            variant_id = request.form.get("variant_id")
            try:
                if action == "doi-hook":
                    res = content_engine.regenerate_hook(conn, post_id, variant_id)
                elif action == "lam-lai":
                    res = content_engine.regenerate_variant(conn, post_id, variant_id)
                else:
                    res = content_engine.switch_angle(conn, post_id, variant_id)
            except Exception as exc:
                res = {"ok": False, "error": "Không tạo được nội dung mới, thử lại sau"}
                # audit() cũng ghi DB nên có thể tự ném (lỗi gốc là khoá DB /
                # connection đã đóng chẳng hạn) -- nuốt luôn, đây chỉ là
                # telemetry best-effort. res đã gán ở trên nên operator vẫn
                # nhận redirect có lỗi tử tế, không rơi về 500 -- đúng thứ
                # mà except block này sinh ra để tránh.
                try:
                    pipeline.audit(conn, "content_variant_row", variant_id or post_id, f"{action}_failed",
                                   actor="system", detail={"error": str(exc)})
                except Exception:
                    pass
        else:
            conn.close()
            abort(404)
        conn.close()
        return redirect(url_for("review", err=None if res.get("ok") else res.get("error")))

    # ----------------------------------------------------------- vận hành

    @app.route("/vanhanh")
    def ops():
        conn = connect()
        now_utc = _parse_ops_now_utc(request.args.get("now")) or datetime.now(timezone.utc)
        worker_row = conn.execute(
            "SELECT value, updated_at FROM system_setting WHERE key=?", (PUBLISH_WORKER_ENABLED,)).fetchone()
        data = dict(
            worker_enabled=publish_worker_enabled(conn),
            worker_updated_at=worker_row["updated_at"] if worker_row else None,
            auto_ops=_build_auto_ops_summary(conn, now_utc),
            queue=jobs.queue_summary(conn),
            failed=[dict(r) for r in conn.execute(
                "SELECT * FROM job_queue WHERE status='FAILED' ORDER BY updated_at DESC LIMIT 10").fetchall()],
            deferred=[dict(r) for r in conn.execute(
                "SELECT * FROM job_queue WHERE status='READY' AND last_error IS NOT NULL "
                "ORDER BY run_after LIMIT 5").fetchall()],
            channels=[dict(r) for r in conn.execute("""
                SELECT c.*, (SELECT COUNT(*) FROM post p WHERE p.channel_id=c.id AND p.status='PUBLISHED'
                             AND substr(p.published_at,1,10)=substr(?,1,10)) AS today,
                       (SELECT COUNT(*) FROM post p WHERE p.channel_id=c.id AND p.status='SCHEDULED') AS queued
                FROM channel c""", (now_utc.isoformat(timespec="seconds"),)).fetchall()],
            posts_by_status=[dict(r) for r in conn.execute(
                "SELECT status, COUNT(*) AS n FROM post GROUP BY status ORDER BY n DESC").fetchall()],
            publish_targets=[dict(r) for r in conn.execute("""
                SELECT pt.*, pr.name AS product_name, ch.handle AS channel_handle
                FROM publish_target pt
                JOIN post p ON p.id = pt.post_id
                JOIN product pr ON pr.id = p.product_id
                JOIN channel ch ON ch.id = pt.channel_id
                ORDER BY pt.updated_at DESC LIMIT 20""").fetchall()],
            audit=[dict(r) for r in conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT 12").fetchall()],
        )
        # D4-B: post.status là rollup thô ("PUBLISHED" = ít nhất 1/N kênh
        # thành công, không phải tất cả) -- gộp publish_target theo post_id
        # để operator thấy đúng từng kênh của bài đa kênh thay vì đoán qua
        # con số tổng. Quét rộng hơn 100 target gần nhất (không chỉ 20 như
        # bảng phẳng ở trên) để không bỏ sót bài đa kênh có target không lọt
        # top-20 theo thời gian cập nhật.
        wide_targets = conn.execute("""
            SELECT pt.*, pr.name AS product_name, ch.handle AS channel_handle, ch.platform AS channel_platform
            FROM publish_target pt
            JOIN post p ON p.id = pt.post_id
            JOIN product pr ON pr.id = p.product_id
            JOIN channel ch ON ch.id = pt.channel_id
            ORDER BY pt.updated_at DESC LIMIT 100""").fetchall()
        by_post = {}
        for r in wide_targets:
            g = by_post.setdefault(r["post_id"], {"product_name": r["product_name"], "targets": []})
            g["targets"].append(dict(r))
        # dict giữ nguyên thứ tự chèn (Python 3.7+) -- vì wide_targets đã
        # ORDER BY updated_at DESC, post nào có target cập nhật gần nhất sẽ
        # được thêm vào by_post trước, nên thứ tự duyệt ở đây đã đúng ý
        # "bài có hoạt động gần đây nhất lên trước" mà không cần sort thêm.
        multi_channel_posts = [
            {"post_id": pid, "product_name": g["product_name"], "targets": g["targets"]}
            for pid, g in by_post.items() if len(g["targets"]) >= 2
        ][:15]
        data["multi_channel_posts"] = multi_channel_posts
        conn.close()
        return render_template("ops.html", page="van-hanh", **data)

    @app.route("/vanhanh/<target_id>/retry", methods=["POST"])
    def retry_publish_target_route(target_id):
        conn = connect()
        res = pipeline.retry_publish_target(conn, target_id, actor="operator")
        conn.close()
        return redirect(url_for("ops", err=None if res.get("ok") else res.get("error")))

    @app.route("/vanhanh/work", methods=["POST"])
    def ops_work():
        # Dùng chung factory với CLI. Trước đây chỗ này hardcode mock.
        conn = connect()
        jobs.drain(conn, ctx=factory.build_context())
        conn.close()
        return redirect(url_for("ops"))

    @app.route("/vanhanh/worker-toggle", methods=["POST"])
    def ops_worker_toggle():
        # Chỉ nhận đúng "0"/"1" -- không suy đoán giá trị khác thành bật/tắt,
        # tránh công tắc publish tự động bị đổi bởi input sai định dạng.
        value = request.form.get("enabled", "")
        if value not in ("0", "1"):
            abort(400, "Giá trị công tắc không hợp lệ")
        conn = connect()
        set_system_setting(conn, PUBLISH_WORKER_ENABLED, value, actor="operator")
        conn.close()
        return redirect(url_for("ops"))

    # ----------------------------------------------------------- chấm điểm

    @app.route("/chamdiem", methods=["GET", "POST"])
    def scoring_page():
        conn = connect()
        weights, filters = scoring.active_config(conn)
        saved = None
        if request.method == "POST":
            for k in weights:
                if k in request.form:
                    weights[k] = float(request.form[k])
            for k in ("min_rating", "min_commission_value", "cooldown_days",
                      "min_review_count", "max_per_category_per_day"):
                if k in request.form:
                    val = request.form[k]
                    filters[k] = float(val) if k == "min_rating" else int(val)
            saved = scoring.save_config(conn, weights, filters, note="chỉnh từ giao diện")
        preview = scoring.score_candidates(conn, limit=999, explain=True)
        conn.close()
        return render_template("scoring.html", page="cham-diem", weights=weights, filters=filters,
                               saved=saved, passed=[p for p in preview if not p["rejected"]][:20],
                               rejected=[p for p in preview if p["rejected"]][:10],
                               total=len(preview))

    # ------------------------------------------------- postback (công khai)

    @app.route("/webhook/at/postback")
    def postback():
        """Accesstrade gửi GET và mong nhận HTTP 200.

        Khoá bí mật nằm trên URL vì Accesstrade không ký request. Đặt
        ACP_WEBHOOK_SECRET rồi khai postback URL kèm ?k=<secret>. Không có khoá
        thì ai biết đường dẫn cũng bơm được doanh thu giả vào hệ thống.
        """
        if webhook_secret:
            given = request.args.get("k", "")
            if not given or not hmac.compare_digest(given, webhook_secret):
                abort(403)
        conn = connect()
        status, cid = pipeline.ingest_postback(conn, request.args.to_dict())
        conn.close()
        if status == "invalid":
            return jsonify(ok=False, error="Thiếu transaction_id hoặc external_product_id"), 400
        return jsonify(ok=True, status=status, id=cid), 200

    # ---------------------------------------------- OAuth Threads (công khai)

    @app.route("/oauth/threads/callback")
    def oauth_callback():
        """Meta chuyển hướng về đây kèm ?code=. Hiển thị code để đổi lấy token.

        Cố ý KHÔNG tự đổi token tại đây: bước đó cần app_secret, và giữ secret
        ngoài tiến trình web an toàn hơn cho một thao tác thiết lập chỉ làm một lần.
        """
        code = request.args.get("code", "")
        err = request.args.get("error_description") or request.args.get("error")
        return render_template("oauth.html", page=None, code=code, err=err), (200 if code else 400)

    @app.route("/oauth/threads/deauthorize", methods=["POST"])
    def oauth_deauthorize():
        """Meta gọi khi người dùng gỡ app. Đánh dấu kênh cần kết nối lại."""
        conn = connect()
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE platform='threads'")
        conn.close()
        return jsonify(ok=True), 200

    @app.route("/oauth/threads/delete", methods=["POST"])
    def oauth_delete():
        code = secrets.token_hex(8)
        return jsonify(url=f"{request.host_url}oauth/threads/delete/status?code={code}",
                       confirmation_code=code), 200

    @app.route("/oauth/threads/delete/status")
    def oauth_delete_status():
        return jsonify(status="completed", code=request.args.get("code", "")), 200

    # ------------------------------------------------ OAuth Meta (Facebook/IG)

    @app.route("/oauth/meta/start")
    def oauth_meta_start():
        """Bắt đầu Facebook Login. Khác Threads: route này và callback đều bắt
        buộc đăng nhập ACP trước, dù nằm dưới prefix /oauth/ công khai --
        kiểm tra thủ công ở đây vì đây là hành động quản trị (thêm account có
        thể publish), không phải webhook/redirect không mang session như
        Threads deauthorize."""
        if admin_password and not session.get("uid"):
            return redirect(url_for("login", next="/oauth/meta/start"))
        state = secrets.token_urlsafe(24)
        session["meta_oauth_state"] = state
        redirect_uri = request.host_url.rstrip("/") + "/oauth/meta/callback"
        svc = factory.get_meta_connection_service()
        return redirect(svc.oauth_authorize_url(state, redirect_uri))

    @app.route("/oauth/meta/callback")
    def oauth_meta_callback():
        if admin_password and not session.get("uid"):
            return redirect(url_for("login", next="/oauth/meta/start"))
        err = request.args.get("error_description") or request.args.get("error")
        if err:
            return redirect(url_for("channels", err=err))
        code = request.args.get("code", "")
        state = request.args.get("state", "")
        expected = session.get("meta_oauth_state", "")
        # So bytes chứ không so str: hmac.compare_digest ném TypeError nếu một
        # trong hai str chứa ký tự ngoài ASCII -- state đến từ query string do
        # người dùng/kẻ tấn công kiểm soát, non-ASCII từng làm callback 500.
        if (not code or not state or not expected
                or not hmac.compare_digest(state.encode(), expected.encode())):
            abort(400, "State OAuth không hợp lệ")
        session.pop("meta_oauth_state", None)

        redirect_uri = request.host_url.rstrip("/") + "/oauth/meta/callback"
        svc = factory.get_meta_connection_service()
        conn = connect()
        try:
            res = connections.connect_meta_account(conn, svc, code, redirect_uri, actor="operator")
        except AuthError as e:
            # exchange_code/list_pages thất bại trước khi có connection_id -- chưa
            # có meta_connection nào để đánh dấu NEEDS_REAUTH, chỉ báo lỗi.
            conn.close()
            return redirect(url_for("channels", err=f"Kết nối Meta thất bại: {e}"))
        except Exception as e:
            conn.close()
            return redirect(url_for("channels", err=f"Lỗi không mong muốn khi kết nối Meta: {e}"))
        conn.close()
        if not res.get("ok"):
            return redirect(url_for("channels", err=res.get("error")))
        return redirect(url_for("channels",
                                summary=f"Đã import {res['imported']} account, cập nhật {res['updated']}"))

    @app.route("/kenh/meta/sync", methods=["POST"])
    def kenh_meta_sync():
        """Đồng bộ lại TẤT CẢ connection Meta đã có -- một operator có thể đã
        kết nối nhiều tài khoản Meta khác nhau (upsert theo meta_user_id, xem
        core/connections.py), nút "Đồng bộ lại" phải làm mới cả loạt chứ không
        chỉ connection gần nhất."""
        conn = connect()
        rows = conn.execute("SELECT id FROM meta_connection").fetchall()
        if not rows:
            conn.close()
            return redirect(url_for("channels", err="Chưa kết nối Meta"))
        svc = factory.get_meta_connection_service()
        imported = updated = reconnect_required = 0
        errors = []
        for row in rows:
            try:
                res = connections.sync_meta_accounts(conn, svc, row["id"], actor="operator")
            except AuthError as e:
                conn.execute("UPDATE meta_connection SET status='NEEDS_REAUTH', updated_at=? WHERE id=?",
                             (now(), row["id"]))
                errors.append(str(e))
                continue
            except Exception as e:
                errors.append(str(e))
                continue
            if not res.get("ok"):
                errors.append(res.get("error"))
                continue
            imported += res.get("imported", 0)
            updated += res.get("updated", 0)
            reconnect_required += res.get("reconnect_required", 0)
        conn.close()
        if errors and not (imported or updated):
            return redirect(url_for("channels", err="; ".join(str(e) for e in errors)))
        return redirect(url_for("channels",
                                summary=f"Đã import {imported} account, cập nhật {updated}"
                                        + (f", {reconnect_required} cần kết nối lại" if reconnect_required else "")))

    @app.route("/api/funnel")
    def api_funnel():
        conn = connect()
        f = attribution.funnel(conn)
        conn.close()
        return jsonify(f)

    from .seeding_routes import register_seeding
    register_seeding(app)

    return app
