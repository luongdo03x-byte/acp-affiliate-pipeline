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

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)

from ..adapters import factory
from ..adapters.base import AuthError
from ..adapters.shopee_affiliate import (
    AffiliateImportError, ConfirmedProductInput, ManualShopeeSource,
    ProductMetadata, ResolvedAffiliateUrl,
)
from ..core import attribution, jobs, media_library, pipeline, scoring, storage
from ..core import connections
from ..core.db import connect, now

MEDIA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "var", "media")

# Đường không cần đăng nhập. Media phải mở vì Meta tự tải ảnh về khi publish.
PUBLIC_PREFIXES = ("/media/", "/webhook/", "/oauth/", "/dangnhap", "/static/", "/healthz")

PLATFORM_LABELS = {"threads": "Threads", "facebook": "Facebook", "instagram": "Instagram"}


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
        return render_template(
            "products.html", page="san-pham", mode="affiliate", items=[], q="", err=err,
            source_name="manual_shopee", pending_review=pending, channels=channels,
            affiliate_url=affiliate_url, resolved=resolved,
            metadata=metadata or ProductMetadata(), metadata_warning=warning,
            selected_channels=selected_channels or [], platform_labels=PLATFORM_LABELS,
            media_assets=media_assets, account_groups=account_groups,
        ), status

    @app.route("/sanpham")
    def products():
        """Tìm sản phẩm hoặc nhập link affiliate. Không có hành vi publish."""
        mode = request.args.get("mode", "search")
        if mode == "affiliate":
            return _render_affiliate(affiliate_url=request.args.get("affiliate_url", ""))

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
        for ch in conn.execute("SELECT * FROM channel ORDER BY platform, code").fetchall():
            nl = pipeline.channel_niches(conn, ch["id"])
            rows.append(dict(ch, niches=nl,
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
                               summary=request.args.get("summary"),
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
        conn = connect()
        res = pipeline.create_account_group(conn, name, channel_ids)
        conn.close()
        return redirect(url_for("channels", err=None if res.get("ok") else res.get("error")))

    @app.route("/kenh/nhom/<group_id>/sua", methods=["POST"])
    def account_group_update(group_id):
        channel_ids = request.form.getlist("channel_ids")
        conn = connect()
        res = pipeline.update_account_group_channels(conn, group_id, channel_ids)
        conn.close()
        return redirect(url_for("channels", err=None if res.get("ok") else res.get("error")))

    @app.route("/kenh/nhom/<group_id>/xoa", methods=["POST"])
    def account_group_delete(group_id):
        conn = connect()
        res = pipeline.delete_account_group(conn, group_id)
        conn.close()
        return redirect(url_for("channels", err=None if res.get("ok") else res.get("error")))

    # ----------------------------------------------------------- duyệt bài

    @app.route("/duyet")
    def review():
        conn = connect()
        rows = [dict(r) for r in conn.execute("""
            SELECT p.*, pr.name AS product_name, pr.category_code, pr.current_price,
                   pr.commission_value, pr.rating, pr.review_count, pr.sold_count,
                   ch.handle AS channel_handle, ch.code AS channel_code,
                   ch.platform AS channel_platform, t.name AS template_name
            FROM post p
            JOIN product pr ON pr.id = p.product_id
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
        recent = [dict(r) for r in conn.execute("""
            SELECT p.id, p.status, p.scheduled_at, p.published_at, pr.name AS product_name
            FROM post p JOIN product pr ON pr.id = p.product_id
            WHERE p.status IN ('SCHEDULED','PUBLISHED','REJECTED')
            ORDER BY p.updated_at DESC LIMIT 8""").fetchall()]
        conn.close()
        return render_template("review.html", page="duyet", posts=rows, recent=recent,
                               platform_labels=PLATFORM_LABELS)

    @app.route("/duyet/<post_id>/<action>", methods=["POST"])
    def review_action(post_id, action):
        conn = connect()
        if action == "approve":
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
                                            caption_overrides=caption_overrides or None)
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

    return app
