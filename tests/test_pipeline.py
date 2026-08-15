"""Test các bất biến mà nếu sai thì mất tiền hoặc mất tài khoản.

    python3 -m acp.tests.test_pipeline
"""
import json
import os
import random
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_tmp = tempfile.mkdtemp()
os.environ["ACP_DB"] = os.path.join(_tmp, "test.db")

from acp.core import db  # noqa: E402
db.DB_PATH = os.environ["ACP_DB"]

from acp.adapters.base import ContentViolationError, PublishError, RateLimitError  # noqa: E402
from acp.adapters.mock import MockAccessTrade, MockFacebookPublisher, MockThreads  # noqa: E402
from acp.core import attribution, content, crypto, imaging, jobs, pipeline, scoring  # noqa: E402
from acp.core.db import connect, init_db, now, ulid  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✓' if cond else '✗'} {name}{'' if cond else '  → ' + str(detail)}")


def setup():
    init_db()
    conn = connect()
    conn.execute("INSERT INTO campaign (id, code, name, is_active, created_at) VALUES (?,?,?,1,?)",
                 (ulid(), "test", "Chiến dịch test", now()))
    conn.execute("INSERT INTO caption_template (id, code, name, body, is_active) VALUES (?,?,?,?,1)",
                 (ulid(), "price_drop", "Báo giảm giá", "price_drop"))
    conn.execute("""INSERT INTO channel (id, code, platform, handle, external_user_id, status,
                    token_encrypted, daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,'threads',?,?,'ACTIVE',?,?,?,?)""",
                 (ulid(), "ch1", "@test", "uid1", crypto.encrypt("tok"), 12, 90, now()))
    # max_per_category_per_day nới ra 20 (mặc định 3): "hôm nay" không đổi
    # suốt một lượt chạy file test này, mà toàn bộ ~40 test dùng chung DB +
    # dùng chung plan_content()/score_candidates() -- trần 3 món/danh mục/ngày
    # mặc định (dành cho vận hành thật) khiến kho 13 danh mục cạn hạn mức
    # (không phải cạn sản phẩm) chỉ sau ~39 lượt approve trong TOÀN FILE, sát
    # nút với số test hiện có. Thêm 3 test mới (Task 7, dùng plan_content) làm
    # tràn trần này, khiến test_publish_post_blocks_disabled_channel (chạy sau,
    # seed=81, không liên quan gì tới approve_post đa kênh) hết ứng viên oan.
    # Không đổi giá trị mặc định thật (core/scoring.py) -- chỉ nới cấu hình
    # riêng của bộ test này.
    test_filters = dict(scoring.DEFAULT_FILTERS, max_per_category_per_day=20)
    scoring.save_config(conn, scoring.DEFAULT_WEIGHTS, test_filters, "test")
    pipeline.ingest_datafeed(conn, MockAccessTrade(), limit=80)
    return conn


def test_crypto():
    print("\nMã hoá token")
    blob = crypto.encrypt("EAAG_secret_token_123")
    check("token mã hoá không lộ bản rõ", b"EAAG_secret" not in blob)
    check("giải mã khôi phục đúng", crypto.decrypt(blob) == "EAAG_secret_token_123")
    check("nonce khác nhau mỗi lần mã hoá", crypto.encrypt("x") != crypto.encrypt("x"))
    check("redact che phần lớn chuỗi", crypto.redact("supersecrettoken").endswith("oken"))


def test_content_guards():
    print("\nRào chắn nội dung")
    link = "https://go.isclix.com/x?sub1=abc"
    ok = f"Nồi chiên Bear 4L\n\nĐang bán 890.000đ.\n\n{link}\n\n{content.DISCLOSURE_DEFAULT}"
    check("caption hợp lệ không bị bắt lỗi", content.validate(ok) == [], content.validate(ok))
    check("thiếu disclosure bị chặn",
          any("nhãn tiếp thị" in p for p in content.validate(ok.replace(content.DISCLOSURE_DEFAULT, ""))))
    check("từ tuyệt đối hoá bị chặn",
          any("tuyệt đối hoá" in p for p in content.validate(ok.replace("Nồi chiên", "Nồi chiên tốt nhất"))))
    check("bịa trải nghiệm cá nhân bị chặn",
          any("trải nghiệm" in p for p in content.validate(ok.replace("Đang bán", "Mình đã dùng và thấy hay. Đang bán"))))
    check("thiếu link bị chặn", any("link" in p for p in content.validate(ok.replace(link, ""))))
    check("vượt 500 ký tự bị chặn", any("500" in p for p in content.validate(ok + "x" * 500)))

    conn = connect()
    p = conn.execute("SELECT * FROM product ORDER BY length(name) DESC LIMIT 1").fetchone()
    long_cap = content.generate(p, "spec_highlight", "https://x.co/" + "a" * 180)
    check("caption luôn được cắt vừa 500 ký tự", len(long_cap) <= 500, len(long_cap))
    check("cắt xong vẫn giữ disclosure", content.DISCLOSURE_DEFAULT in long_cap)
    conn.close()


def test_content_validate_platform_max_len():
    print("\ncontent.validate dùng đúng max_len theo platform, không hard-code Threads")
    link = "https://go.isclix.com/x?sub1=abc"
    long_caption = ("Nồi chiên Bear 4L. " * 30 + f"\n\n{link}\n\n{content.DISCLOSURE_DEFAULT}")
    check("caption dài 3000+ ký tự vượt quá mặc định (Threads, 500)",
          any("500" in p or "Dài" in p for p in content.validate(long_caption)),
          content.validate(long_caption))
    check("cùng caption đó PASS khi max_len=63206 (Facebook)",
          content.validate(long_caption, max_len=content.PLATFORM_MAX_LEN["facebook"]) == [],
          content.validate(long_caption, max_len=content.PLATFORM_MAX_LEN["facebook"]))
    check("PLATFORM_MAX_LEN có đủ 3 platform đúng giá trị đã biết",
          content.PLATFORM_MAX_LEN == {"threads": 500, "facebook": 63206, "instagram": 2200},
          content.PLATFORM_MAX_LEN)


def test_imaging_compose_skips_watermark_when_handle_none():
    print("\nimaging.compose bỏ watermark handle khi handle=None")
    from PIL import Image
    out_dir = tempfile.mkdtemp()
    product_with = {"id": "imgtest_with", "external_product_id": "imgtest_with",
                    "name": "Sản phẩm test watermark", "current_price": 199000,
                    "original_price": None, "image_path_local": None}
    product_without = dict(product_with, id="imgtest_without", external_product_id="imgtest_without")

    path_with = imaging.compose(product_with, out_dir, discount_pct=0.0, handle="@kenhtest")
    path_without = imaging.compose(product_without, out_dir, discount_pct=0.0, handle=None)

    img_with = Image.open(path_with).convert("RGB")
    img_without = Image.open(path_without).convert("RGB")
    # Crop region for text watermark. Text is drawn at y=CANVAS[1]-PAD-12
    # but PIL places it top-aligned, so it extends both up and down.
    region = (imaging.PAD, imaging.CANVAS[1] - imaging.PAD - 35,
              imaging.CANVAS[0] - imaging.PAD, imaging.CANVAS[1] - imaging.PAD + 5)
    pixels_with = set(img_with.crop(region).getdata())
    pixels_without = set(img_without.crop(region).getdata())
    # JPEG compression alters exact RGB, so check for close matches (±5)
    muted_r, muted_g, muted_b = imaging.MUTED
    has_muted_like_with = any(abs(p[0]-muted_r) <= 5 and abs(p[1]-muted_g) <= 5 and abs(p[2]-muted_b) <= 5
                               for p in pixels_with)
    has_muted_like_without = any(abs(p[0]-muted_r) <= 5 and abs(p[1]-muted_g) <= 5 and abs(p[2]-muted_b) <= 5
                                 for p in pixels_without)
    check("có handle: vùng watermark có pixel màu MUTED (chữ được vẽ)",
          has_muted_like_with, len(pixels_with))
    check("handle=None: vùng watermark KHÔNG có pixel màu MUTED (không vẽ chữ)",
          not has_muted_like_without, len(pixels_without))


def test_scoring():
    print("\nChấm điểm")
    conn = connect()
    everything = scoring.score_candidates(conn, limit=999, explain=True)
    blocked = [s for s in everything
               if s["product"]["category_code"] in scoring.DEFAULT_FILTERS["blocked_categories"]]
    check("danh mục cấm luôn bị loại", all(s["rejected"] for s in blocked), len(blocked))
    low = [s for s in everything if (s["product"]["rating"] or 0) < 4.0]
    check("sản phẩm dưới 4 sao bị loại", all(s["rejected"] for s in low), len(low))
    passed = scoring.score_candidates(conn, limit=10)
    check("top-K sắp xếp giảm dần theo điểm",
          all(passed[i]["score"] >= passed[i + 1]["score"] for i in range(len(passed) - 1)))
    check("mọi ứng viên đều đạt hoa hồng tối thiểu",
          all(s["product"]["commission_value"] >= 5000 for s in passed))
    conn.close()


def test_subid_roundtrip():
    print("\nQuy kết qua sub_id")
    subs = attribution.encode_sub_ids("POST123", "gd2026", "B", "threads_main")
    check("post_id nằm ở sub1", subs["sub1"] == "POST123")
    check("giải mã trả lại đủ 4 trường", len(attribution.decode_sub_ids(subs)) == 4)


def test_conversion_dedup():
    print("\nKhử trùng lặp chuyển đổi")
    conn = connect()
    ev = {"transaction_id": "TX1", "external_product_id": "SP1", "sale_amount": 100000,
          "commission": 6000, "sub1": None}
    check("postback đầu tiên được ghi", attribution.record_conversion(conn, ev)[0] == "recorded")
    check("gửi lại đúng postback đó bị loại", attribution.record_conversion(conn, ev)[0] == "duplicate")

    # Một đơn nhiều món -> Accesstrade gửi nhiều postback cùng transaction_id.
    ev2 = dict(ev, external_product_id="SP2")
    check("cùng đơn khác sản phẩm vẫn được ghi", attribution.record_conversion(conn, ev2)[0] == "recorded")
    check("thiếu transaction_id bị từ chối",
          attribution.record_conversion(conn, {"external_product_id": "SP9"})[0] == "invalid")

    ev3 = dict(ev, transaction_id="TX3", sub1="khong-ton-tai")
    st, cid = attribution.record_conversion(conn, ev3)
    row = conn.execute("SELECT post_id FROM conversion WHERE id=?", (cid,)).fetchone()
    check("sub1 lạ vẫn ghi doanh thu, chỉ là không quy kết", st == "recorded" and row["post_id"] is None)
    conn.close()


