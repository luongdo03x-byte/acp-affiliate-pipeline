"""Test các bất biến mà nếu sai thì mất tiền hoặc mất tài khoản.

    python3 -m acp.tests.test_pipeline
"""
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_tmp = tempfile.mkdtemp()
os.environ["ACP_DB"] = os.path.join(_tmp, "test.db")

from acp.core import db  # noqa: E402
db.DB_PATH = os.environ["ACP_DB"]

from acp.adapters.base import ContentViolationError, PublishError, RateLimitError  # noqa: E402
from acp.adapters.mock import MockAccessTrade, MockThreads  # noqa: E402
from acp.core import attribution, content, crypto, jobs, pipeline, scoring  # noqa: E402
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
    scoring.save_config(conn, scoring.DEFAULT_WEIGHTS, scoring.DEFAULT_FILTERS, "test")
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
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "channel": ch})

    post = conn.execute("SELECT * FROM post WHERE status='PENDING_REVIEW' LIMIT 1").fetchone()
    check("bài sinh ra ở trạng thái chờ duyệt", post is not None)
    res = pipeline.approve_post(conn, post["id"])
    check("duyệt xong thì lên lịch", res["ok"])
    conn.execute("UPDATE job_queue SET run_after=? WHERE idempotency_key=?", (now(), f"pub:{post['id']}"))
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "channel": ch})

    before = len(ch.published)
    # Ép chạy lại đúng job publish đó -- mô phỏng retry sau khi bài đã lên thành công.
    jobs.enqueue(conn, "PUBLISH_POST", {"post_id": post["id"], "channel_id": post["channel_id"]})
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "channel": ch})
    check("chạy lại job publish không đăng bài lần hai", len(ch.published) == before,
          f"{before} → {len(ch.published)}")

    row = conn.execute("SELECT status, thread_id FROM post WHERE id=?", (post["id"],)).fetchone()
    check("bài đã có thread_id sau khi đăng", row["status"] == "PUBLISHED" and row["thread_id"])
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
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "channel": ch})
    for r in conn.execute("SELECT id FROM post WHERE status='PENDING_REVIEW'").fetchall():
        pipeline.approve_post(conn, r["id"])
    conn.execute("UPDATE job_queue SET run_after=? WHERE job_type='PUBLISH_POST' AND status='READY'", (now(),))
    approved = conn.execute("SELECT COUNT(*) FROM post WHERE status='SCHEDULED'").fetchone()[0]
    before = len(ch.published)
    jobs.drain(conn, ctx={"source": MockAccessTrade(), "channel": ch})
    posted = len(ch.published) - before
    check("có đủ bài để thử vượt trần", approved >= 2, f"chỉ có {approved} bài đã lên lịch")
    check("chạm trần thì đăng đúng 1 bài rồi dừng", posted == 1, f"đăng thêm {posted}")
    deferred = conn.execute(
        "SELECT COUNT(*) FROM job_queue WHERE job_type='PUBLISH_POST' AND status='READY' "
        "AND last_error LIKE '%trần%'").fetchone()[0]
    check("phần vượt trần bị hoãn chứ không đánh hỏng", deferred >= 1, f"{deferred} job bị hoãn")
    conn.execute("UPDATE channel SET daily_post_cap = 12")
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

    # Test backward compatibility: bare string (old pipeline.py calling convention)
    result_str = ch.publish({}, "caption ngắn", "https://img.example/a.jpg")
    check("publish chuỗi URL (tương thích ngược) trả về PublishResult", bool(result_str.external_post_id))
    check("publish chuỗi URL tạo bài khác", result_str.external_post_id != result.external_post_id)


if __name__ == "__main__":
    conn = setup(); conn.close()
    test_crypto()
    test_content_guards()
    test_scoring()
    test_subid_roundtrip()
    test_conversion_dedup()
    test_job_retry_semantics()
    test_idempotency_and_double_post()
    test_daily_cap()
    test_db_constraints()
    test_publish_target_schema()
    test_publisher_media_list()
    print(f"\n{len(PASS)} đạt, {len(FAIL)} hỏng")
    if FAIL:
        print("Hỏng: " + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
