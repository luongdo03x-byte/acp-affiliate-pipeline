"""Test cho phần pilot: nguồn TikTok Shop, factory, single-product, bảo mật web.

    python3 -m acp.tests.test_pilot
"""
import html
import json
import os
import random
import re
import sys
import tempfile
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_tmp = tempfile.mkdtemp()
os.environ["ACP_DB"] = os.path.join(_tmp, "pilot.db")

from acp.core import db  # noqa: E402
db.DB_PATH = os.environ["ACP_DB"]

from acp.adapters import factory  # noqa: E402
from acp.adapters.live import AT_BASE, AccessTradeSource  # noqa: E402
from acp.adapters.mock import MockAccessTrade  # noqa: E402
from acp.adapters.tiktokshop import AT_ROOT, AccessTradeTikTokShopSource  # noqa: E402
from acp.core import crypto, jobs, niche, pipeline, scoring  # noqa: E402
from acp.core import content, content_checker, content_facts, content_hook, content_scoring, content_variant, playbook, valuepost  # noqa: E402
from acp.core.db import connect, init_db, now, ulid  # noqa: E402

# The release checkout's var/media may be a symlink to production runtime data.
# Keep every generated image from this test process under its temporary fixture.
pipeline.MEDIA_DIR = os.path.join(_tmp, "media")
from acp.web import server as web_server  # noqa: E402
web_server.MEDIA_DIR = os.path.join(_tmp, "web-media")

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
    # max_per_category_per_day nới ra 20 (mặc định 3) -- cùng lý do đã ghi ở
    # setup() của tests/test_pipeline.py: sau khi merge, file này chạy ~90 test
    # dùng chung DB, nhiều test hơn hẳn feat/shopee-affiliate-import gốc (thêm
    # cả loạt test của main), "hôm nay" không đổi suốt cả file -- trần 3
    # món/danh mục/ngày mặc định cạn giữa chừng khiến plan_content() hết ứng
    # viên oan ở các test chạy sau. Không đổi giá trị mặc định thật.
    test_filters = dict(scoring.DEFAULT_FILTERS, max_per_category_per_day=20)
    scoring.save_config(conn, scoring.DEFAULT_WEIGHTS, test_filters, "test")
    # Bật công tắc tổng publish_worker_enabled (mặc định "0", main thêm sau khi
    # feat/shopee-affiliate-import đã tách nhánh) -- xem ghi chú đầy đủ ở
    # setup() của tests/test_pipeline.py, cùng lý do.
    from acp.core import system_settings
    system_settings.set_system_setting(conn, "publish_worker_enabled", "1", actor="test-setup")
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

    os.environ.pop("ACP_CAPTION_LLM", None)
    check("get_caption_llm() tắt mặc định (không set ACP_CAPTION_LLM)",
          factory.get_caption_llm() is None)
    os.environ["ACP_CAPTION_LLM"] = "gemini"
    llm = factory.get_caption_llm()
    check("get_caption_llm() trả về llm_gemini.rewrite khi bật",
          llm is not None and llm.__name__ == "rewrite")
    os.environ.pop("ACP_CAPTION_LLM", None)
    os.environ.pop("ACP_CONTENT_ENGINE_LLM", None)
    check("get_content_engine_llm() tắt mặc định (không set ACP_CONTENT_ENGINE_LLM)",
          factory.get_content_engine_llm() is None)
    os.environ["ACP_CONTENT_ENGINE_LLM"] = "gemini"
    llm2 = factory.get_content_engine_llm()
    check("get_content_engine_llm() trả về llm_gemini.rewrite_json khi bật",
          llm2 is not None and llm2.__name__ == "rewrite_json")
    os.environ.pop("ACP_CONTENT_ENGINE_LLM", None)


def test_mock_meta_connection_service():
    print("\nMetaConnectionService (mock)")
    from acp.adapters.base import MetaConnectionService
    from acp.adapters.mock import MockMetaConnectionService

    svc = MockMetaConnectionService()
    check("là MetaConnectionService", isinstance(svc, MetaConnectionService))

    url = svc.oauth_authorize_url("state123", "https://acp.example/oauth/meta/callback")
    check("authorize URL chứa state", "state123" in url, url)
    check("authorize URL chứa redirect_uri", "acp.example" in url, url)

    exchanged = svc.exchange_code("fake-code", "https://acp.example/oauth/meta/callback")
    check("exchange_code trả token", bool(exchanged.token))
    check("exchange_code trả meta_user_id ổn định", exchanged.meta_user_id == "mock_meta_user_1",
          exchanged.meta_user_id)

    pages = svc.list_pages(exchanged.token)
    check("mock trả đúng 2 Page", len(pages) == 2, len(pages))
    check("Page có page_token", all(p.page_token for p in pages))

    ig = svc.instagram_for_page(pages[0].external_account_id, pages[0].page_token)
    check("Page đầu có Instagram gắn kèm", ig is not None and ig.username)
    ig2 = svc.instagram_for_page(pages[1].external_account_id, pages[1].page_token)
    check("Page thứ hai không có Instagram", ig2 is None)


def test_live_meta_connection_service_url_building():
    print("\nLiveMetaConnectionService (không cần mạng)")
    import os as _os
    from acp.adapters.live import LiveMetaConnectionService

    old_id, old_secret = _os.environ.get("META_APP_ID"), _os.environ.get("META_APP_SECRET")
    _os.environ["META_APP_ID"] = "test_app_id"
    _os.environ["META_APP_SECRET"] = "test_app_secret"
    try:
        svc = LiveMetaConnectionService()
        url = svc.oauth_authorize_url("state456", "https://acp.example/oauth/meta/callback")
        check("authorize URL đúng host Meta", "facebook.com" in url, url)
        check("authorize URL chứa client_id", "test_app_id" in url, url)
        check("authorize URL chứa state", "state456" in url, url)
        check("authorize URL chứa quyền pages_show_list", "pages_show_list" in url, url)
        check("authorize URL chứa quyền instagram_basic", "instagram_basic" in url, url)
        check("app_secret KHÔNG lộ trong authorize URL", "test_app_secret" not in url, url)
    finally:
        if old_id is None:
            _os.environ.pop("META_APP_ID", None)
        else:
            _os.environ["META_APP_ID"] = old_id
        if old_secret is None:
            _os.environ.pop("META_APP_SECRET", None)
        else:
            _os.environ["META_APP_SECRET"] = old_secret


def test_factory_meta_connection_service():
    print("\nFactory chọn MetaConnectionService")
    from acp.adapters.base import MetaConnectionService
    from acp.adapters.mock import MockMetaConnectionService
    factory.reset_cache()
    os.environ.pop("ACP_ADAPTER", None)
    svc = factory.get_meta_connection_service()
    check("mặc định trả về mock", isinstance(svc, MockMetaConnectionService))
    check("là MetaConnectionService", isinstance(svc, MetaConnectionService))


def test_factory_meta_connection_service_live_routing():
    print("\nFactory chọn LiveMetaConnectionService khi ACP_ADAPTER=live")
    from acp.adapters.live import LiveMetaConnectionService
    factory.reset_cache()
    os.environ["ACP_ADAPTER"] = "live"
    os.environ["META_APP_ID"] = "test_live_app_id"
    os.environ["META_APP_SECRET"] = "test_live_app_secret"
    try:
        svc = factory.get_meta_connection_service()
        check("ACP_ADAPTER=live trả về LiveMetaConnectionService", isinstance(svc, LiveMetaConnectionService))
    finally:
        os.environ.pop("ACP_ADAPTER", None)
        os.environ.pop("META_APP_ID", None)
        os.environ.pop("META_APP_SECRET", None)


def test_factory_registers_facebook_instagram_publishers():
    print("\nFactory đăng ký đủ facebook/instagram publisher")
    from acp.adapters.base import Publisher
    from acp.adapters.mock import MockFacebookPublisher, MockInstagramPublisher
    factory.reset_cache()
    os.environ.pop("ACP_ADAPTER", None)

    publishers = factory.get_publishers()
    check("có đủ 3 platform", set(publishers) == {"threads", "facebook", "instagram"},
          set(publishers))
    check("facebook là MockFacebookPublisher (mặc định mock)",
          isinstance(publishers["facebook"], MockFacebookPublisher))
    check("instagram là MockInstagramPublisher (mặc định mock)",
          isinstance(publishers["instagram"], MockInstagramPublisher))
    check("cả hai đều là Publisher",
          isinstance(publishers["facebook"], Publisher) and isinstance(publishers["instagram"], Publisher))

    os.environ["ACP_ADAPTER"] = "live"
    os.environ["META_APP_ID"] = "test_app_id"
    os.environ["META_APP_SECRET"] = "test_app_secret"
    try:
        from acp.adapters.live import FacebookPublisher, InstagramPublisher
        live_publishers = factory.get_publishers()
        check("ACP_ADAPTER=live trả về FacebookPublisher thật",
              isinstance(live_publishers["facebook"], FacebookPublisher))
        check("ACP_ADAPTER=live trả về InstagramPublisher thật",
              isinstance(live_publishers["instagram"], InstagramPublisher))
    finally:
        os.environ.pop("ACP_ADAPTER", None)
        os.environ.pop("META_APP_ID", None)
        os.environ.pop("META_APP_SECRET", None)


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

    # content.generate() không còn tự thêm disclosure mặc định (disclosure=''
    # theo signature mới của main, người dùng đã xác nhận giữ nguyên khi
    # merge) -- pipeline không truyền disclosure= vào bất kỳ lời gọi generate()
    # nào nên caption tự động không còn nhãn tiếp thị liên kết theo mặc định.
    check("caption không tự thêm disclosure mặc định (đã tắt theo quyết định, xem core/content.py)",
          pipeline.content.DISCLOSURE_DEFAULT not in post["caption_final"], post["caption_final"])

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
    check("manual caption không tự thêm disclosure mặc định (đã tắt theo quyết định)",
          content.DISCLOSURE_DEFAULT not in post["caption_final"], post["caption_final"])
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
    # Toàn bộ khối "if post_type == 'SALES': ..." (thiếu link/disclosure/CTA,
    # nhiều hơn 1 CTA) đã bị comment out trong content.validate() -- quyết
    # định tắt rào chắn của main, người dùng đã xác nhận giữ nguyên khi merge
    # nhánh multi-account. post_type vẫn là tham số hợp lệ (dùng để phân biệt
    # bài SALES/VALUE ở nơi khác), chỉ riêng nhánh rào chắn theo post_type
    # này hiện không còn kiểm tra gì -- test dưới đây xác nhận đúng hiện
    # trạng "không còn chặn" thay vì hiện trạng cũ.
    product = {"name": "Nồi chiên không dầu 5L", "current_price": 890000, "sold_count": 300,
               "rating": 4.7, "review_count": 150, "category_code": "gia-dung", "description": "Dung tích 5L"}
    caption = content.generate(product, "price_drop", "https://go.isclix.com/x", discount_pct=0.1,
                                hook_code="H1_GIAGIAM", disclosure=content.DISCLOSURE_DEFAULT)
    check("caption bán hàng có hook, CTA, link, disclosure (disclosure truyền vào rõ ràng)",
          caption.startswith(playbook.render_hook("H1_GIAGIAM", product, 0.1)[:10])
          and "https://go.isclix.com/x" in caption and content.DISCLOSURE_DEFAULT in caption)
    check("bài bán hàng hợp lệ thì validate() rỗng",
          content.validate(caption, post_type="SALES") == [], content.validate(caption, post_type="SALES"))

    value_caption = valuepost.checklist_text("gia-dung", "Nhà cửa & gia dụng")
    check("bài giá trị không có link vẫn qua validate() khi post_type=VALUE",
          content.validate(value_caption, disclosure=valuepost.DISCLOSURE_VALUE,
                            post_type="VALUE") == [])
    check("cùng caption đó, validate() như bài bán hàng KHÔNG còn chặn thiếu link/disclosure/CTA (rào chắn đã tắt)",
          content.validate(value_caption, post_type="SALES") == [],
          content.validate(value_caption, post_type="SALES"))

    two_cta = f"{playbook.CTA_LIBRARY[0]} {playbook.CTA_LIBRARY[1]} https://x.com {content.DISCLOSURE_DEFAULT}"
    check("hai CTA trong bài bán hàng KHÔNG còn bị validate() chặn (rào chắn đã tắt)",
          not any("CTA" in p for p in content.validate(two_cta, post_type="SALES")),
          content.validate(two_cta, post_type="SALES"))


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


