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
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)

from ..adapters import factory
from ..adapters.accesstrade_client import AccessTradeClient
from ..adapters.shopee_affiliate import (
    AffiliateImportError, ConfirmedProductInput, ManualShopeeSource,
    ProductMetadata, ResolvedAffiliateUrl, metadata_state,
)
from ..core import attribution, content, helper_pairing, jobs, pipeline, scoring, storage
from ..core.db import connect, now
from ..core.products import ProductFilters, ProductService, SyncAlreadyRunning

MEDIA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "var", "media")

# Đường không cần đăng nhập. Media phải mở vì Meta tự tải ảnh về khi publish.
# /api/helper/ cũng public VÌ SAO: ACP Shopee Helper (Chrome extension) không
# mang session cookie của người vận hành -- nó tự bảo vệ bằng token một lần
# dùng + chỉ nhận request từ loopback (xem core/helper_pairing.py và route
# helper_submit() bên dưới), không phải bằng đăng nhập.
PUBLIC_PREFIXES = ("/media/", "/webhook/", "/oauth/", "/dangnhap", "/static/", "/healthz", "/api/helper/")


class ProductUserError(Exception):
    """A catalog message deliberately approved for display to an operator."""

    def __init__(self, user_message):
        self.user_message = user_message
        super().__init__(user_message)


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


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
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
        channels = [dict(r) for r in conn.execute(
            "SELECT code, handle FROM channel WHERE status='ACTIVE' ORDER BY code").fetchall()]
        conn.close()
        return pending, channels

    def _render_affiliate(*, affiliate_url="", resolved=None, metadata=None,
                          err=None, warning=None, selected_channel=None, status=200):
        pending, channels = _product_common_context()
        meta = metadata or ProductMetadata()
        return render_template(
            "products.html", page="san-pham", mode="affiliate", items=[], q="", err=err,
            source_name="manual_shopee", pending_review=pending, channels=channels,
            affiliate_url=affiliate_url, resolved=resolved,
            metadata=meta, metadata_warning=warning,
            metadata_state=metadata_state(meta) if resolved else None,
            selected_channel=selected_channel,
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
                f"({result.inserted} mới, {result.updated} cập nhật).")

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

    @app.route("/sanpham")
    def products():
        """Local ACCESSTRADE catalog, with the separate manual Shopee workspace."""
        mode = request.args.get("mode", "catalog")
        if mode == "affiliate":
            return _render_affiliate(affiliate_url=request.args.get("affiliate_url", ""))

        filters = ProductFilters.from_request(request)
        items, err = [], request.args.get("err")
        catalog = {"count": 0, "in_stock": 0, "ready": 0, "last_synced_at": None}
        conn = connect()
        try:
            service = ProductService(conn, AccessTradeClient.from_env())
            items = _safe_catalog_items(service.search_local(filters))
            catalog = _catalog_summary(conn)
        except Exception:
            app.logger.exception("Catalog query failed")
            err = err or "Không thể tiếp tục. Vui lòng thử lại."
        finally:
            conn.close()
        pending, channels = _product_common_context()
        return render_template(
            "products.html", page="san-pham", mode="catalog", items=items, filters=filters,
            catalog=catalog, synced=request.args.get("synced"), err=err,
            pending_review=pending, channels=channels, resolved=None,
            metadata=ProductMetadata(), affiliate_url="")

    @app.route("/sanpham/sync", methods=["POST"])
    def sync_products():
        conn = connect()
        try:
            result = ProductService(conn, AccessTradeClient.from_env()).sync(
                title_keywords=request.form.get("q") or None)
            return _catalog_redirect(synced=_sync_summary(result))
        except Exception as error:
            if not isinstance(error, ProductUserError):
                app.logger.exception("Catalog sync failed")
            return _catalog_redirect(err=_catalog_error(error))
        finally:
            conn.close()

    @app.route("/sanpham/<product_id>/affiliate-link", methods=["POST"])
    def create_catalog_affiliate_link(product_id):
        conn = connect()
        try:
            client = AccessTradeClient.from_env()
            product = ProductService(conn, client).get(product_id)
            if not product:
                raise ProductUserError("Không tìm thấy sản phẩm trong catalog")
            if not product["has_inventory"] or not product["detail_link"]:
                raise ProductUserError("Sản phẩm không đủ điều kiện tạo link affiliate")
            link = client.create_product_link(
                product["detail_link"], post_id=f"product:{product['external_product_id']}",
                external_product_id=product["external_product_id"])
            full_url = getattr(link, "full_url", None)
            short_url = getattr(link, "short_url", None)
            if not (full_url or short_url):
                raise ProductUserError("Không thể tạo link affiliate cho sản phẩm")
            linked_at = now()
            conn.execute("""UPDATE product
                            SET affiliate_url=?, affiliate_short_url=?, affiliate_link_status='PRODUCT_ONLY',
                                affiliate_link_error=NULL, affiliate_link_created_at=?, updated_at=?
                            WHERE id=? AND provider='ACCESSTRADE_TIKTOK'""",
                         (full_url or short_url, short_url, linked_at, linked_at, product_id))
            return _catalog_redirect(synced="Đã tạo link affiliate để sao chép.")
        except Exception as error:
            if not isinstance(error, ProductUserError):
                app.logger.exception("Catalog standalone link failed")
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
                on_link_error=lambda error: app.logger.error(
                    "Catalog post affiliate link failed",
                    exc_info=(type(error), error, error.__traceback__)))
            if not result.get("ok"):
                raise ProductUserError(result.get("error") or "Không thể tạo bài nháp.")
            return redirect(url_for("review"))
        except Exception as error:
            if not isinstance(error, ProductUserError):
                app.logger.exception("Catalog post creation failed")
            return _catalog_redirect(err=_catalog_error(error))
        finally:
            conn.close()

    @app.route("/sanpham/tao-bai", methods=["POST"])
    def create_from_product():
        external_id = request.form.get("external_product_id", "").strip()
        source_name = request.form.get("nguon") or None
        q = request.form.get("q", "")
        if not external_id:
            return redirect(url_for("products", q=q, err="Thiếu mã sản phẩm"))
        conn = connect()
        try:
            res = pipeline.create_post_for_product(
                conn, factory.build_context(source_name), external_id,
                campaign_code=os.environ.get("ACP_CAMPAIGN_CODE", "gd2026"))
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
        channel_code = request.form.get("channel_code", "").strip()

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
        if not channel_code:
            missing.append("kênh Threads")
        if missing:
            return _render_affiliate(
                affiliate_url=affiliate_url, resolved=resolved, metadata=metadata,
                selected_channel=channel_code,
                err="Thiếu hoặc không hợp lệ: " + ", ".join(missing), status=400)

        conn = connect()
        channel = conn.execute(
            "SELECT code FROM channel WHERE code=? AND status='ACTIVE'", (channel_code,)).fetchone()
        if not channel:
            conn.close()
            return _render_affiliate(
                affiliate_url=affiliate_url, resolved=resolved, metadata=metadata,
                selected_channel=channel_code, err="Kênh Threads không tồn tại hoặc không hoạt động.", status=400)

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
                channel_code=channel_code)
        except AffiliateImportError as exc:
            conn.close()
            return _render_affiliate(
                affiliate_url=affiliate_url, resolved=resolved, metadata=metadata,
                selected_channel=channel_code, err=str(exc), status=400)
        except Exception:
            conn.close()
            return _render_affiliate(
                affiliate_url=affiliate_url, resolved=resolved, metadata=metadata,
                selected_channel=channel_code,
                err="Không thể tạo bài nháp. Kiểm tra dữ liệu và thử lại.", status=500)
        conn.close()
        if not res.get("ok"):
            return _render_affiliate(
                affiliate_url=affiliate_url, resolved=resolved, metadata=metadata,
                selected_channel=channel_code, err=res.get("error") or "Không thể tạo bài nháp.", status=400)
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
        if request.method == "POST":
            cid = request.form.get("channel_id", "")
            applied = pipeline.set_channel_niches(conn, cid, request.form.getlist("niches"))
            row = conn.execute("SELECT handle FROM channel WHERE id=?", (cid,)).fetchone()
            saved = row["handle"] if row else cid

        rows = []
        for ch in conn.execute("SELECT * FROM channel ORDER BY code").fetchall():
            nl = pipeline.channel_niches(conn, ch["id"])
            rows.append(dict(ch, niches=nl,
                             pool=len(scoring.score_candidates(conn, limit=9999, niches=nl)),
                             published=conn.execute(
                                 "SELECT COUNT(*) FROM post WHERE channel_id=? AND status='PUBLISHED'",
                                 (ch["id"],)).fetchone()[0]))
        pending = conn.execute(
            "SELECT COUNT(*) FROM post WHERE status IN ('PENDING_REVIEW','DRAFT')").fetchone()[0]
        conn.close()
        return render_template("channels.html", page="kenh", channels=rows,
                               all_niches=niche_mod.NICHES, saved=saved, pending_review=pending)

    # ----------------------------------------------------------- duyệt bài

    @app.route("/duyet")
    def review():
        conn = connect()
        # LEFT JOIN -- bài không bán hàng (post_type='VALUE') không có product_id,
        # INNER JOIN sẽ âm thầm giấu chúng khỏi màn hình duyệt.
        rows = [dict(r) for r in conn.execute("""
            SELECT p.*, pr.name AS product_name, pr.category_code, pr.current_price,
                   pr.commission_value, pr.rating, pr.review_count, pr.sold_count,
                   ch.handle AS channel_handle, t.name AS template_name
            FROM post p
            LEFT JOIN product pr ON pr.id = p.product_id
            JOIN channel ch ON ch.id = p.channel_id
            LEFT JOIN caption_template t ON t.id = p.caption_template_id
            WHERE p.status IN ('PENDING_REVIEW', 'DRAFT')
            ORDER BY p.created_at DESC""").fetchall()]
        recent = [dict(r) for r in conn.execute("""
            SELECT p.id, p.status, p.scheduled_at, p.published_at, pr.name AS product_name
            FROM post p LEFT JOIN product pr ON pr.id = p.product_id
            WHERE p.status IN ('SCHEDULED','PUBLISHED','REJECTED')
            ORDER BY p.updated_at DESC LIMIT 8""").fetchall()]
        conn.close()
        return render_template("review.html", page="duyet", posts=rows, recent=recent)

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
            res = pipeline.approve_post(conn, post_id, actor="operator",
                                        caption_override=request.form.get("caption") or None,
                                        scheduled_at=scheduled_at)
        elif action == "reject":
            res = pipeline.reject_post(conn, post_id, request.form.get("reason") or "Không phù hợp", "operator")
        else:
            conn.close()
            abort(404)
        conn.close()
        return redirect(url_for("review", err=None if res.get("ok") else res.get("error")))

    # ----------------------------------------------------------- vận hành

    @app.route("/vanhanh")
    def ops():
        conn = connect()
        data = dict(
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
                FROM channel c""", (now(),)).fetchall()],
            posts_by_status=[dict(r) for r in conn.execute(
                "SELECT status, COUNT(*) AS n FROM post GROUP BY status ORDER BY n DESC").fetchall()],
            audit=[dict(r) for r in conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT 12").fetchall()],
        )
        conn.close()
        return render_template("ops.html", page="van-hanh", **data)

    @app.route("/vanhanh/work", methods=["POST"])
    def ops_work():
        # Dùng chung factory với CLI. Trước đây chỗ này hardcode mock.
        conn = connect()
        jobs.drain(conn, ctx=factory.build_context())
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

    @app.route("/api/funnel")
    def api_funnel():
        conn = connect()
        f = attribution.funnel(conn)
        conn.close()
        return jsonify(f)

    return app
