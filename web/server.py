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
from ..core import attribution, jobs, pipeline, scoring
from ..core.db import connect, now

MEDIA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "var", "media")

# Đường không cần đăng nhập. Media phải mở vì Meta tự tải ảnh về khi publish.
PUBLIC_PREFIXES = ("/media/", "/webhook/", "/oauth/", "/dangnhap", "/static/", "/healthz")


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

    @app.route("/sanpham")
    def products():
        """Tìm sản phẩm trong nguồn và tạo bài. Cố ý KHÔNG có nút đăng ngay."""
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
        conn = connect()
        pending = conn.execute(
            "SELECT COUNT(*) FROM post WHERE status IN ('PENDING_REVIEW','DRAFT')").fetchone()[0]
        conn.close()
        return render_template("products.html", page="san-pham", items=items, q=q, err=err,
                               source_name=source_name or os.environ.get("ACP_SOURCE", "mock"),
                               pending_review=pending)

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
        rows = [dict(r) for r in conn.execute("""
            SELECT p.*, pr.name AS product_name, pr.category_code, pr.current_price,
                   pr.commission_value, pr.rating, pr.review_count, pr.sold_count,
                   ch.handle AS channel_handle, t.name AS template_name
            FROM post p
            JOIN product pr ON pr.id = p.product_id
            JOIN channel ch ON ch.id = p.channel_id
            LEFT JOIN caption_template t ON t.id = p.caption_template_id
            WHERE p.status IN ('PENDING_REVIEW', 'DRAFT')
            ORDER BY p.created_at DESC""").fetchall()]
        recent = [dict(r) for r in conn.execute("""
            SELECT p.id, p.status, p.scheduled_at, p.published_at, pr.name AS product_name
            FROM post p JOIN product pr ON pr.id = p.product_id
            WHERE p.status IN ('SCHEDULED','PUBLISHED','REJECTED')
            ORDER BY p.updated_at DESC LIMIT 8""").fetchall()]
        conn.close()
        return render_template("review.html", page="duyet", posts=rows, recent=recent)

    @app.route("/duyet/<post_id>/<action>", methods=["POST"])
    def review_action(post_id, action):
        conn = connect()
        if action == "approve":
            res = pipeline.approve_post(conn, post_id, actor="operator",
                                        caption_override=request.form.get("caption") or None)
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
