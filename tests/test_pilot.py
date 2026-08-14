"""Test cho phần pilot: nguồn TikTok Shop, factory, single-product, bảo mật web.

    python3 -m acp.tests.test_pilot
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_tmp = tempfile.mkdtemp()
os.environ["ACP_DB"] = os.path.join(_tmp, "pilot.db")

from acp.core import db  # noqa: E402
db.DB_PATH = os.environ["ACP_DB"]

from acp.adapters import factory  # noqa: E402
from acp.adapters.live import AT_BASE, AccessTradeSource  # noqa: E402
from acp.adapters.mock import MockAccessTrade  # noqa: E402
from acp.adapters.tiktokshop import AT_ROOT, AccessTradeTikTokShopSource  # noqa: E402
from acp.core import crypto, niche, pipeline, scoring  # noqa: E402
from acp.core import content  # noqa: E402
from acp.core.db import connect, init_db, now, ulid  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✓' if cond else '✗'} {name}{'' if cond else '  → ' + str(detail)}")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


def setup():
    init_db()
    conn = connect()
    conn.execute("INSERT INTO campaign (id, code, name, is_active, created_at) VALUES (?,?,?,1,?)",
                 (ulid(), "gd2026", "Chiến dịch test", now()))
    conn.execute("INSERT INTO caption_template (id, code, name, body, is_active) VALUES (?,?,?,?,1)",
                 (ulid(), "spec_highlight", "Nêu thông số", "spec_highlight"))
    conn.execute("""INSERT INTO channel (id, code, platform, handle, external_user_id, status,
                    token_encrypted, daily_post_cap, min_gap_minutes, niches, created_at)
                    VALUES (?,?,'threads',?,?,'ACTIVE',?,?,?,?,?)""",
                 (ulid(), "ch1", "@test", "uid1", crypto.encrypt("tok"), 5, 90, "[]", now()))
    scoring.save_config(conn, scoring.DEFAULT_WEIGHTS, scoring.DEFAULT_FILTERS, "test")
    conn.close()


# --------------------------------------------------------------------- URL

def test_no_double_version_prefix():
    """Hồi quy: AT_BASE đã chứa /v1, nên path KHÔNG được bắt đầu bằng /v1 nữa.

    Lỗi này từng lọt vào bản giao và sẽ làm hỏng MỌI lời gọi live -- URL thành
    .../v1/v1/transactions. Test đọc thẳng source để chặn tái diễn.
    """
    print("\nGhép URL")
    check("AT_BASE kết thúc bằng /v1", AT_BASE.endswith("/v1"), AT_BASE)

    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "adapters", "live.py")
    body = open(src, encoding="utf-8").read()
    bad = [ln.strip() for ln in body.splitlines()
           if ('_get("/v1' in ln or '_post("/v1' in ln)]
    check("live.py không có path lặp /v1", not bad, bad)

    tk = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "adapters", "tiktokshop.py")
    check("AT_ROOT không chứa số phiên bản", not AT_ROOT.rstrip("/").endswith(("/v1", "/v2")), AT_ROOT)
    tbody = open(tk, encoding="utf-8").read()
    check("tiktokshop.py ghi full path có phiên bản",
          '"/v2/tiktokshop_product_feeds"' in tbody, "thiếu endpoint feed v2")


# ------------------------------------------------------------ TikTok Shop

def test_tiktok_normalize():
    print("\nChuẩn hoá dữ liệu TikTok Shop")
    data = fixture("tiktokshop_feed_v2.json")
    rows = data["data"]["products"]

    p = AccessTradeTikTokShopSource.normalize(rows[0])
    check("lấy đúng mã sản phẩm", p.external_product_id == "1729384756102938475", p.external_product_id)
    check("lấy đúng tên", p.name.startswith("Máy xay cầm tay"), p.name)
    check("giá đọc từ sales_price.minimum_amount", p.current_price == 289000, p.current_price)
    check("giá gốc đọc từ original_price", p.original_price == 459000, p.original_price)
    check("hoa hồng tuyệt đối", p.commission_value == 23120, p.commission_value)
    check("tỷ lệ 8 được chuẩn hoá thành 0.08", p.commission_rate == 0.08, p.commission_rate)
    check("units_sold vào sold_count", p.sold_count == 12480, p.sold_count)
    check("merchant lấy từ shop.name", p.merchant == "Nhà Bếp Thông Minh Official", p.merchant)
    check("product_url lấy từ detail_link", p.product_url.endswith("EXAMPLE001/"), p.product_url)
    check("ảnh lấy từ main_image_url", "EXAMPLE001" in (p.image_url_original or ""), p.image_url_original)
    check("danh mục chuẩn hoá không dấu cách", p.category_code == "home-appliances", p.category_code)

    # Bản ghi 2: chỉ có rate 0.06, amount = 0 -> phải tự tính ra tiền hoa hồng.
    p2 = AccessTradeTikTokShopSource.normalize(rows[1])
    check("hoa hồng tự tính khi API chỉ trả tỷ lệ",
          p2.commission_value == int(199000 * 0.06), p2.commission_value)
    check("tỷ lệ 0.06 giữ nguyên không nhân 100", p2.commission_rate == 0.06, p2.commission_rate)


def test_tiktok_search_filters():
    print("\nLọc kết quả TikTok Shop")
    data = fixture("tiktokshop_feed_v2.json")
    rows = data["data"]["products"]
    good = []
    for r in rows:
        try:
            p = AccessTradeTikTokShopSource.normalize(r)
        except Exception:
            continue
        if p.external_product_id and p.name and p.current_price > 0:
            good.append(p)
    check("bản ghi thiếu id bị loại", all(p.external_product_id for p in good))
    check("bản ghi giá 0 bị loại", all(p.current_price > 0 for p in good))
    check("giữ lại đúng 2 sản phẩm hợp lệ", len(good) == 2, len(good))
    check("một bản ghi hỏng không làm gãy cả trang", len(good) > 0)


def test_link_response_parsing():
    print("\nĐọc response tạo link")
    data = fixture("accesstrade_create_link.json")
    link = AccessTradeTikTokShopSource.parse_link_response(data)
    check("ưu tiên short_link", link.startswith("https://shorten."), link)

    no_short = {"data": {"success_link": [{"aff_link": "https://tracking.example/x"}]}}
    check("không có short_link thì lấy aff_link",
          AccessTradeTikTokShopSource.parse_link_response(no_short).endswith("/x"))

    try:
        AccessTradeTikTokShopSource.parse_link_response({"data": {"success_link": [], "error_link": ["hỏng"]}})
        check("link rỗng phải ném lỗi", False, "không ném lỗi")
    except Exception as e:
        check("link rỗng phải ném lỗi", "không tạo được link" in str(e), str(e))


def test_transaction_status_mapping():
    print("\nMap trạng thái giao dịch")
    # Accesstrade trả trạng thái dạng SỐ. Coi nhầm là chuỗi thì báo cáo doanh thu sai.
    row = {"transaction_id": "T1", "product_id": "P1", "transaction_value": "450000",
           "commission": "27000", "status": 1, "transaction_time": "2026-08-01T10:00:00",
           "utm_content": "POST123", "_extra": {"parameters": {"sub1": "POST123", "sub3": "B"}}}
    n = AccessTradeSource._normalize_transaction(row)
    check("status=1 thành approved", n["status"] == "approved", n["status"])
    check("status=0 thành pending",
          AccessTradeSource._normalize_transaction(dict(row, status=0))["status"] == "pending")
    check("status=2 thành rejected",
          AccessTradeSource._normalize_transaction(dict(row, status=2))["status"] == "rejected")
    check("số tiền ép về kiểu nguyên", n["sale_amount"] == 450000 and n["commission"] == 27000)
    check("sub1 đọc từ _extra.parameters", n["sub1"] == "POST123", n["sub1"])


# ---------------------------------------------------------------- factory

def test_factory():
    print("\nFactory adapter")
    factory.reset_cache()
    os.environ.pop("ACP_ADAPTER", None)
    os.environ.pop("ACP_SOURCE", None)
    check("mặc định là giả lập", not factory.is_live())
    check("nguồn mặc định là mock", factory.get_source().name == "accesstrade",
          factory.get_source().name)
    check("chọn được nguồn tiktokshop",
          factory.get_source("tiktokshop").name == "accesstrade_tiktokshop")
    try:
        factory.get_source("khong-ton-tai")
        check("nguồn sai phải ném lỗi", False, "không ném lỗi")
    except ValueError:
        check("nguồn sai phải ném lỗi", True)

    from acp.adapters.base import Publisher

    ctx = factory.build_context()
    check("context có đủ source, publishers, storage",
          all(k in ctx for k in ("source", "publishers", "storage")), list(ctx))
    check("publishers có threads là Publisher",
          isinstance(ctx["publishers"].get("threads"), Publisher), ctx["publishers"])

    # Web và CLI phải đi qua cùng một factory -- lỗi cũ là web hardcode mock.
    srv = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "server.py")
    body = open(srv, encoding="utf-8").read()
    check("server.py không khởi tạo mock trực tiếp",
          "MockThreads(" not in body and "MockAccessTrade(" not in body)
    check("server.py dùng factory.build_context", "factory.build_context()" in body)


# --------------------------------------------------------- single product

def test_single_product_flow():
    print("\nMột sản phẩm thành một bài")
    factory.reset_cache()
    conn = connect()
    src = MockAccessTrade()
    sample = src.fetch_products(limit=200)
    target = next(p for p in sample if p.rating and p.rating >= 4.5 and p.current_price > 0)

    ctx = {"source": src, "publishers": {}, "storage": _FakeStorage()}
    res = pipeline.create_post_for_product(conn, ctx, target.external_product_id, "gd2026")
    check("tạo bài thành công", res.get("ok"), res.get("error"))
    check("dừng ở chờ duyệt, KHÔNG đăng", res.get("status") in ("PENDING_REVIEW", "DRAFT"), res.get("status"))

    post = conn.execute("SELECT * FROM post WHERE id=?", (res["post_id"],)).fetchone()
    check("bài chưa có thread_id", post["thread_id"] is None)
    check("không có job publish nào được tạo",
          conn.execute("SELECT COUNT(*) FROM job_queue WHERE job_type='PUBLISH_POST'").fetchone()[0] == 0)
    check("caption trong giới hạn 500 ký tự", len(post["caption_final"]) <= 500, len(post["caption_final"]))
    check("có link affiliate", bool(post["affiliate_link"]))
    check("post_id nằm trong link", res["post_id"] in post["affiliate_link"])
    check("sub_id_payload có đủ 4 trường", post["sub_id_payload"].count("sub") >= 4)

    # Bỏ qua chấm điểm là có chủ ý, nhưng rào chắn nội dung thì không được bỏ.
    check("vẫn có disclosure", pipeline.content.DISCLOSURE_DEFAULT in post["caption_final"])

    again = pipeline.create_post_for_product(conn, ctx, target.external_product_id, "gd2026")
    check("gọi lại tạo bài mới chứ không trùng sản phẩm trong kho",
          again.get("ok") and again["product_id"] == res["product_id"],
          f"{again.get('error')}")
    check("mã sản phẩm không tồn tại thì báo lỗi rõ",
          pipeline.create_post_for_product(conn, ctx, "KHONG_CO_THAT", "gd2026").get("ok") is False)
    conn.close()


def test_manual_shopee_post_flow():
    print("\\nShopee affiliate có sẵn thành một bài")
    from acp.adapters.base import RawProduct

    class _NoTrackingSource:
        name = "manual_shopee"
        def create_tracking_link(self, *args, **kwargs):
            raise AssertionError("manual Shopee không được gọi create_tracking_link")

    conn = connect()
    raw = RawProduct(
        external_product_id="SHOPEE_TEST_1",
        name="Váy hoa nữ test",
        current_price=289000,
        original_price=399000,
        commission_value=0,
        commission_rate=None,
        category_code="khac",
        product_url="https://shopee.vn/vay-i.123.456",
        merchant="shopee.vn",
        image_url_original="https://img.example/product.jpg",
        image_path_local=None,
    )
    link = "https://s.shopee.vn/affiliate-EXACT"
    res = pipeline.create_post_from_manual_affiliate_product(
        conn, {"storage": _FakeStorage()}, _NoTrackingSource(), raw,
        affiliate_url=link, campaign_code="gd2026", channel_code="ch1")
    check("manual tạo bài thành công", res.get("ok"), res.get("error"))
    post = conn.execute("SELECT * FROM post WHERE id=?", (res["post_id"],)).fetchone()
    check("giữ nguyên affiliate link", post["affiliate_link"] == link, post["affiliate_link"])
    check("manual không có thread_id", post["thread_id"] is None)
    check("manual không tạo publish job",
          conn.execute("SELECT COUNT(*) FROM job_queue WHERE job_type='PUBLISH_POST'").fetchone()[0] == 0)
    payload = json.loads(post["sub_id_payload"])
    check("manual ghi provider rõ ràng", payload.get("provider") == "shopee_direct", payload)
    check("manual ghi link mode prebuilt", payload.get("link_mode") == "prebuilt", payload)
    check("manual không giả sub1", not any(k.startswith("sub") for k in payload), payload)
    check("manual không nhúng post id vào link", res["post_id"] not in post["affiliate_link"])
    check("manual vẫn có disclosure", content.DISCLOSURE_DEFAULT in post["caption_final"])
    conn.close()


class _FakeStorage:
    kind = "fake"

    def put(self, path):
        return "https://cdn.example.com/" + os.path.basename(path)


# -------------------------------------------------------------- bảo mật web

def test_web_security():
    print("\nBảo mật web")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    os.environ["ACP_WEBHOOK_SECRET"] = "khoa-webhook"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()

    check("dashboard chuyển hướng khi chưa đăng nhập", c.get("/").status_code == 302)
    check("trang duyệt cũng bị chặn", c.get("/duyet").status_code == 302)
    check("chọn sản phẩm cũng bị chặn", c.get("/sanpham").status_code == 302)
    check("healthz vẫn mở", c.get("/healthz").status_code == 200)
    check("media vẫn mở cho Meta tải ảnh", c.get("/media/khongco.jpg").status_code in (200, 404))

    check("mật khẩu sai không vào được",
          c.post("/dangnhap", data={"password": "sai"}).status_code == 200)
    r = c.post("/dangnhap", data={"password": "matkhau-test"})
    check("mật khẩu đúng thì vào được", r.status_code == 302, r.status_code)
    check("sau đăng nhập xem được dashboard", c.get("/").status_code == 200)

    # Shopee direct: web flow is server-rendered, CSRF-protected and must not
    # instantiate/call the ACCESSTRADE source just to open the manual tab.
    from acp.adapters.base import RawProduct
    from acp.adapters.shopee_affiliate import ProductMetadata, ResolvedAffiliateUrl

    class _FakeManualShopee:
        name = "manual_shopee"

        def resolve(self, affiliate_url):
            return ResolvedAffiliateUrl(
                affiliate_url=affiliate_url,
                product_url="https://shopee.vn/vay-i.123.456")

        def metadata(self, product_url):
            return ProductMetadata(
                name="Váy hoa nữ test", current_price=289000, original_price=399000,
                image_url="https://img.example/product.jpg", shop="Shop Test")

        def validate_confirmed_urls(self, affiliate_url, product_url):
            if not affiliate_url.startswith("https://s.shopee.vn/"):
                raise AssertionError("affiliate URL bị đổi")
            if product_url != "https://shopee.vn/vay-i.123.456":
                raise AssertionError("product URL bị đổi")

        def prepare_product(self, confirmed, media_dir):
            return RawProduct(
                external_product_id="456", name=confirmed.name,
                current_price=confirmed.current_price, original_price=confirmed.original_price,
                commission_value=0, commission_rate=None, category_code="khac",
                product_url=confirmed.product_url, merchant="shopee.vn",
                image_url_original=confirmed.image_url, image_path_local=None)

        def create_tracking_link(self, *args, **kwargs):
            raise AssertionError("manual Shopee không được gọi create_tracking_link")

    app.config["SHOPEE_SOURCE_FACTORY"] = lambda: _FakeManualShopee()
    original_get_source = factory.get_source
    try:
        factory.get_source = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("manual tab không được gọi ACCESSTRADE source"))
        manual_page = c.get("/sanpham?mode=affiliate")
    finally:
        factory.get_source = original_get_source
    check("tab affiliate mở mà không gọi source ACCESSTRADE",
          manual_page.status_code == 200 and "Nhập link affiliate" in manual_page.get_data(as_text=True))

    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    check("resolve affiliate thiếu CSRF bị chặn",
          c.post("/sanpham/affiliate/resolve", data={"affiliate_url": "https://s.shopee.vn/abc"}).status_code == 400)

    before = connect().execute("SELECT COUNT(*) FROM post").fetchone()[0]
    resolved_page = c.post("/sanpham/affiliate/resolve", data={
        "_csrf": csrf, "affiliate_url": "https://s.shopee.vn/abc"})
    body = resolved_page.get_data(as_text=True)
    check("resolve affiliate luôn mở màn hình xác nhận",
          resolved_page.status_code == 200 and "Váy hoa nữ test" in body and "Tạo bài nháp" in body)
    conn = connect()
    after_resolve = conn.execute("SELECT COUNT(*) FROM post").fetchone()[0]
    conn.close()
    check("resolve metadata chưa tạo post", before == after_resolve, (before, after_resolve))

    invalid_create = c.post("/sanpham/affiliate/create", data={
        "_csrf": csrf,
        "affiliate_url": "https://s.shopee.vn/abc",
        "product_url": "https://shopee.vn/vay-i.123.456",
        "name": "", "current_price": "0", "image_url": "", "channel_code": "ch1",
    })
    check("create thiếu tên giá ảnh bị từ chối", invalid_create.status_code == 400, invalid_create.status_code)

    created = c.post("/sanpham/affiliate/create", data={
        "_csrf": csrf,
        "affiliate_url": "https://s.shopee.vn/abc",
        "product_url": "https://shopee.vn/vay-i.123.456",
        "name": "Váy hoa nữ test",
        "current_price": "289000",
        "original_price": "399000",
        "image_url": "https://img.example/product.jpg",
        "shop": "Shop Test",
        "channel_code": "ch1",
    })
    check("create affiliate draft redirect sang duyệt",
          created.status_code == 302 and "/duyet" in created.location, getattr(created, "location", ""))
    conn = connect()
    manual = conn.execute("""
        SELECT p.*, pr.source FROM post p
        JOIN product pr ON pr.id=p.product_id
        WHERE pr.source='manual_shopee'
        ORDER BY p.created_at DESC LIMIT 1
    """).fetchone()
    check("web manual tạo đúng source", manual is not None and manual["source"] == "manual_shopee")
    check("web manual dừng ở review", manual and manual["status"] in ("PENDING_REVIEW", "DRAFT"), manual["status"] if manual else None)
    check("web manual không có thread_id", manual and manual["thread_id"] is None)
    check("web manual giữ nguyên affiliate link", manual and manual["affiliate_link"] == "https://s.shopee.vn/abc",
          manual["affiliate_link"] if manual else None)
    payload = json.loads(manual["sub_id_payload"]) if manual else {}
    check("web manual dùng attribution shopee_direct", payload.get("provider") == "shopee_direct", payload)
    check("web manual không có fake sub id", not any(k.startswith("sub") for k in payload), payload)
    check("web manual không tạo publish job",
          conn.execute("SELECT COUNT(*) FROM job_queue WHERE job_type='PUBLISH_POST'").fetchone()[0] == 0)
    conn.close()

    check("POST thiếu CSRF bị chặn",
          c.post("/vanhanh/work", data={}).status_code == 400)

    check("webhook sai khoá bị chặn",
          c.get("/webhook/at/postback?transaction_id=T&external_product_id=P&k=sai").status_code == 403)
    check("webhook thiếu khoá bị chặn",
          c.get("/webhook/at/postback?transaction_id=T&external_product_id=P").status_code == 403)
    ok = c.get("/webhook/at/postback?transaction_id=T9&external_product_id=P9"
               "&sale_amount=1000&commission=60&k=khoa-webhook")
    check("webhook đúng khoá được nhận", ok.status_code == 200, ok.status_code)

    check("OAuth callback không cần đăng nhập",
          c.get("/oauth/threads/callback?code=ABC123").status_code == 200)
    check("OAuth callback thiếu code trả 400",
          c.get("/oauth/threads/callback").status_code == 400)
    check("deauthorize đánh dấu kênh cần kết nối lại",
          c.post("/oauth/threads/deauthorize").status_code == 200)

    conn = connect()
    st = conn.execute("SELECT status FROM channel LIMIT 1").fetchone()["status"]
    conn.close()
    check("kênh chuyển sang NEEDS_REAUTH", st == "NEEDS_REAUTH", st)

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY", "ACP_WEBHOOK_SECRET"):
        os.environ.pop(var, None)


class _FakeHttpResponse:
    def __init__(self, status=200, headers=None, body=b"", url="https://shopee.vn/x"):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.url = url

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        pass


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.trust_env = True
        self.headers = {}
        self.cookies = type("_Cookies", (), {"clear": lambda self: None})()

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected HTTP call")
        return self.responses.pop(0)


def _public_dns(host, port, *args, **kwargs):
    return [(2, 1, 6, "", ("93.184.216.34", port or 443))]


def _private_dns(host, port, *args, **kwargs):
    return [(2, 1, 6, "", ("127.0.0.1", port or 443))]


def test_shopee_safe_url():
    print("\\nLink affiliate Shopee an toàn")
    from acp.adapters.safe_http import SafeHttpClient, SafeHttpError
    from acp.adapters.shopee_affiliate import AffiliateUrlResolver, AffiliateImportError

    resolver = AffiliateUrlResolver(SafeHttpClient(session=_FakeSession([]), dns_resolver=_public_dns))
    check("nhận host shopee.vn", resolver.validate_shopee_url("https://shopee.vn/item-i.1.2").startswith("https://shopee.vn"))
    check("nhận host s.shopee.vn", resolver.validate_shopee_url("https://s.shopee.vn/abc").startswith("https://s.shopee.vn"))

    for bad in ("file:///etc/passwd", "ftp://shopee.vn/x", "https://example.com/x"):
        try:
            resolver.validate_shopee_url(bad)
            check(f"chặn URL không hợp lệ {bad}", False)
        except AffiliateImportError:
            check(f"chặn URL không hợp lệ {bad}", True)

    try:
        SafeHttpClient(session=_FakeSession([]), dns_resolver=_private_dns).get(
            "https://shopee.vn/x", allowed_hosts={"shopee.vn"})
        check("chặn DNS trỏ loopback/private", False)
    except SafeHttpError:
        check("chặn DNS trỏ loopback/private", True)

    session = _FakeSession([
        _FakeHttpResponse(302, {"Location": "https://shopee.vn/product-i.1.2"}),
        _FakeHttpResponse(200, {"Content-Type": "text/html; charset=utf-8"}, b"<html></html>",
                          "https://shopee.vn/product-i.1.2"),
    ])
    resolved = AffiliateUrlResolver(SafeHttpClient(session=session, dns_resolver=_public_dns)).resolve(
        "https://s.shopee.vn/abc")
    check("follow redirect thủ công tới Shopee", resolved.product_url == "https://shopee.vn/product/1/2", resolved.product_url)
    check("không dùng auto redirect", all(call[1].get("allow_redirects") is False for call in session.calls))

    # Shopee affiliate links currently may land on an opaapi/lp URL carrying a
    # short-lived credential_token.  The operator-facing product URL must be
    # canonical and must never persist that token.
    opaapi = _FakeSession([
        _FakeHttpResponse(302, {"Location":
            "https://shopee.vn/opaapi/lp/252198883/269450640062?__mobile__=1&credential_token=SECRET"}),
        _FakeHttpResponse(200, {"Content-Type": "text/html; charset=utf-8"}, b"<html></html>"),
    ])
    resolved_opaapi = AffiliateUrlResolver(
        SafeHttpClient(session=opaapi, dns_resolver=_public_dns)).resolve("https://s.shopee.vn/xyz")
    check("opaapi được chuẩn hoá thành URL sản phẩm sạch",
          resolved_opaapi.product_url == "https://shopee.vn/product/252198883/269450640062",
          resolved_opaapi.product_url)
    check("URL sản phẩm không giữ credential_token",
          "credential_token" not in resolved_opaapi.product_url, resolved_opaapi.product_url)

    # Some Shopee affiliate links land on /opaanlp/<shop>/<item> instead of
    # /opaapi/lp/<shop>/<item>. It carries the same product identity and must
    # be canonicalized before metadata lookup.
    opaanlp = _FakeSession([
        _FakeHttpResponse(302, {"Location":
            "https://shopee.vn/opaanlp/252198883/26945064006?credential_token=SECRET"}),
        _FakeHttpResponse(200, {"Content-Type": "text/html; charset=utf-8"}, b"<html></html>"),
    ])
    resolved_opaanlp = AffiliateUrlResolver(
        SafeHttpClient(session=opaanlp, dns_resolver=_public_dns)).resolve("https://s.shopee.vn/opaanlp")
    check("opaanlp được chuẩn hoá thành URL sản phẩm sạch",
          resolved_opaanlp.product_url == "https://shopee.vn/product/252198883/26945064006",
          resolved_opaanlp.product_url)

    evil = _FakeSession([_FakeHttpResponse(302, {"Location": "https://evil.example/x"})])
    try:
        AffiliateUrlResolver(SafeHttpClient(session=evil, dns_resolver=_public_dns)).resolve("https://s.shopee.vn/abc")
        check("chặn redirect ra host ngoài allowlist", False)
    except AffiliateImportError:
        check("chặn redirect ra host ngoài allowlist", True)


def fixture_text(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


def test_shopee_metadata():
    print("\\nMetadata Shopee manual")
    from acp.adapters.safe_http import SafeHttpResponse
    from acp.adapters.shopee_affiliate import (
        ConfirmedProductInput, ManualShopeeSource, ProductMetadataResolver,
    )

    class _StaticHttp:
        def __init__(self, body, ctype="text/html"):
            self.body = body.encode("utf-8")
            self.ctype = ctype

        def get(self, url, allowed_hosts=None, expected_content_prefix=None):
            return SafeHttpResponse(url, self.body, self.ctype)

    meta = ProductMetadataResolver(_StaticHttp(fixture_text("shopee_product_jsonld.html"))).resolve(
        "https://shopee.vn/vay-i.123.456")
    check("JSON-LD ưu tiên tên sản phẩm", meta.name == "Váy hoa nữ test", meta.name)
    check("JSON-LD đọc giá VND", meta.current_price == 289000, meta.current_price)
    check("JSON-LD đọc ảnh", meta.image_url.endswith("/product.jpg"), meta.image_url)
    check("JSON-LD đọc shop/brand", meta.shop == "Shop Test", meta.shop)

    og = ProductMetadataResolver(_StaticHttp(fixture_text("shopee_product_og.html"))).resolve(
        "https://shopee.vn/tui-i.12.34")
    check("OpenGraph fallback tên", og.name == "Túi xách nữ test", og.name)
    check("OpenGraph fallback giá", og.current_price == 199000, og.current_price)
    check("metadata thiếu shop không bịa", og.shop is None, og.shop)

    class _HtmlThenApi:
        def __init__(self):
            self.calls = []

        def get(self, url, allowed_hosts=None, expected_content_prefix=None):
            self.calls.append((url, expected_content_prefix))
            if "/api/v4/pdp/get_pc?" in url:
                body = json.dumps({
                    "error": None,
                    "data": {
                        "item": {
                            "title": "Váy tự lấy từ Shopee",
                            "image": "abc123imagehash",
                        },
                        "product_price": {
                            "price": {"single_value": 28900000000},
                            "price_before_discount": {"single_value": 45900000000},
                        },
                        "shop_detailed": {"name": "Shop tự động"},
                    }
                }).encode("utf-8")
                return SafeHttpResponse(url, body, "application/json")
            return SafeHttpResponse(url, b"<html><body></body></html>", "text/html")

    auto_http = _HtmlThenApi()
    auto = ProductMetadataResolver(auto_http).resolve(
        "https://shopee.vn/product/252198883/269450640062")
    check("metadata thiếu trong HTML thì thử JSON public",
          any("/api/v4/pdp/get_pc?" in call[0] for call in auto_http.calls), auto_http.calls)
    check("JSON public tự lấy tên", auto.name == "Váy tự lấy từ Shopee", auto.name)
    check("JSON public chuẩn hoá giá Shopee", auto.current_price == 289000, auto.current_price)
    check("JSON public đọc giá gốc", auto.original_price == 459000, auto.original_price)
    check("JSON public dựng URL ảnh CDN",
          auto.image_url == "https://down-vn.img.susercontent.com/file/abc123imagehash", auto.image_url)
    check("JSON public đọc shop", auto.shop == "Shop tự động", auto.shop)

    confirmed = ConfirmedProductInput(
        affiliate_url="https://s.shopee.vn/abc",
        product_url="https://shopee.vn/vay-i.123.456",
        name="Váy hoa nữ test",
        current_price=289000,
        original_price=None,
        image_url="https://img.example/product.jpg",
        shop="Shop Test",
    )
    raw = ManualShopeeSource.normalize_confirmed(confirmed)
    check("lấy item id từ URL", raw.external_product_id == "456", raw.external_product_id)
    check("merchant cố định shopee.vn", raw.merchant == "shopee.vn", raw.merchant)
    check("không bịa commission", raw.commission_value == 0 and raw.commission_rate is None)
    check("không bịa rating/review", raw.rating is None and raw.review_count == 0)

    fallback = ConfirmedProductInput(
        affiliate_url="https://s.shopee.vn/abc",
        product_url="https://shopee.vn/product-khong-co-id?x=1",
        name="SP", current_price=100000, original_price=None,
        image_url="https://img.example/x.jpg", shop=None,
    )
    a = ManualShopeeSource.normalize_confirmed(fallback).external_product_id
    b = ManualShopeeSource.normalize_confirmed(fallback).external_product_id
    check("fallback ID deterministic", a == b and a.startswith("url_"), a)


def test_shopee_image_materialize():
    print("\\nẢnh Shopee manual")
    from io import BytesIO
    from PIL import Image
    from acp.adapters.safe_http import SafeHttpResponse
    from acp.adapters.shopee_affiliate import ManualShopeeSource

    buf = BytesIO()
    Image.new("RGB", (2, 2), "white").save(buf, format="JPEG")
    image_bytes = buf.getvalue()

    class _ImageHttp:
        def get(self, url, allowed_hosts=None, expected_content_prefix=None):
            return SafeHttpResponse(url, image_bytes, "image/jpeg")

    src = ManualShopeeSource(http=_ImageHttp())
    media = tempfile.mkdtemp()
    path = src.materialize_image("https://img.example/product.jpg", media)
    check("tải ảnh vào media source", os.path.isfile(path), path)
    check("tên file không dùng input URL", "img.example" not in os.path.basename(path), path)
    with Image.open(path) as im:
        check("file ảnh Pillow đọc được", im.size == (2, 2), im.size)


def test_shopee_web_contract_source():
    print("\\nHợp đồng web Shopee direct")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    server_body = open(os.path.join(root, "web", "server.py"), encoding="utf-8").read()
    product_tpl = open(os.path.join(root, "web", "templates", "products.html"), encoding="utf-8").read()
    check("có factory riêng cho Shopee direct", 'SHOPEE_SOURCE_FACTORY' in server_body)
    check("có route phân tích affiliate", '/sanpham/affiliate/resolve' in server_body)
    check("có route tạo bài affiliate", '/sanpham/affiliate/create' in server_body)
    check("manual dùng pipeline prebuilt", 'create_post_from_manual_affiliate_product' in server_body)
    check("manual chỉ dựng storage context", '{"storage": storage.get_storage()}' in server_body)
    check("UI có tab nhập affiliate", 'Nhập link affiliate' in product_tpl)
    check("UI có nút phân tích", 'Phân tích link' in product_tpl)
    check("UI có nút tạo bài nháp", 'Tạo bài nháp' in product_tpl)
    check("UI sản phẩm không có Đăng ngay", 'Đăng ngay' not in product_tpl)


def test_dark_premium_template_contract():
    print("\\nDark Premium template contract")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tpl_dir = os.path.join(root, "web", "templates")
    static_css = os.path.join(root, "web", "static", "acp.css")
    base = open(os.path.join(tpl_dir, "base.html"), encoding="utf-8").read()
    check("base tải stylesheet Dark Premium", "acp.css" in base)
    check("base dùng app shell", "app-shell" in base and "sidebar" in base)
    check("CSS dùng accent violet", os.path.exists(static_css) and "#8B5CF6" in open(static_css, encoding="utf-8").read() if os.path.exists(static_css) else False)
    check("CSS có responsive 960px", os.path.exists(static_css) and "max-width: 960px" in open(static_css, encoding="utf-8").read() if os.path.exists(static_css) else False)
    check("CSS có focus-visible", os.path.exists(static_css) and ":focus-visible" in open(static_css, encoding="utf-8").read() if os.path.exists(static_css) else False)
    for name in ("dashboard.html", "products.html", "review.html", "channels.html", "ops.html", "scoring.html"):
        body = open(os.path.join(tpl_dir, name), encoding="utf-8").read()
        check(f"{name} có page header", "page-header" in body)
    product = open(os.path.join(tpl_dir, "products.html"), encoding="utf-8").read()
    check("trang sản phẩm không có action đăng ngay", "Đăng ngay" not in product)


def test_shopee_edge_hardening():
    print("\\nShopee edge hardening")
    from acp.adapters.safe_http import SafeHttpClient, SafeHttpError, SafeHttpResponse
    from acp.adapters.shopee_affiliate import ProductMetadataResolver, ManualShopeeSource, _vnd_int

    check("giá JSON-LD dạng .0 không bị nhân 10", _vnd_int("199000.0") == 199000, _vnd_int("199000.0"))

    malformed = '<html><head><meta property="og:title" content="Fallback OK"><script type="application/ld+json">{bad json</script></head></html>'
    class _Static:
        def get(self, url, allowed_hosts=None, expected_content_prefix=None):
            return SafeHttpResponse(url, malformed.encode(), "text/html")
    meta = ProductMetadataResolver(_Static()).resolve("https://shopee.vn/x-i.1.2")
    check("JSON-LD hỏng vẫn fallback OpenGraph", meta.name == "Fallback OK", meta.name)

    empty = '<html><head></head><body></body></html>'
    class _Empty:
        def get(self, url, allowed_hosts=None, expected_content_prefix=None):
            return SafeHttpResponse(url, empty.encode(), "text/html")
    blank = ProductMetadataResolver(_Empty()).resolve("https://shopee.vn/x-i.1.2")
    check("HTML không metadata không bịa dữ liệu",
          blank.name is None and blank.current_price is None and blank.image_url is None and blank.shop is None)

    too_big = _FakeSession([_FakeHttpResponse(200, {"Content-Type":"text/html"}, b"123456")])
    try:
        SafeHttpClient(session=too_big, dns_resolver=_public_dns, max_bytes=5).get(
            "https://shopee.vn/x", allowed_hosts={"shopee.vn"}, expected_content_prefix="text/html")
        check("chặn response quá lớn", False)
    except SafeHttpError:
        check("chặn response quá lớn", True)

    wrong = _FakeSession([_FakeHttpResponse(200, {"Content-Type":"application/json"}, b"{}")])
    try:
        SafeHttpClient(session=wrong, dns_resolver=_public_dns).get(
            "https://shopee.vn/x", allowed_hosts={"shopee.vn"}, expected_content_prefix="text/html")
        check("chặn metadata sai content type", False)
    except SafeHttpError:
        check("chặn metadata sai content type", True)

    redirects = _FakeSession([
        _FakeHttpResponse(302, {"Location":"https://shopee.vn/b"}),
        _FakeHttpResponse(302, {"Location":"https://shopee.vn/c"}),
    ])
    try:
        SafeHttpClient(session=redirects, dns_resolver=_public_dns, max_redirects=1).get(
            "https://shopee.vn/a", allowed_hosts={"shopee.vn"})
        check("giới hạn số redirect", False)
    except SafeHttpError:
        check("giới hạn số redirect", True)

    class _WrongImage:
        def get(self, url, allowed_hosts=None, expected_content_prefix=None):
            return SafeHttpResponse(url, b"<html>", "text/html")
    try:
        ManualShopeeSource(http=_WrongImage()).materialize_image("https://img.example/x", tempfile.mkdtemp())
        check("chặn URL ảnh trả HTML", False)
    except Exception:
        check("chặn URL ảnh trả HTML", True)


def test_production_guard():
    print("\nChặn cấu hình thiếu an toàn ở production")
    os.environ["ACP_ENV"] = "production"
    os.environ.pop("ACP_ADMIN_PASSWORD", None)
    from acp.web.server import create_app
    try:
        create_app()
        check("production thiếu mật khẩu phải từ chối chạy", False, "app vẫn khởi động")
    except RuntimeError as e:
        check("production thiếu mật khẩu phải từ chối chạy", "ACP_ADMIN_PASSWORD" in str(e))
    os.environ.pop("ACP_ENV", None)


def test_niche_matching():
    print("\nLọc theo chủ đề kênh")
    N = ["thoi-trang-nu", "my-pham"]

    def m(name, cat="", merchant="shop"):
        return not niche.match_reasons({"name": name, "category_code": cat, "merchant": merchant}, N)

    check("nhận váy nữ", m("Váy hoa nhí dáng suông", "thoi-trang"))
    check("nhận túi xách nữ", m("Túi xách nữ da PU", "fashion"))
    check("nhận mỹ phẩm", m("Serum vitamin C 30ml", "beauty"))
    check("loại đồ gia dụng", not m("Nồi chiên không dầu", "gia-dung"))
    check("loại hàng nam dù cùng danh mục thời trang", not m("Giày sneaker nam", "fashion"))
    check("loại hàng trẻ em", not m("Váy bé gái 5 tuổi", "thoi-trang"))
    check("loại thực phẩm chức năng khỏi mỹ phẩm", not m("Viên uống collagen", "beauty"))

    # Bỏ dấu rồi so chuỗi con là sai: "dặm" thành "dam" trùng "đầm",
    # "tốc" thành "toc" trùng "tóc". Hai ca này từng lọt qua.
    check("“ăn dặm” không bị nhầm thành “đầm”", not m("Ghế ăn dặm Comotomo", "me-va-be"))
    check("“siêu tốc” không bị nhầm thành “ủ tóc”", not m("Bình đun siêu tốc 1L", "gia-dung"))
    check("“ngọc trai” không bị nhầm thành “bé trai”", m("Kẹp tóc ngọc trai", "phu-kien-thoi-trang"))
    check("“Việt Nam” không bị coi là hàng nam", m("Đầm maxi thương hiệu Việt Nam", "fashion"))

    # Danh mục rộng của sàn gộp cả hàng nam -> phải có từ khoá cụ thể mới nhận.
    check("chỉ trùng danh mục rộng thì chưa nhận", not m("Quần jogger Aristino", "thoi-trang"))
    check("máy massage không phải mỹ phẩm", not m("Máy massage cầm tay", "cham-soc-ca-nhan"))

    check("không bật chủ đề thì không lọc gì",
          niche.match_reasons({"name": "Nồi chiên", "category_code": "gia-dung"}, []) == [])


def test_niche_content_guard():
    print("\nRào chắn nội dung theo chủ đề")
    link, d = "https://x.co/a", content.DISCLOSURE_DEFAULT

    def v(txt, n):
        return content.validate(f"{txt}\n\n{link}\n\n{d}", niches=n)

    clean = "Serum vitamin C 30ml. Đang bán 289.000đ."
    check("caption sạch vẫn qua", v(clean, ["my-pham"]) == [], v(clean, ["my-pham"]))
    check("khẳng định điều trị bị chặn khi bật mỹ phẩm",
          any("điều trị" in p for p in v("Serum giúp trị mụn hiệu quả", ["my-pham"])))
    check("bắt được cả biến thể viết không dấu",
          any("điều trị" in p for p in v("Serum giup tri mun", ["my-pham"])))
    check("chặn cam kết trắng da", any("điều trị" in p for p in v("Kem làm trắng da cấp tốc", ["my-pham"])))
    check("không bật mỹ phẩm thì không áp luật riêng", v("Serum giúp trị mụn", None) == [])
    check("thời trang nữ không có cụm cấm riêng", niche.banned_phrases(["thoi-trang-nu"]) == [])
    check("mỹ phẩm có cụm cấm riêng", len(niche.banned_phrases(["my-pham"])) > 10)


def test_niche_per_channel():
    print("\nChủ đề gắn theo từng kênh")
    from acp.core.db import ulid as _ulid
    conn = connect()

    ids = {}
    for code, handle, nl in [("c_nu", "@nu", ["thoi-trang-nu", "my-pham"]),
                             ("c_be", "@be", ["me-va-be"]),
                             ("c_pet", "@pet", ["thu-cung"]),
                             ("c_all", "@all", [])]:
        cid = _ulid(); ids[code] = cid
        conn.execute("""INSERT INTO channel (id, code, platform, handle, external_user_id, status,
                        token_encrypted, daily_post_cap, min_gap_minutes, niches, created_at)
                        VALUES (?,?,'threads',?,?,'ACTIVE',?,5,90,'[]',?)""",
                     (cid, code, handle, f"uid_{code}", crypto.encrypt("t"), now()))
        pipeline.set_channel_niches(conn, cid, nl)

    check("đọc lại đúng chủ đề của kênh nữ",
          set(pipeline.channel_niches(conn, ids["c_nu"])) == {"thoi-trang-nu", "my-pham"})
    check("kênh bé có chủ đề riêng", pipeline.channel_niches(conn, ids["c_be"]) == ["me-va-be"])
    check("kênh không đặt chủ đề trả về rỗng", pipeline.channel_niches(conn, ids["c_all"]) == [])
    check("mã chủ đề không hợp lệ bị bỏ qua khi lưu",
          pipeline.set_channel_niches(conn, ids["c_all"], ["khong-co-that", "thu-cung"]) == ["thu-cung"])
    pipeline.set_channel_niches(conn, ids["c_all"], [])

    products = [("Váy hoa nhí dáng suông", "thoi-trang"), ("Bình sữa cổ rộng 240ml", "me-va-be"),
                ("Cát vệ sinh cho mèo 10L", "thu-cung"), ("Nồi chiên không dầu 5L", "gia-dung")]
    for name, cat in products:
        conn.execute("""INSERT INTO product (id, source, merchant, external_product_id, name,
                        current_price, commission_value, category_code, rating, review_count,
                        sold_count, product_url, is_available, created_at, updated_at)
                        VALUES (?,'t','shopee.vn',?,?,300000,30000,?,4.6,500,900,'https://x',1,?,?)""",
                     (_ulid(), f"PC_{abs(hash(name)) % 999999}", name, cat, now(), now()))

    def pool(code):
        return {s["product"]["name"]
                for s in scoring.score_candidates(conn, limit=9999,
                                                  niches=pipeline.channel_niches(conn, ids[code]))}

    nu, be, pet, allc = pool("c_nu"), pool("c_be"), pool("c_pet"), pool("c_all")
    check("kênh nữ chỉ thấy hàng nữ", "Váy hoa nhí dáng suông" in nu and "Cát vệ sinh cho mèo 10L" not in nu)
    check("kênh bé chỉ thấy hàng mẹ bé", "Bình sữa cổ rộng 240ml" in be and "Váy hoa nhí dáng suông" not in be)
    check("kênh thú cưng chỉ thấy hàng thú cưng",
          "Cát vệ sinh cho mèo 10L" in pet and "Bình sữa cổ rộng 240ml" not in pet)
    check("ba kênh nhìn thấy ba tập khác nhau", nu != be and be != pet and nu != pet)
    check("kênh không lọc thấy được cả nồi chiên", "Nồi chiên không dầu 5L" in allc)

    # Nhóm mẹ & bé KHÔNG được tự loại chính mình bằng bộ lọc trẻ em.
    check("hàng trẻ em vào được kênh mẹ & bé",
          not niche.match_reasons({"name": "Quần áo trẻ em cotton", "category_code": "me-va-be"}, ["me-va-be"]))
    check("hàng trẻ em vẫn bị loại khỏi kênh nữ",
          bool(niche.match_reasons({"name": "Váy bé gái 5 tuổi", "category_code": "thoi-trang"}, ["thoi-trang-nu"])))

    # Đổi chủ đề bất cứ lúc nào.
    pipeline.set_channel_niches(conn, ids["c_pet"], ["gia-dung"])
    after = pool("c_pet")
    check("đổi chủ đề thì tập sản phẩm đổi theo",
          "Nồi chiên không dầu 5L" in after and "Cát vệ sinh cho mèo 10L" not in after)
    conn.close()


def test_migration_adds_column():
    print("\nNâng cấp CSDL cũ")
    import sqlite3, tempfile as _tf
    path = os.path.join(_tf.mkdtemp(), "old.db")
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE channel (id TEXT PRIMARY KEY, code TEXT, daily_post_cap INTEGER)")
    c.execute("INSERT INTO channel VALUES ('x','cu',9)")
    c.commit()
    c.row_factory = sqlite3.Row
    applied = db.migrate(c)
    check("thêm được cột niches vào CSDL cũ", "channel.niches" in applied, applied)
    check("chạy lại migration không làm gì thêm", db.migrate(c) == [])
    row = c.execute("SELECT code, daily_post_cap, niches FROM channel").fetchone()
    check("dữ liệu cũ còn nguyên", row["code"] == "cu" and row["daily_post_cap"] == 9)
    check("cột mới có giá trị mặc định rỗng", row["niches"] == "[]", row["niches"])
    c.close()


if __name__ == "__main__":
    setup()
    test_niche_matching()
    test_niche_content_guard()
    test_niche_per_channel()
    test_migration_adds_column()
    test_no_double_version_prefix()
    test_tiktok_normalize()
    test_tiktok_search_filters()
    test_link_response_parsing()
    test_transaction_status_mapping()
    test_factory()
    test_single_product_flow()
    test_shopee_safe_url()
    test_shopee_metadata()
    test_shopee_image_materialize()
    test_manual_shopee_post_flow()
    test_shopee_web_contract_source()
    test_dark_premium_template_contract()
    test_shopee_edge_hardening()
    test_web_security()
    test_production_guard()
    print(f"\n{len(PASS)} đạt, {len(FAIL)} hỏng")
    if FAIL:
        print("Hỏng: " + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
