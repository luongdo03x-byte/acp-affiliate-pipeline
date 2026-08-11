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
from acp.core import content, playbook, valuepost  # noqa: E402
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

    ctx = factory.build_context()
    check("context có đủ source, channel, storage",
          all(k in ctx for k in ("source", "channel", "storage")), list(ctx))

    # Web và CLI phải đi qua cùng một factory -- lỗi cũ là web hardcode mock.
    srv = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "server.py")
    body = open(srv, encoding="utf-8").read()
    check("server.py không khởi tạo mock trực tiếp",
          "MockThreads(" not in body and "MockAccessTrade(" not in body)
    check("server.py dùng factory.build_context", "factory.build_context()" in body)

    os.environ.pop("ACP_CAPTION_LLM", None)
    check("get_caption_llm() tắt mặc định (không set ACP_CAPTION_LLM)",
          factory.get_caption_llm() is None)
    os.environ["ACP_CAPTION_LLM"] = "gemini"
    llm = factory.get_caption_llm()
    check("get_caption_llm() trả về llm_gemini.rewrite khi bật",
          llm is not None and llm.__name__ == "rewrite")
    os.environ.pop("ACP_CAPTION_LLM", None)


# --------------------------------------------------------- single product

def test_single_product_flow():
    print("\nMột sản phẩm thành một bài")
    factory.reset_cache()
    conn = connect()
    src = MockAccessTrade()
    sample = src.fetch_products(limit=200)
    target = next(p for p in sample if p.rating and p.rating >= 4.5 and p.current_price > 0)

    ctx = {"source": src, "channel": None, "storage": _FakeStorage()}
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


# --------------------------------------------------------- hook + CTA + mix

def test_playbook_hooks_and_cta():
    print("\nThư viện hook mở đầu + CTA")
    check("có đúng 9 hook", len(playbook.HOOKS) == 9, len(playbook.HOOKS))
    check("mỗi hook đều render được, không rỗng", all(
        playbook.render_hook(code, {"name": "Sản phẩm test", "current_price": 100000,
                                     "sold_count": 500, "rating": 4.8, "review_count": 200,
                                     "category_code": "gia-dung"}, 0.15)
        for code in playbook.hook_codes()))
    check("pick_hook tôn trọng mã hợp lệ được truyền vào",
          playbook.pick_hook("H1_GIAGIAM") == "H1_GIAGIAM")
    check("pick_hook bỏ qua mã không hợp lệ, bốc ngẫu nhiên thay vì lỗi",
          playbook.pick_hook("MA_KHONG_TON_TAI") in playbook.HOOKS)
    check("có ít nhất 3 CTA trong thư viện", len(playbook.CTA_LIBRARY) >= 3)
    check("một CTA thì không bị coi là nhiều CTA",
          not playbook.contains_multiple_cta(f"...{playbook.CTA_LIBRARY[0]}..."))
    check("hai CTA trong cùng caption thì bị chặn",
          playbook.contains_multiple_cta(f"{playbook.CTA_LIBRARY[0]} {playbook.CTA_LIBRARY[1]}"))

    banned_phrases = ["trang bán ghi nhận", "có số liệu đáng chú ý"]
    social_product = {"name": "Sản phẩm test", "current_price": 100000,
                       "sold_count": 512, "rating": 4.8, "review_count": 200,
                       "category_code": "gia-dung"}
    no_social_product = {"name": "Sản phẩm test", "current_price": 100000,
                          "sold_count": 0, "rating": None, "review_count": 0,
                          "category_code": "gia-dung"}
    for code in playbook.hook_codes():
        for product in (social_product, no_social_product):
            text = playbook.render_hook(code, product, 0.15)
            low = text.lower()
            check(f"hook {code} không còn giọng báo cáo số liệu",
                  all(p not in low for p in banned_phrases), text)
            check(f"hook {code} không bịa trải nghiệm sử dụng",
                  content.validate(f"{text}\n\n{playbook.CTA_LIBRARY[0]}\nhttps://x.test/y\n\n"
                                    f"{content.DISCLOSURE_DEFAULT}") == [],
                  text)
    check("H5 dùng số liệu kiểu 'người mua rồi' chứ không phải 'đã bán ... lượt'",
          "người mua rồi" in playbook.render_hook("H5_XAHOI", social_product, 0).lower())


