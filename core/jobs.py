"""Hàng đợi công việc (BRD FR7).

Không dùng RabbitMQ/Kafka. Ở mức 1.000 bài/ngày (~0,7 bài/phút) một bảng trong
CSDL quan hệ là đủ, và giảm được một thành phần hạ tầng phải vận hành.

SQLite: BEGIN IMMEDIATE khoá ghi toàn bộ CSDL -- an toàn nhưng chỉ một worker
ghi tại một thời điểm. PostgreSQL: đổi sang
    SELECT ... WHERE status='READY' AND run_after <= now()
    ORDER BY priority DESC, run_after FOR UPDATE SKIP LOCKED LIMIT ?
thì nhiều worker chạy song song thật.
"""
import json
import socket
import os
import traceback
from datetime import datetime, timedelta, timezone

from .db import now, transaction

BACKOFF_MINUTES = [1, 5, 25]
WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"

# Một job RUNNING lâu hơn ngần này coi như worker đã chết giữa chừng. Worker
# chạy dưới systemd oneshot có TimeoutStartSec, nên bị SIGTERM sau khi claim là
# chuyện bình thường -- không có ai trả job về thì nó nằm RUNNING vĩnh viễn.
RUNNING_LEASE_MINUTES = 15

# _defer cố ý không tiêu ngân sách retry: chạm hạn mức không phải lỗi của mình.
# Nhưng có điều kiện không bao giờ tự hết (catalog quá hạn đồng bộ), và khi đó
# job hoãn lại mỗi giờ mãi mãi mà không ai biết. Sau ngần này lần hoãn liên
# tiếp, đẩy job ra FAILED để operator nhìn thấy ở /vanhanh.
MAX_CONSECUTIVE_DEFERS = 12

_handlers = {}


def handler(job_type: str):
    def deco(fn):
        _handlers[job_type] = fn
        return fn
    return deco


