"""Focused tests for the Facebook Seeding Assistant.

Run from the parent directory that contains the ``acp`` package:
    ACP_ADAPTER=mock ACP_SOURCE=mock ACP_CAPTION_LLM= python3 -m acp.tests.test_seeding
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

_tmp = tempfile.mkdtemp(prefix="acp-seeding-test-")
os.environ["ACP_DB"] = os.path.join(_tmp, "seeding.db")

from acp.core import db, system_settings  # noqa: E402

db.DB_PATH = os.environ["ACP_DB"]


class IsolatedDbTestCase(unittest.TestCase):
    def setUp(self) -> None:
        db.DB_PATH = os.path.join(_tmp, f"{self._testMethodName}.db")
        if os.path.exists(db.DB_PATH):
            os.unlink(db.DB_PATH)
        db.init_db()
        self.conn = db.connect()

    def tearDown(self) -> None:
        self.conn.close()


class SeedingSchemaTests(IsolatedDbTestCase):
    def test_seeding_tables_exist(self) -> None:
        names = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertTrue(
            {
                "seeding_campaign",
                "seeding_template",
                "seeding_target",
                "seeding_shift",
                "seeding_activity",
            }.issubset(names)
        )

    def test_global_pause_defaults_false_and_is_audited(self) -> None:
        self.assertFalse(system_settings.seeding_global_paused(self.conn))
        system_settings.set_seeding_global_paused(self.conn, True, actor="test")
        self.assertTrue(system_settings.seeding_global_paused(self.conn))
        row = self.conn.execute(
            "SELECT action, actor FROM audit_log "
            "WHERE entity='system_setting' AND entity_id=? "
            "ORDER BY id DESC LIMIT 1",
            (system_settings.SEEDING_GLOBAL_PAUSED,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(("set", "test"), (row["action"], row["actor"]))


class SeedingDomainTests(IsolatedDbTestCase):
    def _seeding(self):
        from acp.core import seeding

        seeding.set_llm(None)
        return seeding

    def _campaign(self, *, auto_submit=True, threshold=0.90):
        seeding = self._seeding()
        return seeding.create_campaign(
            self.conn,
            name="Campaign A",
            brand="Brand",
            brief="Chỉ dùng thông tin và claim đã được duyệt.",
            allowed_claims=["free_consultation"],
            prohibited_topics=["refund"],
            disclosure_policy="promotional",
            auto_submit=auto_submit,
            confidence_threshold=threshold,
        )

    def _queue_one(self, campaign_id, suffix="1"):
        seeding = self._seeding()
        url = f"https://www.facebook.com/groups/demo/posts/{suffix}/"
        result = seeding.import_targets(self.conn, campaign_id, [url])
        self.assertEqual(1, result["created"])
        shift = seeding.start_shift(self.conn, campaign_id)
        target = seeding.next_target(self.conn, shift["id"])
        return shift, target

    def test_campaign_rejects_threshold_below_safety_floor(self) -> None:
        seeding = self._seeding()
        with self.assertRaises(ValueError):
            seeding.create_campaign(
                self.conn,
                name="Unsafe",
                brand="Brand",
                brief="brief",
                allowed_claims=[],
                prohibited_topics=[],
                disclosure_policy="",
                confidence_threshold=0.84,
            )

    def test_target_import_filters_invalid_and_preserves_order(self) -> None:
        seeding = self._seeding()
        campaign = self._campaign()
        result = seeding.import_targets(
            self.conn,
            campaign["id"],
            [
                "https://www.facebook.com/groups/demo/posts/1/",
                "https://www.facebook.com/groups/demo/posts/2/#comment",
                "https://www.facebook.com/groups/demo/posts/1/",
                "javascript:alert(1)",
                "https://example.com/not-facebook",
            ],
        )
        self.assertEqual(
            {"created": 2, "duplicates": 1, "invalid": 2}, result
        )
        shift = seeding.start_shift(self.conn, campaign["id"])
        first = seeding.next_target(self.conn, shift["id"])
        self.assertTrue(first["url"].endswith("/posts/1/"))

    def test_template_without_llm_stays_review_only_when_auto_is_uncertain(self) -> None:
        seeding = self._seeding()
        campaign = self._campaign(auto_submit=True)
        seeding.add_template(
            self.conn,
            campaign["id"],
            intent="recommendation_request",
            source_text="Bạn có thể tham khảo Brand; hiện có tư vấn miễn phí.",
            allowed_claims=["free_consultation"],
        )
        shift, target = self._queue_one(campaign["id"])
        decision = seeding.prepare_target(
            self.conn,
            shift["id"],
            target["id"],
            {
                "url": target["url"],
                "post_text": "Mọi người cho mình xin chỗ tư vấn uy tín với ạ?",
                "surface_name": "Group Demo",
            },
        )
        self.assertEqual("REVIEW_REQUIRED", decision["decision"])
        self.assertEqual(
            "Bạn có thể tham khảo Brand; hiện có tư vấn miễn phí.",
            decision["drafts"][0],
        )

    def test_safe_structured_llm_result_can_be_auto_ready(self) -> None:
        seeding = self._seeding()
        campaign = self._campaign(auto_submit=True)
        template = seeding.add_template(
            self.conn,
            campaign["id"],
            intent="recommendation_request",
            source_text="Bạn có thể tham khảo Brand; hiện có tư vấn miễn phí.",
            allowed_claims=["free_consultation"],
        )
        shift, target = self._queue_one(campaign["id"])

        def fake_llm(_prompt):
            return json.dumps(
                {
                    "intent": "recommendation_request",
                    "draft": "Bạn có thể tham khảo Brand; hiện có tư vấn miễn phí.",
                    "confidence": 0.96,
                    "risk_labels": [],
                    "template_id": template["id"],
                    "claims_used": ["free_consultation"],
                },
                ensure_ascii=False,
            )

        seeding.set_llm(fake_llm)
        decision = seeding.prepare_target(
            self.conn,
            shift["id"],
            target["id"],
            {
                "url": target["url"],
                "post_text": "Mọi người cho mình xin chỗ tư vấn uy tín với ạ?",
                "surface_name": "Group Demo",
            },
        )
        self.assertEqual("AUTO_READY", decision["decision"])
        self.assertEqual("LOW", decision["risk_level"])
        self.assertGreaterEqual(decision["confidence"], 0.90)

    def test_complaint_context_forces_review_even_with_high_model_confidence(self) -> None:
        seeding = self._seeding()
        campaign = self._campaign(auto_submit=True)
        shift, target = self._queue_one(campaign["id"])

        def fake_llm(_prompt):
            return json.dumps(
                {
                    "intent": "recommendation_request",
                    "draft": "Bạn có thể tham khảo Brand.",
                    "confidence": 0.99,
                    "risk_labels": [],
                    "claims_used": [],
                },
                ensure_ascii=False,
            )

        seeding.set_llm(fake_llm)
        decision = seeding.prepare_target(
            self.conn,
            shift["id"],
            target["id"],
            {
                "url": target["url"],
                "post_text": "Mình đang khiếu nại và muốn hoàn tiền vì trải nghiệm quá tệ.",
            },
        )
        self.assertEqual("REVIEW_REQUIRED", decision["decision"])
        self.assertTrue(
            {"complaint", "refund_dispute"}.intersection(decision["risk_labels"])
        )

    def test_first_person_testimonial_and_unknown_claim_force_review(self) -> None:
        seeding = self._seeding()
        campaign = self._campaign(auto_submit=True)
        shift, target = self._queue_one(campaign["id"])

        def fake_llm(_prompt):
            return json.dumps(
                {
                    "intent": "recommendation_request",
                    "draft": "Mình đã làm ở đây rồi và được bảo hành trọn đời.",
                    "confidence": 0.99,
                    "risk_labels": [],
                    "claims_used": ["lifetime_warranty"],
                },
                ensure_ascii=False,
            )

        seeding.set_llm(fake_llm)
        decision = seeding.prepare_target(
            self.conn,
            shift["id"],
            target["id"],
            {"url": target["url"], "post_text": "Xin địa chỉ tham khảo."},
        )
        self.assertEqual("REVIEW_REQUIRED", decision["decision"])
        self.assertIn("first_person_testimonial", decision["risk_labels"])
        self.assertIn("unsupported_claim", decision["risk_labels"])

    def test_duplicate_recent_comment_forces_review(self) -> None:
        seeding = self._seeding()
        campaign = self._campaign(auto_submit=True)
        shift1, target1 = self._queue_one(campaign["id"], "1")

        def fake_llm(_prompt):
            return json.dumps(
                {
                    "intent": "recommendation_request",
                    "draft": "Bạn có thể tham khảo Brand và hỏi tư vấn miễn phí nhé.",
                    "confidence": 0.97,
                    "risk_labels": [],
                    "claims_used": ["free_consultation"],
                },
                ensure_ascii=False,
            )

        seeding.set_llm(fake_llm)
        first = seeding.prepare_target(
            self.conn,
            shift1["id"],
            target1["id"],
            {"url": target1["url"], "post_text": "Xin chỗ tham khảo."},
        )
        self.assertEqual("AUTO_READY", first["decision"])
        seeding.record_result(
            self.conn,
            shift1["id"],
            target1["id"],
            result="POSTED",
            mode="auto",
            final_text=first["drafts"][0],
            proof_ref="comment:1",
        )
        seeding.end_shift(self.conn, shift1["id"])

        seeding.import_targets(
            self.conn,
            campaign["id"],
            ["https://www.facebook.com/groups/demo/posts/2/"],
        )
        shift2 = seeding.start_shift(self.conn, campaign["id"])
        target2 = seeding.next_target(self.conn, shift2["id"])
        second = seeding.prepare_target(
            self.conn,
            shift2["id"],
            target2["id"],
            {"url": target2["url"], "post_text": "Xin chỗ tham khảo khác."},
        )
        self.assertEqual("REVIEW_REQUIRED", second["decision"])
        self.assertIn("duplicate_recent_comment", second["risk_labels"])

    def test_global_pause_overrides_auto_ready(self) -> None:
        seeding = self._seeding()
        campaign = self._campaign(auto_submit=True)
        shift, target = self._queue_one(campaign["id"])

        def fake_llm(_prompt):
            return json.dumps(
                {
                    "intent": "generic",
                    "draft": "Bạn có thể tham khảo thông tin chính thức của Brand nhé.",
                    "confidence": 0.99,
                    "risk_labels": [],
                    "claims_used": [],
                },
                ensure_ascii=False,
            )

        seeding.set_llm(fake_llm)
        system_settings.set_seeding_global_paused(self.conn, True, actor="test")
        decision = seeding.prepare_target(
            self.conn,
            shift["id"],
            target["id"],
            {"url": target["url"], "post_text": "Xin thông tin."},
        )
        self.assertEqual("REVIEW_REQUIRED", decision["decision"])
        self.assertIn("global_pause", decision["risk_labels"])

    def test_terminal_result_is_idempotent_and_does_not_double_count(self) -> None:
        seeding = self._seeding()
        campaign = self._campaign(auto_submit=False)
        shift, target = self._queue_one(campaign["id"])
        first = seeding.record_result(
            self.conn,
            shift["id"],
            target["id"],
            result="POSTED",
            mode="reviewed",
            final_text="Nội dung đã duyệt",
            proof_ref="comment:123",
        )
        second = seeding.record_result(
            self.conn,
            shift["id"],
            target["id"],
            result="POSTED",
            mode="reviewed",
            final_text="Nội dung đã duyệt",
            proof_ref="comment:123",
        )
        self.assertEqual(1, first["posted_count"])
        self.assertEqual(1, second["posted_count"])
        with self.assertRaises(ValueError):
            seeding.record_result(
                self.conn,
                shift["id"],
                target["id"],
                result="UNKNOWN",
                mode="reviewed",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