def test_content_post_type():
    print("\ncontent.validate() theo post_type")
    product = {"name": "Nồi chiên không dầu 5L", "current_price": 890000, "sold_count": 300,
               "rating": 4.7, "review_count": 150, "category_code": "gia-dung", "description": "Dung tích 5L"}
    caption = content.generate(product, "price_drop", "https://go.isclix.com/x", discount_pct=0.1,
                                hook_code="H1_GIAGIAM")
    check("caption bán hàng có hook, CTA, link, disclosure",
          caption.startswith(playbook.render_hook("H1_GIAGIAM", product, 0.1)[:10])
          and "https://go.isclix.com/x" in caption and content.DISCLOSURE_DEFAULT in caption)
    check("bài bán hàng hợp lệ thì validate() rỗng",
          content.validate(caption, post_type="SALES") == [], content.validate(caption, post_type="SALES"))

    value_caption = valuepost.checklist_text("gia-dung", "Nhà cửa & gia dụng")
    check("bài giá trị không có link vẫn qua validate() khi post_type=VALUE",
          content.validate(value_caption, disclosure=valuepost.DISCLOSURE_VALUE,
                            post_type="VALUE") == [])
    check("cùng caption đó nhưng validate() như bài bán hàng thì bị chặn thiếu link/disclosure/CTA",
          len(content.validate(value_caption, post_type="SALES")) >= 2)

    two_cta = f"{playbook.CTA_LIBRARY[0]} {playbook.CTA_LIBRARY[1]} https://x.com {content.DISCLOSURE_DEFAULT}"
    check("hai CTA trong bài bán hàng bị validate() chặn",
          any("CTA" in p for p in content.validate(two_cta, post_type="SALES")))


def test_hook_rotation_in_plan_content():
    """limit chia đều cho MỌI kênh đang ACTIVE -- lúc test này chạy đã có nhiều kênh
    từ test_niche_per_channel(), nên đặt limit lớn và chỉ soi job của kênh 'ch1' để
    per_channel đủ chỗ xoay vòng qua nhiều hơn 1 hook."""
    print("\nXoay vòng hook làm biến thể trong plan_content")
    conn = connect()
    ch1_id = conn.execute("SELECT id FROM channel WHERE code='ch1'").fetchone()["id"]
    factory.reset_cache()
    ctx = factory.build_context("mock")
    pipeline.ingest_datafeed(conn, ctx["source"], limit=120)
    conn.execute("DELETE FROM job_queue")
    n_channels = conn.execute("SELECT COUNT(*) FROM channel WHERE status='ACTIVE'").fetchone()[0]
    created = pipeline.plan_content(conn, "gd2026", limit=6 * n_channels)
    check("tạo được nhiều job", len(created) >= 3, len(created))
    variants = [json.loads(r["payload"])["variant_code"] for r in conn.execute(
        "SELECT payload FROM job_queue WHERE job_type='GENERATE_CONTENT' AND payload LIKE ?",
        (f'%"channel_id": "{ch1_id}"%',)).fetchall()]
    check("variant_code toàn là mã hook hợp lệ", all(v in playbook.HOOKS for v in variants), variants)
    check("có xoay vòng, không phải một mã cố định", len(set(variants)) > 1, variants)
    conn.close()


# -------------------------------------------------------------- bảo mật web