def test_update_insights_empty_dict_noop():
    print("\nupdate_insights bỏ qua dict rỗng (FB/IG chưa trả insight thật)")
    conn = connect()
    product = conn.execute("SELECT id FROM product LIMIT 1").fetchone()
    channel = conn.execute("SELECT id FROM channel LIMIT 1").fetchone()
    campaign = conn.execute("SELECT id FROM campaign LIMIT 1").fetchone()

    post_id = ulid()
    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, variant_code,
                    caption_body, disclosure_text, caption_final, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                 (post_id, product["id"], channel["id"], campaign["id"], "A",
                  "thân bài", "nhãn tiếp thị", "thân bài", now(), now()))

    # Publisher fallback (Publisher.fetch_insights mặc định) trả {} -- không được
    # tạo ra một dòng post_metrics toàn số 0 trông như thể đã đo đạc thật.
    attribution.update_insights(conn, post_id, {})
    row = conn.execute("SELECT * FROM post_metrics WHERE post_id=?", (post_id,)).fetchone()
    check("dict rỗng không tạo dòng post_metrics", row is None, row)

    # Đã có số liệu thật từ trước -- một lần gọi rỗng về sau (vd. lần fetch tiếp
    # theo lỗi mạng) không được xoá sạch về 0.
    attribution.update_insights(conn, post_id, {"views": 100, "likes": 5, "replies": 2, "reposts": 1})
    row = conn.execute("SELECT * FROM post_metrics WHERE post_id=?", (post_id,)).fetchone()
    check("dict có số liệu thật vẫn ghi đúng views/likes/replies/reposts",
          row is not None and (row["views"], row["likes"], row["replies"], row["reposts"]) == (100, 5, 2, 1),
          dict(row) if row else None)

    attribution.update_insights(conn, post_id, {})
    row = conn.execute("SELECT * FROM post_metrics WHERE post_id=?", (post_id,)).fetchone()
    check("dict rỗng gọi sau đó không ghi đè số liệu thật đã có về 0",
          (row["views"], row["likes"], row["replies"], row["reposts"]) == (100, 5, 2, 1),
          dict(row) if row else None)
    conn.close()


def test_job_retry_semantics():
    print("\nNgữ nghĩa retry")

    calls = {"n": 0}

    @jobs.handler("TEST_FLAKY")
    def flaky(conn, payload, ctx):
        calls["n"] += 1
        raise PublishError("mạng chập chờn")

    @jobs.handler("TEST_VIOLATION")
    def violation(conn, payload, ctx):
        calls["n"] += 1
        raise ContentViolationError("vi phạm chính sách")

    @jobs.handler("TEST_RATELIMIT")
    def limited(conn, payload, ctx):
        calls["n"] += 1
        raise RateLimitError("hết hạn mức")

    conn = connect()
    jobs.enqueue(conn, "TEST_VIOLATION", {})
    calls["n"] = 0
    jobs.run_once(conn, ctx={})
    row = conn.execute("SELECT status, attempt_count FROM job_queue WHERE job_type='TEST_VIOLATION'").fetchone()
    check("lỗi vi phạm nội dung không bao giờ retry", row["status"] == "FAILED" and calls["n"] == 1,
          dict(row))

    jobs.enqueue(conn, "TEST_RATELIMIT", {})
    jobs.run_once(conn, ctx={})
    row = conn.execute("SELECT status, attempt_count FROM job_queue WHERE job_type='TEST_RATELIMIT'").fetchone()
    check("rate limit hoãn lại mà không tiêu lượt retry",
          row["status"] == "READY" and row["attempt_count"] == 0, dict(row))

    jobs.enqueue(conn, "TEST_FLAKY", {})
    for _ in range(5):
        conn.execute("UPDATE job_queue SET run_after=? WHERE job_type='TEST_FLAKY'", (now(),))
        jobs.run_once(conn, ctx={})
    row = conn.execute("SELECT status, attempt_count FROM job_queue WHERE job_type='TEST_FLAKY'").fetchone()
    check("lỗi mạng retry đúng 3 lần rồi dừng",
          row["status"] == "FAILED" and row["attempt_count"] == 3, dict(row))
    conn.close()


def test_idempotency_and_double_post():
    print("\nChống đăng trùng")
    conn = connect()
    n1 = jobs.enqueue(conn, "NOOP", {"a": 1}, idempotency_key="same-key")
    n2 = jobs.enqueue(conn, "NOOP", {"a": 1}, idempotency_key="same-key")
    check("cùng idempotency_key chỉ tạo một job", n1 > 0 and n2 == 0)

    ids = pipeline.plan_content(conn, "test", limit=3, rng=random.Random(1))
    check("chấm điểm tạo được job sinh nội dung", len(ids) > 0)
    ch = MockThreads(seed=1)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": ch}})

    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
    check("bài sinh ra ở trạng thái chờ duyệt", post is not None)
    res = pipeline.approve_post(conn, post["id"])
    check("duyệt xong thì lên lịch", res["ok"])
    target_id = res["publish_target_id"]
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?", (now(), f"pub:{target_id}"))
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": ch}})

    before = len(ch.published)
    # Ép chạy lại đúng job publish đó -- mô phỏng retry sau khi bài đã lên thành công.
    jobs.enqueue(conn, "PUBLISH_POST",
                 {"publish_target_id": target_id, "post_id": post["id"], "channel_id": post["channel_id"]})
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": ch}})
    check("chạy lại job publish không đăng bài lần hai", len(ch.published) == before,
          f"{before} → {len(ch.published)}")

    row = conn.execute("SELECT status, thread_id FROM post WHERE id=?", (post["id"],)).fetchone()
    check("bài đã có thread_id sau khi đăng", row["status"] == "PUBLISHED" and row["thread_id"])
    target = conn.execute("SELECT status, external_post_id FROM publish_target WHERE id=?", (target_id,)).fetchone()
    check("publish_target cũng SUCCESS", target["status"] == "SUCCESS" and target["external_post_id"])
    conn.close()


def test_daily_cap():
    print("\nTrần đăng bài theo ngày")
    conn = connect()
    # Test trước đã đăng bài trên kênh này, nên trần phải tính từ số hiện có,
    # không được đặt cứng bằng 1.
    today = now()[:10]
    already = conn.execute(
        "SELECT COUNT(*) FROM post WHERE status='PUBLISHED' AND substr(published_at,1,10)=?",
        (today,)).fetchone()[0]
    conn.execute("UPDATE channel SET daily_post_cap = ?", (already + 1,))

    ch = MockThreads(seed=5)
    pipeline.plan_content(conn, "test", limit=4, rng=random.Random(2))
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": ch}})
    for r in conn.execute("SELECT id FROM post WHERE status='PENDING_REVIEW'").fetchall():
        pipeline.approve_post(conn, r["id"])
    conn.execute("UPDATE job_queue SET run_after=? WHERE job_type='PUBLISH_POST' AND status='READY'", (now(),))
    approved = conn.execute("SELECT COUNT(*) FROM post WHERE status='SCHEDULED'").fetchone()[0]
    before = len(ch.published)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": ch}})
    posted = len(ch.published) - before
    check("có đủ bài để thử vượt trần", approved >= 2, f"chỉ có {approved} bài đã lên lịch")
    check("chạm trần thì đăng đúng 1 bài rồi dừng", posted == 1, f"đăng thêm {posted}")
    deferred = conn.execute(
        "SELECT COUNT(*) FROM job_queue WHERE job_type='PUBLISH_POST' AND status='READY' "
        "AND last_error LIKE '%trần%'").fetchone()[0]
    check("phần vượt trần bị hoãn chứ không đánh hỏng", deferred >= 1, f"{deferred} job bị hoãn")
    conn.execute("UPDATE channel SET daily_post_cap = 12")
    conn.close()


def test_next_slot_and_daily_cap_scoped_per_channel_via_publish_target():
    print("\n_next_slot/_published_today tính theo publish_target, không rò rỉ giữa các kênh")
    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (fb_id, "fb_slot_test", "facebook", "FB Slot Test", "ACTIVE", 1, 12, 90, now()))
    try:
        ch1 = conn.execute("SELECT id FROM channel WHERE code='ch1'").fetchone()

        # Kênh ch1 đã có publish_target SUCCESS gần đây (từ các test trước, hoặc
        # tự tạo một cái) -- kênh facebook mới thì chưa có gì.
        product = conn.execute("SELECT id FROM product LIMIT 1").fetchone()
        campaign = conn.execute("SELECT id FROM campaign LIMIT 1").fetchone()
        post_id = ulid()
        conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, variant_code,
                        caption_body, disclosure_text, caption_final, created_at, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (post_id, product["id"], ch1["id"], campaign["id"], "A",
                      "thân bài", "nhãn tiếp thị", "thân bài", now(), now()))
        target_id = ulid()
        conn.execute("""INSERT INTO publish_target (id, post_id, channel_id, status,
                        created_at, updated_at) VALUES (?,?,?,'SUCCESS',?,?)""",
                     (target_id, post_id, ch1["id"], now(), now()))

        slot_ch1 = pipeline._next_slot(conn, ch1["id"])
        slot_fb = pipeline._next_slot(conn, fb_id)
        check("kênh vừa có publish_target SUCCESS thì slot bị đẩy về tương lai (giãn cách)",
              slot_ch1 > now(), (slot_ch1, now()))
        check("kênh facebook mới, chưa có publish_target nào thì slot = ngay bây giờ (không bị ảnh hưởng bởi ch1)",
              slot_fb <= now(), (slot_fb, now()))

        conn.execute("UPDATE publish_target SET status='SUCCESS', updated_at=? WHERE id=?",
                     (now(), target_id))
        check("_published_today đếm đúng kênh ch1", pipeline._published_today(conn, ch1["id"]) >= 1)
        check("_published_today KHÔNG đếm nhầm sang kênh facebook", pipeline._published_today(conn, fb_id) == 0)
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id=?", (fb_id,))
        conn.close()


def test_publish_target_failure_semantics():
    print("\npublish_target theo dõi lỗi")
    conn = connect()
    ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(21))
    check("có job sinh nội dung", len(ids) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=21)}})
    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
    res = pipeline.approve_post(conn, post["id"])
    target_id = res["publish_target_id"]
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?", (now(), f"pub:{target_id}"))

    failing = MockThreads(fail_rate=1.0, seed=22)  # luôn PublishError
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": failing}})
    target = conn.execute("SELECT * FROM publish_target WHERE id=?", (target_id,)).fetchone()
    check("publish_target FAILED sau lỗi mạng", target["status"] == "FAILED", target["status"])
    check("attempt_count tăng lên", target["attempt_count"] == 1, target["attempt_count"])
    check("last_error được ghi lại", bool(target["last_error"]))

    ids2 = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(23))
    check("có job sinh nội dung 2", len(ids2) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=23)}})
    post2 = conn.execute(
        "SELECT * FROM post WHERE status='PENDING_REVIEW' AND id != ? LIMIT 1", (post["id"],)).fetchone()
    res2 = pipeline.approve_post(conn, post2["id"])
    target2_id = res2["publish_target_id"]
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?", (now(), f"pub:{target2_id}"))

    limited = MockThreads(rate_limited=True, seed=24)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": limited}})
    target2 = conn.execute("SELECT * FROM publish_target WHERE id=?", (target2_id,)).fetchone()
    check("rate limit không tăng attempt_count", target2["attempt_count"] == 0, target2["attempt_count"])
    check("rate limit trả target về SCHEDULED chứ không FAILED",
          target2["status"] == "SCHEDULED", target2["status"])
    conn.close()


