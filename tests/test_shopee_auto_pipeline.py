import unittest
from unittest import mock


class ShopeeAutoEnrichmentCliTests(unittest.TestCase):
    def test_shopee_enrich_runs_one_bounded_batch(self):
        import run

        fake_summary = {
            "processed": 3,
            "ready": 2,
            "needs_helper": 1,
            "failed": 0,
            "pending": 0,
        }
        with mock.patch("run.init_db"), \
             mock.patch("run.shopee_image_enrichment.run_batch", return_value=fake_summary) as batch:
            rc = run.cmd_shopee_enrich()

        self.assertEqual(rc, 0)
        self.assertEqual(batch.call_count, 1)
        self.assertEqual(batch.call_args.kwargs["limit"], 20)

    def test_shopee_enrich_hides_provider_exception_details(self):
        import run

        with mock.patch("run.init_db"), \
             mock.patch(
                 "run.shopee_image_enrichment.run_batch",
                 side_effect=RuntimeError("secret upstream body"),
             ):
            with mock.patch("builtins.print") as printer:
                rc = run.cmd_shopee_enrich()

        self.assertEqual(rc, 1)
        rendered = " ".join(str(call) for call in printer.call_args_list)
        self.assertNotIn("secret upstream body", rendered)

    def test_auto_schedule_service_runs_shopee_enrichment_before_scheduler(self):
        with open("ops/acp-auto-schedule.service", encoding="utf-8") as fh:
            text = fh.read()
        self.assertLess(text.index('run.py" shopee-enrich'), text.index('run.py" auto-schedule'))
        self.assertIn('run.py" worker-once', text)


if __name__ == "__main__":
    unittest.main()