def test_caption_llm_wired_regardless_of_manual_flow():
    """create_app() phải bật content.set_llm() ngay lúc khởi tạo, KHÔNG chỉ
    qua factory.build_context() -- luồng nhập Shopee affiliate thủ công
    (web/server.py::create_affiliate_product) cố ý không gọi build_context()
    để tránh khởi tạo nguồn ACCESSTRADE thật ("provider boundary"), nên nếu
    Gemini chỉ được bật trong build_context() thì luồng operator thật sự
    dùng hàng ngày sẽ không bao giờ thấy caption được viết lại."""
    print("\nLLM caption được bật kể cả khi dùng luồng Shopee thủ công")
    # create_app() đòi ACP_ADMIN_PASSWORD/ACP_SECRET_KEY khi ACP_ENV=production
    # (đã bật sẵn khi chạy qua manage.sh test) -- lưu/khôi phục đúng giá trị
    # gốc, không pop() thẳng tay (xem lỗi tương tự đã sửa ở test_web_security).
    _saved = {k: os.environ.get(k) for k in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY")}
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    os.environ["ACP_CAPTION_LLM"] = "gemini"
    content.set_llm(None)
    try:
        from acp.web.server import create_app
        create_app()
        check("create_app() tự bật content._llm_fn theo ACP_CAPTION_LLM",
              content._llm_fn is not None and content._llm_fn.__name__ == "rewrite")
    finally:
        content.set_llm(None)
        os.environ.pop("ACP_CAPTION_LLM", None)
        for k, v in _saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_web_security():
    print("\nBảo mật web")
    # Lưu lại giá trị gốc (nếu có, ví dụ ACP_SECRET_KEY nạp sẵn từ .env.local khi
    # chạy qua manage.sh test) để khôi phục đúng ở cuối hàm -- pop() thẳng tay sẽ
    # xoá luôn cấu hình thật, làm test_value_posts() chạy sau bị thiếu ACP_SECRET_KEY
    # khi ACP_ENV=production (create_app() đòi khoá này, xem web/server.py).
    _saved_env = {k: os.environ.get(k) for k in
                  ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY", "ACP_WEBHOOK_SECRET")}
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

    # Chọn giờ đăng thủ công qua form /duyet -- input datetime-local không có
    # offset, quy ước là giờ VN (UTC+7) rồi quy đổi sang UTC trước khi lưu.
    approve_resp = c.post(f"/duyet/{manual['id']}/approve",
                           data={"_csrf": csrf, "scheduled_at": "2026-12-25T17:00"})
    check("duyệt qua web với giờ tuỳ chỉnh redirect về /duyet không lỗi",
          approve_resp.status_code == 302 and "err=" not in (approve_resp.location or ""),
          getattr(approve_resp, "location", ""))
    conn = connect()
    scheduled_row = conn.execute("SELECT scheduled_at FROM post WHERE id=?", (manual["id"],)).fetchone()
    conn.close()
    check("web quy đổi đúng giờ VN (UTC+7) sang UTC khi lưu",
          scheduled_row["scheduled_at"] == "2026-12-25T10:00:00+00:00",
          scheduled_row["scheduled_at"] if scheduled_row else None)

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

    for var, original in _saved_env.items():
        if original is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = original


# --------------------------------------------------------- bài không bán hàng

def test_value_posts():
    """"Phương pháp 3 bài" (core/valuepost.py). test_web_security() đặt kênh 'ch1'
    về NEEDS_REAUTH -- phải bật lại ACTIVE trước khi chạy, nếu không create_value_post
    báo "kênh không hoạt động" là đúng nhưng không kiểm được đường thành công."""
    print("\nBài không bán hàng (phương pháp 3 bài)")
    conn = connect()
    conn.execute("UPDATE channel SET status='ACTIVE' WHERE code='ch1'")
    pipeline.set_channel_niches(conn, conn.execute(
        "SELECT id FROM channel WHERE code='ch1'").fetchone()["id"], ["gia-dung"])

    # checklist không cần dữ liệu giá -- phải luôn thành công.
    res = pipeline.create_value_post(conn, "gd2026", "ch1", kind="checklist")
    check("checklist tạo bài thành công", res["ok"], res.get("error"))
    check("bài giá trị không có product_id", conn.execute(
        "SELECT product_id FROM post WHERE id=?", (res["post_id"],)).fetchone()[0] is None)
    check("post_type = VALUE", conn.execute(
        "SELECT post_type FROM post WHERE id=?", (res["post_id"],)).fetchone()[0] == "VALUE")
    check("checklist qua được validate() (không cần link/CTA)", res["problems"] == [], res["problems"])
    check("checklist trạng thái PENDING_REVIEW", res["status"] == "PENDING_REVIEW")

    # Hồi quy: approve_post() từng không đọc post_type từ DB nên áp nhầm luật
    # "phải có link affiliate" của bài bán hàng lên bài giá trị -- duyệt trên web
    # báo lỗi "Thiếu nhãn tiếp thị liên kết; Thiếu link affiliate" và KHÔNG BAO GIỜ
    # lên lịch được. Bắt được lúc thao tác thật trên trình duyệt, không phải qua
    # create_value_post() (hàm đó tự truyền post_type đúng nên không lộ lỗi này).
    appr = pipeline.approve_post(conn, res["post_id"], actor="test")
    check("duyệt được bài giá trị (không đòi link affiliate)", appr["ok"], appr.get("error"))
    check("bài giá trị duyệt xong thì lên lịch", conn.execute(
        "SELECT status FROM post WHERE id=?", (res["post_id"],)).fetchone()[0] == "SCHEDULED")

    # Không đủ dữ liệu giá -> valuepost.build() phải trả None chứ không bịa số liệu.
    # Kiểm ở tầng hàm thuần thay vì qua create_value_post(): tới lúc test này chạy,
    # DB dùng chung đã có lịch sử giá từ các test ingest trước đó nên không còn
    # "chưa có dữ liệu" thật để kiểm qua đường tích hợp.
    check("valuepost.build('price_level') không có median thì trả None, không bịa số liệu",
          valuepost.build("price_level", niche_name="x", median_price=None) is None)
    check("valuepost.build('real_discount') không có món nào thì trả None",
          valuepost.build("real_discount", niche_name="x", discounted_products=[]) is None)

    # Seed lịch sử giá cho nhóm gia-dung rồi thử lại -- phải thành công và có số liệu thật.
    pid = ulid()
    conn.execute("""INSERT INTO product (id, source, merchant, external_product_id, name, description,
                    current_price, original_price, commission_value, category_code, product_url, is_available,
                    created_at, updated_at) VALUES (?,'mock','shop','ext1','Nồi test','',900000,1200000,
                    50000,'gia-dung','https://x.test/p',1,?,?)""", (pid, now(), now()))
    conn.execute("INSERT INTO product_price_history (product_id, price, observed_at) VALUES (?,?,?)",
                 (pid, 1000000, now()))
    res_price = pipeline.create_value_post(conn, "gd2026", "ch1", kind="price_level")
    check("price_level có dữ liệu thì tạo bài thành công", res_price["ok"], res_price.get("error"))
    check("caption price_level nêu con số", any(ch.isdigit() for ch in res_price["caption"]))

    # kênh không tồn tại / campaign sai -> báo lỗi rõ, không ném exception.
    bad = pipeline.create_value_post(conn, "gd2026", "khong-ton-tai", kind="checklist")
    check("kênh không tồn tại thì báo lỗi rõ", bad["ok"] is False)

    # post_mix: (ratio-1) job bán hàng + 1 bài giá trị, cho MỘT kênh chỉ định.
    ctx = {"source": None, "channel": None, "storage": _FakeStorage()}
    factory.reset_cache()
    ctx = factory.build_context("mock")
    pipeline.ingest_datafeed(conn, ctx["source"], limit=60)
    before_jobs = conn.execute("SELECT COUNT(*) FROM job_queue WHERE job_type='GENERATE_CONTENT'").fetchone()[0]
    mix = pipeline.post_mix(conn, ctx, "gd2026", "ch1", ratio=3)
    after_jobs = conn.execute("SELECT COUNT(*) FROM job_queue WHERE job_type='GENERATE_CONTENT'").fetchone()[0]
    check("post_mix tạo tối đa (ratio-1) job bán hàng cho kênh", after_jobs - before_jobs <= 2)
    check("post_mix tạo đúng 1 bài giá trị cho kênh", len(mix["value_posts"]) == 1, mix["value_posts"])
    check("post_mix báo cáo đúng kênh", mix["value_posts"][0]["channel"] == "ch1")

    # /duyet phải LEFT JOIN product -- INNER JOIN sẽ giấu bài giá trị khỏi màn hình duyệt.
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})
    body = c.get("/duyet").get_data(as_text=True)
    check("/duyet hiện được bài giá trị (không bị INNER JOIN product giấu đi)",
          "Bài không bán hàng" in body or "bài giá trị" in body)
    os.environ.pop("ACP_ADMIN_PASSWORD", None)

    conn.close()


