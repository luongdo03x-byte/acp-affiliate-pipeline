"""An Auto plan that cannot publish must surface, not defer in silence.

`_defer` deliberately spends no retry budget, which is right for a real rate
limit but wrong for a condition that never clears on its own -- a catalog past
its sync window is the case that stalled 11 publish jobs for five days without
a single operator-visible signal. Once a slot is well past its scheduled time
it can no longer be published as planned, so it belongs back in /duyet.
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from acp.core import auto_post_plans, db


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class OverdueAutoTargetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "stale.db")
        db.init_db()
        self.conn = db.connect()
        self.now = datetime.now(timezone.utc)
        stamp = _iso(self.now)
        self.conn.execute(
            "INSERT INTO campaign (id,code,name,is_active,created_at) VALUES ('c','gd2026','C',1,?)",
            (stamp,),
        )
        self.conn.execute(
            """INSERT INTO channel (id,code,platform,handle,status,enabled,niches,
                   auto_schedule_enabled,daily_post_target,daily_post_cap,
                   posting_timezone,posting_slots,created_at)
               VALUES ('ch','threads-main','threads','@a','ACTIVE',1,'[]',1,2,3,
                       'Asia/Bangkok','["09:30"]',?)""",
            (stamp,),
        )
        self.conn.execute(
            """INSERT INTO product (id,source,merchant,external_product_id,name,
                   current_price,commission_value,category_code,product_url,
                   is_available,created_at,updated_at)
               VALUES ('p','manual_shopee','shopee.vn','1','SP',100000,5000,'khac',
                       'https://shopee.vn/product/1/1',1,?,?)""",
            (stamp, stamp),
        )

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_path
        self.tmp.cleanup()

    def _target(self, *, hours_overdue: float, status: str = "SCHEDULED") -> str:
        stamp = _iso(self.now)
        scheduled = _iso(self.now - timedelta(hours=hours_overdue))
        self.conn.execute(
            """INSERT INTO post (id,campaign_id,product_id,channel_id,status,variant_code,
                   caption_body,disclosure_text,caption_final,scheduled_at,
                   created_at,updated_at)
               VALUES ('po','c','p','ch','SCHEDULED','price_drop','Caption','#ad',
                       'Caption',?,?,?)""",
            (scheduled, stamp, stamp),
        )
        self.conn.execute(
            """INSERT INTO publish_target (id,post_id,channel_id,status,scheduled_at,
                   auto_scheduled,created_at,updated_at)
               VALUES ('t','po','ch',?,?,1,?,?)""",
            (status, scheduled, stamp, stamp),
        )
        return "t"

    def _post(self):
        return self.conn.execute("SELECT * FROM post WHERE id='po'").fetchone()

    def test_target_well_past_its_slot_is_reported_overdue(self):
        self._target(hours_overdue=auto_post_plans.OVERDUE_GRACE_HOURS + 1)

        self.assertTrue(auto_post_plans.is_overdue(self.conn, "t", now_utc=self.now))

    def test_target_only_slightly_late_is_not_overdue(self):
        self._target(hours_overdue=0.5)

        self.assertFalse(auto_post_plans.is_overdue(self.conn, "t", now_utc=self.now))

    def test_surfacing_sends_the_post_back_to_review_with_a_readable_reason(self):
        self._target(hours_overdue=auto_post_plans.OVERDUE_GRACE_HOURS + 1)

        auto_post_plans.surface_overdue(self.conn, "t", "product_sync_stale", now_utc=self.now)

        post = self._post()
        self.assertEqual(post["status"], "PENDING_REVIEW")
        self.assertIn("product_sync_stale", post["reject_reason"] or "")

    def test_surfacing_cancels_the_target_so_it_stops_burning_worker_passes(self):
        self._target(hours_overdue=auto_post_plans.OVERDUE_GRACE_HOURS + 1)

        auto_post_plans.surface_overdue(self.conn, "t", "product_sync_stale", now_utc=self.now)

        target = self.conn.execute("SELECT * FROM publish_target WHERE id='t'").fetchone()
        self.assertEqual(target["status"], "CANCELLED")

    def test_surfacing_never_touches_a_post_already_published(self):
        """A sibling target may already be live; rescuing this slot must not retract it."""
        self._target(hours_overdue=auto_post_plans.OVERDUE_GRACE_HOURS + 1)
        self.conn.execute("UPDATE post SET status='PUBLISHED' WHERE id='po'")

        auto_post_plans.surface_overdue(self.conn, "t", "product_sync_stale", now_utc=self.now)

        self.assertEqual(self._post()["status"], "PUBLISHED")

    def test_surfacing_is_idempotent(self):
        self._target(hours_overdue=auto_post_plans.OVERDUE_GRACE_HOURS + 1)

        auto_post_plans.surface_overdue(self.conn, "t", "product_sync_stale", now_utc=self.now)
        auto_post_plans.surface_overdue(self.conn, "t", "product_sync_stale", now_utc=self.now)

        self.assertEqual(self._post()["status"], "PENDING_REVIEW")


if __name__ == "__main__":
    unittest.main()


class OverdueGuardCoversManualTargetsTests(OverdueAutoTargetTests):
    """A slot days past its time must not publish, whoever scheduled it.

    Reclaiming a stuck worker returns old PUBLISH_POST jobs to the queue. Most
    of the ones this system actually stranded were `auto_scheduled=0`, which
    never reaches the Auto reconcile path -- without a guard covering them the
    recovery itself would blast days-old captions and prices onto live accounts.
    """

    def _install(self):
        from acp.core import auto_post_runtime, jobs, pipeline

        auto_post_runtime._INSTALLED = False
        self.published = []
        pipeline.publish_post = lambda conn, payload, ctx: self.published.append(payload)
        jobs._handlers["PUBLISH_POST"] = pipeline.publish_post
        auto_post_runtime.install()
        return jobs._handlers["PUBLISH_POST"]

    def test_manual_target_days_late_is_surfaced_not_published(self):
        self._target(hours_overdue=96, status="SCHEDULED")
        self.conn.execute("UPDATE publish_target SET auto_scheduled=0 WHERE id='t'")
        handler = self._install()

        handler(self.conn, {"publish_target_id": "t", "post_id": "po"}, {})

        self.assertEqual(self.published, [])
        self.assertEqual(self._post()["status"], "PENDING_REVIEW")

    def test_manual_target_on_time_still_publishes_normally(self):
        self._target(hours_overdue=0, status="SCHEDULED")
        self.conn.execute("UPDATE publish_target SET auto_scheduled=0 WHERE id='t'")
        handler = self._install()

        handler(self.conn, {"publish_target_id": "t", "post_id": "po"}, {})

        self.assertEqual(len(self.published), 1)
        self.assertEqual(self._post()["status"], "SCHEDULED")