def enqueue(conn, job_type: str, payload: dict, *, priority: int = 0,
            run_after: str = None, idempotency_key: str = None) -> int:
    """Trả về id job, hoặc 0 nếu idempotency_key đã tồn tại.

    Khoá idempotency là tuyến phòng thủ thứ nhất chống đăng trùng: cùng một
    post_id không bao giờ tạo được hai job publish.
    """
    if idempotency_key:
        row = conn.execute("SELECT id FROM job_queue WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        if row:
            return 0
    cur = conn.execute("""
        INSERT INTO job_queue (job_type, payload, status, priority, run_after,
                               idempotency_key, created_at, updated_at)
        VALUES (?,?,'READY',?,?,?,?,?)
    """, (job_type, json.dumps(payload, ensure_ascii=False), priority,
          run_after or now(), idempotency_key, now(), now()))
    return cur.lastrowid


def reclaim_stale(conn, *, now_utc: datetime = None, lease_minutes: int = None) -> int:
    """Trả các job RUNNING quá hạn thuê về READY. Trả về số job đã thu hồi.

    Tiêu một lượt attempt mỗi lần thu hồi: nếu chính job đó là thứ làm worker
    chết, nó sẽ hết ngân sách và FAILED thay vì giết worker mãi mãi.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    minutes = RUNNING_LEASE_MINUTES if lease_minutes is None else lease_minutes
    cutoff = (now_utc - timedelta(minutes=minutes)).isoformat(timespec="seconds")

    reclaimed = 0
    with transaction(conn):
        rows = conn.execute(
            """SELECT id, attempt_count, max_attempts, job_type FROM job_queue
               WHERE status = 'RUNNING' AND COALESCE(locked_at, '') <= ?""",
            (cutoff,),
        ).fetchall()
        for row in rows:
            attempts = int(row["attempt_count"] or 0) + 1
            reason = (
                f"Worker treo hoặc bị dừng giữa chừng sau {minutes} phút "
                f"({row['job_type']}); thu hồi lượt {attempts}."
            )
            if attempts >= int(row["max_attempts"] or 3):
                conn.execute(
                    """UPDATE job_queue SET status='FAILED', attempt_count=?, last_error=?,
                           locked_at=NULL, locked_by=NULL, updated_at=? WHERE id=?""",
                    (attempts, reason[:500], now(), row["id"]),
                )
            else:
                conn.execute(
                    """UPDATE job_queue SET status='READY', attempt_count=?, last_error=?,
                           locked_at=NULL, locked_by=NULL, run_after=?, updated_at=? WHERE id=?""",
                    (attempts, reason[:500], now(), now(), row["id"]),
                )
            reclaimed += 1
    return reclaimed


def claim(conn, limit: int = 10, *, skip_publish: bool = False):
    """Lấy job và đánh dấu RUNNING trong cùng một giao dịch."""
    with transaction(conn):
        publish_filter = "AND job_type != 'PUBLISH_POST'" if skip_publish else ""
        rows = conn.execute(f"""
            SELECT * FROM job_queue
            WHERE status = 'READY' AND run_after <= ?
            {publish_filter}
            ORDER BY priority DESC, run_after ASC
            LIMIT ?
        """, (now(), limit)).fetchall()
        claimed = []
        for r in rows:
            conn.execute("UPDATE job_queue SET status='RUNNING', locked_at=?, locked_by=?, updated_at=? WHERE id=?",
                         (now(), WORKER_ID, now(), r["id"]))
            claimed.append(dict(r))
    return claimed


def _defer(conn, job, minutes: int, reason: str) -> None:
    """Hoãn mà KHÔNG tăng attempt_count -- dùng cho rate limit. Chạm hạn mức
    không phải lỗi của mình, không nên tiêu vào ngân sách retry.

    Nhưng đếm số lần hoãn liên tiếp: hạn mức thật sẽ hết sau vài lần, còn điều
    kiện hỏng (catalog quá hạn đồng bộ) thì không bao giờ tự hết. Quá ngưỡng
    thì cho FAILED để operator nhìn thấy, thay vì im lặng lặp mãi."""
    deferred = int(_job_field(conn, job, "defer_count") or 0) + 1
    if deferred > MAX_CONSECUTIVE_DEFERS:
        conn.execute(
            """UPDATE job_queue SET status='FAILED', defer_count=?, last_error=?,
                   locked_at=NULL, locked_by=NULL, updated_at=? WHERE id=?""",
            (deferred, f"Hoãn {deferred} lần liên tiếp mà không thông: {reason}"[:500],
             now(), job["id"]),
        )
        return
    nxt = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    conn.execute("""UPDATE job_queue SET status='READY', run_after=?, last_error=?, defer_count=?,
                    locked_at=NULL, locked_by=NULL, updated_at=? WHERE id=?""",
                 (nxt, reason[:500], deferred, now(), job["id"]))


def _job_field(conn, job, column: str):
    """Đọc giá trị hiện tại trong CSDL, không tin bản chụp lúc claim."""
    row = conn.execute(f"SELECT {column} FROM job_queue WHERE id=?", (job["id"],)).fetchone()
    return row[column] if row else None


def _fail(conn, job, err: str, retryable: bool) -> None:
    attempts = job["attempt_count"] + 1
    if retryable and attempts < job["max_attempts"]:
        delay = BACKOFF_MINUTES[min(attempts - 1, len(BACKOFF_MINUTES) - 1)]
        nxt = (datetime.now(timezone.utc) + timedelta(minutes=delay)).isoformat(timespec="seconds")
        conn.execute("""UPDATE job_queue SET status='READY', attempt_count=?, run_after=?,
                        last_error=?, locked_by=NULL, updated_at=? WHERE id=?""",
                     (attempts, nxt, err[:500], now(), job["id"]))
    else:
        conn.execute("""UPDATE job_queue SET status='FAILED', attempt_count=?, last_error=?,
                        locked_by=NULL, updated_at=? WHERE id=?""",
                     (attempts, err[:500], now(), job["id"]))


def _release_disabled_publish(conn, job) -> None:
    """Trả job publish về READY khi operator tắt công tắc ngay sau claim."""
    conn.execute("""UPDATE job_queue SET status='READY', locked_at=NULL, locked_by=NULL,
                    updated_at=? WHERE id=?""", (now(), job["id"]))


def run_once(conn, limit: int = 10, ctx: dict = None) -> dict:
    """Chạy một lượt. Trả về thống kê để CLI và test đọc được."""
    from ..adapters.base import RateLimitError, ContentViolationError, AuthError, PublishError
    from .system_settings import publish_worker_enabled

    ctx = ctx or {}
    stats = {"done": 0, "retried": 0, "failed": 0, "deferred": 0, "skipped": 0, "reclaimed": 0}
    # Worker trước có thể đã bị systemd dừng giữa chừng; thu hồi trước khi claim
    # để job treo quay lại hàng đợi thay vì nằm RUNNING vĩnh viễn.
    stats["reclaimed"] = reclaim_stale(conn)
    for job in claim(conn, limit, skip_publish=not publish_worker_enabled(conn)):
        fn = _handlers.get(job["job_type"])
        if not fn:
            _fail(conn, job, f"Không có handler cho loại job '{job['job_type']}'", retryable=False)
            stats["failed"] += 1
            continue
        if job["job_type"] == "PUBLISH_POST" and not publish_worker_enabled(conn):
            _release_disabled_publish(conn, job)
            stats["skipped"] += 1
            continue
        try:
            fn(conn, json.loads(job["payload"]), ctx)
            conn.execute("""UPDATE job_queue SET status='DONE', defer_count=0, locked_by=NULL,
                            updated_at=? WHERE id=?""", (now(), job["id"]))
            stats["done"] += 1

        except RateLimitError as e:
            _defer(conn, job, 60, f"Hoãn vì hạn mức: {e}")
            stats["deferred"] += 1

        except ContentViolationError as e:
            # Không bao giờ retry. Đẩy bài về hàng đợi duyệt để người xem lại --
            # NHƯNG chỉ khi bài chưa từng đăng thành công ở kênh nào khác. Nếu
            # post.status đã là PUBLISHED (một publish_target khác của cùng bài
            # đã SUCCESS -- có thể xảy ra từ sub-project D, một post có N target
            # độc lập), đẩy về PENDING_REVIEW ở đây sẽ: (1) rút bài đang live
            # khỏi báo cáo PUBLISHED dù published_at vẫn còn, (2) khiến các
            # target khác đang chờ tự huỷ oan (điều kiện huỷ ở publish_post()
            # coi PENDING_REVIEW là "bài không còn đăng được"), (3) khi duyệt
            # lại thì kênh đã SUCCESS bị tạo target mới, đăng trùng. Chỉ target
            # bị từ chối cần biết -- nó đã FAILED (_fail bên dưới), operator
            # xem/retry đúng target đó ở /vanhanh, không cần rút cả bài.
            payload = json.loads(job["payload"])
            if payload.get("post_id"):
                post = conn.execute("SELECT status FROM post WHERE id=?", (payload["post_id"],)).fetchone()
                if post and post["status"] != "PUBLISHED":
                    conn.execute("UPDATE post SET status='PENDING_REVIEW', reject_reason=?, updated_at=? WHERE id=?",
                                 (f"Nền tảng từ chối nội dung: {e}", now(), payload["post_id"]))
            _fail(conn, job, str(e), retryable=False)
            stats["failed"] += 1

        except AuthError as e:
            payload = json.loads(job["payload"])
            if payload.get("channel_id"):
                conn.execute("UPDATE channel SET status='NEEDS_REAUTH' WHERE id=?", (payload["channel_id"],))
            _fail(conn, job, f"Token hỏng: {e}", retryable=False)
            stats["failed"] += 1

        except PublishError as e:
            _fail(conn, job, str(e), retryable=True)
            stats["retried" if job["attempt_count"] + 1 < job["max_attempts"] else "failed"] += 1

        except Exception as e:
            _fail(conn, job, f"{e}\n{traceback.format_exc()[:400]}", retryable=True)
            stats["retried" if job["attempt_count"] + 1 < job["max_attempts"] else "failed"] += 1

    return stats


def drain(conn, ctx: dict = None, max_rounds: int = 50) -> dict:
    """Chạy tới khi hết job sẵn sàng. Dùng cho CLI và test."""
    total = {"done": 0, "retried": 0, "failed": 0, "deferred": 0, "skipped": 0, "reclaimed": 0}
    for _ in range(max_rounds):
        s = run_once(conn, limit=25, ctx=ctx)
        for k in total:
            total[k] += s[k]
        if sum(s.values()) == 0:
            break
    return total


def queue_summary(conn):
    return {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM job_queue GROUP BY status").fetchall()}