# ------------------------------------------------------- Shopee affiliate import

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


# --------------------------------------------------- ACP Shopee Helper (Chrome)

def test_shopee_helper_pairing():
    print("\nPairing ACP Shopee Helper")
    from acp.core import helper_pairing
    from acp.adapters.shopee_affiliate import (
        ProductMetadata, metadata_state, AUTO_COMPLETE, AUTO_PARTIAL, BROWSER_HELPER_REQUIRED,
    )

    check("đủ tên/giá/ảnh -> AUTO_COMPLETE",
          metadata_state(ProductMetadata(name="x", current_price=1, image_url="y")) == AUTO_COMPLETE)
    check("thiếu ảnh -> AUTO_PARTIAL",
          metadata_state(ProductMetadata(name="x", current_price=1)) == AUTO_PARTIAL)
    check("trống hết -> BROWSER_HELPER_REQUIRED",
          metadata_state(ProductMetadata()) == BROWSER_HELPER_REQUIRED)

    # --- module thuần, không qua HTTP ---
    helper_pairing.reset()
    issued = helper_pairing.issue("https://shopee.vn/product/1/2")
    check("issue() trả token + TTL", bool(issued.get("token")) and issued.get("expires_in") == 300)

    check("poll() trước khi nộp -> pending",
          helper_pairing.poll(issued["token"]) == {"status": "pending"})

    check("submit() sai product_url bị từ chối",
          helper_pairing.submit(issued["token"], "https://shopee.vn/product/9/9", {"name": "x"}) is False)
    check("submit() đúng token + product_url thành công",
          helper_pairing.submit(issued["token"], "https://shopee.vn/product/1/2",
                                 {"name": "Váy test", "current_price": 199000}) is True)
    check("submit() lần hai với token đã dùng bị từ chối (một lần dùng)",
          helper_pairing.submit(issued["token"], "https://shopee.vn/product/1/2", {"name": "y"}) is False)

    polled = helper_pairing.poll(issued["token"])
    check("poll() sau khi nộp -> ready kèm đúng metadata",
          polled == {"status": "ready", "metadata": {"name": "Váy test", "current_price": 199000}}, polled)
    check("poll() token lạ -> None", helper_pairing.poll("khong-ton-tai") is None)

    # TTL: token hết hạn thì poll() không còn thấy.
    helper_pairing.reset()
    expired = helper_pairing.issue("https://shopee.vn/product/3/4")
    helper_pairing._tokens[expired["token"]]["created_at"] -= (helper_pairing.TTL_SECONDS + 1)
    check("token hết hạn thì poll() trả None", helper_pairing.poll(expired["token"]) is None)

    # --- qua HTTP, đúng đường thật operator dùng ---
    helper_pairing.reset()
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()

    check("token-issue chưa đăng nhập bị chặn",
          c.post("/sanpham/affiliate/helper/token", data={"product_url": "x"}).status_code == 302)
    check("helper/status chưa đăng nhập bị chặn",
          c.get("/sanpham/affiliate/helper/status?token=x").status_code == 302)

    c.post("/dangnhap", data={"password": "matkhau-test"})
    home = c.get("/duyet").get_data(as_text=True)
    csrf = home.split('name="_csrf" value="')[1].split('"')[0]

    r = c.post("/sanpham/affiliate/helper/token",
               data={"product_url": "https://shopee.vn/product/5/6", "_csrf": csrf})
    check("token-issue đã đăng nhập trả 200 + token", r.status_code == 200 and r.get_json().get("token"))
    token = r.get_json()["token"]

    r = c.get(f"/sanpham/affiliate/helper/status?token={token}")
    check("helper/status trả pending trước khi extension gửi", r.get_json() == {"status": "pending"})

    # Endpoint extension gọi -- KHÔNG cần đăng nhập, chỉ cần loopback + token đúng.
    bad_origin = c.post(
        "/api/helper/shopee-product",
        json={"token": token, "product_url": "https://shopee.vn/product/5/6", "metadata": {"name": "z"}},
        environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
    check("request không phải từ loopback bị chặn (403)", bad_origin.status_code == 403, bad_origin.status_code)

    bad_token = c.post(
        "/api/helper/shopee-product",
        json={"token": "sai-token", "product_url": "https://shopee.vn/product/5/6", "metadata": {"name": "z"}},
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    check("token sai bị chặn (410)", bad_token.status_code == 410, bad_token.status_code)

    ok = c.post(
        "/api/helper/shopee-product",
        json={"token": token, "product_url": "https://shopee.vn/product/5/6",
              "metadata": {"name": "Đầm hoa", "current_price": 259000, "image_url": "https://img.example/a.jpg",
                           "shop": "Shop A", "cookie": "khong-duoc-nhan-truong-la"}},
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    check("extension nộp đúng token/product_url thành công (200)", ok.status_code == 200, ok.status_code)

    r = c.get(f"/sanpham/affiliate/helper/status?token={token}")
    body = r.get_json()
    check("helper/status trả ready sau khi extension gửi", body.get("status") == "ready", body)
    check("không nhận trường lạ ngoài 5 trường cho phép (không có 'cookie')",
          "cookie" not in body.get("metadata", {}), body)
    check("giữ đúng 5 trường metadata cho phép",
          body.get("metadata") == {"name": "Đầm hoa", "current_price": 259000,
                                    "image_url": "https://img.example/a.jpg", "shop": "Shop A",
                                    "original_price": None}, body)

    reused = c.post(
        "/api/helper/shopee-product",
        json={"token": token, "product_url": "https://shopee.vn/product/5/6", "metadata": {"name": "lan hai"}},
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    check("nộp lại token đã dùng bị chặn (410)", reused.status_code == 410, reused.status_code)

    os.environ.pop("ACP_ADMIN_PASSWORD", None)


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


def test_migration_rebuilds_post_table():
    """Hồi quy: bảng post cũ có product_id NOT NULL + post_metrics/conversion có FK
    trỏ vào post(id). migrate() phải dựng lại post KHÔNG làm hỏng FK của hai bảng
    kia -- lỗi gốc là RENAME TABLE tự viết lại FK thành "post_old", rồi post_old
    bị DROP thì FK trỏ vào một bảng không còn tồn tại.

    Dùng db.connect() (không phải sqlite3.connect() thô) vì đây là đường thật của
    ứng dụng -- isolation_level=None ảnh hưởng tới việc PRAGMA foreign_keys có áp
    dụng lại được sau migrate() hay không.
    """
    print("\nDựng lại bảng post (bỏ NOT NULL trên product_id)")
    import tempfile as _tf
    old_db_path = db.DB_PATH
    db.DB_PATH = os.path.join(_tf.mkdtemp(), "old_post.db")
    c = db.connect()
    c.executescript("""
        CREATE TABLE product (id TEXT PRIMARY KEY);
        CREATE TABLE channel (id TEXT PRIMARY KEY, code TEXT, daily_post_cap INTEGER);
        CREATE TABLE campaign (id TEXT PRIMARY KEY);
        CREATE TABLE caption_template (id TEXT PRIMARY KEY);
        CREATE TABLE post (
            id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL REFERENCES product(id),
            channel_id TEXT NOT NULL REFERENCES channel(id),
            campaign_id TEXT NOT NULL REFERENCES campaign(id),
            caption_template_id TEXT, variant_code TEXT NOT NULL,
            caption_body TEXT NOT NULL, disclosure_text TEXT NOT NULL,
            caption_final TEXT NOT NULL, image_url_composited TEXT,
            affiliate_link TEXT, sub_id_payload TEXT, score REAL,
            status TEXT NOT NULL DEFAULT 'DRAFT', scheduled_at TEXT, published_at TEXT,
            thread_id TEXT, reviewed_by TEXT, reviewed_at TEXT, reject_reason TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_post_status ON post(status, scheduled_at);
        CREATE TABLE post_metrics (post_id TEXT PRIMARY KEY REFERENCES post(id), clicks INTEGER DEFAULT 0);
        CREATE TABLE conversion (
            id TEXT PRIMARY KEY, post_id TEXT REFERENCES post(id),
            transaction_id TEXT NOT NULL, external_product_id TEXT NOT NULL,
            sale_amount INTEGER NOT NULL, commission INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', converted_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE (transaction_id, external_product_id)
        );
    """)
    c.execute("INSERT INTO product VALUES ('p1')")
    c.execute("INSERT INTO channel VALUES ('c1','ch1', 9)")
    c.execute("INSERT INTO campaign VALUES ('cm1')")
    c.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, variant_code, caption_body,
                 disclosure_text, caption_final, status, created_at, updated_at)
                 VALUES ('post1','p1','c1','cm1','A','body','disc','final','PUBLISHED',?,?)""", (now(), now()))
    c.execute("INSERT INTO post_metrics (post_id, clicks) VALUES ('post1', 5)")
    c.execute("""INSERT INTO conversion (id, post_id, transaction_id, external_product_id, sale_amount,
                 commission, status, converted_at, updated_at)
                 VALUES ('conv1','post1','tx1','p1',100000,10000,'approved',?,?)""", (now(), now()))

    applied = db.migrate(c)
    check("dựng lại post báo cáo trong applied", any("product_id" in a for a in applied), applied)

    info = {r[1]: r for r in c.execute("PRAGMA table_info(post)").fetchall()}
    check("product_id không còn NOT NULL", info["product_id"][3] == 0)
    check("post_type có cột mới, mặc định SALES",
          c.execute("SELECT post_type FROM post WHERE id='post1'").fetchone()[0] == "SALES")

    old = c.execute("SELECT * FROM post WHERE id='post1'").fetchone()
    check("bài cũ còn nguyên dữ liệu", old["caption_final"] == "final" and old["product_id"] == "p1")
    joined = c.execute("""SELECT pm.clicks, cv.commission FROM post p
                          JOIN post_metrics pm ON pm.post_id=p.id
                          JOIN conversion cv ON cv.post_id=p.id WHERE p.id='post1'""").fetchone()
    check("post_metrics/conversion vẫn JOIN được sau khi dựng lại bảng post",
          joined is not None and joined["clicks"] == 5 and joined["commission"] == 10000)

    for tbl in ("post_metrics", "conversion"):
        sql = c.execute("SELECT sql FROM sqlite_master WHERE name=?", (tbl,)).fetchone()[0]
        check(f"{tbl}.post_id vẫn tham chiếu 'post', không phải 'post_old'",
              "post_old" not in sql and "REFERENCES post(" in sql, sql)

    c.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, variant_code, caption_body,
                 disclosure_text, caption_final, post_type, status, created_at, updated_at)
                 VALUES ('post2', NULL,'c1','cm1','H1','vbody','vdisc','vfinal','VALUE','PENDING_REVIEW',?,?)""",
                (now(), now()))
    check("product_id NULL được chấp nhận sau khi dựng lại bảng (bài giá trị)",
          c.execute("SELECT product_id FROM post WHERE id='post2'").fetchone()[0] is None)

    check("PRAGMA foreign_key_check sạch sau khi dựng lại bảng",
          c.execute("PRAGMA foreign_key_check").fetchall() == [])
    try:
        c.execute("INSERT INTO post_metrics (post_id, clicks) VALUES ('khong-ton-tai', 0)")
        check("FK vẫn được cưỡng chế sau khi dựng lại bảng (insert sai bị chặn)", False, "không bị chặn")
    except Exception:
        check("FK vẫn được cưỡng chế sau khi dựng lại bảng (insert sai bị chặn)", True)

    check("chạy lại migrate() lần hai không dựng lại nữa", db.migrate(c) == [])
    c.close()
    db.DB_PATH = old_db_path


if __name__ == "__main__":
    setup()
    test_niche_matching()
    test_niche_content_guard()
    test_niche_per_channel()
    test_migration_adds_column()
    test_migration_rebuilds_post_table()
    test_no_double_version_prefix()
    test_tiktok_normalize()
    test_tiktok_search_filters()
    test_link_response_parsing()
    test_transaction_status_mapping()
    test_factory()
    test_single_product_flow()
    test_playbook_hooks_and_cta()
    test_content_post_type()
    test_hook_rotation_in_plan_content()
    test_shopee_safe_url()
    test_shopee_metadata()
    test_shopee_image_materialize()
    test_manual_shopee_post_flow()
    test_shopee_web_contract_source()
    test_dark_premium_template_contract()
    test_shopee_edge_hardening()
    test_shopee_helper_pairing()
    test_caption_llm_wired_regardless_of_manual_flow()
    test_web_security()
    test_value_posts()  # phải chạy SAU test_web_security() -- xem docstring
    test_production_guard()
    print(f"\n{len(PASS)} đạt, {len(FAIL)} hỏng")
    if FAIL:
        print("Hỏng: " + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