def test_publish_post_authorror_marks_channel():
    print("\nLỗi xác thực khi publish vẫn đánh dấu kênh (payload giữ channel_id)")
    from acp.adapters.base import AuthError

    class _AuthFailPublisher(MockThreads):
        def publish(self, channel_row, caption, media=None):
            raise AuthError("token thu hồi")

    conn = connect()
    ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(25))
    check("có job sinh nội dung", len(ids) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=25)}})
    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
    res = pipeline.approve_post(conn, post["id"])
    target_id = res["publish_target_id"]
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?", (now(), f"pub:{target_id}"))

    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": _AuthFailPublisher()}})

    channel = conn.execute("SELECT status FROM channel WHERE id=?", (post["channel_id"],)).fetchone()
    check("job publish AuthError vẫn đánh dấu kênh NEEDS_REAUTH", channel["status"] == "NEEDS_REAUTH", channel["status"])
    target = conn.execute("SELECT status FROM publish_target WHERE id=?", (target_id,)).fetchone()
    check("publish_target FAILED tương ứng", target["status"] == "FAILED", target["status"])
    conn.execute("UPDATE channel SET status='ACTIVE' WHERE id=?", (post["channel_id"],))
    conn.close()


def test_retry_publish_target():
    print("\nThử lại publish_target lỗi")
    conn = connect()
    ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(31))
    check("có job sinh nội dung", len(ids) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=31)}})
    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
    res = pipeline.approve_post(conn, post["id"])
    target_id = res["publish_target_id"]
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?", (now(), f"pub:{target_id}"))

    failing = MockThreads(fail_rate=1.0, seed=32)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": failing}})
    target = conn.execute("SELECT status FROM publish_target WHERE id=?", (target_id,)).fetchone()
    check("target FAILED trước khi retry", target["status"] == "FAILED", target["status"])

    bad = pipeline.retry_publish_target(conn, "khong-ton-tai")
    check("retry target không tồn tại báo lỗi", bad["ok"] is False)

    res2 = pipeline.retry_publish_target(conn, target_id)
    check("retry tạo job mới", res2["ok"] and res2["job_id"], res2)
    again = pipeline.retry_publish_target(conn, target_id)
    check("retry lần hai khi đang PENDING bị chặn", again["ok"] is False, again)

    conn.execute("UPDATE job_queue SET run_after=? WHERE id=?", (now(), res2["job_id"]))
    ok_publisher = MockThreads(seed=33)  # publisher khác, không lỗi -- mô phỏng sự cố đã hết
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": ok_publisher}})
    target = conn.execute("SELECT status, external_post_id FROM publish_target WHERE id=?", (target_id,)).fetchone()
    check("retry thành công thì target SUCCESS", target["status"] == "SUCCESS" and target["external_post_id"], dict(target))

    n_targets = conn.execute("SELECT COUNT(*) FROM publish_target WHERE post_id=?", (post["id"],)).fetchone()[0]
    check("retry không tạo publish_target mới", n_targets == 1, n_targets)
    conn.close()


def test_publish_post_legacy_payload_compat():
    print("\nJob PUBLISH_POST payload cũ (trước khi có publish_target_id)")
    conn = connect()
    ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(41))
    check("có job sinh nội dung", len(ids) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=41)}})
    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
    res = pipeline.approve_post(conn, post["id"])
    check("duyệt xong thì lên lịch", res["ok"])
    # Mô phỏng đúng tình huống nâng cấp: job cũ còn nằm trong var/job_queue từ trước
    # khi có publish_target, nên KHÔNG có publish_target nào cho post này -- xoá cả
    # target lẫn job mà approve_post (code mới) vừa tạo.
    conn.execute("DELETE FROM publish_target WHERE post_id=?", (post["id"],))
    conn.execute("DELETE FROM job_queue WHERE idempotency_key=?", (f"pub:{res['publish_target_id']}",))
    n_before = conn.execute("SELECT COUNT(*) FROM publish_target WHERE post_id=?", (post["id"],)).fetchone()[0]
    check("đã dọn sạch publish_target để mô phỏng payload cũ", n_before == 0, n_before)

    ch = MockThreads(seed=42)
    jobs.enqueue(conn, "PUBLISH_POST", {"post_id": post["id"], "channel_id": post["channel_id"]})
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": ch}})

    row = conn.execute("SELECT status, thread_id FROM post WHERE id=?", (post["id"],)).fetchone()
    check("bài payload cũ vẫn đăng thành công", row["status"] == "PUBLISHED" and bool(row["thread_id"]), dict(row))
    target = conn.execute(
        "SELECT * FROM publish_target WHERE post_id=? AND channel_id=?",
        (post["id"], post["channel_id"])).fetchone()
    check("publish_target được tạo bù cho payload cũ", target is not None, target)
    check("publish_target tạo bù ở trạng thái SUCCESS", target is not None and target["status"] == "SUCCESS",
          dict(target) if target else None)

    before = len(ch.published)
    # Chạy lại đúng job payload cũ đó lần nữa -- mô phỏng retry hàng đợi sau khi đã
    # đăng thành công. Không được đăng trùng, không được tạo publish_target thứ hai.
    jobs.enqueue(conn, "PUBLISH_POST", {"post_id": post["id"], "channel_id": post["channel_id"]})
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": ch}})
    check("chạy lại job payload cũ không đăng trùng", len(ch.published) == before,
          f"{before} → {len(ch.published)}")
    n_targets = conn.execute("SELECT COUNT(*) FROM publish_target WHERE post_id=?", (post["id"],)).fetchone()[0]
    check("payload cũ không tạo thêm publish_target thứ hai", n_targets == 1, n_targets)
    conn.close()


def test_publish_post_malformed_payload_raises():
    print("\nJob PUBLISH_POST payload hỏng hoàn toàn")
    conn = connect()
    try:
        pipeline.publish_post(conn, {}, {"source": MockAccessTrade(), "publishers": {}})
        check("payload rỗng phải báo lỗi rõ ràng", False, "không ném lỗi")
    except ValueError as e:
        check("payload rỗng phải báo lỗi rõ ràng", "publish_target" in str(e), str(e))
    conn.close()


def test_publish_target_cancelled_on_stale_post_status():
    print("\npublish_target CANCELLED khi bài không còn ở trạng thái đăng được")
    from acp.adapters.base import ContentViolationError as _CVE

    class _ViolationPublisher(MockThreads):
        def publish(self, channel_row, caption, media=None):
            raise _CVE("nội dung vi phạm chính sách nền tảng")

    conn = connect()
    ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(61))
    check("có job sinh nội dung", len(ids) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=61)}})
    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
    res = pipeline.approve_post(conn, post["id"])
    target_id = res["publish_target_id"]
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?", (now(), f"pub:{target_id}"))

    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": _ViolationPublisher()}})
    target = conn.execute("SELECT status FROM publish_target WHERE id=?", (target_id,)).fetchone()
    check("target FAILED sau khi nội dung bị nền tảng từ chối", target["status"] == "FAILED", target["status"])
    post_after = conn.execute("SELECT status FROM post WHERE id=?", (post["id"],)).fetchone()
    check("bài bị đẩy về PENDING_REVIEW", post_after["status"] == "PENDING_REVIEW", post_after["status"])

    res2 = pipeline.retry_publish_target(conn, target_id)
    check("retry target FAILED được chấp nhận", res2["ok"] and res2["job_id"], res2)
    target2 = conn.execute("SELECT status FROM publish_target WHERE id=?", (target_id,)).fetchone()
    check("retry đưa target về PENDING", target2["status"] == "PENDING", target2["status"])

    # KHÔNG đưa bài trở lại SCHEDULED -- đúng khoảng hở thực tế: bài vẫn PENDING_REVIEW
    # (chưa được duyệt lại qua /duyet) khi job publish retry chạy tới.
    conn.execute("UPDATE job_queue SET run_after=? WHERE id=?", (now(), res2["job_id"]))
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=62)}})

    target3 = conn.execute("SELECT status, last_error FROM publish_target WHERE id=?", (target_id,)).fetchone()
    check("target CANCELLED khi bài không còn ở trạng thái đăng được", target3["status"] == "CANCELLED", target3["status"])
    check("last_error giải thích lý do CANCELLED", bool(target3["last_error"]), target3["last_error"])

    res3 = pipeline.retry_publish_target(conn, target_id)
    check("retry bị chặn rõ ràng khi target đã CANCELLED", res3["ok"] is False, res3)
    conn.close()


def test_sibling_target_not_cancelled_after_first_target_publishes():
    print("\nTarget B (kênh khác) không bị huỷ khi target A (kênh khác) cùng post đã publish trước")
    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (fb_id, "fb_sibling_test", "facebook", "FB Sibling Test", "ACTIVE", 1, 12, 0, now()))
    try:
        ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(81))
        check("có job sinh nội dung", len(ids) > 0)
        jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=81)}})
        post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
        ch1_id = post["channel_id"]

        # approve_post() 1-kênh như hiện tại -- tạo target A trên ch1.
        res = pipeline.approve_post(conn, post["id"])
        check("duyệt thành công", res["ok"], res)
        target_a_id = res["publish_target_id"]
        conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?",
                     (now(), f"pub:{target_a_id}"))

        # Target B thủ công trên kênh facebook, cùng post -- mô phỏng đúng
        # trạng thái Task 7 sẽ tạo ra, mà không phụ thuộc approve_post đã sửa.
        # run_after cố ý đặt XA trong tương lai để job B chắc chắn KHÔNG chạy
        # ở lượt drain() đầu tiên -- tránh phụ thuộc vào thứ tự xử lý job cùng
        # run_after mà job_queue không cam kết.
        future = (datetime.fromisoformat(now()) + timedelta(hours=1)).isoformat(timespec="seconds")
        target_b_id = ulid()
        conn.execute("""INSERT INTO publish_target (id, post_id, channel_id, status,
                        scheduled_at, created_at, updated_at)
                        VALUES (?,?,?,'SCHEDULED',?,?,?)""",
                     (target_b_id, post["id"], fb_id, future, now(), now()))
        jobs.enqueue(conn, "PUBLISH_POST",
                     {"publish_target_id": target_b_id, "post_id": post["id"], "channel_id": fb_id},
                     run_after=future, idempotency_key=f"pub:{target_b_id}")

        # Lượt 1: chỉ job A sẵn sàng (job B còn ở tương lai) -- target A
        # publish thành công, post.status -> PUBLISHED.
        jobs.drain(conn, ctx={"source": MockAccessTrade(),
                              "publishers": {"threads": MockThreads(seed=82), "facebook": MockFacebookPublisher(seed=83)}})

        post_after_a = conn.execute("SELECT status FROM post WHERE id=?", (post["id"],)).fetchone()
        check("post.status = PUBLISHED sau khi target A thành công",
              post_after_a["status"] == "PUBLISHED", post_after_a["status"])
        target_a_after = conn.execute(
            "SELECT status FROM publish_target WHERE id=?", (target_a_id,)).fetchone()
        check("target A SUCCESS", target_a_after["status"] == "SUCCESS", target_a_after["status"])

        # Lượt 2: đưa job B về sẵn sàng ngay -- đây là phép thử thật của bug:
        # post.status giờ đã là PUBLISHED (không phải SCHEDULED), target B có
        # bị huỷ oan không.
        conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?",
                     (now(), f"pub:{target_b_id}"))
        jobs.drain(conn, ctx={"source": MockAccessTrade(),
                              "publishers": {"threads": MockThreads(seed=82), "facebook": MockFacebookPublisher(seed=83)}})
        target_b_after = conn.execute(
            "SELECT status, last_error FROM publish_target WHERE id=?", (target_b_id,)).fetchone()
        check("target B (kênh facebook) vẫn được publish, KHÔNG bị CANCELLED vì post đã PUBLISHED",
              target_b_after["status"] == "SUCCESS", dict(target_b_after))
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id=?", (fb_id,))
        conn.close()


