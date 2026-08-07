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


def claim(conn, limit: int = 10):
    """Lấy job và đánh dấu RUNNING trong cùng một giao dịch."""
    with transaction(conn):
        rows = conn.execute("""
            SELECT * FROM job_queue
            WHERE status = 'READY' AND run_after <= ?
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
    không phải lỗi của mình, không nên tiêu vào ngân sách retry."""
    nxt = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    conn.execute("UPDATE job_queue SET status='READY', run_after=?, last_error=?, locked_by=NULL, updated_at=? WHERE id=?",
                 (nxt, reason[:500], now(), job["id"]))


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


def run_once(conn, limit: int = 10, ctx: dict = None) -> dict:
    """Chạy một lượt. Trả về thống kê để CLI và test đọc được."""
    from ..adapters.base import RateLimitError, ContentViolationError, AuthError, PublishError

    ctx = ctx or {}
    stats = {"done": 0, "retried": 0, "failed": 0, "deferred": 0, "skipped": 0}
    for job in claim(conn, limit):
        fn = _handlers.get(job["job_type"])
        if not fn:
            _fail(conn, job, f"Không có handler cho loại job '{job['job_type']}'", retryable=False)
            stats["failed"] += 1
            continue
        try:
            fn(conn, json.loads(job["payload"]), ctx)
            conn.execute("UPDATE job_queue SET status='DONE', locked_by=NULL, updated_at=? WHERE id=?",
                         (now(), job["id"]))
            stats["done"] += 1

        except RateLimitError as e:
            _defer(conn, job, 60, f"Hoãn vì hạn mức: {e}")
            stats["deferred"] += 1

        except ContentViolationError as e:
            # Không bao giờ retry. Đẩy bài về hàng đợi duyệt để người xem lại.
            payload = json.loads(job["payload"])
            if payload.get("post_id"):
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
    total = {"done": 0, "retried": 0, "failed": 0, "deferred": 0, "skipped": 0}
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
