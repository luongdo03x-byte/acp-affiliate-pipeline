"""Recovery paths for a queue whose worker can be killed mid-job.

The publish worker runs as a systemd oneshot with a start timeout. When it is
terminated after `claim` but before the handler returns, the job stays RUNNING
forever: nothing else in the system ever moves it back. These tests pin the two
guards that keep a killed worker from silently retiring work.
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from acp.core import db, jobs


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class StaleRunningJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "queue.db")
        db.init_db()
        self.conn = db.connect()
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def _insert_running(self, *, minutes_ago: int, attempt_count: int = 0,
                        max_attempts: int = 3, job_type: str = "PUBLISH_POST") -> int:
        locked = _iso(self.now - timedelta(minutes=minutes_ago))
        cur = self.conn.execute(
            """INSERT INTO job_queue (job_type, payload, status, priority, attempt_count,
                                      max_attempts, run_after, locked_at, locked_by,
                                      created_at, updated_at)
               VALUES (?, '{}', 'RUNNING', 0, ?, ?, ?, ?, 'dead-worker-1', ?, ?)""",
            (job_type, attempt_count, max_attempts, locked, locked, locked, locked),
        )
        return cur.lastrowid

    def _row(self, job_id: int):
        return self.conn.execute("SELECT * FROM job_queue WHERE id=?", (job_id,)).fetchone()

    def test_running_job_past_lease_returns_to_ready(self):
        job_id = self._insert_running(minutes_ago=120)

        reclaimed = jobs.reclaim_stale(self.conn, now_utc=self.now)

        self.assertEqual(reclaimed, 1)
        row = self._row(job_id)
        self.assertEqual(row["status"], "READY")
        self.assertIsNone(row["locked_by"])

    def test_reclaim_burns_one_attempt_so_a_worker_killer_cannot_loop_forever(self):
        job_id = self._insert_running(minutes_ago=120, attempt_count=0)

        jobs.reclaim_stale(self.conn, now_utc=self.now)

        self.assertEqual(self._row(job_id)["attempt_count"], 1)

    def test_reclaim_fails_job_that_exhausted_its_attempts(self):
        job_id = self._insert_running(minutes_ago=120, attempt_count=2, max_attempts=3)

        jobs.reclaim_stale(self.conn, now_utc=self.now)

        row = self._row(job_id)
        self.assertEqual(row["status"], "FAILED")
        self.assertIn("treo", (row["last_error"] or "").lower())

    def test_job_still_inside_its_lease_is_left_alone(self):
        job_id = self._insert_running(minutes_ago=2)

        reclaimed = jobs.reclaim_stale(self.conn, now_utc=self.now)

        self.assertEqual(reclaimed, 0)
        self.assertEqual(self._row(job_id)["status"], "RUNNING")

    def test_run_once_reclaims_before_claiming(self):
        """A stuck job must become runnable again without an operator noticing."""
        job_id = self._insert_running(minutes_ago=120, job_type="NOOP_JOB")
        seen = []
        jobs._handlers["NOOP_JOB"] = lambda conn, payload, ctx: seen.append(payload)
        try:
            jobs.run_once(self.conn, ctx={})
        finally:
            jobs._handlers.pop("NOOP_JOB", None)

        self.assertEqual(len(seen), 1)
        self.assertEqual(self._row(job_id)["status"], "DONE")


class UnboundedDeferTests(unittest.TestCase):
    """A rate-limit defer must not become a silent forever-loop.

    `_defer` deliberately does not burn retry budget, which is right for a real
    rate limit but wrong for a condition that never clears on its own (a stale
    product catalog). After enough consecutive defers the job must surface.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "defer.db")
        db.init_db()
        self.conn = db.connect()

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def _job(self, job_id: int):
        return self.conn.execute("SELECT * FROM job_queue WHERE id=?", (job_id,)).fetchone()

    def test_defer_keeps_job_ready_and_does_not_burn_attempts(self):
        job_id = jobs.enqueue(self.conn, "PUBLISH_POST", {})
        job = self._job(job_id)

        jobs._defer(self.conn, job, 60, "Hoãn vì hạn mức: chờ")

        row = self._job(job_id)
        self.assertEqual(row["status"], "READY")
        self.assertEqual(row["attempt_count"], 0)

    def test_job_deferred_too_many_times_is_failed_for_operator_review(self):
        job_id = jobs.enqueue(self.conn, "PUBLISH_POST", {})

        for _ in range(jobs.MAX_CONSECUTIVE_DEFERS + 1):
            jobs._defer(self.conn, self._job(job_id), 60, "Hoãn vì hạn mức: product_sync_stale")

        row = self._job(job_id)
        self.assertEqual(row["status"], "FAILED")
        self.assertIn("product_sync_stale", row["last_error"] or "")

    def test_successful_run_resets_the_defer_streak(self):
        job_id = jobs.enqueue(self.conn, "NOOP_JOB", {})
        for _ in range(jobs.MAX_CONSECUTIVE_DEFERS - 1):
            jobs._defer(self.conn, self._job(job_id), 60, "Hoãn vì hạn mức: chờ")
        self.conn.execute("UPDATE job_queue SET run_after=? WHERE id=?", (db.now(), job_id))

        jobs._handlers["NOOP_JOB"] = lambda conn, payload, ctx: None
        try:
            jobs.run_once(self.conn, ctx={})
        finally:
            jobs._handlers.pop("NOOP_JOB", None)

        row = self._job(job_id)
        self.assertEqual(row["status"], "DONE")
        self.assertEqual(row["defer_count"], 0)


if __name__ == "__main__":
    unittest.main()