def test_content_violation_does_not_unpublish_already_published_post():
    print("\nKênh B bị từ chối nội dung KHÔNG được rút bài đã đăng thành công ở kênh A")
    from acp.adapters.base import ContentViolationError as _CVE

    class _ViolationPublisher(MockFacebookPublisher):
        def publish(self, channel_row, caption, media=None):
            raise _CVE("nội dung vi phạm chính sách nền tảng")

    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (fb_id, "fb_violation_test", "facebook", "FB Violation Test", "ACTIVE", 1, 12, 0, now()))
    try:
        ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(141))
        check("có job sinh nội dung", len(ids) > 0)
        jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=141)}})
        post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()

        # Target A (threads) chạy trước và thành công -> post.status = PUBLISHED.
        res = pipeline.approve_post(conn, post["id"])
        check("duyệt thành công", res["ok"], res)
        target_a_id = res["publish_target_id"]
        conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?",
                     (now(), f"pub:{target_a_id}"))

        # Target B (facebook) thủ công, run_after đặt XA trong tương lai để chắc
        # chắn không chạy chung lượt drain với target A.
        future = (datetime.fromisoformat(now()) + timedelta(hours=1)).isoformat(timespec="seconds")
        target_b_id = ulid()
        conn.execute("""INSERT INTO publish_target (id, post_id, channel_id, status,
                        scheduled_at, created_at, updated_at)
                        VALUES (?,?,?,'SCHEDULED',?,?,?)""",
                     (target_b_id, post["id"], fb_id, future, now(), now()))
        jobs.enqueue(conn, "PUBLISH_POST",
                     {"publish_target_id": target_b_id, "post_id": post["id"], "channel_id": fb_id},
                     run_after=future, idempotency_key=f"pub:{target_b_id}")

        jobs.drain(conn, ctx={"source": MockAccessTrade(),
                              "publishers": {"threads": MockThreads(seed=142),
                                             "facebook": MockFacebookPublisher(seed=143)}})
        post_after_a = conn.execute("SELECT status, published_at FROM post WHERE id=?",
                                    (post["id"],)).fetchone()
        check("post.status = PUBLISHED sau khi target A thành công",
              post_after_a["status"] == "PUBLISHED", post_after_a["status"])
        check("published_at đã được ghi", bool(post_after_a["published_at"]), post_after_a["published_at"])

        # Lượt 2: target B bị nền tảng từ chối nội dung.
        conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?",
                     (now(), f"pub:{target_b_id}"))
        jobs.drain(conn, ctx={"source": MockAccessTrade(),
                              "publishers": {"threads": MockThreads(seed=144),
                                             "facebook": _ViolationPublisher(seed=145)}})

        post_after_b = conn.execute("SELECT status, published_at FROM post WHERE id=?",
                                    (post["id"],)).fetchone()
        check("post VẪN là PUBLISHED, không bị đẩy về PENDING_REVIEW vì kênh B vi phạm",
              post_after_b["status"] == "PUBLISHED", post_after_b["status"])
        check("published_at vẫn còn nguyên", bool(post_after_b["published_at"]), post_after_b["published_at"])

        target_b_after = conn.execute(
            "SELECT status, last_error FROM publish_target WHERE id=?", (target_b_id,)).fetchone()
        check("target B FAILED (vi phạm vẫn được ghi lại ở đúng target)",
              target_b_after["status"] == "FAILED", dict(target_b_after))
        check("target B có last_error giải thích lý do", bool(target_b_after["last_error"]),
              target_b_after["last_error"])
        target_a_after = conn.execute(
            "SELECT status FROM publish_target WHERE id=?", (target_a_id,)).fetchone()
        check("target A vẫn SUCCESS", target_a_after["status"] == "SUCCESS", target_a_after["status"])
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id=?", (fb_id,))
        conn.close()


def test_generate_content_writes_post_channel_selection():
    print("\nBài do pipeline TỰ ĐỘNG sinh cũng phải có post_channel_selection")
    conn = connect()
    before = {r["id"] for r in conn.execute("SELECT id FROM post").fetchall()}
    ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(151))
    check("có job sinh nội dung", len(ids) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=151)}})
    new_ids = [r["id"] for r in conn.execute("SELECT id FROM post").fetchall() if r["id"] not in before]
    check("pipeline tự động tạo được ít nhất 1 bài mới", len(new_ids) >= 1, new_ids)

    for post_id in new_ids:
        post = conn.execute("SELECT channel_id FROM post WHERE id=?", (post_id,)).fetchone()
        rows = conn.execute("SELECT channel_id FROM post_channel_selection WHERE post_id=?",
                            (post_id,)).fetchall()
        check("có đúng 1 dòng post_channel_selection cho bài tự động",
              len(rows) == 1, [dict(r) for r in rows])
        check("channel_id khớp post.channel_id",
              len(rows) == 1 and rows[0]["channel_id"] == post["channel_id"],
              (post["channel_id"], [dict(r) for r in rows]))

    # Đọc lại qua đúng helper mà /duyet dùng để dựng checklist.
    sels = pipeline.post_channel_selections(conn, new_ids)
    check("post_channel_selections() trả checklist không rỗng cho mọi bài tự động",
          all(sels.get(pid) for pid in new_ids), {k: len(v) for k, v in sels.items()})
    conn.close()


def test_fetch_insights_idempotency_key_per_target_not_per_post():
    print("\nFETCH_INSIGHTS của 2 target cùng post không bị coi trùng idempotency")
    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (fb_id, "fb_insights_test", "facebook", "FB Insights Test", "ACTIVE", 1, 12, 0, now()))
    try:
        ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(91))
        check("có job sinh nội dung", len(ids) > 0)
        jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=91)}})
        post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()

        res = pipeline.approve_post(conn, post["id"])
        target_a_id = res["publish_target_id"]
        conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?",
                     (now(), f"pub:{target_a_id}"))

        target_b_id = ulid()
        conn.execute("""INSERT INTO publish_target (id, post_id, channel_id, status,
                        scheduled_at, created_at, updated_at)
                        VALUES (?,?,?,'SCHEDULED',?,?,?)""",
                     (target_b_id, post["id"], fb_id, now(), now(), now()))
        jobs.enqueue(conn, "PUBLISH_POST",
                     {"publish_target_id": target_b_id, "post_id": post["id"], "channel_id": fb_id},
                     run_after=now(), idempotency_key=f"pub:{target_b_id}")

        jobs.drain(conn, ctx={"source": MockAccessTrade(),
                              "publishers": {"threads": MockThreads(seed=92), "facebook": MockFacebookPublisher(seed=93)}})

        # Lọc theo post_id trong payload -- job_queue dùng chung 1 DB cho cả file
        # test (không xoá job sau khi DONE), nên nếu không lọc sẽ dính cả
        # FETCH_INSIGHTS của các bài khác từ những test chạy trước đó.
        insight_jobs = conn.execute(
            "SELECT idempotency_key FROM job_queue WHERE job_type='FETCH_INSIGHTS' AND payload LIKE ?",
            (f'%"post_id": "{post["id"]}"%',)).fetchall()
        check("có đúng 2 job FETCH_INSIGHTS (1 mỗi target), không bị dedupe nhầm",
              len(insight_jobs) == 2, [r["idempotency_key"] for r in insight_jobs])
        check("idempotency_key theo target chứ không theo post (2 key khác nhau)",
              len({r["idempotency_key"] for r in insight_jobs}) == 2)

        target_a = conn.execute("SELECT external_post_id FROM publish_target WHERE id=?", (target_a_id,)).fetchone()
        target_b = conn.execute("SELECT external_post_id FROM publish_target WHERE id=?", (target_b_id,)).fetchone()
        check("target A và B có external_post_id khác nhau (2 lần publish riêng biệt)",
              target_a["external_post_id"] != target_b["external_post_id"],
              (target_a["external_post_id"], target_b["external_post_id"]))
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id=?", (fb_id,))
        conn.close()


def test_approve_post_multi_channel_creates_n_targets():
    print("\napprove_post(channel_ids=[...]) sinh đúng N publish_target, mỗi kênh 1 slot riêng")
    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (fb_id, "fb_approve_test", "facebook", "FB Approve Test", "ACTIVE", 1, 12, 90, now()))
    try:
        ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(101))
        check("có job sinh nội dung", len(ids) > 0)
        jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=101)}})
        post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
        ch1_id = post["channel_id"]

        res = pipeline.approve_post(conn, post["id"], channel_ids=[ch1_id, fb_id])
        check("duyệt đa kênh thành công", res["ok"], res)
        check("trả về đúng 2 target trong 'targets'", len(res["targets"]) == 2, res["targets"])
        check("giữ tương thích ngược: publish_target_id trỏ target đầu tiên",
              res["publish_target_id"] == res["targets"][0]["publish_target_id"])

        rows = conn.execute("SELECT channel_id, status FROM publish_target WHERE post_id=?",
                            (post["id"],)).fetchall()
        check("có đúng 2 dòng publish_target trong DB", len(rows) == 2, len(rows))
        check("cả 2 đều SCHEDULED", all(r["status"] == "SCHEDULED" for r in rows), [dict(r) for r in rows])

        jobs_count = conn.execute(
            "SELECT COUNT(*) FROM job_queue WHERE job_type='PUBLISH_POST' AND idempotency_key LIKE ?",
            (f"pub:%",)).fetchone()[0]
        check("có ít nhất 2 job PUBLISH_POST đang chờ (1 mỗi target)", jobs_count >= 2, jobs_count)

        post_after = conn.execute("SELECT status FROM post WHERE id=?", (post["id"],)).fetchone()
        check("post.status = SCHEDULED (1 lần, dùng chung)", post_after["status"] == "SCHEDULED")
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id=?", (fb_id,))
        conn.close()


def test_approve_post_channel_ids_none_falls_back_to_post_channel_id():
    print("\napprove_post(channel_ids=None) tương thích ngược -- 1 target trên post.channel_id")
    conn = connect()
    ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(102))
    check("có job sinh nội dung", len(ids) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=102)}})
    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()

    res = pipeline.approve_post(conn, post["id"])
    check("duyệt không truyền channel_ids vẫn thành công", res["ok"], res)
    check("chỉ tạo đúng 1 target trên kênh của post", len(res["targets"]) == 1, res["targets"])
    check("target đó đúng post.channel_id", res["targets"][0]["channel_id"] == post["channel_id"])
    conn.close()


def test_approve_post_rejects_disabled_channel_in_list_creates_no_target():
    print("\napprove_post với 1 kênh bị disabled trong channel_ids -> lỗi, không tạo target nào")
    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                 (fb_id, "fb_approve_disabled_test", "facebook", "FB Approve Disabled", "ACTIVE", 0, now()))
    try:
        ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(103))
        check("có job sinh nội dung", len(ids) > 0)
        jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=103)}})
        post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
        ch1_id = post["channel_id"]

        before = conn.execute("SELECT COUNT(*) FROM publish_target").fetchone()[0]
        res = pipeline.approve_post(conn, post["id"], channel_ids=[ch1_id, fb_id])
        check("duyệt thất bại vì có kênh disabled", res["ok"] is False, res)
        check("lỗi nêu rõ tên kênh", "fb_approve_disabled_test" in (res.get("error") or ""), res.get("error"))
        after = conn.execute("SELECT COUNT(*) FROM publish_target").fetchone()[0]
        check("không tạo publish_target nào (tất-cả-hoặc-không-gì)", before == after, (before, after))
        post_after = conn.execute("SELECT status FROM post WHERE id=?", (post["id"],)).fetchone()
        check("post vẫn PENDING_REVIEW, không bị đổi trạng thái",
              post_after["status"] == "PENDING_REVIEW", post_after["status"])
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id=?", (fb_id,))
        conn.close()


