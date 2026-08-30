"""Bulk approval for the /duyet queue.

/duyet already has "Bỏ qua tất cả" but no counterpart to accept a whole batch.
When Auto surfaces a backlog (a stale catalog pushed 21 slots back to review in
one go), approving them one form at a time is the slow half of the workflow.

Bulk approve cannot ask for a time per post, so it reuses the same per-channel
hot-slot picker the single-post form uses via `auto_pick_time`.
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from acp.core import db


class ReviewApproveAllTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = {k: os.environ.get(k) for k in
                   ("ACP_ADMIN_PASSWORD", "ACP_ADAPTER", "ACP_SOURCE")}
        os.environ.update(ACP_ADMIN_PASSWORD="test-password",
                          ACP_ADAPTER="mock", ACP_SOURCE="mock")

    @classmethod
    def tearDownClass(cls):
        for key, value in cls.old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def setUp(self):
        from acp.web import create_app

        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "review.db")
        db.init_db()
        self.conn = db.connect()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        stamp = self.now.isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO campaign (id,code,name,is_active,created_at) VALUES ('camp','c','C',1,?)",
            (stamp,),
        )
        for code in ("cha", "chb"):
            self.conn.execute(
                """INSERT INTO channel (id,code,platform,handle,status,enabled,niches,
                       auto_schedule_enabled,daily_post_target,daily_post_cap,
                       posting_timezone,posting_slots,created_at)
                   VALUES (?,?,'threads',?,'ACTIVE',1,'[]',1,2,3,'Asia/Bangkok',
                           '["09:30","20:30"]',?)""",
                (code, f"threads-{code}", f"@{code}", stamp),
            )
        self.conn.execute(
            """INSERT INTO product (id,source,merchant,external_product_id,name,
                   current_price,commission_value,category_code,product_url,
                   is_available,created_at,updated_at)
               VALUES ('p','manual_shopee','shopee.vn','1','SP',100000,5000,'khac',
                       'https://shopee.vn/product/1/1',1,?,?)""",
            (stamp, stamp),
        )
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["uid"] = "operator"
            session["csrf"] = "csrf-test"
        self.csrf = "csrf-test"

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def _pending(self, post_id: str, *, channel_id: str = "cha", selections=None,
                 status: str = "PENDING_REVIEW"):
        stamp = self.now.isoformat(timespec="seconds")
        self.conn.execute(
            """INSERT INTO post (id,product_id,channel_id,campaign_id,variant_code,
                   caption_body,disclosure_text,caption_final,affiliate_link,
                   post_type,status,created_at,updated_at)
               VALUES (?,'p',?,'camp','H1','caption','#ad','caption',
                       'https://s.shopee.vn/p','SALES',?,?,?)""",
            (post_id, channel_id, status, stamp, stamp),
        )
        for cid in selections or []:
            self.conn.execute(
                """INSERT INTO post_channel_selection (post_id, channel_id, created_at)
                   VALUES (?,?,?)""",
                (post_id, cid, stamp),
            )
        return post_id

    def _status(self, post_id: str) -> str:
        return self.conn.execute(
            "SELECT status FROM post WHERE id=?", (post_id,)
        ).fetchone()["status"]

    def _approve_all(self):
        return self.client.post("/duyet/approve-all", data={"_csrf": self.csrf},
                                follow_redirects=False)

    def test_button_is_offered_on_the_review_page(self):
        self._pending("po1")

        body = self.client.get("/duyet").data.decode("utf-8")

        self.assertIn("/duyet/approve-all", body)
        self.assertIn("Duyệt tất cả", body)

    def test_button_is_hidden_when_nothing_is_waiting(self):
        body = self.client.get("/duyet").data.decode("utf-8")

        self.assertNotIn("/duyet/approve-all", body)

    def test_every_pending_post_is_approved(self):
        for index in range(3):
            self._pending(f"po{index}")

        response = self._approve_all()

        self.assertEqual(response.status_code, 302)
        for index in range(3):
            self.assertNotEqual(self._status(f"po{index}"), "PENDING_REVIEW")

    def test_approved_posts_get_publish_targets_on_their_selected_channels(self):
        self._pending("po1", channel_id="cha", selections=["cha", "chb"])

        self._approve_all()

        channels = {r["channel_id"] for r in self.conn.execute(
            "SELECT channel_id FROM publish_target WHERE post_id='po1'").fetchall()}
        self.assertEqual(channels, {"cha", "chb"})

    def test_post_without_any_selection_row_falls_back_to_its_own_channel(self):
        """Older posts predate post_channel_selection; they must stay approvable."""
        self._pending("po1", channel_id="chb", selections=[])

        self._approve_all()

        channels = [r["channel_id"] for r in self.conn.execute(
            "SELECT channel_id FROM publish_target WHERE post_id='po1'").fetchall()]
        self.assertEqual(channels, ["chb"])

    def test_draft_posts_are_included_like_the_reject_all_counterpart(self):
        self._pending("po1", status="DRAFT")

        self._approve_all()

        self.assertNotEqual(self._status("po1"), "DRAFT")

    def test_one_failing_post_does_not_block_the_rest(self):
        """Bulk approve must not be all-or-nothing: a single bad row is not a reason
        to leave twenty good ones sitting in the queue."""
        self._pending("po_ok")
        # Kênh bị tắt: approve_post từ chối ở _resolve_channels_by_id, đúng một
        # ca hỏng thật chứ không phải dựng ép bằng khoá ngoại không hợp lệ.
        self._pending("po_bad", channel_id="chb")
        self.conn.execute("UPDATE channel SET status='DISABLED', enabled=0 WHERE id='chb'")

        self._approve_all()

        self.assertNotEqual(self._status("po_ok"), "PENDING_REVIEW")

    def test_result_message_reports_how_many_were_approved(self):
        self._pending("po1")
        self._pending("po2")

        response = self._approve_all()

        self.assertIn("2", response.headers.get("Location", ""))

    def test_requires_csrf_token(self):
        self._pending("po1")

        response = self.client.post("/duyet/approve-all", data={})

        self.assertNotEqual(response.status_code, 302)
        self.assertEqual(self._status("po1"), "PENDING_REVIEW")


if __name__ == "__main__":
    unittest.main()