def test_content_engine_llm_wired_at_create_app():
    """create_app() phải bật đủ 6 hook Content Engine v2 (extractor,
    hook_generator, hook_judge, body_generator, variant_judge,
    hybrid_judge) khi ACP_CONTENT_ENGINE_LLM=gemini -- cùng lý do đặt ở
    create_app() như content.set_llm() phía trên (G1)."""
    print("\nLLM Content Engine v2 được bật đủ 6 hook tại create_app()")
    _saved = {k: os.environ.get(k) for k in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY")}
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    os.environ["ACP_CONTENT_ENGINE_LLM"] = "gemini"
    content_facts.set_extractor(None)
    content_hook.set_hook_generator(None)
    content_hook.set_hook_judge(None)
    content_variant.set_body_generator(None)
    content_checker.set_variant_judge(None)
    content_scoring.set_hybrid_judge(None)
    try:
        from acp.web.server import create_app
        create_app()
        check("extractor được bật", content_facts._extractor_fn is not None
              and content_facts._extractor_fn.__name__ == "rewrite_json")
        check("hook_generator được bật", content_hook._hook_generator_fn is not None
              and content_hook._hook_generator_fn.__name__ == "rewrite_json")
        check("hook_judge được bật", content_hook._hook_judge_fn is not None
              and content_hook._hook_judge_fn.__name__ == "rewrite_json")
        check("body_generator được bật", content_variant._body_generator_fn is not None
              and content_variant._body_generator_fn.__name__ == "rewrite_json")
        check("variant_judge được bật", content_checker._variant_judge_fn is not None
              and content_checker._variant_judge_fn.__name__ == "rewrite_json")
        check("hybrid_judge được bật", content_scoring._hybrid_judge_fn is not None
              and content_scoring._hybrid_judge_fn.__name__ == "rewrite_json")
    finally:
        content_facts.set_extractor(None)
        content_hook.set_hook_generator(None)
        content_hook.set_hook_judge(None)
        content_variant.set_body_generator(None)
        content_checker.set_variant_judge(None)
        content_scoring.set_hybrid_judge(None)
        os.environ.pop("ACP_CONTENT_ENGINE_LLM", None)
        for k, v in _saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_content_engine_llm_not_wired_when_flag_off():
    """Không set ACP_CONTENT_ENGINE_LLM -- create_app() không đụng gì tới
    6 hook (giữ nguyên None), không đổi baseline rule-based/template
    của toàn bộ E1-E6."""
    print("\ncreate_app() KHÔNG bật hook nào khi ACP_CONTENT_ENGINE_LLM không set")
    _saved = {k: os.environ.get(k) for k in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY")}
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    os.environ.pop("ACP_CONTENT_ENGINE_LLM", None)
    content_facts.set_extractor(None)
    content_hook.set_hook_generator(None)
    content_hook.set_hook_judge(None)
    content_variant.set_body_generator(None)
    content_checker.set_variant_judge(None)
    content_scoring.set_hybrid_judge(None)
    try:
        from acp.web.server import create_app
        create_app()
        check("extractor vẫn None", content_facts._extractor_fn is None)
        check("hook_generator vẫn None", content_hook._hook_generator_fn is None)
        check("hook_judge vẫn None", content_hook._hook_judge_fn is None)
        check("body_generator vẫn None", content_variant._body_generator_fn is None)
        check("variant_judge vẫn None", content_checker._variant_judge_fn is None)
        check("hybrid_judge vẫn None", content_scoring._hybrid_judge_fn is None)
    finally:
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
        "name": "", "current_price": "0", "image_url": "", "channel_codes": ["ch1"],
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
        "channel_codes": ["ch1"],
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
    # channel_ids bắt buộc từ D1 (đa kênh) -- route chặn checklist rỗng, không
    # còn tự rơi về kênh gốc của bài như hành vi cũ trước khi merge.
    approve_resp = c.post(f"/duyet/{manual['id']}/approve",
                           data={"_csrf": csrf, "scheduled_at": "2026-12-25T17:00",
                                 "channel_ids": [manual["channel_id"]]})
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


def test_publish_target_retry_route():
    print("\nRoute thử lại publish target")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()

    check("route thử lại yêu cầu đăng nhập",
          c.post("/vanhanh/khong-ton-tai/retry").status_code == 302)

    c.post("/dangnhap", data={"password": "matkhau-test"})
    check("trang vận hành mở được sau đăng nhập", c.get("/vanhanh").status_code == 200)

    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    r = c.post("/vanhanh/khong-ton-tai/retry", data={"_csrf": csrf})
    check("route thử lại target không tồn tại vẫn redirect (không sập trang)", r.status_code == 302, r.status_code)
    check("báo lỗi target không tồn tại qua query", "err=" in r.location, r.location)

    r2 = c.post("/vanhanh/khong-ton-tai/retry", data={})
    check("thiếu CSRF bị chặn", r2.status_code == 400, r2.status_code)

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def _ticked_channel_ids(body: str, post_id: str) -> list:
    """Đúng những gì TRÌNH DUYỆT sẽ gửi lên khi bấm 'Duyệt & lên lịch': các
    checkbox channel_ids đang được tích trong form của riêng bài post_id.
    Checklist rỗng -> gửi rỗng -> vướng rào 'chọn ít nhất 1 kênh'."""
    start = body.find(f'action="/duyet/{post_id}/approve"')
    if start < 0:
        return []
    form = body[start:body.find("</form>", start)]
    return re.findall(r'name="channel_ids" value="([^"]+)"[^>]*checked', form)


def test_duyet_approve_route_end_to_end():
    print("\n/duyet duyệt được bài do pipeline TỰ ĐỘNG sinh (và cả bài cũ không có lựa chọn kênh)")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    ch_id = ulid()
    # Kênh riêng cho test này: ch1 của setup() đã bị test_web_security đẩy sang
    # NEEDS_REAUTH nên plan_content() không còn nhắm tới nó nữa.
    conn.execute("""INSERT INTO channel (id, code, platform, handle, external_user_id, status,
                    enabled, token_encrypted, daily_post_cap, min_gap_minutes, niches, created_at)
                    VALUES (?,?,'threads',?,?,'ACTIVE',1,?,?,?,?,?)""",
                 (ch_id, "ch_duyet_test", "@duyet_test", "uid_duyet",
                  crypto.encrypt("tok"), 12, 0, "[]", now()))
    try:
        # KHÔNG dùng plan_content()->drain()->generate_content() nữa: sau khi
        # merge, file này chạy ~90 test dùng chung DB, và score_candidates()
        # LUÔN trả về đúng 1 sản phẩm "top" y hệt nhau (dữ liệu mock tĩnh,
        # điểm không đổi) -- idempotency_key của GENERATE_CONTENT không tính
        # theo channel_id (chỉ "gen:{product_id}:{hook}"), nên hễ có 1 test
        # NÀO TRƯỚC ĐÓ trong cả file từng gọi plan_content() với niches rỗng
        # là combo (top-product, hook đầu) bị "chiếm" vĩnh viễn trong
        # job_queue (dòng không bao giờ bị xoá) -- plan_content() ở đây luôn
        # trả về [] dù score_candidates() vẫn tìm được ứng viên bình thường.
        # Đây là giới hạn có thật của idempotency key, không phải lỗi do
        # merge -- tạo bài trực tiếp qua create_post_for_product() (không qua
        # idempotency key theo product+hook) để test không phụ thuộc thứ tự
        # chạy của hàng chục test khác. Vẫn đúng ý "bài do pipeline tạo,
        # post_channel_selection có đúng 1 dòng" mà phần còn lại của test
        # (kiểm tra /duyet) cần.
        pipeline.ingest_datafeed(conn, MockAccessTrade(), limit=200)
        before = {r["id"] for r in conn.execute("SELECT id FROM post").fetchall()}
        target = next(p for p in MockAccessTrade().fetch_products(limit=200)
                      if p.rating and p.rating >= 4.5 and p.current_price > 0)
        auto_res = pipeline.create_post_for_product(
            conn, {"source": MockAccessTrade(), "publishers": {}, "storage": _FakeStorage()},
            target.external_product_id, "gd2026", channel_code="ch_duyet_test")
        check("pipeline tạo được bài cho kênh test", auto_res.get("ok"), auto_res.get("error"))
        auto_posts = [dict(r) for r in conn.execute(
            "SELECT id, channel_id, status FROM post WHERE channel_id=?", (ch_id,)).fetchall()
            if r["id"] not in before and r["status"] == "PENDING_REVIEW"]
        check("có bài PENDING_REVIEW do pipeline sinh", len(auto_posts) >= 1, auto_posts)
        if not auto_posts:
            conn.close()
            return
        post_id = auto_posts[0]["id"]

        page = c.get("/duyet")
        body = page.get_data(as_text=True)
        check("trang /duyet mở được", page.status_code == 200, page.status_code)
        ticked = _ticked_channel_ids(body, post_id)
        check("bài tự động có checkbox kênh được tích sẵn (checklist KHÔNG rỗng)",
              ticked == [ch_id], ticked)
        check("checkbox hiện đúng handle của kênh", "@duyet_test" in body, "không thấy handle")

        with c.session_transaction() as sess:
            csrf = sess["csrf"]
        # Gửi ĐÚNG những gì form vừa render ra -- nếu checklist rỗng thì đây là
        # POST không có channel_ids, y hệt thao tác thật của operator.
        r = c.post(f"/duyet/{post_id}/approve",
                   data={"_csrf": csrf, "channel_ids": ticked})
        check("duyệt trả về 302 (redirect về /duyet)", r.status_code == 302, r.status_code)
        check("duyệt KHÔNG kèm thông báo lỗi", "err=" not in (r.location or ""), r.location)
        targets = conn.execute("SELECT channel_id, status FROM publish_target WHERE post_id=?",
                               (post_id,)).fetchall()
        check("sinh đúng 1 publish_target cho bài vừa duyệt", len(targets) == 1, [dict(t) for t in targets])
        check("publish_target trỏ đúng kênh đã tích",
              len(targets) == 1 and targets[0]["channel_id"] == ch_id, [dict(t) for t in targets])

        # --- Bài "cũ": không có dòng post_channel_selection nào (bài tạo từ
        # trước khi có bảng này, hoặc do một writer tương lai quên ghi). /duyet
        # phải tự rơi về kênh gốc của bài, nếu không thì bài kẹt vĩnh viễn.
        legacy = pipeline.create_post_for_product(
            conn, {"source": MockAccessTrade(), "publishers": {}, "storage": _FakeStorage()},
            next(p for p in MockAccessTrade().fetch_products(limit=200)
                 if p.rating and p.rating >= 4.5 and p.current_price > 0).external_product_id,
            "gd2026", channel_code="ch_duyet_test")
        check("tạo được bài mô phỏng bài cũ", legacy.get("ok"), legacy.get("error"))
        legacy_id = legacy["post_id"]
        conn.execute("DELETE FROM post_channel_selection WHERE post_id=?", (legacy_id,))
        check("bài cũ đã không còn dòng post_channel_selection nào",
              conn.execute("SELECT COUNT(*) FROM post_channel_selection WHERE post_id=?",
                           (legacy_id,)).fetchone()[0] == 0)

        page2 = c.get("/duyet")
        body2 = page2.get_data(as_text=True)
        ticked2 = _ticked_channel_ids(body2, legacy_id)
        check("bài cũ vẫn hiện checkbox kênh nhờ fallback về post.channel_id",
              ticked2 == [ch_id], ticked2)
        r2 = c.post(f"/duyet/{legacy_id}/approve",
                    data={"_csrf": csrf, "channel_ids": ticked2})
        check("duyệt bài cũ trả về 302, không bị rào 'chọn ít nhất 1 kênh' chặn",
              r2.status_code == 302 and "err=" not in (r2.location or ""),
              (r2.status_code, r2.location))
        legacy_targets = conn.execute("SELECT COUNT(*) FROM publish_target WHERE post_id=?",
                                      (legacy_id,)).fetchone()[0]
        check("bài cũ cũng sinh đúng 1 publish_target", legacy_targets == 1, legacy_targets)
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id=?", (ch_id,))
        conn.close()
        for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
            os.environ.pop(var, None)


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
          polled == {"status": "ready", "metadata": {
              "name": "Váy test", "current_price": 199000,
              "original_price": None, "image_url": None, "shop": None,
          }}, polled)
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
        json={"token": token, "product_url": "https://shopee.vn/product/5/6",
              "observed_url": "https://shopee.vn/product/5/6", "metadata": {"name": "z"}},
        environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
    check("request không phải từ loopback bị chặn (403)", bad_origin.status_code == 403, bad_origin.status_code)

    bad_token = c.post(
        "/api/helper/shopee-product",
        json={"token": "sai-token", "product_url": "https://shopee.vn/product/5/6",
              "observed_url": "https://shopee.vn/product/5/6", "metadata": {"name": "z"}},
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    check("token sai bị chặn (410)", bad_token.status_code == 410, bad_token.status_code)

    ok = c.post(
        "/api/helper/shopee-product",
        json={"token": token, "product_url": "https://shopee.vn/product/5/6",
              "observed_url": "https://shopee.vn/product/5/6",
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
        json={"token": token, "product_url": "https://shopee.vn/product/5/6",
              "observed_url": "https://shopee.vn/product/5/6", "metadata": {"name": "lan hai"}},
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    check("nộp lại token đã dùng bị chặn (410)", reused.status_code == 410, reused.status_code)

    os.environ.pop("ACP_ADMIN_PASSWORD", None)


def test_oauth_meta_routes():
    print("\nRoute OAuth Meta")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()

    check("/oauth/meta/start yêu cầu đăng nhập",
          c.get("/oauth/meta/start", follow_redirects=False).status_code == 302)

    c.post("/dangnhap", data={"password": "matkhau-test"})
    start = c.get("/oauth/meta/start", follow_redirects=False)
    check("start redirect sang Meta", start.status_code == 302, start.status_code)
    check("start redirect chứa state", "state=" in start.location, start.location)
    with c.session_transaction() as sess:
        check("state được lưu vào session", bool(sess.get("meta_oauth_state")))
        real_state = sess["meta_oauth_state"]

    bad = c.get(f"/oauth/meta/callback?code=abc&state=sai-state", follow_redirects=False)
    check("callback state sai bị từ chối", bad.status_code == 400, bad.status_code)

    ok = c.get(f"/oauth/meta/callback?code=abc&state={real_state}", follow_redirects=False)
    check("callback state đúng thành công, redirect /kenh", ok.status_code == 302 and "/kenh" in ok.location,
          (ok.status_code, ok.location))

    conn = connect()
    n_channels = conn.execute("SELECT COUNT(*) FROM channel WHERE platform IN ('facebook','instagram')").fetchone()[0]
    check("import được account qua route thật", n_channels == 3, n_channels)
    connection = conn.execute("SELECT id FROM meta_connection LIMIT 1").fetchone()
    conn.close()

    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    sync = c.post("/kenh/meta/sync", data={"_csrf": csrf})
    check("đồng bộ lại thành công, redirect /kenh", sync.status_code == 302 and "/kenh" in sync.location,
          (sync.status_code, sync.location))

    no_csrf = c.post("/kenh/meta/sync", data={})
    check("đồng bộ thiếu CSRF bị chặn", no_csrf.status_code == 400, no_csrf.status_code)

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_channel_enable_disable_route():
    print("\nRoute bật/tắt kênh")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    ch = conn.execute("SELECT id FROM channel LIMIT 1").fetchone()
    conn.close()

    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    r = c.post(f"/kenh/{ch['id']}/disable", data={"_csrf": csrf})
    check("tắt kênh thành công", r.status_code == 302, r.status_code)
    conn = connect()
    row = conn.execute("SELECT enabled FROM channel WHERE id=?", (ch["id"],)).fetchone()
    check("kênh đã tắt (enabled=0)", row["enabled"] == 0, row["enabled"])
    conn.close()

    r2 = c.post(f"/kenh/{ch['id']}/enable", data={"_csrf": csrf})
    check("bật lại kênh thành công", r2.status_code == 302, r2.status_code)
    conn = connect()
    row2 = conn.execute("SELECT enabled FROM channel WHERE id=?", (ch["id"],)).fetchone()
    check("kênh đã bật lại (enabled=1)", row2["enabled"] == 1, row2["enabled"])
    conn.close()

    r3 = c.post("/kenh/khong-ton-tai/disable", data={"_csrf": csrf})
    check("tắt kênh không tồn tại vẫn redirect, không sập trang", r3.status_code == 302, r3.status_code)

    page = c.get("/kenh")
    check("trang /kenh vẫn render 200 sau các thao tác trên", page.status_code == 200, page.status_code)

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_product_checklist_shows_all_platforms():
    print("\nChecklist /sanpham hiện đủ các nền tảng (threads/facebook/instagram), không chỉ Threads")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                 (ulid(), "fb_test_checklist", "facebook", "Fake Page", "ACTIVE", 1, now()))
    conn.close()

    page = c.get("/sanpham?mode=search")
    body = page.get_data(as_text=True)
    check("checklist CÓ chứa kênh facebook (D1: đa nền tảng, không chỉ Threads)",
          "Fake Page" in body, "không thấy trong checklist")
    check("checklist dùng tên trường channel_codes (checkbox, không phải select đơn)",
          'name="channel_codes"' in body, body[:300])

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
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
    row2 = c.execute("SELECT enabled, connection_id FROM channel").fetchone()
    check("cột enabled có giá trị mặc định 1 trên dữ liệu cũ", row2["enabled"] == 1, row2["enabled"])
    check("cột connection_id NULL trên dữ liệu cũ", row2["connection_id"] is None)
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


class _FixedMetaService:
    """Fixture riêng cho test này (KHÔNG dùng MockMetaConnectionService mặc
    định) -- test_oauth_meta_routes ở Task 5 đi qua factory.get_meta_connection_service()
    và tạo account bằng fixture mặc định của MockMetaConnectionService trong
    CÙNG một CSDL tạm dùng chung cho cả file test_pilot.py; nếu test này dùng
    chung fixture đó, ai chạy trước sẽ khiến người chạy sau thấy 'đã tồn tại'
    thay vì 'mới import'. Dùng meta_user_id/external_account_id RIÊNG để không
    bao giờ đụng fixture mặc định, và luôn lọc theo connection_id của CHÍNH
    lần import này thay vì đếm toàn bảng -- đúng quy ước test_daily_cap đã có
    trong test_pipeline.py (tính theo số hiện có/delta, không đặt cứng)."""

    def oauth_authorize_url(self, state, redirect_uri):
        return f"https://mock/authorize?state={state}"

    def exchange_code(self, code, redirect_uri):
        from acp.adapters.base import ExchangedToken
        return ExchangedToken(token=f"tok_{code}", expires_in=5184000, meta_user_id="test4_user")

    def list_pages(self, user_token):
        from acp.adapters.base import PageInfo
        return [
            PageInfo("9000000000001", "Fashion Page Test", "tok_page_1"),
            PageInfo("9000000000002", "Tech Deals Test", "tok_page_2"),
        ]

    def instagram_for_page(self, page_id, page_token):
        from acp.adapters.base import InstagramInfo
        if page_id == "9000000000001":
            return InstagramInfo("9700000000001", "test.fashion", page_token)
        return None


def test_meta_account_import_and_sync():
    print("\nImport + đồng bộ account Meta")
    from acp.core import connections

    conn = connect()
    svc = _FixedMetaService()

    res = connections.connect_meta_account(conn, svc, "fake-code",
                                            "https://acp.example/oauth/meta/callback")
    check("connect_meta_account thành công", res.get("ok"), res)
    check("import đúng 3 account (2 Page + 1 IG)", res["imported"] == 3, res)
    check("lần đầu không có account cần cập nhật", res["updated"] == 0, res)
    connection_id = res["connection_id"]

    fb_rows = conn.execute(
        "SELECT * FROM channel WHERE platform='facebook' AND connection_id=?", (connection_id,)).fetchall()
    check("có 2 kênh facebook thuộc đúng connection này", len(fb_rows) == 2, len(fb_rows))
    ig_rows = conn.execute(
        "SELECT * FROM channel WHERE platform='instagram' AND connection_id=?", (connection_id,)).fetchall()
    check("có 1 kênh instagram thuộc đúng connection này", len(ig_rows) == 1, len(ig_rows))
    check("kênh instagram có username", ig_rows[0]["username"] == "test.fashion", dict(ig_rows[0]))
    check("kênh facebook có external_account_id", fb_rows[0]["external_account_id"])
    check("kênh facebook có token riêng, không rỗng", fb_rows[0]["token_encrypted"])
    check("kênh mới enabled=1", fb_rows[0]["enabled"] == 1)
    check("kênh mới status=ACTIVE", fb_rows[0]["status"] == "ACTIVE")

    connection = conn.execute("SELECT * FROM meta_connection WHERE meta_user_id=?",
                              ("test4_user",)).fetchone()
    check("tạo đúng 1 meta_connection", connection is not None and connection["id"] == connection_id)

    # Đồng bộ lại không được tạo trùng.
    res2 = connections.sync_meta_accounts(conn, svc, connection_id)
    check("sync lại không tạo account mới", res2["imported"] == 0, res2)
    total_channels = conn.execute(
        "SELECT COUNT(*) FROM channel WHERE connection_id=?", (connection_id,)).fetchone()[0]
    check("tổng số kênh thuộc connection không đổi sau sync", total_channels == 3, total_channels)

    # Kết nối lại bằng đúng meta_user_id không tạo connection thứ hai.
    res3 = connections.connect_meta_account(conn, svc, "fake-code-2",
                                             "https://acp.example/oauth/meta/callback")
    check("kết nối lại cùng user không tạo connection trùng", res3["connection_id"] == connection_id, res3)
    n_conn = conn.execute("SELECT COUNT(*) FROM meta_connection WHERE meta_user_id=?",
                          ("test4_user",)).fetchone()[0]
    check("chỉ có đúng 1 meta_connection cho user này", n_conn == 1, n_conn)

    conn.close()


def test_meta_sync_marks_vanished_account_reconnect_required():
    print("\nSync đánh dấu account mất quyền, không xoá")
    from acp.core import connections

    class _ShrinkingMetaService:
        """Lần đầu trả 2 Page, lần sau chỉ còn 1 -- mô phỏng operator gỡ quyền
        Page thứ hai trên Meta."""
        def __init__(self):
            self.calls = 0

        def oauth_authorize_url(self, state, redirect_uri):
            return "https://mock/x"

        def exchange_code(self, code, redirect_uri):
            from acp.adapters.base import ExchangedToken
            return ExchangedToken(token="tok", expires_in=1000, meta_user_id="shrink_user")

        def list_pages(self, user_token):
            from acp.adapters.base import PageInfo
            self.calls += 1
            if self.calls == 1:
                return [PageInfo("2000000000001", "Page A", "tok_a"),
                        PageInfo("2000000000002", "Page B", "tok_b")]
            return [PageInfo("2000000000001", "Page A", "tok_a")]

        def instagram_for_page(self, page_id, page_token):
            return None

    conn = connect()
    svc = _ShrinkingMetaService()
    res = connections.connect_meta_account(conn, svc, "code", "https://acp.example/oauth/meta/callback")
    check("import lần đầu 2 Page", res["imported"] == 2, res)

    res2 = connections.sync_meta_accounts(conn, svc, res["connection_id"])
    check("sync phát hiện 1 account mất quyền", res2["reconnect_required"] == 1, res2)

    page_a = conn.execute("SELECT status FROM channel WHERE external_account_id=?",
                          ("2000000000001",)).fetchone()
    page_b = conn.execute("SELECT status FROM channel WHERE external_account_id=?",
                          ("2000000000002",)).fetchone()
    check("Page còn quyền vẫn ACTIVE", page_a["status"] == "ACTIVE", page_a["status"])
    check("Page mất quyền chuyển NEEDS_REAUTH", page_b["status"] == "NEEDS_REAUTH", page_b["status"])
    check("Page mất quyền KHÔNG bị xoá", page_b is not None)
    conn.close()


def test_oauth_meta_callback_auth_error_no_500():
    print("\nCallback OAuth Meta: AuthError khi đổi code không được sập thành 500")
    from acp.adapters.base import AuthError as _AuthError

    class _FailingExchangeService:
        def oauth_authorize_url(self, state, redirect_uri):
            return f"https://mock/authorize?state={state}"

        def exchange_code(self, code, redirect_uri):
            raise _AuthError("token Meta hết hạn khi đổi code")

        def list_pages(self, user_token):
            return []

        def instagram_for_page(self, page_id, page_token):
            return None

    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()

    with c.session_transaction() as sess:
        sess["meta_oauth_state"] = "state-ok"

    original = factory.get_meta_connection_service
    factory.get_meta_connection_service = lambda: _FailingExchangeService()
    try:
        r = c.get("/oauth/meta/callback?code=abc&state=state-ok", follow_redirects=False)
    finally:
        factory.get_meta_connection_service = original

    check("callback AuthError không 500", r.status_code == 302, r.status_code)
    check("callback AuthError redirect về /kenh kèm err=",
          "/kenh" in r.location and "err=" in r.location, r.location)


def test_kenh_meta_sync_auth_error_marks_needs_reauth():
    print("\nĐồng bộ Meta: AuthError không sập 500, đánh dấu connection NEEDS_REAUTH")
    from acp.adapters.base import AuthError as _AuthError

    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    mc_id = ulid()
    conn.execute("""INSERT INTO meta_connection (id, provider, token_encrypted, meta_user_id,
                    status, created_at, updated_at) VALUES (?,'meta',?,?,'ACTIVE',?,?)""",
                 (mc_id, crypto.encrypt("user_token"), "auth_err_user", now(), now()))
    conn.close()

    class _FailingSyncService:
        def oauth_authorize_url(self, state, redirect_uri):
            return "https://mock/x"

        def exchange_code(self, code, redirect_uri):
            raise AssertionError("không dùng trong test này")

        def list_pages(self, user_token):
            raise _AuthError("token Meta bị thu hồi")

        def instagram_for_page(self, page_id, page_token):
            return None

    original = factory.get_meta_connection_service
    factory.get_meta_connection_service = lambda: _FailingSyncService()
    try:
        with c.session_transaction() as sess:
            csrf = sess["csrf"]
        r = c.post("/kenh/meta/sync", data={"_csrf": csrf})
    finally:
        factory.get_meta_connection_service = original

    check("sync AuthError không 500", r.status_code == 302, r.status_code)
    check("sync AuthError redirect kèm err=", "err=" in r.location, r.location)
    conn = connect()
    row = conn.execute("SELECT status FROM meta_connection WHERE id=?", (mc_id,)).fetchone()
    check("connection chuyển NEEDS_REAUTH sau AuthError", row["status"] == "NEEDS_REAUTH", row["status"])
    conn.close()

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_oauth_meta_callback_nonascii_state_clean_400():
    print("\nCallback OAuth Meta: state không phải ASCII trả 400 sạch, không sập 500")
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()

    with c.session_transaction() as sess:
        sess["meta_oauth_state"] = "state-ascii-only"

    r = c.get("/oauth/meta/callback?code=abc&state=" + quote("tiếng-việt-é"))
    check("state non-ASCII trả 400 sạch, không 500", r.status_code == 400, r.status_code)


def test_kenh_meta_sync_syncs_all_connections():
    print("\nĐồng bộ Meta đồng bộ lại TẤT CẢ connection, không chỉ cái gần nhất")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    from acp.core import connections
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    class _ServiceA:
        def oauth_authorize_url(self, state, redirect_uri):
            return "https://mock/x"

        def exchange_code(self, code, redirect_uri):
            from acp.adapters.base import ExchangedToken
            return ExchangedToken(token="tokA_multi", expires_in=1000, meta_user_id="multi_user_A")

        def list_pages(self, user_token):
            from acp.adapters.base import PageInfo
            return [PageInfo("8100000000001", "Page A1", "tokA1")]

        def instagram_for_page(self, page_id, page_token):
            return None

    class _ServiceB:
        def oauth_authorize_url(self, state, redirect_uri):
            return "https://mock/y"

        def exchange_code(self, code, redirect_uri):
            from acp.adapters.base import ExchangedToken
            return ExchangedToken(token="tokB_multi", expires_in=1000, meta_user_id="multi_user_B")

        def list_pages(self, user_token):
            from acp.adapters.base import PageInfo
            return [PageInfo("8200000000001", "Page B1", "tokB1")]

        def instagram_for_page(self, page_id, page_token):
            return None

    conn = connect()
    res_a = connections.connect_meta_account(conn, _ServiceA(), "code-a",
                                              "https://acp.example/oauth/meta/callback")
    res_b = connections.connect_meta_account(conn, _ServiceB(), "code-b",
                                              "https://acp.example/oauth/meta/callback")
    check("kết nối A thành công", res_a.get("ok"), res_a)
    check("kết nối B thành công", res_b.get("ok"), res_b)

    # Đặt last_sync_at cũ để phát hiện được cả hai connection đều được sync
    # LẠI thật sự (không chỉ connection tạo sau cùng).
    old_ts = "2020-01-01T00:00:00+00:00"
    conn.execute("UPDATE channel SET last_sync_at=? WHERE connection_id IN (?,?)",
                 (old_ts, res_a["connection_id"], res_b["connection_id"]))
    conn.close()

    class _CombinedService:
        """sync_meta_accounts tự giải mã user_token đã lưu theo từng connection
        rồi gọi list_pages(user_token) -- một mock DUY NHẤT phân biệt được
        connection nào đang được đồng bộ qua chính token đó."""

        def oauth_authorize_url(self, state, redirect_uri):
            return "https://mock/z"

        def exchange_code(self, code, redirect_uri):
            raise AssertionError("không dùng trong test này")

        def list_pages(self, user_token):
            from acp.adapters.base import PageInfo
            if user_token == "tokA_multi":
                return [PageInfo("8100000000001", "Page A1", "tokA1_v2")]
            return [PageInfo("8200000000001", "Page B1", "tokB1_v2")]

        def instagram_for_page(self, page_id, page_token):
            return None

    original = factory.get_meta_connection_service
    factory.get_meta_connection_service = lambda: _CombinedService()
    try:
        with c.session_transaction() as sess:
            csrf = sess["csrf"]
        r = c.post("/kenh/meta/sync", data={"_csrf": csrf})
    finally:
        factory.get_meta_connection_service = original

    check("sync route thành công (redirect /kenh)", r.status_code == 302 and "/kenh" in r.location, r.location)
    conn = connect()
    ch_a = conn.execute("SELECT last_sync_at FROM channel WHERE connection_id=?",
                        (res_a["connection_id"],)).fetchone()
    ch_b = conn.execute("SELECT last_sync_at FROM channel WHERE connection_id=?",
                        (res_b["connection_id"],)).fetchone()
    check("connection A được sync lại (last_sync_at cập nhật)", ch_a["last_sync_at"] != old_ts, ch_a["last_sync_at"])
    check("connection B được sync lại (last_sync_at cập nhật)", ch_b["last_sync_at"] != old_ts, ch_b["last_sync_at"])
    conn.close()

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_create_affiliate_product_accepts_facebook_channel():
    print("\ncreate_affiliate_product CHẤP NHẬN kênh Facebook qua checklist channel_codes (D1)")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                 (ulid(), "fb_accept_test", "facebook", "FB Accept", "ACTIVE", 1, now()))
    conn.close()

    # Tránh gọi HTTP thật ra ngoài -- image_url dùng miền img.example (RFC 2606,
    # không bao giờ resolve DNS), giống cách test_web_security() đã làm với
    # _FakeManualShopee. Mục tiêu test này là kiểm chứng route/pipeline chấp
    # nhận channel_codes Facebook, không phải kiểm chứng tải ảnh qua mạng thật.
    from acp.adapters.base import RawProduct

    class _FakeManualShopeeAccept:
        name = "manual_shopee"

        def validate_confirmed_urls(self, affiliate_url, product_url):
            pass

        def prepare_product(self, confirmed, media_dir):
            return RawProduct(
                external_product_id="789", name=confirmed.name,
                current_price=confirmed.current_price, original_price=confirmed.original_price,
                commission_value=0, commission_rate=None, category_code="khac",
                product_url=confirmed.product_url, merchant="shopee.vn",
                image_url_original=confirmed.image_url, image_path_local=None)

        def create_tracking_link(self, *args, **kwargs):
            raise AssertionError("manual Shopee không được gọi create_tracking_link")

    app.config["SHOPEE_SOURCE_FACTORY"] = lambda: _FakeManualShopeeAccept()

    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    r = c.post("/sanpham/affiliate/create", data={
        "_csrf": csrf,
        "affiliate_url": "https://s.shopee.vn/abc",
        "product_url": "https://shopee.vn/vay-i.123.456",
        "name": "Váy hoa nữ test",
        "current_price": "289000",
        "image_url": "https://img.example/product.jpg",
        "channel_codes": ["fb_accept_test"],
    })
    check("gửi mã kênh Facebook được chấp nhận, redirect sang /duyet",
          r.status_code == 302 and "/duyet" in r.location, (r.status_code, getattr(r, "location", "")))

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_duyet_approve_saves_caption_platform_and_override():
    print("\n/duyet approve lưu đúng caption theo platform + override theo account, đăng đúng caption")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (fb_id, "fb_duyet_caption_test", "facebook", "FB Duyệt Caption", "ACTIVE", 1, 12, 0, now()))
    # Kênh Threads riêng cho test này: "ch1" của setup() có thể đã bị
    # test_web_security() đẩy sang NEEDS_REAUTH ở lúc chạy chung cả suite
    # (xem test_duyet_approve_route_end_to_end() ở trên, cùng lý do), nên
    # không dùng lại "ch1" làm kênh chính ở đây.
    th_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, external_user_id, status,
                    enabled, token_encrypted, daily_post_cap, min_gap_minutes, niches, created_at)
                    VALUES (?,?,'threads',?,?,'ACTIVE',1,?,?,?,?,?)""",
                 (th_id, "th_duyet_caption_test", "@duyet_caption_test", "uid_duyet_caption_test",
                  crypto.encrypt("tok"), 12, 0, "[]", now()))
    src = MockAccessTrade()
    target = next(p for p in src.fetch_products(limit=50) if p.product_url)
    ctx = {"source": src, "publishers": {}}
    # Tạo post qua create_post_for_product(channel_codes=[...]) (đã có từ D1)
    # để post_channel_selection có SẴN cả Threads lẫn Facebook ngay từ lúc
    # tạo -- nhờ vậy /duyet render field caption_facebook NGAY LẦN GET ĐẦU
    # TIÊN, kiểm tra được tên field template render khớp với tên route đọc
    # (D1 từng lọt 1 lỗi Critical vì route/template lệch nhau mà không test
    # nào bắt được, xem đầu Task 6) mà không cần approve trước rồi mới có.
    # Kênh Threads đứng đầu -> kênh chính (post.channel_id) là Threads,
    # giống kịch bản thường gặp nhất.
    res = pipeline.create_post_for_product(
        conn, ctx, target.external_product_id, "gd2026",
        channel_codes=["th_duyet_caption_test", "fb_duyet_caption_test"])
    check("tạo bài đa kênh (facebook + threads) thành công", res.get("ok"), res.get("error"))
    post = conn.execute("SELECT * FROM post WHERE id=?", (res["post_id"],)).fetchone()
    conn.close()

    # Kiểm tra TEMPLATE thực sự render đúng tên field mà route sẽ đọc --
    # không chỉ POST thẳng bằng tên field đúng sẵn.
    page_before = c.get("/duyet")
    body_before = page_before.get_data(as_text=True)
    check("form /duyet render field caption_facebook (post có kênh facebook trong lựa chọn)",
          'name="caption_facebook"' in body_before, body_before[:2000])
    check("form /duyet render đúng field caption_override_<channel_id> cho account facebook",
          f'name="caption_override_{fb_id}"' in body_before, body_before[:2000])

    # caption_facebook đi qua content.validate() y hệt caption gốc (đủ nhãn
    # tiếp thị liên kết + link) -- ghép từ nhãn mặc định và đúng link đã có
    # trong caption gốc của bài (post["caption_final"] đã qua validate lúc
    # tạo bài nên chắc chắn có 1 dòng link).
    link_line = next(l for l in post["caption_final"].split("\n") if l.startswith("http"))
    fb_caption = f"Caption Facebook riêng nhập từ /duyet. {content.DISCLOSURE_DEFAULT}\n\n{link_line}"

    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    r = c.post(f"/duyet/{post['id']}/approve", data={
        "_csrf": csrf,
        "caption": post["caption_final"],
        "channel_ids": [post["channel_id"], fb_id],
        "caption_facebook": fb_caption,
        f"caption_override_{fb_id}": "",
    })
    check("duyệt thành công, redirect về /duyet", r.status_code == 302 and "err=" not in (r.location or ""),
          (r.status_code, r.location))

    conn = connect()
    post_after = conn.execute("SELECT caption_facebook FROM post WHERE id=?", (post["id"],)).fetchone()
    check("post.caption_facebook lưu đúng giá trị từ form",
          post_after["caption_facebook"] == fb_caption, dict(post_after))
    target_fb = conn.execute("SELECT caption_override FROM publish_target WHERE post_id=? AND channel_id=?",
                             (post["id"], fb_id)).fetchone()
    check("target facebook không có override (form gửi rỗng)", target_fb["caption_override"] is None, dict(target_fb))
    conn.close()

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def _override_field(body: str, post_id: str, channel_id: str) -> tuple:
    """(thẻ <details ...> mở, nội dung ô caption_override) của account
    channel_id trong form duyệt của bài post_id -- đúng thứ trình duyệt hiển
    thị cho operator và sẽ gửi lại khi bấm 'Duyệt & lên lịch'."""
    start = body.find(f'action="/duyet/{post_id}/approve"')
    if start < 0:
        return ("", None)
    form = body[start:body.find("</form>", start)]
    idx = form.find(f'name="caption_override_{channel_id}"')
    if idx < 0:
        return ("", None)
    d = form.rfind("<details", 0, idx)
    details_tag = form[d:form.find(">", d) + 1] if d >= 0 else ""
    ta = form.find(">", idx) + 1
    return (details_tag, html.unescape(form[ta:form.find("</textarea>", ta)]))


def test_duyet_keeps_channel_override_after_bounce():
    print("\n/duyet điền lại override theo account sau khi bài bị bounce về PENDING_REVIEW")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (fb_id, "fb_bounce_override_test", "facebook", "FB Bounce Override", "ACTIVE", 1, 12, 0, now()))
    # Kênh Threads riêng cho test này ("ch1" của setup() có thể đã bị
    # test_web_security() đẩy sang NEEDS_REAUTH khi chạy chung cả suite).
    th_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, external_user_id, status,
                    enabled, token_encrypted, daily_post_cap, min_gap_minutes, niches, created_at)
                    VALUES (?,?,'threads',?,?,'ACTIVE',1,?,?,?,?,?)""",
                 (th_id, "th_bounce_override_test", "@bounce_override_test", "uid_bounce_override",
                  crypto.encrypt("tok"), 12, 0, "[]", now()))
    src = MockAccessTrade()
    target = next(p for p in src.fetch_products(limit=50) if p.product_url)
    res = pipeline.create_post_for_product(
        conn, {"source": src, "publishers": {}}, target.external_product_id, "gd2026",
        channel_codes=["th_bounce_override_test", "fb_bounce_override_test"])
    check("tạo bài đa kênh (threads + facebook) thành công", res.get("ok"), res.get("error"))
    post = conn.execute("SELECT * FROM post WHERE id=?", (res["post_id"],)).fetchone()
    link_line = next(l for l in post["caption_final"].split("\n") if l.startswith("http"))
    override_text = (f"Caption riêng operator gõ cho đúng account Threads này. "
                     f"{content.DISCLOSURE_DEFAULT}\n\n{link_line}")
    conn.close()

    # --- Lần duyệt 1: operator nhập override cho riêng account Threads.
    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    r = c.post(f"/duyet/{post['id']}/approve", data={
        "_csrf": csrf,
        "caption": post["caption_final"],
        "channel_ids": [th_id, fb_id],
        f"caption_override_{th_id}": override_text,
        f"caption_override_{fb_id}": "",
    })
    check("duyệt lần 1 thành công", r.status_code == 302 and "err=" not in (r.location or ""),
          (r.status_code, r.location))
    conn = connect()
    t1 = conn.execute("SELECT caption_override FROM publish_target WHERE post_id=? AND channel_id=?",
                      (post["id"], th_id)).fetchone()
    check("lần duyệt 1 lưu override vào publish_target của account Threads",
          t1 and t1["caption_override"] == override_text, dict(t1) if t1 else None)

    # --- Bounce: ContentViolationError ở account Facebook đẩy CẢ BÀI về
    # PENDING_REVIEW và huỷ các target còn lại (core/jobs.py, sub-project D1).
    # Mô phỏng thẳng trạng thái sau bounce, giống test_pipeline.py vẫn làm.
    conn.execute("UPDATE publish_target SET status='CANCELLED' WHERE post_id=?", (post["id"],))
    conn.execute("UPDATE post SET status='PENDING_REVIEW' WHERE id=?", (post["id"],))
    conn.close()

    # --- Bài quay lại /duyet: ô override phải còn nguyên chữ operator đã gõ.
    page = c.get("/duyet")
    body = page.get_data(as_text=True)
    check("trang /duyet mở được sau bounce", page.status_code == 200, page.status_code)
    th_tag, th_val = _override_field(body, post["id"], th_id)
    check("ô override của account Threads được điền lại đúng chữ đã nhập trước đó",
          th_val == override_text, repr(th_val))
    check("<details> tự mở khi có override cũ (operator không bỏ sót)", "open" in th_tag, th_tag)
    fb_tag, fb_val = _override_field(body, post["id"], fb_id)
    check("account chưa từng có override vẫn để trống và <details> vẫn đóng",
          fb_val == "" and "open" not in fb_tag, (fb_tag, repr(fb_val)))

    # --- Duyệt lại đúng như form vừa render (operator KHÔNG gõ lại gì cả).
    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    r = c.post(f"/duyet/{post['id']}/approve", data={
        "_csrf": csrf,
        "caption": post["caption_final"],
        "channel_ids": [th_id, fb_id],
        f"caption_override_{th_id}": th_val,
        f"caption_override_{fb_id}": fb_val,
    })
    check("duyệt lại thành công", r.status_code == 302 and "err=" not in (r.location or ""),
          (r.status_code, r.location))
    conn = connect()
    t2 = conn.execute("""SELECT caption_override FROM publish_target
                         WHERE post_id=? AND channel_id=? AND status='SCHEDULED'""",
                      (post["id"], th_id)).fetchone()
    check("publish_target MỚI sau khi duyệt lại vẫn giữ override của operator",
          t2 and t2["caption_override"] == override_text, dict(t2) if t2 else None)
    t2fb = conn.execute("""SELECT caption_override FROM publish_target
                           WHERE post_id=? AND channel_id=? AND status='SCHEDULED'""",
                        (post["id"], fb_id)).fetchone()
    check("account Facebook vẫn không có override (ô để trống)",
          t2fb and t2fb["caption_override"] is None, dict(t2fb) if t2fb else None)
    conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id IN (?,?)", (th_id, fb_id))
    conn.close()

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_thuvien_anh_upload_list_delete_end_to_end():
    print("\n/thuvien-anh: upload file + dán URL, hiện đúng trong grid, xoá đúng luồng")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    from io import BytesIO
    from PIL import Image
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    # Kiểm tra TEMPLATE thực sự render đúng field mà route sẽ đọc, trước
    # khi POST -- cùng lý do đã áp dụng ở D1/D2 (route/template lệch nhau
    # không test nào bắt được).
    page_before = c.get("/thuvien-anh")
    check("trang /thuvien-anh mở được", page_before.status_code == 200, page_before.status_code)
    body_before = page_before.get_data(as_text=True)
    check("form upload có field file 'image'", 'name="image"' in body_before, body_before[:1000])
    check("form upload có field 'image_url'", 'name="image_url"' in body_before, body_before[:1000])

    img = Image.new("RGB", (12, 12), (5, 6, 7))
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    r = c.post("/thuvien-anh/upload", data={
        "_csrf": csrf,
        "image": (buf, "test.png"),
    }, content_type="multipart/form-data")
    check("upload file thành công, redirect về /thuvien-anh",
          r.status_code == 302 and "err=" not in (r.location or ""), (r.status_code, r.location))

    page_after = c.get("/thuvien-anh")
    body_after = page_after.get_data(as_text=True)
    conn = connect()
    asset = conn.execute("SELECT * FROM media_asset WHERE source='upload' ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    check("asset vừa upload có mặt trong grid", asset["url"] in body_after, asset["url"] if asset else None)

    with c.session_transaction() as sess:
        csrf2 = sess["csrf"]
    r2 = c.post(f"/thuvien-anh/{asset['id']}/xoa", data={"_csrf": csrf2})
    check("xoá asset không ai dùng thành công",
          r2.status_code == 302 and "err=" not in (r2.location or ""), (r2.status_code, r2.location))
    conn = connect()
    gone = conn.execute("SELECT 1 FROM media_asset WHERE id=?", (asset["id"],)).fetchone()
    conn.close()
    check("asset đã bị xoá khỏi CSDL", gone is None)

    # Nhánh "dán URL" -- vẫn chưa test nào đụng tới
    # materialize_external_image() (đường tải ảnh ngoài qua SafeHttpClient,
    # có chặn SSRF/redirect). Route thật không nhận http_client tiêm vào nên
    # giả lập ngay tại tầng HTTP (session + DNS resolver), không mock thẳng
    # hàm materialize_external_image() -- để hàm thật vẫn chạy nguyên vẹn.
    from acp.adapters.safe_http import SafeHttpClient
    import acp.core.media_library as ml

    img2 = Image.new("RGB", (10, 10), (9, 8, 7))
    buf2 = BytesIO()
    img2.save(buf2, format="PNG")
    png_bytes = buf2.getvalue()

    fake_session = _FakeSession([_FakeHttpResponse(200, {"Content-Type": "image/png"}, png_bytes)])
    orig_safe_http_client = ml.SafeHttpClient
    ml.SafeHttpClient = lambda *a, **kw: SafeHttpClient(
        session=fake_session, dns_resolver=_public_dns,
        **{k: v for k, v in kw.items() if k not in ("session", "dns_resolver")})
    try:
        with c.session_transaction() as sess:
            csrf3 = sess["csrf"]
        r3 = c.post("/thuvien-anh/upload", data={
            "_csrf": csrf3,
            "image_url": "https://cdn.example.com/anh-san-pham.png",
        })
        check("dán URL thành công, redirect về /thuvien-anh",
              r3.status_code == 302 and "err=" not in (r3.location or ""), (r3.status_code, r3.location))
    finally:
        ml.SafeHttpClient = orig_safe_http_client

    check("SafeHttpClient giả lập thực sự được gọi (materialize_external_image không bị bypass)",
          len(fake_session.calls) == 1, fake_session.calls)

    page_after_url = c.get("/thuvien-anh")
    body_after_url = page_after_url.get_data(as_text=True)
    conn = connect()
    asset_url = conn.execute(
        "SELECT * FROM media_asset WHERE source='url' ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    check("asset dán URL được ghi vào media_asset với source='url'",
          asset_url is not None, dict(asset_url) if asset_url else None)
    check("asset dán URL có mặt trong grid /thuvien-anh",
          asset_url is not None and asset_url["url"] in body_after_url,
          asset_url["url"] if asset_url else None)

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_sanpham_affiliate_create_with_media_asset_ids_end_to_end():
    print("\n/sanpham affiliate: chọn ảnh thêm từ thư viện lúc tạo bài, ghi đúng post_media")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    asset_id = ulid()
    conn.execute("INSERT INTO media_asset (id, url, source, created_at) VALUES (?,?,?,?)",
                 (asset_id, "https://fake-storage.example/sanpham-test.jpg", "upload", now()))
    # Kênh riêng cho test này -- không dùng lại "ch1" của setup() vì các test
    # chạy trước trong cùng tiến trình (vd. test_web_security()) có thể đã
    # đẩy nó sang NEEDS_REAUTH (xem ghi chú tương tự ở
    # test_duyet_approve_saves_caption_platform_and_override()).
    th_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, external_user_id, status,
                    enabled, token_encrypted, daily_post_cap, min_gap_minutes, niches, created_at)
                    VALUES (?,?,'threads',?,?,'ACTIVE',1,?,?,?,?,?)""",
                 (th_id, "th_media_test", "@th_media_test", "uid_th_media_test",
                  crypto.encrypt("tok"), 12, 0, "[]", now()))
    conn.close()

    from acp.adapters.shopee_affiliate import ProductMetadata, ResolvedAffiliateUrl

    class _FakeManualShopeeMedia:
        name = "manual_shopee"
        def resolve(self, affiliate_url):
            return ResolvedAffiliateUrl(
                affiliate_url=affiliate_url, product_url="https://shopee.vn/vay-i.123.456")
        def metadata(self, product_url):
            return ProductMetadata(
                name="Váy hoa nữ test D3", current_price=289000, original_price=None,
                image_url="https://img.example/product.jpg", shop=None)
        def validate_confirmed_urls(self, affiliate_url, product_url):
            pass
        def prepare_product(self, confirmed, media_dir):
            from acp.adapters.base import RawProduct
            return RawProduct(
                external_product_id="media-test-1", name=confirmed.name,
                current_price=confirmed.current_price, original_price=confirmed.original_price,
                commission_value=0, commission_rate=None, category_code="khac",
                product_url=confirmed.product_url, merchant="shopee.vn",
                image_url_original=confirmed.image_url, image_path_local=None)
        def create_tracking_link(self, *args, **kwargs):
            raise AssertionError("manual Shopee không được gọi create_tracking_link")

    app.config["SHOPEE_SOURCE_FACTORY"] = lambda: _FakeManualShopeeMedia()

    with c.session_transaction() as sess:
        csrf = sess["csrf"]

    # Kiểm tra TEMPLATE thực sự render đúng field mà route sẽ đọc, trước khi
    # POST tạo bài thật. GET đơn thuần (không kèm resolve) chỉ ra được màn
    # hình "Nhập link affiliate" (chưa có checklist vì chưa có resolved) --
    # /sanpham/affiliate/resolve mới là nơi màn hình xác nhận (có checklist)
    # được render, và resolve() không tạo post/product nào (an toàn dùng
    # làm bước xem trước, giống test hiện có "resolve metadata chưa tạo post").
    page = c.post("/sanpham/affiliate/resolve", data={
        "_csrf": csrf, "affiliate_url": "https://s.shopee.vn/abc"})
    body = page.get_data(as_text=True)
    check("checklist ảnh thêm render đúng field media_asset_ids",
          'name="media_asset_ids"' in body, body[:500])
    check("checklist có ảnh vừa tạo trong thư viện",
          "sanpham-test.jpg" in body, "không thấy trong checklist")

    r = c.post("/sanpham/affiliate/create", data={
        "_csrf": csrf,
        "affiliate_url": "https://s.shopee.vn/abc",
        "product_url": "https://shopee.vn/vay-i.123.456",
        "name": "Váy hoa nữ test D3",
        "current_price": "289000",
        "image_url": "https://img.example/product.jpg",
        "channel_codes": ["th_media_test"],
        "media_asset_ids": [asset_id],
    })
    check("tạo bài với ảnh thêm thành công, redirect sang /duyet",
          r.status_code == 302 and "/duyet" in r.location, (r.status_code, getattr(r, "location", "")))

    conn = connect()
    post = conn.execute("""SELECT p.id FROM post p JOIN product pr ON pr.id = p.product_id
                           WHERE pr.external_product_id='media-test-1' ORDER BY p.id DESC LIMIT 1""").fetchone()
    check("tìm được post vừa tạo", post is not None, post)
    pm = conn.execute("SELECT media_asset_id, position FROM post_media WHERE post_id=?", (post["id"],)).fetchone()
    check("post_media ghi đúng asset đã chọn, position=1",
          pm is not None and pm["media_asset_id"] == asset_id and pm["position"] == 1,
          dict(pm) if pm else None)
    conn.close()

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_sanpham_search_mode_shows_media_checklist_and_per_row_prompt():
    print("\n/sanpham tìm kiếm: hiện checklist ảnh thêm + prompt AI riêng từng dòng sản phẩm")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    asset_id = ulid()
    conn.execute("INSERT INTO media_asset (id, url, source, created_at) VALUES (?,?,?,?)",
                 (asset_id, "https://fake-storage.example/search-mode-test.jpg", "upload", now()))
    conn.close()

    page = c.get("/sanpham?mode=search&nguon=mock")
    body = page.get_data(as_text=True)
    check("checklist ảnh thêm render đúng field media_asset_ids ở chế độ tìm kiếm",
          'name="media_asset_ids"' in body, body[:500])
    check("checklist có ảnh vừa tạo trong thư viện",
          "search-mode-test.jpg" in body, "không thấy trong checklist")
    # Đếm số dòng sản phẩm từ tiêu đề đã render để đối chiếu số khối prompt per-row
    product_count_match = re.search(r'<h2>(\d+) sản phẩm</h2>', body)
    expected_prompt_count = int(product_count_match.group(1)) if product_count_match else 0
    check("mỗi dòng sản phẩm có khối gợi ý prompt riêng (nhiều khối <details>)",
          body.count("Gợi ý prompt") == expected_prompt_count,
          f"thấy {body.count('Gợi ý prompt')}, cần {expected_prompt_count}")

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_kenh_account_group_crud_end_to_end():
    print("\n/kenh: tạo/sửa/xoá AccountGroup, checklist đúng field, ghi đúng thành viên")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    ch1 = conn.execute("SELECT id, code FROM channel WHERE code='ch1'").fetchone()
    aux_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (aux_id, "kenh_ag_test", "facebook", "Kenh AG Test", "ACTIVE", 1, 12, 0, now()))
    conn.close()

    # Kiểm tra TEMPLATE thực sự render đúng field mà route sẽ đọc, trước
    # khi POST -- cùng lý do đã áp dụng ở D1/D3.
    page = c.get("/kenh")
    check("trang /kenh mở được", page.status_code == 200, page.status_code)
    body = page.get_data(as_text=True)
    check("form tạo nhóm có field 'name'", 'name="name"' in body, body[:1000])
    check("form tạo nhóm có checklist 'channel_ids'", 'name="channel_ids"' in body, body[:1000])

    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    r = c.post("/kenh/nhom/tao", data={
        "_csrf": csrf, "name": "Nhóm test D4 kenh",
        "channel_ids": [ch1["id"], aux_id],
    })
    check("tạo nhóm thành công, redirect về /kenh",
          r.status_code == 302 and "err=" not in (r.location or ""), (r.status_code, r.location))

    page_after = c.get("/kenh")
    body_after = page_after.get_data(as_text=True)
    check("tên nhóm vừa tạo có mặt trên trang", "Nhóm test D4 kenh" in body_after, "không thấy")

    conn = connect()
    group = conn.execute(
        "SELECT id FROM account_group WHERE name='Nhóm test D4 kenh' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    check("tìm được nhóm vừa tạo", group is not None, group)
    n_members = conn.execute("SELECT COUNT(*) FROM account_group_channel WHERE group_id=?",
                             (group["id"],)).fetchone()[0]
    check("đúng 2 thành viên", n_members == 2, n_members)
    conn.close()

    with c.session_transaction() as sess:
        csrf2 = sess["csrf"]
    r2 = c.post(f"/kenh/nhom/{group['id']}/sua", data={"_csrf": csrf2, "channel_ids": [aux_id]})
    check("sửa nhóm thành công, redirect về /kenh",
          r2.status_code == 302 and "err=" not in (r2.location or ""), (r2.status_code, r2.location))
    conn = connect()
    members = {r["channel_id"] for r in conn.execute(
        "SELECT channel_id FROM account_group_channel WHERE group_id=?", (group["id"],)).fetchall()}
    check("sau khi sửa chỉ còn đúng 1 thành viên (aux_id)", members == {aux_id}, members)
    conn.close()

    with c.session_transaction() as sess:
        csrf3 = sess["csrf"]
    r3 = c.post(f"/kenh/nhom/{group['id']}/xoa", data={"_csrf": csrf3})
    check("xoá nhóm thành công, redirect về /kenh",
          r3.status_code == 302 and "err=" not in (r3.location or ""), (r3.status_code, r3.location))
    conn = connect()
    gone = conn.execute("SELECT 1 FROM account_group WHERE id=?", (group["id"],)).fetchone()
    check("nhóm đã bị xoá khỏi CSDL", gone is None)
    conn.close()

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_sanpham_shows_account_group_quick_select_both_modes():
    print("\n/sanpham cả 2 chế độ: hiện nút chọn nhanh theo nhóm, đúng channel_codes nhúng vào onclick")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    ch1 = conn.execute("SELECT id, code FROM channel WHERE code='ch1'").fetchone()
    group_res = pipeline.create_account_group(conn, "Nhóm sanpham test", [ch1["id"]])
    conn.close()
    check("tạo nhóm test thành công", group_res.get("ok"), group_res.get("error"))

    # Chế độ Tìm kiếm
    page_search = c.get("/sanpham?mode=search&nguon=mock")
    body_search = page_search.get_data(as_text=True)
    check("chế độ tìm kiếm: tên nhóm hiện trên trang", "Nhóm sanpham test" in body_search, "không thấy")
    check("chế độ tìm kiếm: đúng channel_codes của nhóm nhúng vào onclick",
          ('acpTickGroup(this, [&#34;' + ch1["code"] + '&#34;]') in body_search, body_search[:2000])

    # Chế độ Affiliate: nút nhóm nằm trong form xác nhận (product-confirm__form),
    # chỉ render sau khi có resolved/metadata (xem ghi chú route thật ở D3 --
    # GET /sanpham?mode=affiliate KHÔNG bao giờ tới được form đó, phải POST
    # /sanpham/affiliate/resolve, route không mutate DB, dùng làm bước xem
    # trước đúng khuôn D3 đã lập).
    from acp.adapters.shopee_affiliate import ResolvedAffiliateUrl, ProductMetadata

    class _FakeManualShopeeAG:
        name = "manual_shopee"
        def resolve(self, url):
            return ResolvedAffiliateUrl(affiliate_url=url, product_url="https://shopee.vn/vay-i.1.1")
        def metadata(self, product_url):
            return ProductMetadata(name="SP test", current_price=100000, image_url="https://img/x.jpg")

    app.config["SHOPEE_SOURCE_FACTORY"] = lambda: _FakeManualShopeeAG()
    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    resolved_page = c.post("/sanpham/affiliate/resolve", data={
        "_csrf": csrf, "affiliate_url": "https://s.shopee.vn/abc"})
    body_affiliate = resolved_page.get_data(as_text=True)
    check("chế độ affiliate: tên nhóm hiện trên trang", "Nhóm sanpham test" in body_affiliate, "không thấy")
    check("chế độ affiliate: đúng channel_codes của nhóm nhúng vào onclick",
          ('acpTickGroup(this, [&#34;' + ch1["code"] + '&#34;]') in body_affiliate, body_affiliate[:2000])

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_vanhanh_shows_multi_channel_post_breakdown():
    print("\n/vanhanh: bài đa kênh hiện breakdown theo publish_target (D4-B)")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (fb_id, "fb_vanhanh_test", "facebook", "FB Vận Hành Test", "ACTIVE", 1, 12, 0, now()))
    ig_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (ig_id, "ig_vanhanh_test", "instagram", "IG Vận Hành Test", "ACTIVE", 1, 12, 0, now()))
    # Kênh riêng cho bài đơn-kênh, KHÔNG dùng "ch1" dùng chung (có thể đã bị
    # test_web_security() đẩy sang NEEDS_REAUTH khi chạy chung cả suite).
    solo_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (solo_id, "solo_vanhanh_test", "facebook", "Solo Vận Hành Test", "ACTIVE", 1, 12, 0, now()))

    src = MockAccessTrade()
    sample = [p for p in src.fetch_products(limit=50) if p.product_url]
    multi_target, solo_target = sample[0], sample[1]
    ctx = {"source": src, "publishers": {}}

    res_multi = pipeline.create_post_for_product(
        conn, ctx, multi_target.external_product_id, "gd2026",
        channel_codes=["fb_vanhanh_test", "ig_vanhanh_test"])
    check("tạo bài đa kênh thành công", res_multi.get("ok"), res_multi.get("error"))
    post_multi = conn.execute("SELECT * FROM post WHERE id=?", (res_multi["post_id"],)).fetchone()
    product_multi = conn.execute("SELECT name FROM product WHERE id=?", (post_multi["product_id"],)).fetchone()

    res_solo = pipeline.create_post_for_product(
        conn, ctx, solo_target.external_product_id, "gd2026", channel_codes=["solo_vanhanh_test"])
    check("tạo bài đơn kênh thành công", res_solo.get("ok"), res_solo.get("error"))
    post_solo = conn.execute("SELECT * FROM post WHERE id=?", (res_solo["post_id"],)).fetchone()
    product_solo = conn.execute("SELECT name FROM product WHERE id=?", (post_solo["product_id"],)).fetchone()
    conn.close()

    with c.session_transaction() as sess:
        csrf = sess["csrf"]
    r = c.post(f"/duyet/{post_multi['id']}/approve", data={
        "_csrf": csrf, "caption": post_multi["caption_final"], "channel_ids": [fb_id, ig_id]})
    check("duyệt bài đa kênh thành công", r.status_code == 302 and "err=" not in (r.location or ""),
          (r.status_code, r.location))
    with c.session_transaction() as sess:
        csrf2 = sess["csrf"]
    r2 = c.post(f"/duyet/{post_solo['id']}/approve", data={
        "_csrf": csrf2, "caption": post_solo["caption_final"], "channel_ids": [solo_id]})
    check("duyệt bài đơn kênh thành công", r2.status_code == 302 and "err=" not in (r2.location or ""),
          (r2.status_code, r2.location))

    conn = connect()
    targets = conn.execute("SELECT id, channel_id FROM publish_target WHERE post_id=?",
                           (post_multi["id"],)).fetchall()
    check("bài đa kênh có đúng 2 publish_target", len(targets) == 2, len(targets))
    fb_target = next(t for t in targets if t["channel_id"] == fb_id)
    ig_target = next(t for t in targets if t["channel_id"] == ig_id)
    conn.execute("UPDATE publish_target SET status='SUCCESS', updated_at=? WHERE id=?", (now(), fb_target["id"]))
    conn.execute("UPDATE publish_target SET status='FAILED', last_error='lỗi test D4-B', updated_at=? WHERE id=?",
                 (now(), ig_target["id"]))
    conn.close()

    page = c.get("/vanhanh")
    check("trang /vanhanh mở được", page.status_code == 200, page.status_code)
    body = page.get_data(as_text=True)
    section = re.search(r'<section id="multi-channel-breakdown">.*?</section>', body, re.S)
    check("có khối breakdown đa kênh trong trang", section is not None, body[:500])
    section_html = section.group(0) if section else ""
    check("bài đa kênh xuất hiện trong breakdown", product_multi["name"] in section_html,
          product_multi["name"])
    check("breakdown hiện đủ trạng thái SUCCESS và FAILED của 2 kênh",
          "SUCCESS" in section_html and "FAILED" in section_html, section_html[:1500])
    check("bài đơn kênh KHÔNG xuất hiện trong breakdown đa kênh (chỉ <2 target thì không cần breakdown)",
          product_solo["name"] not in section_html, product_solo["name"])

    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_duyet_shows_variants_block_when_generation_run_exists():
    print("\nGET /duyet hiện khối CONTENT VARIANTS khi bài có content_generation_run READY")
    from acp.core import system_settings
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    system_settings.set_setting(conn, "content_engine_v2_enabled", "1")
    src = MockAccessTrade()
    target = next(p for p in src.fetch_products(limit=80) if p.product_url)
    ctx = {"source": src, "publishers": {}, "storage": _FakeStorage()}
    res = pipeline.create_post_for_product(conn, ctx, target.external_product_id, "gd2026")
    system_settings.set_setting(conn, "content_engine_v2_enabled", "0")
    check("tạo bài thành công", res.get("ok"), res.get("error"))
    if res.get("ok"):
        run = conn.execute("SELECT * FROM content_generation_run WHERE post_id=?", (res["post_id"],)).fetchone()
        # Assert tường minh thay vì lặng lẽ bỏ qua: fixture không ra được run
        # READY thì đây là failure có tên, không phải "test đạt với 0 check".
        check("có content_generation_run READY", run is not None and run["status"] == "READY",
              run["status"] if run else None)
        if run and run["status"] == "READY":
            body = c.get("/duyet").get_data(as_text=True)
            check("có chữ CONTENT VARIANTS trong trang", "CONTENT VARIANTS" in body, "không tìm thấy")
            check("có nhãn Variant hoặc Bản tốt nhất", ("Variant" in body or "Bản tốt nhất" in body))
    conn.close()
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_duyet_no_variants_block_when_no_generation_run():
    print("\nGET /duyet KHÔNG hiện khối CONTENT VARIANTS cho bài tạo lúc flag tắt")
    from acp.core import system_settings
    conn = connect()
    system_settings.set_setting(conn, "content_engine_v2_enabled", "0")
    src = MockAccessTrade()
    target = next(p for p in src.fetch_products(limit=50) if p.product_url)
    ctx = {"source": src, "publishers": {}, "storage": _FakeStorage()}
    res = pipeline.create_post_for_product(conn, ctx, target.external_product_id, "gd2026")
    check("tạo bài thành công", res.get("ok"), res.get("error"))
    if res.get("ok"):
        run = conn.execute("SELECT * FROM content_generation_run WHERE post_id=?", (res["post_id"],)).fetchone()
        check("không có content_generation_run", run is None, run)
    conn.close()


def test_duyet_variant_card_embeds_use_variant_button():
    print("\nGET /duyet mỗi variant card có nút acpUseVariant với data caption_by_platform nhúng qua tojson")
    from acp.core import system_settings
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    system_settings.set_setting(conn, "content_engine_v2_enabled", "1")
    src = MockAccessTrade()
    target = next(p for p in src.fetch_products(limit=80) if p.product_url)
    ctx = {"source": src, "publishers": {}, "storage": _FakeStorage()}
    res = pipeline.create_post_for_product(conn, ctx, target.external_product_id, "gd2026")
    run = conn.execute("SELECT * FROM content_generation_run WHERE post_id=?", (res.get("post_id"),)).fetchone() if res.get("ok") else None
    system_settings.set_setting(conn, "content_engine_v2_enabled", "0")
    check("tạo được bài có content_generation_run READY",
          res.get("ok") and run is not None and run["status"] == "READY",
          (res.get("error"), run["status"] if run else None))
    if res.get("ok") and run and run["status"] == "READY":
        body = c.get("/duyet").get_data(as_text=True)
        check("có acpUseVariant( trong trang (nút chọn variant)", "acpUseVariant(" in body, "không tìm thấy")
        # Đủ 3 nút regenerate, không chỉ mỗi "Đổi hook" -- 2 action lam-lai/
        # doi-angle đã có ở review_action() nhưng trước đây không có UI nào gọi.
        check("có form POST /duyet/<id>/doi-hook", f"/duyet/{res['post_id']}/doi-hook" in body)
        check("có form POST /duyet/<id>/lam-lai", f"/duyet/{res['post_id']}/lam-lai" in body)
        check("có form POST /duyet/<id>/doi-angle", f"/duyet/{res['post_id']}/doi-angle" in body)
    conn.close()
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_duyet_page_defines_acp_use_variant_function():
    print("\nGET /duyet có định nghĩa function acpUseVariant trong <script>")
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})
    body = c.get("/duyet").get_data(as_text=True)
    check("có function acpUseVariant", "function acpUseVariant(" in body, "không tìm thấy")
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def _mk_ready_variant_row_and_client():
    """Tạo 1 bài qua Content Engine v2 (flag bật tạm thời) + 1 Flask test
    client đã đăng nhập, trả (post_id, variant_row đầu tiên, client, csrf)
    -- dùng chung cho các test regenerate action.

    Fixture hỏng (không tạo được bài READY) thì check() ngay TẠI ĐÂY thành
    một failure có tên rõ ràng, rồi vẫn trả (None, None, None, None) để
    guard `if post_id:` của caller là lớp phòng thủ thứ hai. Trước đây hàm
    này im lặng trả None -- mọi test dùng nó sẽ "đạt" với 0 check chạy
    thật, không phân biệt được với "tất cả check đều đạt"."""
    from acp.core import system_settings
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})
    with c.session_transaction() as sess:
        csrf = sess["csrf"]

    conn = connect()
    system_settings.set_setting(conn, "content_engine_v2_enabled", "1")
    src = MockAccessTrade()
    target = next(p for p in src.fetch_products(limit=80) if p.product_url)
    ctx = {"source": src, "publishers": {}, "storage": _FakeStorage()}
    res = pipeline.create_post_for_product(conn, ctx, target.external_product_id, "gd2026")
    system_settings.set_setting(conn, "content_engine_v2_enabled", "0")
    check("fixture regenerate: tạo được bài", res.get("ok"), res.get("error"))
    if not res.get("ok"):
        conn.close()
        return None, None, None, None
    run = conn.execute("SELECT * FROM content_generation_run WHERE post_id=?", (res["post_id"],)).fetchone()
    check("fixture regenerate: có content_generation_run READY",
          run is not None and run["status"] == "READY", run["status"] if run else None)
    if not run or run["status"] != "READY":
        conn.close()
        return None, None, None, None
    variant_row = conn.execute("SELECT * FROM content_variant_row WHERE run_id=? ORDER BY label LIMIT 1", (run["id"],)).fetchone()
    check("fixture regenerate: có ít nhất 1 content_variant_row", variant_row is not None)
    if variant_row is None:
        conn.close()
        return None, None, None, None
    conn.close()
    return res["post_id"], dict(variant_row), c, csrf


def test_review_action_doi_hook_changes_only_hook():
    print("\nPOST /duyet/<id>/doi-hook chỉ đổi hook, không đổi angle/main_message/cta của variant")
    post_id, variant, c, csrf = _mk_ready_variant_row_and_client()
    if post_id:
        c.post(f"/duyet/{post_id}/doi-hook", data={"variant_id": variant["id"], "_csrf": csrf})
        conn = connect()
        after = conn.execute("SELECT * FROM content_variant_row WHERE id=?", (variant["id"],)).fetchone()
        conn.close()
        check("angle không đổi", after["angle"] == variant["angle"], (after["angle"], variant["angle"]))
        check("main_message không đổi", after["main_message"] == variant["main_message"])
        check("cta không đổi", after["cta"] == variant["cta"])
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_review_action_lam_lai_regenerates_same_angle():
    print("\nPOST /duyet/<id>/lam-lai sinh lại variant cùng angle, nội dung thực sự đổi")
    from acp.core import content_variant as _cv
    post_id, variant, c, csrf = _mk_ready_variant_row_and_client()
    if post_id:
        # Không có body generator thì generate_body() rơi về template cố định
        # -- sinh lại ra y hệt chuỗi cũ, "đổi hay không đổi" phụ thuộc may rủi
        # của rng.choice(CTA_POOL) 2 phần tử. Đăng ký generator trả nội dung
        # khác nhau mỗi lần để khẳng định chắc chắn: route CÓ chạy lại bộ sinh
        # và CÓ ghi kết quả mới xuống DB, không âm thầm no-op.
        calls = {"n": 0}

        def counting_body_generator(prompt):
            calls["n"] += 1
            return json.dumps({"main_message": f"Ý chính sinh lại lần {calls['n']}",
                               "body": [f"Điểm phụ lần {calls['n']}"]}, ensure_ascii=False)

        _cv.set_body_generator(counting_body_generator)
        try:
            c.post(f"/duyet/{post_id}/lam-lai", data={"variant_id": variant["id"], "_csrf": csrf})
        finally:
            _cv.set_body_generator(None)
        conn = connect()
        after = conn.execute("SELECT * FROM content_variant_row WHERE id=?", (variant["id"],)).fetchone()
        conn.close()
        check("angle giữ nguyên (regenerate cùng angle)", after["angle"] == variant["angle"])
        check("bộ sinh body được gọi lại đúng 1 lần", calls["n"] == 1, calls["n"])
        changed = [f for f in ("hook", "main_message", "cta", "body_json") if after[f] != variant[f]]
        check("nội dung thực sự được sinh lại (ít nhất 1 trong hook/main_message/cta/body_json đổi)",
              bool(changed), changed)
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_review_action_doi_angle_changes_angle():
    print("\nPOST /duyet/<id>/doi-angle đổi THẬT sang angle khác chưa dùng trong run")
    from acp.core import content_angle
    post_id, variant, c, csrf = _mk_ready_variant_row_and_client()
    if post_id:
        conn = connect()
        used_before = {r["angle"] for r in conn.execute(
            "SELECT cv.angle FROM content_variant_row cv JOIN content_generation_run cgr "
            "ON cv.run_id=cgr.id WHERE cgr.post_id=?", (post_id,)).fetchall()}
        conn.close()
        resp = c.post(f"/duyet/{post_id}/doi-angle", data={"variant_id": variant["id"], "_csrf": csrf})
        conn = connect()
        after = conn.execute("SELECT * FROM content_variant_row WHERE id=?", (variant["id"],)).fetchone()
        conn.close()
        check("variant vẫn còn tồn tại sau request (không bị xoá/lỗi 500)", after is not None)
        # Pool candidate của "đổi angle" là TOÀN BỘ content_angle.ANGLES (11
        # angle), còn 1 run chỉ dùng 1-3 -- luôn còn angle chưa dùng, nên phải
        # đổi được thật, không được rơi vào "Không còn angle nào khác để đổi".
        check("redirect không kèm err= (route xử lý thành công)",
              "err=" not in (resp.headers.get("Location") or ""),
              resp.headers.get("Location"))
        check("angle đã đổi sang giá trị khác", after["angle"] != variant["angle"],
              (after["angle"], variant["angle"]))
        check("angle mới nằm trong content_angle.ANGLES", after["angle"] in content_angle.ANGLES,
              after["angle"])
        check("angle mới chưa từng dùng trong run này", after["angle"] not in used_before,
              (after["angle"], used_before))
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_review_action_invalid_action_still_404():
    print("\nPOST /duyet/<id>/<action_la> action lạ vẫn 404 như trước E6 (không mở khoá action tuỳ ý)")
    post_id, variant, c, csrf = _mk_ready_variant_row_and_client()
    if post_id:
        resp = c.post(f"/duyet/{post_id}/hanh-dong-khong-ton-tai", data={"_csrf": csrf})
        check("404 với action không được định nghĩa", resp.status_code == 404, resp.status_code)
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_review_action_doi_hook_missing_variant_id_errors_gracefully():
    print("\nPOST /duyet/<id>/doi-hook thiếu variant_id -> lỗi rõ, không crash 500")
    post_id, variant, c, csrf = _mk_ready_variant_row_and_client()
    if post_id:
        resp = c.post(f"/duyet/{post_id}/doi-hook", data={"_csrf": csrf})
        check("không crash (không phải 500)", resp.status_code != 500, resp.status_code)
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_review_action_doi_hook_rejects_variant_from_other_post():
    print("\nPOST /duyet/<post_A>/doi-hook với variant_id thuộc post_B -> bị chặn, không đổi hook của post_B")
    from acp.core import system_settings
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})
    with c.session_transaction() as sess:
        csrf = sess["csrf"]

    conn = connect()
    system_settings.set_setting(conn, "content_engine_v2_enabled", "1")
    src = MockAccessTrade()
    candidates = [p for p in src.fetch_products(limit=80) if p.product_url]
    ctx = {"source": src, "publishers": {}, "storage": _FakeStorage()}
    res_a = pipeline.create_post_for_product(conn, ctx, candidates[0].external_product_id, "gd2026")
    res_b = pipeline.create_post_for_product(conn, ctx, candidates[1].external_product_id, "gd2026")
    system_settings.set_setting(conn, "content_engine_v2_enabled", "0")

    check("tạo được cả 2 bài", res_a.get("ok") and res_b.get("ok"),
          (res_a.get("error"), res_b.get("error")))
    if res_a.get("ok") and res_b.get("ok"):
        post_a_id, post_b_id = res_a["post_id"], res_b["post_id"]
        run_a = conn.execute("SELECT * FROM content_generation_run WHERE post_id=?", (post_a_id,)).fetchone()
        run_b = conn.execute("SELECT * FROM content_generation_run WHERE post_id=?", (post_b_id,)).fetchone()
        check("cả 2 bài đều có run READY",
              bool(run_a and run_a["status"] == "READY" and run_b and run_b["status"] == "READY"),
              (run_a["status"] if run_a else None, run_b["status"] if run_b else None))
        if run_a and run_a["status"] == "READY" and run_b and run_b["status"] == "READY":
            variant_b_before = conn.execute(
                "SELECT * FROM content_variant_row WHERE run_id=? ORDER BY label LIMIT 1", (run_b["id"],)).fetchone()
            conn.close()

            resp = c.post(f"/duyet/{post_a_id}/doi-hook",
                          data={"variant_id": variant_b_before["id"], "_csrf": csrf})
            check("không crash (không phải 500)", resp.status_code != 500, resp.status_code)

            conn = connect()
            variant_b_after = conn.execute(
                "SELECT * FROM content_variant_row WHERE id=?", (variant_b_before["id"],)).fetchone()
            conn.close()
            check("hook của variant post_B không đổi (bị chặn trộn nội dung giữa 2 bài)",
                  variant_b_after["hook"] == variant_b_before["hook"],
                  (variant_b_after["hook"], variant_b_before["hook"]))
        else:
            conn.close()
    else:
        conn.close()
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_review_action_regenerate_exception_redirects_gracefully_not_500():
    print("\nPOST /duyet/<id>/doi-hook khi content_engine raise -> redirect có lỗi, không phải 500, có audit")
    from acp.core import content_engine
    post_id, variant, c, csrf = _mk_ready_variant_row_and_client()
    if post_id:
        original = content_engine.regenerate_hook

        def crashing_regenerate(conn, post_id, variant_id):
            raise RuntimeError("giả lập lỗi LLM")

        content_engine.regenerate_hook = crashing_regenerate
        try:
            resp = c.post(f"/duyet/{post_id}/doi-hook", data={"variant_id": variant["id"], "_csrf": csrf})
            check("không phải 500", resp.status_code != 500, resp.status_code)
            check("redirect thường (302)", resp.status_code == 302, resp.status_code)
            conn = connect()
            audit_row = conn.execute(
                "SELECT * FROM audit_log WHERE entity='content_variant_row' AND entity_id=? "
                "AND action='doi-hook_failed' ORDER BY created_at DESC LIMIT 1", (variant["id"],)).fetchone()
            conn.close()
            check("có audit doi-hook_failed", audit_row is not None, audit_row)
        finally:
            content_engine.regenerate_hook = original
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_duyet_does_not_render_fact_unsafe_variant_as_selectable_card():
    print("\nGET /duyet KHÔNG render variant bị loại vì fact-unsafe (3 cột điểm NULL) thành card chọn được")
    from acp.core import system_settings, content_variant as _cv
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    src = MockAccessTrade()
    # Sản phẩm giảm >=5% + category gia-dung -> select_angle_candidates() trả
    # đủ 3 angle (E2), đủ chỗ cho 1 variant bị loại mà run vẫn READY nhờ các
    # variant còn lại -- đúng trường hợp mà Option A phải xử lý.
    target = next(p for p in src.fetch_products(limit=80)
                  if p.product_url and p.category_code == "gia-dung"
                  and p.original_price and p.original_price > p.current_price
                  and (p.original_price - p.current_price) / p.original_price >= 0.05)
    calls = {"n": 0}

    def first_call_unsafe_generator(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            # Bịa trải nghiệm cá nhân -> check_fact_safety() chặn ->
            # select_best_variant() (E4) loại variant khỏi candidates ->
            # persist_run() ghi NULL cả rule_score/hybrid_score/final_score.
            return json.dumps({"main_message": "Mình đã dùng 2 tuần rồi, thấy rất ổn.",
                               "body": []}, ensure_ascii=False)
        return json.dumps({"main_message": "Thông số ghi trên trang bán.",
                           "body": []}, ensure_ascii=False)

    _cv.set_body_generator(first_call_unsafe_generator)
    system_settings.set_setting(conn, "content_engine_v2_enabled", "1")
    try:
        ctx = {"source": src, "publishers": {}, "storage": _FakeStorage()}
        res = pipeline.create_post_for_product(conn, ctx, target.external_product_id, "gd2026")
    finally:
        _cv.set_body_generator(None)
        system_settings.set_setting(conn, "content_engine_v2_enabled", "0")

    check("tạo bài thành công", res.get("ok"), res.get("error"))
    if res.get("ok"):
        run = conn.execute("SELECT * FROM content_generation_run WHERE post_id=?", (res["post_id"],)).fetchone()
        check("run vẫn READY (vẫn còn variant đạt fact safety)",
              run is not None and run["status"] == "READY", run["status"] if run else None)
        variant_rows = conn.execute(
            "SELECT * FROM content_variant_row WHERE run_id=? ORDER BY label",
            (run["id"],)).fetchall() if run else []

        def _is_rejected(r):
            return r["rule_score"] is None and r["hybrid_score"] is None and r["final_score"] is None

        rejected = [r for r in variant_rows if _is_rejected(r)]
        kept = [r for r in variant_rows if not _is_rejected(r)]
        check("có đúng 1 variant bị loại vì fact-unsafe (3 cột điểm NULL)", len(rejected) == 1,
              [(r["label"], r["final_score"]) for r in variant_rows])
        check("vẫn còn ít nhất 1 variant hợp lệ", len(kept) >= 1, len(kept))
        if rejected and kept:
            body = c.get("/duyet").get_data(as_text=True)
            check("variant fact-unsafe KHÔNG xuất hiện trên trang (không chọn/duyệt nhầm được)",
                  rejected[0]["id"] not in body, rejected[0]["id"])
            check("variant hợp lệ vẫn hiện bình thường", kept[0]["id"] in body, kept[0]["id"])
    conn.close()
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_duyet_hides_variant_after_regenerate_produces_unsafe_content():
    print("\nGET /duyet ẩn variant sau khi REGENERATE sinh ra nội dung fact-unsafe (không chỉ lúc tạo bài)")
    from acp.core import content_variant as _cv
    post_id, variant, c, csrf = _mk_ready_variant_row_and_client()
    if post_id:
        conn = connect()
        before = c.get("/duyet").get_data(as_text=True)
        check("trước khi làm lại, variant có hiện trên /duyet", variant["id"] in before, variant["id"])

        def unsafe_gen(prompt):
            # Bịa trải nghiệm cá nhân -> check_fact_safety() chặn ->
            # _rescore_variant() (G3) set cả 3 cột điểm NULL + is_best=0.
            return json.dumps({"main_message": "Mình đã dùng 2 tuần rồi, thấy rất ổn.",
                               "body": []}, ensure_ascii=False)

        _cv.set_body_generator(unsafe_gen)
        try:
            resp = c.post(f"/duyet/{post_id}/lam-lai",
                          data={"variant_id": variant["id"], "_csrf": csrf})
            check("không phải 500", resp.status_code != 500, resp.status_code)
            check("redirect thường (302)", resp.status_code == 302, resp.status_code)
        finally:
            _cv.set_body_generator(None)
        after = conn.execute("SELECT * FROM content_variant_row WHERE id=?", (variant["id"],)).fetchone()
        check("3 cột điểm về NULL sau khi chấm lại",
              after["rule_score"] is None and after["hybrid_score"] is None and after["final_score"] is None,
              (after["rule_score"], after["hybrid_score"], after["final_score"]))
        body = c.get("/duyet").get_data(as_text=True)
        check("variant fact-unsafe sau regenerate KHÔNG còn hiện trên /duyet",
              variant["id"] not in body, variant["id"])
        conn.close()
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


def test_duyet_still_renders_when_content_variant_lookup_fails():
    print("\nGET /duyet vẫn 200 khi phần đọc variant của Content Engine v2 lỗi (bảng chưa migrate) -- không 500")
    from acp.core import system_settings, content_platform as _cp
    os.environ["ACP_ADMIN_PASSWORD"] = "matkhau-test"
    os.environ["ACP_SECRET_KEY"] = "khoa-phien-test"
    from acp.web.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    c.post("/dangnhap", data={"password": "matkhau-test"})

    conn = connect()
    system_settings.set_setting(conn, "content_engine_v2_enabled", "1")
    src = MockAccessTrade()
    target = next(p for p in src.fetch_products(limit=80) if p.product_url)
    ctx = {"source": src, "publishers": {}, "storage": _FakeStorage()}
    res = pipeline.create_post_for_product(conn, ctx, target.external_product_id, "gd2026")
    system_settings.set_setting(conn, "content_engine_v2_enabled", "0")
    check("tạo bài thành công", res.get("ok"), res.get("error"))
    run = conn.execute("SELECT * FROM content_generation_run WHERE post_id=?",
                       (res.get("post_id"),)).fetchone() if res.get("ok") else None
    check("có content_generation_run READY", run is not None and run["status"] == "READY",
          run["status"] if run else None)
    variant_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM content_variant_row WHERE run_id=?", (run["id"],)).fetchall()] if run else []
    conn.close()

    if run and run["status"] == "READY":
        original = _cp.adapt_for_platforms

        def crashing_adapt(*a, **kw):
            # Đại diện cho MỌI lỗi trong khối đọc/dựng variant của E6 (bảng
            # chưa tồn tại, dữ liệu hỏng...) -- trang /duyet không được 500.
            raise RuntimeError("giả lập lỗi khối Content Engine v2 ở /duyet")

        _cp.adapt_for_platforms = crashing_adapt
        try:
            resp = c.get("/duyet")
        finally:
            _cp.adapt_for_platforms = original
        body = resp.get_data(as_text=True)
        check("trang /duyet vẫn trả 200 (không 500)", resp.status_code == 200, resp.status_code)
        check("vẫn render danh sách bài chờ duyệt", "Chờ duyệt" in body)
        check("khối variant bị bỏ trống, không render card nào",
              all(vid not in body for vid in variant_ids), variant_ids)
    for var in ("ACP_ADMIN_PASSWORD", "ACP_SECRET_KEY"):
        os.environ.pop(var, None)


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
    test_mock_meta_connection_service()
    test_live_meta_connection_service_url_building()
    test_factory_meta_connection_service()
    test_factory_meta_connection_service_live_routing()
    test_factory_registers_facebook_instagram_publishers()
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
    test_content_engine_llm_wired_at_create_app()
    test_content_engine_llm_not_wired_when_flag_off()
    test_web_security()
    test_value_posts()  # phải chạy SAU test_web_security() -- xem docstring
    test_publish_target_retry_route()
    test_oauth_meta_routes()
    test_channel_enable_disable_route()
    test_product_checklist_shows_all_platforms()
    test_production_guard()
    test_meta_account_import_and_sync()
    test_meta_sync_marks_vanished_account_reconnect_required()
    test_oauth_meta_callback_auth_error_no_500()
    test_kenh_meta_sync_auth_error_marks_needs_reauth()
    test_oauth_meta_callback_nonascii_state_clean_400()
    test_kenh_meta_sync_syncs_all_connections()
    test_create_affiliate_product_accepts_facebook_channel()
    test_duyet_approve_route_end_to_end()
    test_duyet_approve_saves_caption_platform_and_override()
    test_duyet_keeps_channel_override_after_bounce()
    test_thuvien_anh_upload_list_delete_end_to_end()
    test_sanpham_affiliate_create_with_media_asset_ids_end_to_end()
    test_sanpham_search_mode_shows_media_checklist_and_per_row_prompt()
    test_kenh_account_group_crud_end_to_end()
    test_sanpham_shows_account_group_quick_select_both_modes()
    test_vanhanh_shows_multi_channel_post_breakdown()
    test_duyet_shows_variants_block_when_generation_run_exists()
    test_duyet_no_variants_block_when_no_generation_run()
    test_duyet_variant_card_embeds_use_variant_button()
    test_duyet_page_defines_acp_use_variant_function()
    test_review_action_doi_hook_changes_only_hook()
    test_review_action_lam_lai_regenerates_same_angle()
    test_review_action_doi_angle_changes_angle()
    test_review_action_invalid_action_still_404()
    test_review_action_doi_hook_missing_variant_id_errors_gracefully()
    test_review_action_doi_hook_rejects_variant_from_other_post()
    test_review_action_regenerate_exception_redirects_gracefully_not_500()
    test_duyet_does_not_render_fact_unsafe_variant_as_selectable_card()
    test_duyet_hides_variant_after_regenerate_produces_unsafe_content()
    test_duyet_still_renders_when_content_variant_lookup_fails()
    print(f"\n{len(PASS)} đạt, {len(FAIL)} hỏng")
    if FAIL:
        print("Hỏng: " + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