def test_fetch_insights_legacy_payload_falls_back_to_post_thread_id():
    print("\nFETCH_INSIGHTS payload cũ (không có publish_target_id) fallback đúng về post.thread_id")
    conn = connect()
    ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(101))
    check("có job sinh nội dung", len(ids) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=101)}})
    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()

    res = pipeline.approve_post(conn, post["id"])
    check("duyệt thành công", res["ok"], res)
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?",
                 (now(), f"pub:{res['publish_target_id']}"))
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=102)}})

    post_after = conn.execute("SELECT * FROM post WHERE id=?", (post["id"],)).fetchone()
    check("post đã publish, có sẵn thread_id (mô phỏng bài đăng từ trước D1)",
          post_after["status"] == "PUBLISHED" and bool(post_after["thread_id"]), dict(post_after))

    # Publisher ghi lại external_post_id thực sự nhận được, để kiểm tra fallback
    # có truyền ĐÚNG giá trị (không phải None/rỗng) chứ không chỉ "không crash".
    calls = []

    class RecordingThreads(MockThreads):
        def fetch_insights(self, channel_row, external_post_id):
            calls.append(external_post_id)
            return super().fetch_insights(channel_row, external_post_id)

    # payload kiểu cũ (trước D1): KHÔNG có publish_target_id -- đúng dạng job
    # đã enqueue trước khi Task 4 deploy, có thể vẫn còn tồn đọng trong hàng đợi.
    legacy_payload = {"post_id": post["id"], "channel_id": post_after["channel_id"]}
    pipeline.fetch_insights(conn, legacy_payload, {"publishers": {"threads": RecordingThreads(seed=103)}})

    check("publisher.fetch_insights được gọi với post.thread_id (fallback thật, không phải None)",
          calls == [post_after["thread_id"]], calls)
    metrics = conn.execute("SELECT * FROM post_metrics WHERE post_id=?", (post["id"],)).fetchone()
    check("post_metrics được ghi từ nhánh fallback", metrics is not None, metrics)
    conn.close()


def test_legacy_payload_does_not_resurrect_cancelled_target():
    print("\nJob PUBLISH_POST payload cũ không được hồi sinh publish_target đã CANCELLED")
    from acp.adapters.base import ContentViolationError as _CVE

    class _ViolationPublisher(MockThreads):
        def publish(self, channel_row, caption, media=None):
            raise _CVE("nội dung vi phạm chính sách nền tảng")

    conn = connect()
    ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(71))
    check("có job sinh nội dung", len(ids) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=71)}})
    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
    res = pipeline.approve_post(conn, post["id"])
    old_target_id = res["publish_target_id"]
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?", (now(), f"pub:{old_target_id}"))

    # Đưa target đầu tiên qua FAILED -> retry -> CANCELLED, giống hệt
    # test_publish_target_cancelled_on_stale_post_status.
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": _ViolationPublisher()}})
    old_target = conn.execute("SELECT status FROM publish_target WHERE id=?", (old_target_id,)).fetchone()
    check("target cũ FAILED sau khi nội dung bị nền tảng từ chối", old_target["status"] == "FAILED", old_target["status"])

    res2 = pipeline.retry_publish_target(conn, old_target_id)
    check("retry target FAILED được chấp nhận", res2["ok"] and res2["job_id"], res2)
    conn.execute("UPDATE job_queue SET run_after=? WHERE id=?", (now(), res2["job_id"]))
    # KHÔNG khôi phục bài về trạng thái đăng được -- drain sẽ khiến target bị CANCELLED.
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=72)}})
    old_target2 = conn.execute("SELECT status FROM publish_target WHERE id=?", (old_target_id,)).fetchone()
    check("target cũ CANCELLED (trạng thái cuối, không cho retry)", old_target2["status"] == "CANCELLED", old_target2["status"])

    # Duyệt lại bài từ đầu qua /duyet -- tạo publish_target MỚI, hợp lệ, cho đúng
    # post_id+channel_id, đúng như tài liệu của _cancel_target_stale_post mô tả.
    res3 = pipeline.approve_post(conn, post["id"])
    check("duyệt lại thành công, tạo target mới", res3["ok"], res3)
    new_target_id = res3["publish_target_id"]
    check("target mới khác target cũ đã CANCELLED", new_target_id != old_target_id)
    # Xoá job PUBLISH_POST (có publish_target_id) mà approve_post() vừa tạo, để mô
    # phỏng ĐÚNG kịch bản bug: chỉ còn một job PUBLISH_POST dạng cũ (thiếu
    # publish_target_id) kẹt lại trong var/job_queue từ trước khi nâng cấp.
    conn.execute("DELETE FROM job_queue WHERE idempotency_key=?", (f"pub:{new_target_id}",))

    ch = MockThreads(seed=73)
    before = len(ch.published)
    jobs.enqueue(conn, "PUBLISH_POST", {"post_id": post["id"], "channel_id": post["channel_id"]})
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": ch}})

    old_target3 = conn.execute("SELECT status FROM publish_target WHERE id=?", (old_target_id,)).fetchone()
    check("target CANCELLED cũ vẫn còn nguyên, không bị hồi sinh", old_target3["status"] == "CANCELLED", old_target3["status"])

    new_target = conn.execute(
        "SELECT status, external_post_id FROM publish_target WHERE id=?", (new_target_id,)).fetchone()
    check("target mới (hợp lệ) mới là target được đăng thành công",
          new_target["status"] == "SUCCESS" and bool(new_target["external_post_id"]), dict(new_target))

    row = conn.execute("SELECT status, thread_id FROM post WHERE id=?", (post["id"],)).fetchone()
    check("bài PUBLISHED", row["status"] == "PUBLISHED" and bool(row["thread_id"]), dict(row))
    check("chỉ đăng đúng một lần qua publisher (không đăng trùng)",
          len(ch.published) - before == 1, len(ch.published) - before)
    conn.close()


def test_retry_publish_target_recovers_running():
    print("\nRetry publish_target khi RUNNING (nghi treo do worker crash)")
    conn = connect()
    ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(51))
    check("có job sinh nội dung", len(ids) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=51)}})
    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
    res = pipeline.approve_post(conn, post["id"])
    target_id = res["publish_target_id"]

    # Mô phỏng worker chết ngay sau khi đánh dấu RUNNING nhưng trước khi publish trả
    # về: xoá job đang chạy (coi như tiến trình biến mất, không còn trong hàng đợi)
    # và tự set target về RUNNING như handler đã làm dở.
    conn.execute("DELETE FROM job_queue WHERE idempotency_key=?", (f"pub:{target_id}",))
    conn.execute("UPDATE publish_target SET status='RUNNING', updated_at=? WHERE id=?", (now(), target_id))

    res_running = pipeline.retry_publish_target(conn, target_id)
    check("retry chấp nhận target đang RUNNING (nghi treo)", res_running["ok"] and res_running["job_id"], res_running)

    conn.execute("UPDATE job_queue SET run_after=? WHERE id=?", (now(), res_running["job_id"]))
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=52)}})
    target = conn.execute("SELECT status, external_post_id FROM publish_target WHERE id=?", (target_id,)).fetchone()
    check("retry từ RUNNING đăng thành công thì target SUCCESS",
          target["status"] == "SUCCESS" and bool(target["external_post_id"]), dict(target))

    again_success = pipeline.retry_publish_target(conn, target_id)
    check("retry bị chặn khi target đã SUCCESS", again_success["ok"] is False, again_success)

    pending_id = ulid()
    conn.execute("""INSERT INTO publish_target (id, post_id, channel_id, created_at, updated_at)
                    VALUES (?,?,?,?,?)""",
                 (pending_id, post["id"], post["channel_id"], now(), now()))
    bad_pending = pipeline.retry_publish_target(conn, pending_id)
    check("retry bị chặn khi target đang PENDING", bad_pending["ok"] is False, bad_pending)

    conn.execute("UPDATE publish_target SET status='CANCELLED', last_error='test' WHERE id=?", (pending_id,))
    bad_cancelled = pipeline.retry_publish_target(conn, pending_id)
    check("retry bị chặn khi target đã CANCELLED", bad_cancelled["ok"] is False, bad_cancelled)
    conn.close()


def test_db_constraints():
    print("\nRàng buộc cơ sở dữ liệu")
    import sqlite3
    conn = connect()
    p = conn.execute("SELECT * FROM post LIMIT 1").fetchone()
    for label, sql, arg in [
        ("CSDL chặn disclosure rỗng", "UPDATE post SET disclosure_text=? WHERE id=?", ""),
        ("CSDL chặn caption quá 500 ký tự", "UPDATE post SET caption_final=? WHERE id=?", "x" * 501),
    ]:
        try:
            conn.execute(sql, (arg, p["id"]))
            check(label, False, "cập nhật lọt qua")
        except sqlite3.IntegrityError as e:
            # Phải đúng là CHECK constraint, không phải lỗi khác tình cờ ném ra.
            check(label, "CHECK constraint failed" in str(e), str(e))
        except Exception as e:
            check(label, False, f"sai loại lỗi: {type(e).__name__}: {e}")
    conn.close()


def test_publish_target_schema():
    print("\npublish_target schema")
    conn = connect()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(publish_target)").fetchall()}
    expected = {"id", "post_id", "channel_id", "status", "scheduled_at",
                "external_post_id", "last_error", "attempt_count",
                "created_at", "updated_at"}
    check("publish_target có đủ cột", expected <= cols, cols)

    product = conn.execute("SELECT id FROM product LIMIT 1").fetchone()
    channel = conn.execute("SELECT id FROM channel LIMIT 1").fetchone()
    campaign = conn.execute("SELECT id FROM campaign LIMIT 1").fetchone()
    post_id = ulid()
    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, variant_code,
                    caption_body, disclosure_text, caption_final, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                 (post_id, product["id"], channel["id"], campaign["id"], "A",
                  "thân bài", "nhãn tiếp thị", "thân bài", now(), now()))

    target_id = ulid()
    conn.execute("""INSERT INTO publish_target (id, post_id, channel_id, created_at, updated_at)
                    VALUES (?,?,?,?,?)""",
                 (target_id, post_id, channel["id"], now(), now()))
    row = conn.execute("SELECT * FROM publish_target WHERE id=?", (target_id,)).fetchone()
    check("status mặc định PENDING", row["status"] == "PENDING", row["status"])
    check("attempt_count mặc định 0", row["attempt_count"] == 0, row["attempt_count"])
    check("external_post_id mặc định NULL", row["external_post_id"] is None)
    conn.close()


