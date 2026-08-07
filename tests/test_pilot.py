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

    ctx = factory.build_context()
    check("context có đủ source, channel, storage",
          all(k in ctx for k in ("source", "channel", "storage")), list(ctx))

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
    test_web_security()
    test_production_guard()
    print(f"\n{len(PASS)} đạt, {len(FAIL)} hỏng")
    if FAIL:
        print("Hỏng: " + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
