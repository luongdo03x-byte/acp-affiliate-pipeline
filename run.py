#!/usr/bin/env python3
"""CLI của Affiliate Content Pipeline.

    python3 run.py init                 tạo CSDL và dữ liệu nền
    python3 run.py demo                 chạy trọn 7 chặng với adapter giả lập
    python3 run.py ingest               chặng 1
    python3 run.py plan                 chặng 2 + tạo job
    python3 run.py work                 chạy hàng đợi tới khi hết việc
    python3 run.py worker-once          chạy một lượt worker theo công tắc tự đăng
    python3 run.py worker-status        xem công tắc tự đăng và số lượng job an toàn
    python3 run.py auto-schedule        lấp lịch Threads Auto 48 giờ; không bật worker/global publish
    python3 run.py niche                xem chủ đề của từng kênh
    python3 run.py niche <kênh> <chủ đề...>   đặt chủ đề cho một kênh
    python3 run.py search [từ khoá]     tìm sản phẩm trong nguồn
    python3 run.py product-sync [từ khoá] [--auto-prepare]
                                         đồng bộ catalog ACCESSTRADE; chỉ tạo bài chờ duyệt
                                         khi có --auto-prepare và ACP_AUTO_PREPARE_CONTENT=true
                                         thứ tự timer an toàn: sync catalog -> auto-schedule -> worker-once
    python3 run.py product <mã sp>      MỘT sản phẩm -> MỘT bài chờ duyệt
    python3 run.py valuepost <kênh> [loại]   bài không bán hàng cho một kênh
                                         loại: price_level | real_discount | checklist
    python3 run.py mix [kênh]           phương pháp 3 bài -- trộn bán hàng + giá trị
    python3 run.py review               liệt kê bài đang chờ duyệt
    python3 run.py reconcile            chặng 6 -- kéo dữ liệu chuyển đổi về
    python3 run.py trace                soi vì sao chuyển đổi không quy kết được
    python3 run.py doctor               kiểm tra cấu hình trước khi chạy thật
    python3 run.py approve <post_id>    duyệt một bài
    python3 run.py report               báo cáo doanh thu ra terminal
    python3 run.py serve                mở dashboard tại http://127.0.0.1:5000
    python3 run.py genkey               sinh ACP_MASTER_KEY mới
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from acp.adapters.mock import MockAccessTrade, MockThreads, simulate_postbacks
from acp.adapters import factory
from acp.adapters.accesstrade_client import AccessTradeClient, LinkResult
from acp.core import attribution, crypto, jobs, pipeline, scoring
from acp.core import db
from acp.core.db import audit, connect, init_db, now, ulid
from acp.core.products import ProductService, SyncAlreadyRunning, env_bool, env_int

CAMPAIGN_CODE = "gd2026"


def _adapters(source_name=None):
    from acp.adapters import factory
    return factory.get_source(source_name), factory.get_channel()


def _ctx(source_name=None):
    from acp.adapters import factory
    return factory.build_context(source_name)


TEMPLATES = [
    ("price_drop", "Báo giảm giá"),
    ("spec_highlight", "Nêu thông số"),
    ("deal_roundup", "Tổng hợp deal"),
    ("comparison", "So sánh tầm giá"),
]

# Mỗi kênh một ngách riêng. Đổi bằng: run.py niche <mã kênh> <chủ đề...>
CHANNELS = [
    ("threads_nu", "@chonloc.chonu", 12, 90, ["thoi-trang-nu", "my-pham"]),
    ("threads_be", "@dochoi.chobe", 10, 120, ["me-va-be"]),
    ("threads_pet", "@sen.chuanbi", 10, 120, ["thu-cung"]),
]


def cmd_init():
    init_db()
    conn = connect()
    if not conn.execute("SELECT 1 FROM campaign WHERE code=?", (CAMPAIGN_CODE,)).fetchone():
        conn.execute("INSERT INTO campaign (id, code, name, niche, is_active, created_at) VALUES (?,?,?,?,1,?)",
                     (ulid(), CAMPAIGN_CODE, "Gia dụng và phụ kiện 2026", "gia-dung", now()))
    for code, name in TEMPLATES:
        if not conn.execute("SELECT 1 FROM caption_template WHERE code=?", (code,)).fetchone():
            conn.execute("INSERT INTO caption_template (id, code, name, body, is_active) VALUES (?,?,?,?,1)",
                         (ulid(), code, name, code))
    import json as _json
    for code, handle, cap, gap, nl in CHANNELS:
        if not conn.execute("SELECT 1 FROM channel WHERE code=?", (code,)).fetchone():
            conn.execute("""INSERT INTO channel (id, code, platform, handle, external_user_id, status,
                            token_encrypted, daily_post_cap, min_gap_minutes, niches, created_at)
                            VALUES (?,?,'threads',?,?,'ACTIVE',?,?,?,?,?)""",
                         (ulid(), code, handle, f"mock_uid_{code}",
                          crypto.encrypt(f"mock_token_{code}"), cap, gap,
                          _json.dumps(nl, ensure_ascii=False), now()))
    if not conn.execute("SELECT 1 FROM scoring_config WHERE is_active=1").fetchone():
        scoring.save_config(conn, scoring.DEFAULT_WEIGHTS, scoring.DEFAULT_FILTERS, "cấu hình khởi tạo")
    conn.close()
    print(f"✓ Đã tạo CSDL, 1 chiến dịch, 4 template, {len(CHANNELS)} kênh (mỗi kênh một ngách)")


def cmd_ingest():
    conn = connect()
    src, _ = _adapters()
    s = pipeline.ingest_datafeed(conn, src, limit=500)
    total = conn.execute("SELECT COUNT(*) FROM product").fetchone()[0]
    conn.close()
    print(f"✓ Chặng 1 — thêm mới {s['inserted']}, cập nhật {s['updated']}, đổi giá {s['price_changed']}. "
          f"Tổng kho: {total} sản phẩm")


def cmd_plan(limit=10):
    conn = connect()
    ids = pipeline.plan_content(conn, CAMPAIGN_CODE, limit=limit, rng=random.Random(11))
    all_scored = scoring.score_candidates(conn, limit=999, explain=True)
    passed = [s for s in all_scored if not s["rejected"]]
    conn.close()
    print(f"✓ Chặng 2 — {len(passed)}/{len(all_scored)} sản phẩm qua lọc cứng, tạo {len(ids)} job sinh nội dung")


def cmd_work():
    conn = connect()
    stats = jobs.drain(conn, ctx=_ctx())
    q = jobs.queue_summary(conn)
    conn.close()
    print(f"✓ Hàng đợi — xong {stats['done']}, thử lại {stats['retried']}, "
          f"hoãn {stats['deferred']}, thất bại {stats['failed']}")
    print(f"  Trạng thái hiện tại: {q}")


def _format_queue_counts(summary):
    """Render only aggregate queue state; never expose job payload or error data."""
    if not summary:
        return "empty"
    return ", ".join(f"{status}={summary[status]}" for status in sorted(summary))


def cmd_worker_once():
    """Run one timer-safe queue pass without revealing provider/configuration details."""
    from acp.core.system_settings import publish_worker_enabled

    try:
        with db.session() as conn:
            ctx = factory.build_context()
            stats = jobs.run_once(conn, ctx=ctx)
            enabled = publish_worker_enabled(conn)
            queue = jobs.queue_summary(conn)
    except Exception:
        print("Worker execution failed. Check local service logs.")
        return 1

    state = "enabled" if enabled else "disabled"
    print(f"Publish worker: {state}")
    print("Worker pass: " + ", ".join(
        f"{key}={stats[key]}" for key in ("done", "retried", "deferred", "failed", "skipped")))
    print(f"Queue: {_format_queue_counts(queue)}")
    return 0


def cmd_worker_status():
    """Print durable worker state and aggregate queue counts for operators."""
    from acp.core.system_settings import publish_worker_enabled

    try:
        with db.session() as conn:
            enabled = publish_worker_enabled(conn)
            queue = jobs.queue_summary(conn)
    except Exception:
        print("Worker status unavailable. Check local service logs.")
        return 1

    print(f"Publish worker: {'enabled' if enabled else 'disabled'}")
    print(f"Queue: {_format_queue_counts(queue)}")
    return 0


def cmd_auto_schedule():
    """Fill the rolling Auto schedule once without running publish jobs."""
    try:
        init_db()
        with db.session() as conn:
            ctx = factory.build_context()
            ctx["product_client"] = _product_sync_client()
            stats = pipeline.fill_auto_schedule(conn, CAMPAIGN_CODE, ctx=ctx)
    except Exception:
        print("Auto schedule failed. Check local service logs.")
        return 1

    print("Auto schedule: " + ", ".join(
        f"{key}={stats.get(key, 0)}" for key in ("scheduled", "review", "skipped", "cancelled")))
    return 0


def cmd_review():
    conn = connect()
    # LEFT JOIN -- bài không bán hàng (post_type='VALUE') không có product_id.
    rows = conn.execute("""SELECT p.id, p.status, p.variant_code, p.post_type, pr.name, pr.category_code, p.score
                           FROM post p LEFT JOIN product pr ON pr.id=p.product_id
                           WHERE p.status IN ('PENDING_REVIEW','DRAFT')
                           ORDER BY p.score IS NULL, p.score DESC""").fetchall()
    conn.close()
    if not rows:
        print("Không có bài nào chờ duyệt.")
        return
    print(f"{len(rows)} bài chờ duyệt:\n")
    for r in rows:
        label = r["name"][:52] if r["name"] else f"[bài giá trị: {r['variant_code']}]"
        score = f"{r['score']:.3f}" if r["score"] is not None else "  -  "
        print(f"  [{r['status']:<14}] {r['id']}  {score}  {label}")


def cmd_approve_all():
    conn = connect()
    rows = conn.execute("SELECT id FROM post WHERE status='PENDING_REVIEW'").fetchall()
    ok = 0
    for r in rows:
        if pipeline.approve_post(conn, r["id"], actor="demo")["ok"]:
            ok += 1
    conn.close()
    print(f"✓ Chặng 4 — duyệt {ok}/{len(rows)} bài, đã lên lịch đăng")


def cmd_simulate_conversions():
    """Chỉ dùng cho demo: giả lập click và nạp giao dịch vào kho của adapter.

    Luồng này khớp với kiến trúc chạy ở máy cá nhân — KHÔNG có postback gọi vào.
    Dữ liệu chuyển đổi chỉ vào hệ thống qua lệnh `reconcile`.
    """
    conn = connect()
    rng = random.Random(3)
    posts = [dict(r) for r in conn.execute("""
        SELECT p.id, p.variant_code, pr.external_product_id, pr.current_price, pr.commission_rate,
               pr.category_code, c.code AS campaign_code, ch.code AS channel_code
        FROM post p JOIN product pr ON pr.id=p.product_id
        JOIN campaign c ON c.id=p.campaign_id JOIN channel ch ON ch.id=p.channel_id
        WHERE p.status='PUBLISHED'""").fetchall()]
    if not posts:
        conn.close()
        print("Chưa có bài nào đăng thành công.")
        return

    events = simulate_postbacks(posts, rng)
    for p in posts:
        attribution.add_clicks(conn, p["id"], p["_clicks"])
    conn.close()

    # Gán trạng thái cuối như sàn đã chốt, rồi nạp vào kho giao dịch của adapter.
    rec_rng = random.Random(101)
    for e in events:
        roll = rec_rng.random()
        e["status"] = "pending" if roll < 0.18 else ("approved" if roll < 0.88 else "rejected")
    # Gửi trùng 20% để chứng minh khử trùng lặp hoạt động ở phía đối soát.
    events += events[: max(1, len(events) // 5)]

    n = MockAccessTrade().seed_transactions(events)
    print(f"✓ Giả lập — {len(posts)} bài có click, {n} bản ghi giao dịch chờ đối soát")


def cmd_reconcile(days: int = 7):
    """Chặng 6 — kéo dữ liệu chuyển đổi về.

    Đây là nguồn chân lý duy nhất khi chạy không có postback. Cửa sổ 7 ngày là
    cố ý: nó vớt lại cả những đơn phát sinh lúc máy đang tắt.
    """
    from datetime import datetime, timedelta, timezone
    conn = connect()
    src, _ = _adapters()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    s = pipeline.reconcile_transactions(conn, src, since)
    unattributed = conn.execute("SELECT COUNT(*) FROM conversion WHERE post_id IS NULL").fetchone()[0]
    conn.close()
    print(f"✓ Đối soát — thêm mới {s['inserted']}, cập nhật {s['updated']}, giữ nguyên {s['unchanged']}")
    if unattributed:
        print(f"  ⚠ {unattributed} chuyển đổi chưa quy kết được về bài nào. "
              f"Kiểm tra tham số sub_id bằng: python3 run.py trace")


def cmd_trace():
    """Soi vì sao một chuyển đổi không quy kết được. Chạy khi cột Đơn vẫn bằng 0."""
    import json as _json
    conn = connect()
    rows = conn.execute("""SELECT transaction_id, post_id, external_product_id, raw_payload
                           FROM conversion ORDER BY rowid DESC LIMIT 5""").fetchall()
    if not rows:
        print("Chưa có chuyển đổi nào.")
        conn.close()
        return
    print(f"\n{len(rows)} chuyển đổi gần nhất:\n")
    for r in rows:
        ok = "✓ quy kết được" if r["post_id"] else "✗ KHÔNG quy kết được"
        print(f"  {ok}  giao dịch {r['transaction_id']}  sản phẩm {r['external_product_id']}")
        try:
            payload = _json.loads(r["raw_payload"] or "{}")
            keys = {k: v for k, v in payload.items() if k in ("sub1", "sub2", "sub3", "sub4", "utm_content", "pbid")}
            print(f"     tham số nhận được: {keys or 'KHÔNG CÓ THAM SỐ NÀO'}")
        except Exception:
            pass
    print("\n  Nếu thấy '✗' mà tham số vẫn có giá trị, nghĩa là tên tham số chưa được")
    print("  đọc. Thêm tên đó vào core/attribution.py, hàm extract_post_id().\n")
    conn.close()


def cmd_doctor():
    """Kiểm tra cấu hình trước khi chạy thật. Chạy sau khi khai xong biến môi trường."""
    from acp.core import storage
    print("\n  KIỂM TRA CẤU HÌNH\n")
    ok = True

    adapter = os.environ.get("ACP_ADAPTER", "mock")
    print(f"  {'●':<3}adapter: {'THẬT (gọi API ngoài)' if adapter == 'live' else 'giả lập (offline)'}")

    if os.environ.get("ACP_ENV") == "production" and not os.environ.get("ACP_MASTER_KEY"):
        print("  ✗  ACP_MASTER_KEY chưa đặt trong môi trường production"); ok = False
    else:
        has_key = bool(os.environ.get("ACP_MASTER_KEY"))
        print(f"  {'✓' if has_key else '⚠'}  khoá mã hoá: {'đã đặt' if has_key else 'đang dùng khoá dev'}")

    try:
        st = storage.get_storage()
        good, msg = st.healthcheck()
        print(f"  {'✓' if good else '✗'}  lưu trữ ảnh ({st.kind}): {msg}")
        ok = ok and good
    except Exception as e:
        print(f"  ✗  lưu trữ ảnh: {e}"); ok = False

    if adapter == "live":
        for var in ("AT_ACCESS_KEY", "AT_CAMPAIGN_ID"):
            has = bool(os.environ.get(var))
            print(f"  {'✓' if has else '✗'}  {var}: {'đã đặt' if has else 'THIẾU'}")
            ok = ok and has

    try:
        conn = connect()
        chans = conn.execute("SELECT code, status, external_user_id FROM channel").fetchall()
        prods = conn.execute("SELECT COUNT(*) FROM product").fetchone()[0]
        conn.close()
        print(f"  {'✓' if chans else '✗'}  kênh: {len(chans)} kênh"
              + (f" ({', '.join(c['code'] + '/' + c['status'] for c in chans)})" if chans else " — chưa có kênh nào"))
        print(f"  {'✓' if prods else '⚠'}  kho sản phẩm: {prods}")
        ok = ok and bool(chans)
    except Exception as e:
        print(f"  ✗  cơ sở dữ liệu: {e} — chạy `python3 run.py init` trước"); ok = False

    print(f"\n  {'Sẵn sàng.' if ok else 'Còn vấn đề cần xử lý ở các dòng ✗.'}\n")
    return ok


def cmd_niche(channel_code=None, *codes):
    """Xem và đặt chủ đề cho TỪNG KÊNH.

        run.py niche                              xem tất cả kênh
        run.py niche threads_main thu-cung        đặt chủ đề cho một kênh
        run.py niche threads_main                 xoá chủ đề của kênh đó
    """
    from acp.core import niche as niche_mod
    conn = connect()

    if channel_code:
        ch = conn.execute("SELECT * FROM channel WHERE code=?", (channel_code,)).fetchone()
        if not ch:
            have = [r["code"] for r in conn.execute("SELECT code FROM channel").fetchall()]
            print(f"  ✗ Không có kênh “{channel_code}”. Kênh hiện có: {', '.join(have) or '(chưa có)'}")
            conn.close(); return
        bad = [c for c in codes if c not in niche_mod.NICHES]
        if bad:
            print(f"  ✗ Mã chủ đề không hợp lệ: {', '.join(bad)}")
            print(f"    Chọn trong: {', '.join(niche_mod.list_codes())}")
            conn.close(); return
        applied = pipeline.set_channel_niches(conn, ch["id"], list(codes))
        label = ", ".join(niche_mod.NICHES[c]["name"] for c in applied) or "(không lọc)"
        print(f"  ✓ {ch['handle']} → {label}")

    print("\n  CHỦ ĐỀ THEO KÊNH\n")
    for ch in conn.execute("SELECT * FROM channel ORDER BY code").fetchall():
        nl = pipeline.channel_niches(conn, ch["id"])
        names = ", ".join(niche_mod.NICHES[c]["name"] for c in nl if c in niche_mod.NICHES)
        pool = len(scoring.score_candidates(conn, limit=9999, niches=nl))
        print(f"    {ch['code']:<16}{ch['handle']:<24}{names or 'không lọc chủ đề'}")
        print(f"    {'':<16}{'':<24}{pool} sản phẩm hợp lệ trong kho\n")

    print("  CHỦ ĐỀ CÓ SẴN\n")
    for code, n in niche_mod.NICHES.items():
        extra = f"  (+{len(n['extra_banned_phrases'])} cụm cấm riêng)" if n["extra_banned_phrases"] else ""
        print(f"    {code:<18}{n['name']}{extra}")
    print("\n  Đổi bất cứ lúc nào: run.py niche <mã kênh> <chủ đề> [chủ đề...]")
    print("  Bài đã đăng không bị ảnh hưởng.\n")
    conn.close()


def cmd_product(external_id=None, source_name=None):
    """Một sản phẩm cụ thể -> một bài PENDING_REVIEW. Không đăng."""
    if not external_id:
        print("Cách dùng: python3 run.py product <external_product_id> [nguồn]")
        print("  nguồn: tiktokshop | shopee | mock  (mặc định lấy từ ACP_SOURCE)")
        return
    conn = connect()
    ctx = _ctx(source_name)
    print(f"  Nguồn: {ctx['source'].name} — đang tìm sản phẩm {external_id}...")
    res = pipeline.create_post_for_product(conn, ctx, external_id, CAMPAIGN_CODE)
    conn.close()
    if not res["ok"]:
        print(f"  ✗ {res['error']}")
        return
    print(f"\n  ✓ Đã tạo bài {res['post_id']} — trạng thái {res['status']}\n")
    print(f"  Sản phẩm : {res['product_name']}")
    print(f"  Link     : {res['affiliate_link']}")
    print(f"  Ảnh      : {res['image_url']}")
    print(f"\n  --- caption ({len(res['caption'])}/500 ký tự) ---")
    for line in res["caption"].split("\n"):
        print(f"  {line}")
    if res["problems"]:
        print(f"\n  ⚠ Không đạt kiểm tra tự động: {'; '.join(res['problems'])}")
        print("    Bài ở trạng thái DRAFT, sửa caption trên /duyet trước khi duyệt.")
    print(f"\n  Xem và duyệt tại: http://127.0.0.1:5000/duyet\n")


def cmd_search(query=None, source_name=None):
    """Tìm sản phẩm trong nguồn để lấy external_product_id."""
    src = _adapters(source_name)[0]
    if not hasattr(src, "search_products"):
        print(f"  Nguồn {src.name} không hỗ trợ tìm kiếm.")
        return
    if not query:
        from acp.core import niche as niche_mod
        conn = connect()
        ch = conn.execute("SELECT id FROM channel WHERE status='ACTIVE' ORDER BY code LIMIT 1").fetchone()
        nl = pipeline.channel_niches(conn, ch["id"]) if ch else []
        conn.close()
        qs = niche_mod.search_queries(nl)
        if qs:
            query = qs[0]
            print(f"  Không có từ khoá — dùng gợi ý của chủ đề: “{query}”")
            print(f"  Gợi ý khác: {', '.join(qs[1:6])}\n")
    items, cursor = src.search_products(query=query, limit=15)
    if not items:
        print("  Không tìm thấy sản phẩm nào.")
        return
    print(f"\n  {len(items)} sản phẩm từ {src.name}:\n")
    print(f"  {'mã sản phẩm':<22}{'giá':>12}{'hoa hồng':>11}{'đã bán':>9}  tên")
    for p in items:
        print(f"  {p.external_product_id:<22}{p.current_price:>11,}đ{p.commission_value:>10,}đ"
              f"{p.sold_count:>9,}  {p.name[:46]}")
    print(f"\n  Tạo bài: python3 run.py product <mã sản phẩm>\n")


def _product_sync_summary(result):
    """Keep cron output useful without exposing provider credentials or payloads."""
    return (f"Fetched: {getattr(result, 'fetched', 0)} | "
            f"New: {getattr(result, 'inserted', 0)} | "
            f"Updated: {getattr(result, 'updated', 0)} | "
            f"Skipped: {getattr(result, 'skipped', 0)} | "
            f"Failed: {getattr(result, 'failed', 0)}")


class _MockCatalogClient:
    """Expose the existing seed data through the Product Search V2 boundary."""

    def search_products(self, *, limit=50, title_keywords=None, **_):
        products, _ = MockAccessTrade().search_products(query=title_keywords, limit=limit)
        return ([{
            "id": product.external_product_id,
            "title": product.name,
            "shop": {"name": product.merchant},
            "detail_link": product.product_url,
            "main_image_url": product.image_url_original,
            "sales_price": {"minimum_amount": product.current_price, "currency": "VND"},
            "original_price": {"minimum_amount": product.original_price, "currency": "VND"},
            "commission": {"amount": product.commission_value,
                           "rate": int((product.commission_rate or 0) * 10000),
                           "currency": "VND"},
            "units_sold": product.sold_count,
            "has_inventory": True,
            "category": {"code": product.category_code},
        } for product in products], None)

    def create_product_link(self, detail_link, *, post_id, external_product_id):
        return LinkResult(
            full_url=f"https://mock.acp/product/{external_product_id}?post_id={post_id}")


def _product_sync_client():
    if (os.environ.get("ACP_ADAPTER", "").lower() == "mock"
            and os.environ.get("ACP_SOURCE", "").lower() == "mock"):
        return _MockCatalogClient()
    return AccessTradeClient.from_env()


def cmd_product_sync(keyword=None, auto_prepare=False):
    """Synchronize the local catalog; optional preparation deliberately stops at review."""
    if not env_bool("ACP_PRODUCT_SYNC_ENABLED", True):
        print("Product sync disabled by ACP_PRODUCT_SYNC_ENABLED=false.")
        return 0

    try:
        db.init_db()
        with db.session() as conn:
            product_client = _product_sync_client()
            service = ProductService(conn, product_client)
            result = service.sync(title_keywords=keyword)
            print(_product_sync_summary(result))
            if auto_prepare and env_bool("ACP_AUTO_PREPARE_CONTENT", False):
                prepared = 0
                failed = 0
                context = factory.build_context()
                context["product_client"] = product_client
                for product in service.recommended(env_int("ACP_AUTO_PREPARE_CONTENT_COUNT", 3)):
                    post = pipeline.create_post_for_catalog_product(
                        conn, context, product["id"], CAMPAIGN_CODE)
                    if post.get("ok"):
                        prepared += 1
                    else:
                        failed += 1
                print(f"Prepared for review: {prepared}")
                if failed:
                    print(f"Preparation failed: {failed}")
                    return 1
        return 0
    except SyncAlreadyRunning as error:
        print(f"Product sync failed: {error}")
        return 1
    except Exception:
        # Provider and transport exceptions can include sensitive request details.
        print("Product sync failed; check configuration and connectivity.")
        return 1


def cmd_valuepost(channel_code=None, kind=None):
    """Bài không bán hàng cho một kênh -- xem core/valuepost.py."""
    if not channel_code:
        print("Cách dùng: python3 run.py valuepost <mã kênh> [loại]")
        print("  loại: price_level | real_discount | checklist  (bỏ trống thì bốc ngẫu nhiên)")
        return
    conn = connect()
    res = pipeline.create_value_post(conn, CAMPAIGN_CODE, channel_code, kind=kind)
    conn.close()
    if not res["ok"]:
        print(f"  ✗ {res['error']}")
        return
    print(f"\n  ✓ Đã tạo bài {res['post_id']} — loại {res['kind']} — trạng thái {res['status']}\n")
    for line in res["caption"].split("\n"):
        print(f"  {line}")
    if res["problems"]:
        print(f"\n  ⚠ Không đạt kiểm tra tự động: {'; '.join(res['problems'])}")
    print(f"\n  Xem và duyệt tại: http://127.0.0.1:5000/duyet\n")


def cmd_mix(channel_code=None):
    """Phương pháp 3 bài: mỗi kênh -- 2 bài bán hàng chờ sinh nội dung + 1 bài giá trị."""
    conn = connect()
    ctx = _ctx()
    res = pipeline.post_mix(conn, ctx, CAMPAIGN_CODE, channel_code)
    conn.close()
    print(f"\n  Đã tạo {len(res['sales_jobs'])} job sinh bài bán hàng -- chạy `run.py work` để xử lý.\n")
    for vp in res["value_posts"]:
        if vp["ok"]:
            print(f"  ✓ {vp['channel']}: bài giá trị ({vp['kind']}) — {vp['status']}")
        else:
            print(f"  ✗ {vp['channel']}: {vp['error']}")
    print()


def cmd_report():
    conn = connect()
    f = attribution.funnel(conn)
    print("\n  PHỄU DOANH THU")
    print(f"  {'Bài đã đăng':<22}{f['posts']:>12,}")
    print(f"  {'Lượt xem':<22}{f['views']:>12,}")
    print(f"  {'Click':<22}{f['clicks']:>12,}   (CTR {f['ctr']}%)")
    print(f"  {'Đơn được duyệt':<22}{f['orders']:>12,}   (CR {f['cr']}%)")
    print(f"  {'Hoa hồng approved':<22}{f['commission']:>12,}đ")
    print(f"  {'Đang chờ duyệt':<22}{f['pending']:>12,}đ")
    print(f"  {'EPC':<22}{f['epc']:>12,}đ")
    print(f"  {'Bài có ít nhất 1 click':<22}{f['post_click_rate']:>11}%")

    for dim, title in (("category", "THEO DANH MỤC"), ("template", "THEO TEMPLATE CAPTION"), ("channel", "THEO KÊNH")):
        rows = attribution.epc_by(conn, dim)
        if not rows:
            continue
        print(f"\n  {title}")
        print(f"  {'':<24}{'bài':>5}{'click':>8}{'đơn':>6}{'hoa hồng':>13}{'EPC':>9}")
        for r in rows:
            print(f"  {str(r['label'])[:23]:<24}{r['posts']:>5}{r['clicks']:>8}{r['orders']:>6}"
                  f"{r['commission']:>12,}đ{r['epc']:>8,}đ")
    print()
    conn.close()


def _backdate(conn, days: int, rng):
    """Chỉ dùng cho demo: dời lịch đăng về quá khứ để hàng đợi chạy được ngay,
    tạo ra lịch sử vận hành đủ dài cho dashboard có ý nghĩa.

    Trong vận hành thật không có hàm này -- thời gian tự trôi.
    """
    from datetime import datetime, timedelta, timezone
    posts = conn.execute("SELECT id FROM post WHERE status='SCHEDULED' ORDER BY scheduled_at").fetchall()
    base = datetime.now(timezone.utc) - timedelta(days=days)
    for i, p in enumerate(posts):
        when = (base + timedelta(hours=i * (days * 24 / max(1, len(posts))),
                                 minutes=rng.randrange(0, 40))).isoformat(timespec="seconds")
        conn.execute("UPDATE post SET scheduled_at=? WHERE id=?", (when, p["id"]))
        conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?", (when, f"pub:{p['id']}"))


def _flush_insights(conn):
    """Kéo lịch lấy insights (mặc định +24h) về hiện tại để demo có số lượt xem."""
    conn.execute("UPDATE job_queue SET run_after=? WHERE job_type='FETCH_INSIGHTS' AND status='READY'", (now(),))


def _align_published(conn):
    """Chỉ dùng cho demo. Trong vận hành thật published_at chính là lúc gọi API
    thành công; ở đây phải kéo về đúng mốc đã lên lịch, nếu không cả 14 ngày
    lịch sử dồn vào hôm nay và làm đầy hạn mức danh mục theo ngày."""
    conn.execute("UPDATE post SET published_at = scheduled_at "
                 "WHERE status='PUBLISHED' AND scheduled_at IS NOT NULL AND published_at > scheduled_at")


def cmd_demo():
    rng = random.Random(42)
    print("\n=== Chạy trọn pipeline với adapter giả lập ===\n")
    cmd_init(); cmd_ingest()

    # Dựng 14 ngày vận hành. Vòng cuối cố tình dừng ở bước chờ duyệt để màn hình
    # duyệt có việc -- đúng trạng thái người vận hành gặp mỗi sáng.
    ROUNDS = 5
    for round_no in range(ROUNDS):
        conn = connect()
        pipeline.plan_content(conn, CAMPAIGN_CODE, limit=10, rng=rng)
        conn.close()
        conn = connect(); jobs.drain(conn, ctx=_ctx()); conn.close()

        if round_no == ROUNDS - 1:
            break

        conn = connect()
        for r in conn.execute("SELECT id FROM post WHERE status='PENDING_REVIEW'").fetchall():
            pipeline.approve_post(conn, r["id"], actor="demo")
        _backdate(conn, days=14 - round_no * 2, rng=rng)
        conn.close()
        conn = connect(); jobs.drain(conn, ctx=_ctx()); _align_published(conn); _flush_insights(conn); conn.close()
        conn = connect(); jobs.drain(conn, ctx=_ctx()); conn.close()

    conn = connect()
    published = conn.execute("SELECT COUNT(*) FROM post WHERE status='PUBLISHED'").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM post WHERE status IN ('PENDING_REVIEW','DRAFT')").fetchone()[0]
    conn.close()
    print(f"✓ Chặng 3-5 — đăng {published} bài trong 14 ngày mô phỏng, còn {pending} bài chờ duyệt")

    cmd_simulate_conversions()
    cmd_reconcile()
    conn = connect(); jobs.drain(conn, ctx=_ctx()); conn.close()
    cmd_report()
    print("  Mở dashboard:  python3 run.py serve\n")


def cmd_serve():
    from acp.web.server import create_app
    create_app().run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)


COMMANDS = {
    "init": cmd_init, "ingest": cmd_ingest, "plan": cmd_plan, "work": cmd_work,
    "worker-once": cmd_worker_once, "worker-status": cmd_worker_status,
    "auto-schedule": cmd_auto_schedule,
    "review": cmd_review, "approve-all": cmd_approve_all, "report": cmd_report,
    "reconcile": cmd_reconcile, "trace": cmd_trace, "doctor": cmd_doctor,
    "product": cmd_product, "search": cmd_search, "niche": cmd_niche,
    "valuepost": cmd_valuepost, "mix": cmd_mix,
    "product-sync": cmd_product_sync,
    "demo": cmd_demo, "serve": cmd_serve, "simulate": cmd_simulate_conversions,
    "genkey": lambda: print(crypto.generate_key()),
}

def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "demo"
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    elif cmd == "niche":
        cmd_niche(*args[1:])
    elif cmd in ("product", "search", "valuepost", "mix"):
        COMMANDS[cmd](*args[1:3])
    elif cmd == "product-sync":
        product_args = args[1:]
        auto_prepare = "--auto-prepare" in product_args
        keywords = [arg for arg in product_args if arg != "--auto-prepare"]
        if any(arg.startswith("-") for arg in keywords) or len(keywords) > 1:
            print("Cách dùng: python3 run.py product-sync [từ khoá] [--auto-prepare]")
            return 2
        return cmd_product_sync(keyword=keywords[0] if keywords else None,
                                auto_prepare=auto_prepare)
    elif cmd == "approve" and len(args) > 1:
        c = connect(); print(pipeline.approve_post(c, args[1])); c.close()
    elif cmd in ("worker-once", "worker-status", "auto-schedule"):
        return COMMANDS[cmd]()
    elif cmd in COMMANDS:
        COMMANDS[cmd]()
    else:
        print(f"Lệnh không hợp lệ: {cmd}\n"); print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