def test_post_channel_selection_schema():
    print("\npost_channel_selection schema")
    conn = connect()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(post_channel_selection)").fetchall()}
    check("post_channel_selection có đủ cột", {"post_id", "channel_id", "created_at"} <= cols, cols)

    product = conn.execute("SELECT id FROM product LIMIT 1").fetchone()
    channel = conn.execute("SELECT id FROM channel LIMIT 1").fetchone()
    campaign = conn.execute("SELECT id FROM campaign LIMIT 1").fetchone()
    post_id = ulid()
    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, variant_code,
                    caption_body, disclosure_text, caption_final, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                 (post_id, product["id"], channel["id"], campaign["id"], "A",
                  "thân bài", "nhãn tiếp thị", "thân bài", now(), now()))
    conn.execute("INSERT INTO post_channel_selection (post_id, channel_id, created_at) VALUES (?,?,?)",
                 (post_id, channel["id"], now()))
    import sqlite3
    try:
        conn.execute("INSERT INTO post_channel_selection (post_id, channel_id, created_at) VALUES (?,?,?)",
                     (post_id, channel["id"], now()))
        check("PK (post_id, channel_id) chặn trùng lặp", False, "insert trùng lọt qua")
    except sqlite3.IntegrityError as e:
        check("PK (post_id, channel_id) chặn trùng lặp", "UNIQUE constraint failed" in str(e), str(e))
    conn.close()


def test_publisher_media_list():
    print("\nPublisher nhận danh sách media")
    from acp.adapters.base import Publisher
    ch = MockThreads(seed=1)
    check("MockThreads là Publisher", isinstance(ch, Publisher))

    result = ch.publish({}, "caption ngắn", media=["https://img.example/a.jpg"])
    check("publish 1 ảnh trả về PublishResult", bool(result.external_post_id))

    try:
        ch.publish({}, "caption ngắn", media=["https://img.example/a.jpg", "https://img.example/b.jpg"])
        check("publish nhiều ảnh với Threads phải báo lỗi", False, "không ném lỗi")
    except ValueError as e:
        check("publish nhiều ảnh với Threads phải báo lỗi", True, str(e))

    result_empty = ch.publish({}, "caption ngắn", media=None)
    check("publish không ảnh (media=None) trả về PublishResult", bool(result_empty.external_post_id))


def test_publish_result_native_label_field():
    print("\nPublishResult có native_label_status")
    from acp.adapters.base import PublishResult
    old_style = PublishResult(external_post_id="p1", published_at="2026-01-01T00:00:00")
    check("constructor cũ (không native_label_status) vẫn hợp lệ",
          old_style.native_label_status == "not_attempted", old_style.native_label_status)
    new_style = PublishResult(external_post_id="p2", published_at="2026-01-01T00:00:00",
                               native_label_status="applied")
    check("field mới nhận giá trị truyền vào", new_style.native_label_status == "applied")


def test_mock_facebook_publisher():
    print("\nMockFacebookPublisher")
    from acp.adapters.mock import MockFacebookPublisher
    from acp.adapters.base import Publisher, ContentViolationError as _CVE

    pub = MockFacebookPublisher(seed=1)
    check("là Publisher", isinstance(pub, Publisher))
    check("platform đúng", pub.platform == "facebook")

    result = pub.publish({}, "caption", media=["https://img.example/a.jpg"])
    check("publish 1 ảnh trả về PublishResult", bool(result.external_post_id))
    check("native_label_status mặc định applied", result.native_label_status == "applied")

    result2 = pub.publish({}, "caption", media=["https://img.example/a.jpg",
                                                  "https://img.example/b.jpg"])
    check("publish nhiều ảnh cũng trả về PublishResult", bool(result2.external_post_id))
    check("2 lần publish tạo 2 external_post_id khác nhau",
          result.external_post_id != result2.external_post_id)

    try:
        pub.publish({}, "caption", media=[])
        check("0 ảnh phải bị chặn", False, "không ném lỗi")
    except _CVE:
        check("0 ảnh phải bị chặn", True)

    try:
        pub.publish({}, "caption", media=["u"] * 11)
        check("quá 10 ảnh phải bị chặn", False, "không ném lỗi")
    except _CVE:
        check("quá 10 ảnh phải bị chặn", True)

    labeled = MockFacebookPublisher(seed=2, native_label_status="unavailable")
    r3 = labeled.publish({}, "caption", media=["https://img.example/a.jpg"])
    check("native_label_status tham số hoá được", r3.native_label_status == "unavailable")


def test_mock_instagram_publisher():
    print("\nMockInstagramPublisher")
    from acp.adapters.mock import MockInstagramPublisher
    from acp.adapters.base import Publisher, ContentViolationError as _CVE

    pub = MockInstagramPublisher(seed=1)
    check("là Publisher", isinstance(pub, Publisher))
    check("platform đúng", pub.platform == "instagram")

    single = pub.publish({}, "caption", media=["https://img.example/a.jpg"])
    check("publish 1 ảnh (single) trả về PublishResult", bool(single.external_post_id))

    carousel = pub.publish({}, "caption", media=["https://img.example/a.jpg",
                                                   "https://img.example/b.jpg",
                                                   "https://img.example/c.jpg"])
    check("publish carousel (2-10 ảnh) trả về PublishResult", bool(carousel.external_post_id))

    try:
        pub.publish({}, "caption", media=[])
        check("0 ảnh phải bị chặn", False, "không ném lỗi")
    except _CVE:
        check("0 ảnh phải bị chặn", True)

    try:
        pub.publish({}, "caption", media=["u"] * 11)
        check("quá 10 ảnh phải bị chặn", False, "không ném lỗi")
    except _CVE:
        check("quá 10 ảnh phải bị chặn", True)

    try:
        pub.publish({}, "x" * 2201, media=["https://img.example/a.jpg"])
        check("caption quá 2200 ký tự phải bị chặn", False, "không ném lỗi")
    except _CVE:
        check("caption quá 2200 ký tự phải bị chặn", True)


def test_facebook_publisher_validates_before_network():
    print("\nFacebookPublisher validate trước khi gọi mạng")
    from acp.adapters.live import FacebookPublisher
    from acp.adapters.base import Publisher, ContentViolationError as _CVE, AuthError as _AuthError

    pub = FacebookPublisher()
    check("là Publisher", isinstance(pub, Publisher))
    check("platform đúng", pub.platform == "facebook")

    # Validate media TRƯỚC khi chạm self.session -- test này chạy được không
    # cần mạng vì raise xảy ra trước bất kỳ lệnh gọi requests nào.
    try:
        pub.publish({"code": "fb1", "token_encrypted": None}, "caption", media=[])
        check("0 ảnh phải bị chặn trước khi gọi mạng", False, "không ném lỗi")
    except _CVE:
        check("0 ảnh phải bị chặn trước khi gọi mạng", True)

    try:
        pub.publish({"code": "fb1", "token_encrypted": None}, "caption", media=["u"] * 11)
        check("quá 10 ảnh phải bị chặn trước khi gọi mạng", False, "không ném lỗi")
    except _CVE:
        check("quá 10 ảnh phải bị chặn trước khi gọi mạng", True)

    # Token rỗng cũng phải chặn trước khi gọi mạng, đúng pattern ThreadsChannel.
    try:
        pub.publish({"code": "fb1", "token_encrypted": None}, "caption",
                     media=["https://img.example/a.jpg"])
        check("token rỗng phải bị chặn trước khi gọi mạng", False, "không ném lỗi")
    except _AuthError:
        check("token rỗng phải bị chặn trước khi gọi mạng", True)


def test_instagram_publisher_validates_before_network():
    print("\nInstagramPublisher validate trước khi gọi mạng")
    from acp.adapters.live import InstagramPublisher
    from acp.adapters.base import Publisher, ContentViolationError as _CVE, AuthError as _AuthError

    pub = InstagramPublisher()
    check("là Publisher", isinstance(pub, Publisher))
    check("platform đúng", pub.platform == "instagram")

    try:
        pub.publish({"code": "ig1", "token_encrypted": None}, "caption", media=[])
        check("0 ảnh phải bị chặn trước khi gọi mạng", False, "không ném lỗi")
    except _CVE:
        check("0 ảnh phải bị chặn trước khi gọi mạng", True)

    try:
        pub.publish({"code": "ig1", "token_encrypted": None}, "caption", media=["u"] * 11)
        check("quá 10 ảnh phải bị chặn trước khi gọi mạng", False, "không ném lỗi")
    except _CVE:
        check("quá 10 ảnh phải bị chặn trước khi gọi mạng", True)

    try:
        pub.publish({"code": "ig1", "token_encrypted": None}, "x" * 2201,
                     media=["https://img.example/a.jpg"])
        check("caption quá 2200 ký tự phải bị chặn trước khi gọi mạng", False, "không ném lỗi")
    except _CVE:
        check("caption quá 2200 ký tự phải bị chặn trước khi gọi mạng", True)

    try:
        pub.publish({"code": "ig1", "token_encrypted": None}, "caption",
                     media=["https://img.example/a.jpg"])
        check("token rỗng phải bị chặn trước khi gọi mạng", False, "không ném lỗi")
    except _AuthError:
        check("token rỗng phải bị chặn trước khi gọi mạng", True)


def test_publish_post_audits_native_label_status():
    print("\npublish_post ghi audit native_label_requested")
    from acp.adapters.base import Publisher, PublishResult

    class _LabelledPublisher(Publisher):
        platform = "facebook"

        def publish(self, channel_row, caption, media=None):
            return PublishResult(external_post_id="fb_post_1",
                                  published_at=now(), native_label_status="unavailable")

    conn = connect()
    campaign = conn.execute("SELECT id FROM campaign LIMIT 1").fetchone()
    product = conn.execute("SELECT id FROM product LIMIT 1").fetchone()
    channel_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,'ACTIVE',1,999,0,?)""",
                 (channel_id, "fb_audit_test", "facebook", "Audit Test Page", now()))

    # Cần caption hợp lệ (có link + nhãn tiếp thị mặc định) để qua được
    # content.validate() trong approve_post -- xem quy ước tương tự ở
    # test_disabled_channel_does_not_corrupt_status.
    caption = f"Giá tốt hôm nay https://shope.ee/audit-test — {content.DISCLOSURE_DEFAULT}"
    post_id = ulid()
    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, variant_code,
                    caption_body, disclosure_text, caption_final, status, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,'PENDING_REVIEW',?,?)""",
                 (post_id, product["id"], channel_id, campaign["id"], "A",
                  caption, content.DISCLOSURE_DEFAULT, caption, now(), now()))

    res = pipeline.approve_post(conn, post_id)
    check("approve_post thành công", res["ok"], res)
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?",
                 (now(), f"pub:{res['publish_target_id']}"))
    jobs.drain(conn, ctx={"source": MockAccessTrade(),
                          "publishers": {"facebook": _LabelledPublisher()}})

    audit_row = conn.execute(
        "SELECT * FROM audit_log WHERE entity='publish_target' AND action='native_label_requested' "
        "AND entity_id=?", (res["publish_target_id"],)).fetchone()
    check("có audit native_label_requested", audit_row is not None)
    check("audit ghi đúng outcome", "unavailable" in (audit_row["detail"] or ""),
          audit_row["detail"] if audit_row else None)

    conn.close()


def test_publish_post_no_native_label_audit_for_threads():
    print("\npublish_post KHÔNG ghi audit native label cho Threads (not_attempted)")
    conn = connect()
    ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(51))
    check("có job sinh nội dung", len(ids) > 0)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=51)}})
    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
    res = pipeline.approve_post(conn, post["id"])
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?",
                 (now(), f"pub:{res['publish_target_id']}"))
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=51)}})

    audit_row = conn.execute(
        "SELECT * FROM audit_log WHERE entity='publish_target' AND action='native_label_requested' "
        "AND entity_id=?", (res["publish_target_id"],)).fetchone()
    check("Threads (native_label_status mặc định not_attempted) không tạo audit thừa",
          audit_row is None, dict(audit_row) if audit_row else None)
    conn.close()


