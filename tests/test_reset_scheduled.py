"""reset_scheduled_posts: đưa bài SCHEDULED chưa đăng về PENDING_REVIEW.

Phủ 4 bất biến:
  - target chưa đăng -> CANCELLED, job READY -> DONE (không còn gì tự đăng);
  - post quay về PENDING_REVIEW (vẫn phải qua tay người ở /duyet);
  - post đã có target SUCCESS thì KHÔNG BAO GIỜ bị đụng tới;
  - regenerate=False giữ nguyên caption; dry_run không ghi gì.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from acp.core import db, pipeline  # noqa: E402

CAPTION = "Quần linen form rộng, giá tốt\nhttps://s.shopee.vn/abc #tiepthilienket"


def _stamp():
    return db.now()


class ResetScheduledPostsTests(unittest.TestCase):
    def setUp(self):
        self.previous_db_path = db.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = os.path.join(self.tmp.name, "reset-scheduled.db")
        db.init_db()
        self.conn = db.connect()
        try:
            c = self.conn
            stamp = _stamp()
            c.execute("INSERT INTO campaign (id, code, name, is_active, created_at) "
                      "VALUES ('camp','gd2026','test',1,?)", (stamp,))
            c.execute("""INSERT INTO channel (id, code, platform, handle, status,
                          enabled, daily_post_cap, min_gap_minutes, niches, created_at)
                        VALUES ('ch','threads_1','threads','@t1','ACTIVE',1,3,90,'[]',?)""",
                      (stamp,))
            for pid in ("post-queue", "post-published", "post-value"):
                channel_id = "ch"
                c.execute("""
                    INSERT INTO post (id, product_id, channel_id, campaign_id,
                                      variant_code, caption_body, disclosure_text,
                                      caption_final, affiliate_link, post_type,
                                      status, scheduled_at, created_at, updated_at)
                    VALUES (?, NULL, ?, 'camp', 'H1', ?, 'Nội dung có tiếp thị liên kết',
                            ?, 'https://s.shopee.vn/abc', ?,'SCHEDULED', ?, ?, ?)
                """, (pid, channel_id, CAPTION, CAPTION,
                      "VALUE" if pid == "post-value" else "SALES",
                      stamp, stamp, stamp))
            slot = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(timespec="seconds")
            for tid, pid in (("target-queue", "post-queue"),
                             ("target-published", "post-published")):
                c.execute("""
                    INSERT INTO publish_target (id, post_id, channel_id, status,
                          scheduled_at, auto_scheduled, created_at, updated_at)
                    VALUES (?, ?, 'ch', 'SCHEDULED', ?, 0, ?, ?)
                """, (tid, pid, slot, stamp, stamp))
                c.execute("""
                    INSERT INTO job_queue (job_type, payload, status, priority,
                          run_after, idempotency_key, created_at, updated_at)
                    VALUES ('PUBLISH_POST', ?, 'READY', 50, ?, ?, ?, ?)
                """, ('{"publish_target_id": "%s"}' % tid, slot,
                      f"pub:{tid}", stamp, stamp))
            # target-đã-đăng-thật: SUCCESS kèm external_post_id
            c.execute("""
                INSERT INTO publish_target (id, post_id, channel_id, status,
                      external_post_id, scheduled_at, auto_scheduled, created_at, updated_at)
                VALUES ('target-done', 'post-published', 'ch', 'SUCCESS',
                        'tg_post_123', ?, 0, ?, ?)
            """, (stamp, stamp, stamp))
            c.commit()
        except Exception:
            self.conn.close()
            raise

    def tearDown(self):
        try:
            self.conn.close()
        except Exception:
            pass
        db.DB_PATH = self.previous_db_path
        self.tmp.cleanup()

    def test_dry_run_counts_without_writes(self):
        summary = pipeline.reset_scheduled_posts(self.conn, regenerate=True, dry_run=True)

        self.assertEqual(summary["posts"], 1)          # chỉ post-queue đủ điều kiện
        self.assertEqual(summary["targets"], 1)
        self.assertEqual(summary["skipped_already_published"], 1)
        statuses = self.conn.execute(
            "SELECT status FROM publish_target WHERE id='target-queue'").fetchone()["status"]
        self.assertEqual(statuses, "SCHEDULED")        # dry-run không ghi

    def test_reset_cancels_target_job_and_bounces_post_to_review(self):
        summary = pipeline.reset_scheduled_posts(self.conn, regenerate=False)

        self.assertEqual(summary["posts"], 1)
        target = self.conn.execute(
            "SELECT status, last_error FROM publish_target WHERE id='target-queue'"
        ).fetchone()
        self.assertEqual(target["status"], "CANCELLED")
        job = self.conn.execute(
            "SELECT status FROM job_queue WHERE idempotency_key='pub:target-queue'"
        ).fetchone()
        self.assertEqual(job["status"], "DONE")
        post = self.conn.execute(
            "SELECT status, scheduled_at FROM post WHERE id='post-queue'").fetchone()
        self.assertEqual(post["status"], "PENDING_REVIEW")
        self.assertIsNone(post["scheduled_at"])
        # caption giữ nguyên vì regenerate=False
        kept = self.conn.execute(
            "SELECT caption_final FROM post WHERE id='post-queue'").fetchone()["caption_final"]
        self.assertEqual(kept, CAPTION)

    def test_already_published_post_is_never_touched(self):
        pipeline.reset_scheduled_posts(self.conn, regenerate=False)

        post = self.conn.execute(
            "SELECT status FROM post WHERE id='post-published'").fetchone()
        self.assertEqual(post["status"], "SCHEDULED")
        live = self.conn.execute(
            "SELECT status FROM publish_target WHERE id='target-published'").fetchone()
        self.assertEqual(live["status"], "SCHEDULED")
        still_ready = self.conn.execute(
            "SELECT status FROM job_queue WHERE idempotency_key='pub:target-published'"
        ).fetchone()["status"]
        self.assertEqual(still_ready, "READY")


if __name__ == "__main__":
    unittest.main(verbosity=2)