def test_meta_connection_schema():
    print("\nmeta_connection + channel mở rộng")
    conn = connect()
    mc_cols = {r["name"] for r in conn.execute("PRAGMA table_info(meta_connection)").fetchall()}
    expected_mc = {"id", "provider", "token_encrypted", "meta_user_id", "status",
                   "expires_at", "created_at", "updated_at"}
    check("meta_connection có đủ cột", expected_mc <= mc_cols, mc_cols)

    ch_cols = {r["name"] for r in conn.execute("PRAGMA table_info(channel)").fetchall()}
    expected_ch_new = {"connection_id", "external_account_id", "username", "enabled", "last_sync_at"}
    check("channel có đủ cột mở rộng", expected_ch_new <= ch_cols, ch_cols)

    mc_id = ulid()
    conn.execute("""INSERT INTO meta_connection (id, provider, token_encrypted, meta_user_id,
                    status, created_at, updated_at) VALUES (?,'meta',?,?,'ACTIVE',?,?)""",
                 (mc_id, crypto.encrypt("user_token"), "mock_user_1", now(), now()))
    row = conn.execute("SELECT * FROM meta_connection WHERE id=?", (mc_id,)).fetchone()
    check("meta_connection lưu và đọc lại đúng", row["meta_user_id"] == "mock_user_1")

    channel = conn.execute("SELECT * FROM channel LIMIT 1").fetchone()
    check("channel cũ (Threads) có enabled mặc định 1", channel["enabled"] == 1, channel["enabled"])
    check("channel cũ có connection_id NULL", channel["connection_id"] is None)
    conn.close()


def test_disabled_channel_blocks_new_publish():
    print("\nKênh tắt (enabled=0) không tạo publish job mới")
    conn = connect()
    campaign = conn.execute("SELECT id FROM campaign LIMIT 1").fetchone()
    channel = conn.execute("SELECT id FROM channel LIMIT 1").fetchone()
    product = conn.execute("SELECT id FROM product LIMIT 1").fetchone()
    conn.execute("UPDATE channel SET enabled=0 WHERE id=?", (channel["id"],))

    # Tạo thẳng một post PENDING_REVIEW gắn với kênh đã tắt -- không phụ
    # thuộc vào chấm điểm/random để chắc chắn đúng kênh cần test.
    post_id = ulid()
    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, variant_code,
                    caption_body, disclosure_text, caption_final, status, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,'PENDING_REVIEW',?,?)""",
                 (post_id, product["id"], channel["id"], campaign["id"], "A",
                  "thân bài", "nhãn tiếp thị", "thân bài", now(), now()))

    res = pipeline.approve_post(conn, post_id)
    check("approve_post từ chối kênh đã tắt", res["ok"] is False, res)
    check("không tạo publish_target khi bị từ chối",
          conn.execute("SELECT COUNT(*) FROM publish_target WHERE post_id=?",
                       (post_id,)).fetchone()[0] == 0)
    check("post vẫn ở PENDING_REVIEW, chưa bị đổi sang SCHEDULED",
          conn.execute("SELECT status FROM post WHERE id=?", (post_id,)).fetchone()["status"]
          == "PENDING_REVIEW")

    conn.execute("UPDATE channel SET enabled=1 WHERE id=?", (channel["id"],))
    conn.close()


def test_default_channel_fallback_skips_facebook():
    print("\nKênh mặc định (không truyền channel_code) không được rơi vào Facebook/Instagram")
    conn = connect()
    # "ch1" (kênh Threads có sẵn từ setup()) tình cờ đứng trước "facebook_..."
    # theo thứ tự chữ cái nên tạm ẩn đi để cô lập đúng tình huống thật: mã kênh
    # Threads thật (vd "threads_be", xem run.py) luôn đứng SAU "facebook_...".
    conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE code='ch1'")
    fb_id, th_id = ulid(), ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                 (fb_id, "facebook_1000000000001", "facebook", "FB Page", "ACTIVE", 1, now()))
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                 (th_id, "threads_default_test", "threads", "@default", "ACTIVE", 1, now()))
    try:
        src = MockAccessTrade()
        target = next(p for p in src.fetch_products(limit=50) if p.product_url)
        ctx = {"source": src, "publishers": {}}
        res = pipeline.create_post_for_product(conn, ctx, target.external_product_id, "test")
        check("tạo bài thành công", res.get("ok"), res.get("error"))
        picked = conn.execute(
            "SELECT platform FROM channel WHERE id=(SELECT channel_id FROM post WHERE id=?)",
            (res["post_id"],)).fetchone()
        check("kênh mặc định là Threads, không phải Facebook",
              picked is not None and picked["platform"] == "threads", dict(picked) if picked else None)
    finally:
        # Không DELETE: post vừa tạo có FK channel_id trỏ vào (có thể) fb_id, xoá
        # sẽ vi phạm ràng buộc. Vô hiệu hoá bằng NEEDS_REAUTH để không rò rỉ vào
        # các test khác chạy sau (chỉ lọc status='ACTIVE').
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id IN (?,?)", (fb_id, th_id))
        conn.execute("UPDATE channel SET status='ACTIVE' WHERE code='ch1'")
        conn.close()


def test_create_post_with_multiple_channel_codes():
    print("\nTạo post với nhiều channel_codes -> post_channel_selection đủ N dòng, kênh đầu là kênh chính")
    conn = connect()
    fb_id, ig_id = ulid(), ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                 (fb_id, "fb_multi_test", "facebook", "FB Multi Test", "ACTIVE", 1, now()))
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                 (ig_id, "ig_multi_test", "instagram", "IG Multi Test", "ACTIVE", 1, now()))
    try:
        src = MockAccessTrade()
        target = next(p for p in src.fetch_products(limit=50) if p.product_url)
        ctx = {"source": src, "publishers": {}}
        res = pipeline.create_post_for_product(
            conn, ctx, target.external_product_id, "test",
            channel_codes=["ch1", "fb_multi_test", "ig_multi_test"])
        check("tạo bài thành công", res.get("ok"), res.get("error"))

        post = conn.execute("SELECT * FROM post WHERE id=?", (res["post_id"],)).fetchone()
        ch1 = conn.execute("SELECT id FROM channel WHERE code='ch1'").fetchone()
        check("post.channel_id = kênh đầu tiên trong danh sách (ch1)",
              post["channel_id"] == ch1["id"], post["channel_id"])

        selections = conn.execute(
            "SELECT channel_id FROM post_channel_selection WHERE post_id=?", (post["id"],)).fetchall()
        check("đủ 3 dòng post_channel_selection", len(selections) == 3, len(selections))
        selected_ids = {r["channel_id"] for r in selections}
        check("đúng bộ 3 kênh được chọn", selected_ids == {ch1["id"], fb_id, ig_id}, selected_ids)
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id IN (?,?)", (fb_id, ig_id))
        conn.close()


def test_create_post_multiple_channel_codes_rejects_disabled_channel():
    print("\nTạo post với 1 kênh bị disabled trong danh sách -> lỗi rõ ràng, không tạo post")
    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                 (fb_id, "fb_disabled_test", "facebook", "FB Disabled Test", "ACTIVE", 0, now()))
    try:
        src = MockAccessTrade()
        target = next(p for p in src.fetch_products(limit=50) if p.product_url)
        ctx = {"source": src, "publishers": {}}
        before = conn.execute("SELECT COUNT(*) FROM post").fetchone()[0]
        res = pipeline.create_post_for_product(
            conn, ctx, target.external_product_id, "test",
            channel_codes=["ch1", "fb_disabled_test"])
        check("tạo bài thất bại vì có kênh disabled", res.get("ok") is False, res)
        check("thông báo lỗi nêu rõ tên kênh", "fb_disabled_test" in (res.get("error") or ""), res.get("error"))
        after = conn.execute("SELECT COUNT(*) FROM post").fetchone()[0]
        check("không tạo post nào (tất-cả-hoặc-không-gì)", before == after, (before, after))
    finally:
        conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id=?", (fb_id,))
        conn.close()


def test_create_post_blocked_for_disabled_channel():
    print("\nTạo bài (create_post_for_product) bị chặn khi kênh đích đã tắt (enabled=0)")
    conn = connect()
    conn.execute("UPDATE channel SET enabled=0 WHERE code='ch1'")
    try:
        src = MockAccessTrade()
        target = next(p for p in src.fetch_products(limit=50) if p.product_url)
        before = conn.execute("SELECT COUNT(*) FROM post").fetchone()[0]
        ctx = {"source": src, "publishers": {}}
        res = pipeline.create_post_for_product(conn, ctx, target.external_product_id, "test",
                                                channel_code="ch1")
        check("tạo bài bị từ chối khi kênh đích đã tắt", res.get("ok") is False, res)
        after = conn.execute("SELECT COUNT(*) FROM post").fetchone()[0]
        check("không tạo post nào khi bị từ chối", after == before, (before, after))
    finally:
        conn.execute("UPDATE channel SET enabled=1 WHERE code='ch1'")
        conn.close()


def test_plan_content_filters_to_threads_only():
    print("\nplan_content chỉ nhắm kênh Threads, không pha loãng quota bởi Facebook/Instagram")
    conn = connect()
    # Đo bằng cách RÌNH gọi pipeline.enqueue (không dựa vào id trả về của
    # plan_content/job đã ghi xuống DB) -- idempotency_key của GENERATE_CONTENT
    # chỉ khoá theo product_id+variant, KHÔNG theo channel_id, nên nếu chỉ đếm
    # job thật sự được tạo, việc kênh facebook (niches rỗng giống ch1) chọn
    # trùng đúng những sản phẩm ch1 đã enqueue trước đó trong cùng vòng lặp sẽ
    # khiến enqueue() trả về 0 (trùng key) và bug bị che giấu dù code chưa sửa.
    # Rình lời gọi enqueue() nắm được channel_id ở MỌI lần thử, kể cả lần bị
    # dedup, nên phép đo miễn nhiễm với hiệu ứng đó -- và cho phép đếm ĐÚNG số
    # lần thử nhắm kênh Threads để so sánh quota trước/sau khi thêm Facebook.
    original_enqueue = pipeline.enqueue

    def _threads_attempts():
        calls = []

        def _spy(conn_, job_type, payload, **kw):
            calls.append((job_type, payload.get("channel_id")))
            return original_enqueue(conn_, job_type, payload, **kw)

        pipeline.enqueue = _spy
        try:
            pipeline.plan_content(conn, "test", limit=10, rng=random.Random(99))
        finally:
            pipeline.enqueue = original_enqueue
        return [cid for jt, cid in calls if jt == "GENERATE_CONTENT"]

    baseline = _threads_attempts()  # chỉ có ch1 (Threads) tồn tại lúc này
    check("có lần thử enqueue GENERATE_CONTENT (baseline)", len(baseline) > 0, baseline)

    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled, created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                 (fb_id, "facebook_plan_test", "facebook", "FB Page", "ACTIVE", 1, now()))
    try:
        with_facebook = _threads_attempts()
        check("không lần thử nào nhắm kênh Facebook vừa import",
              all(cid != fb_id for cid in with_facebook), with_facebook)
        threads_attempts_with_fb = [cid for cid in with_facebook if cid != fb_id]
        check("quota per_channel của Threads không bị pha loãng bởi kênh Facebook",
              len(threads_attempts_with_fb) == len(baseline),
              (len(baseline), len(threads_attempts_with_fb)))
    finally:
        conn.execute("DELETE FROM channel WHERE id=?", (fb_id,))
        conn.close()


def test_publish_post_missing_publisher_fails_immediately():
    print("\npublish_post: chưa có publisher đăng ký cho platform thì FAILED ngay, không đốt lượt retry")
    conn = connect()
    fb_id = ulid()
    conn.execute("""INSERT INTO channel (id, code, platform, handle, status, enabled,
                    daily_post_cap, min_gap_minutes, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (fb_id, "facebook_missing_pub", "facebook", "FB Page", "ACTIVE", 1, 12, 90, now()))
    try:
        src = MockAccessTrade()
        target = next(p for p in src.fetch_products(limit=50) if p.product_url)
        ctx = {"source": src, "publishers": {}}
        created = pipeline.create_post_for_product(conn, ctx, target.external_product_id, "test",
                                                    channel_code="facebook_missing_pub")
        check("tạo bài cho kênh facebook (chỉ định channel_code tường minh) thành công",
              created.get("ok"), created)

        res = pipeline.approve_post(conn, created["post_id"])
        check("duyệt bài thành công", res.get("ok"), res)
        target_id = res["publish_target_id"]
        conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?", (now(), f"pub:{target_id}"))

        jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=99)}})

        job = conn.execute("SELECT status, attempt_count FROM job_queue WHERE idempotency_key=?",
                           (f"pub:{target_id}",)).fetchone()
        check("job PUBLISH_POST FAILED ngay từ lần thử đầu tiên (không retry)",
              job is not None and job["status"] == "FAILED" and job["attempt_count"] == 1,
              dict(job) if job else None)

        tgt = conn.execute("SELECT status, last_error FROM publish_target WHERE id=?", (target_id,)).fetchone()
        check("publish_target FAILED", tgt["status"] == "FAILED", tgt["status"])
        check("last_error rõ ràng (nêu tên platform), không phải KeyError cụt lủn",
              bool(tgt["last_error"]) and "facebook" in tgt["last_error"] and "publisher" in tgt["last_error"].lower(),
              tgt["last_error"])
    finally:
        conn.close()


def test_publish_post_blocks_disabled_channel():
    print("\npublish_post từ chối đăng khi kênh bị tắt sau khi target đã SCHEDULED")
    conn = connect()
    ids = pipeline.plan_content(conn, "test", limit=1, rng=random.Random(81))
    check("có job sinh nội dung", len(ids) > 0)
    job = conn.execute("SELECT payload FROM job_queue WHERE id=?", (ids[0],)).fetchone()
    gen_payload = json.loads(job["payload"])
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=81)}})
    # Lấy đúng bài vừa sinh ra từ job trên (product_id+channel_id) -- KHÔNG chọn
    # "PENDING_REVIEW đầu tiên tìm thấy" một cách mù quáng: DB dùng chung xuyên
    # suốt cả file test này có thể còn sót bài PENDING_REVIEW từ test khác (ví
    # dụ test_disabled_channel_blocks_new_publish cố ý không approve xong bài nó
    # tạo), nhặt nhầm bài đó thì caption không hợp lệ, approve_post sẽ báo lỗi
    # sai chủ đề với test này.
    post = conn.execute(
        "SELECT * FROM post WHERE product_id=? AND channel_id=? AND status='PENDING_REVIEW' "
        "ORDER BY created_at DESC LIMIT 1",
        (gen_payload["product_id"], gen_payload["channel_id"])).fetchone()
    check("tìm được đúng bài vừa sinh", post is not None, gen_payload)
    res = pipeline.approve_post(conn, post["id"])
    check("duyệt thành công trước khi tắt kênh", res.get("ok"), res)
    target_id = res["publish_target_id"]
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?", (now(), f"pub:{target_id}"))

    conn.execute("UPDATE channel SET enabled=0 WHERE id=?", (post["channel_id"],))
    try:
        jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=82)}})
        target = conn.execute("SELECT status, last_error FROM publish_target WHERE id=?", (target_id,)).fetchone()
        check("target FAILED (không SUCCESS) khi kênh đã bị tắt trước lúc đăng",
              target["status"] == "FAILED", target["status"])
        check("last_error nêu rõ lý do kênh bị tắt",
              "tắt" in (target["last_error"] or "").lower(), target["last_error"])
    finally:
        conn.execute("UPDATE channel SET enabled=1 WHERE id=?", (post["channel_id"],))
        conn.close()


def test_disabled_channel_does_not_corrupt_status():
    print("\npublish_post: tắt kênh (enabled=0) không được đẩy channel.status sang NEEDS_REAUTH")
    conn = connect()
    # Tạo thẳng một post PENDING_REVIEW gắn với kênh đang ACTIVE + enabled --
    # không phụ thuộc vào plan_content/scoring/idempotency-key (dễ vỡ khi chạy
    # sau nhiều test khác đã tiêu thụ hết sản phẩm "recent" -- xem
    # test_disabled_channel_blocks_new_publish để biết cách làm tương tự).
    campaign = conn.execute("SELECT id FROM campaign LIMIT 1").fetchone()
    channel = conn.execute("SELECT id FROM channel WHERE code='ch1'").fetchone()
    product = conn.execute("SELECT id FROM product LIMIT 1").fetchone()
    caption = f"Giá tốt hôm nay https://shope.ee/test-link — {content.DISCLOSURE_DEFAULT}"
    post_id = ulid()
    conn.execute("""INSERT INTO post (id, product_id, channel_id, campaign_id, variant_code,
                    caption_body, disclosure_text, caption_final, status, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,'PENDING_REVIEW',?,?)""",
                 (post_id, product["id"], channel["id"], campaign["id"], "A",
                  caption, content.DISCLOSURE_DEFAULT, caption, now(), now()))

    res = pipeline.approve_post(conn, post_id)
    check("duyệt thành công trước khi tắt kênh (kênh đang ACTIVE, enabled)", res.get("ok"), res)
    target_id = res["publish_target_id"]
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?", (now(), f"pub:{target_id}"))

    # Operator bấm "Tắt" ở /kenh -- kênh vẫn ACTIVE (token còn tốt), chỉ enabled=0.
    conn.execute("UPDATE channel SET enabled=0 WHERE id=?", (channel["id"],))
    try:
        jobs.drain(conn, ctx={"source": MockAccessTrade(), "publishers": {"threads": MockThreads(seed=92)}})

        target = conn.execute("SELECT status FROM publish_target WHERE id=?", (target_id,)).fetchone()
        check("publish_target FAILED (không SUCCESS) khi kênh đã bị tắt",
              target["status"] == "FAILED", target["status"])

        ch_after = conn.execute("SELECT status, enabled FROM channel WHERE id=?", (channel["id"],)).fetchone()
        check("channel.status VẪN LÀ ACTIVE -- tắt kênh không phải lỗi xác thực, không được "
              "đẩy sang NEEDS_REAUTH (Threads không có cơ chế tự phục hồi từ trạng thái này)",
              ch_after["status"] == "ACTIVE", ch_after["status"])
        check("channel.enabled vẫn là 0 (sanity check, không ai tự bật lại)",
              ch_after["enabled"] == 0, ch_after["enabled"])
    finally:
        conn.execute("UPDATE channel SET enabled=1 WHERE id=?", (channel["id"],))
        conn.close()


def test_caption_override_columns_exist():
    print("\ncột caption theo platform/account đã có trong schema")
    conn = connect()
    post_cols = {r["name"] for r in conn.execute("PRAGMA table_info(post)").fetchall()}
    check("post có cột caption_facebook", "caption_facebook" in post_cols, post_cols)
    check("post có cột caption_instagram", "caption_instagram" in post_cols, post_cols)
    target_cols = {r["name"] for r in conn.execute("PRAGMA table_info(publish_target)").fetchall()}
    check("publish_target có cột caption_override", "caption_override" in target_cols, target_cols)
    conn.close()


def test_resolve_caption_precedence():
    print("\n_resolve_caption: override account > caption theo platform > caption gốc")
    post = {"caption_final": "gốc", "caption_facebook": "riêng facebook", "caption_instagram": None}
    ch_fb = {"platform": "facebook"}
    ch_ig = {"platform": "instagram"}
    ch_th = {"platform": "threads"}

    check("có override account -> dùng override, bất kể platform gì",
          pipeline._resolve_caption(post, {"caption_override": "riêng account"}, ch_fb) == "riêng account")
    check("không override, facebook có caption riêng -> dùng caption riêng facebook",
          pipeline._resolve_caption(post, {"caption_override": None}, ch_fb) == "riêng facebook")
    check("không override, instagram KHÔNG có caption riêng (None) -> rơi về gốc",
          pipeline._resolve_caption(post, {"caption_override": None}, ch_ig) == "gốc")
    check("threads không có cột riêng -> luôn rơi về gốc dù post có caption_facebook",
          pipeline._resolve_caption(post, {"caption_override": None}, ch_th) == "gốc")


if __name__ == "__main__":
    conn = setup(); conn.close()
    test_crypto()
    test_content_guards()
    test_content_validate_platform_max_len()
    test_imaging_compose_skips_watermark_when_handle_none()
    test_scoring()
    test_subid_roundtrip()
    test_conversion_dedup()
    test_update_insights_empty_dict_noop()
    test_job_retry_semantics()
    test_idempotency_and_double_post()
    test_daily_cap()
    test_next_slot_and_daily_cap_scoped_per_channel_via_publish_target()
    test_publish_target_failure_semantics()
    test_publish_post_authorror_marks_channel()
    test_retry_publish_target()
    test_publish_post_legacy_payload_compat()
    test_publish_post_malformed_payload_raises()
    test_publish_target_cancelled_on_stale_post_status()
    test_sibling_target_not_cancelled_after_first_target_publishes()
    test_content_violation_does_not_unpublish_already_published_post()
    test_generate_content_writes_post_channel_selection()
    test_fetch_insights_idempotency_key_per_target_not_per_post()
    test_approve_post_multi_channel_creates_n_targets()
    test_approve_post_channel_ids_none_falls_back_to_post_channel_id()
    test_approve_post_rejects_disabled_channel_in_list_creates_no_target()
    test_fetch_insights_legacy_payload_falls_back_to_post_thread_id()
    test_legacy_payload_does_not_resurrect_cancelled_target()
    test_retry_publish_target_recovers_running()
    test_db_constraints()
    test_publish_target_schema()
    test_post_channel_selection_schema()
    test_publisher_media_list()
    test_publish_result_native_label_field()
    test_mock_facebook_publisher()
    test_mock_instagram_publisher()
    test_facebook_publisher_validates_before_network()
    test_instagram_publisher_validates_before_network()
    test_publish_post_audits_native_label_status()
    test_publish_post_no_native_label_audit_for_threads()
    test_meta_connection_schema()
    test_disabled_channel_blocks_new_publish()
    test_default_channel_fallback_skips_facebook()
    test_create_post_with_multiple_channel_codes()
    test_create_post_multiple_channel_codes_rejects_disabled_channel()
    test_create_post_blocked_for_disabled_channel()
    test_plan_content_filters_to_threads_only()
    test_publish_post_missing_publisher_fails_immediately()
    test_publish_post_blocks_disabled_channel()
    test_disabled_channel_does_not_corrupt_status()
    test_caption_override_columns_exist()
    test_resolve_caption_precedence()
    print(f"\n{len(PASS)} đạt, {len(FAIL)} hỏng")
    if FAIL:
        print("Hỏng: " + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
