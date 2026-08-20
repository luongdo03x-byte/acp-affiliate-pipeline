import unittest

from acp.core import shopee_csv_batches


class ShopeeCsvPreviewBatchTests(unittest.TestCase):
    def setUp(self):
        shopee_csv_batches.reset_previews()

    def tearDown(self):
        shopee_csv_batches.reset_previews()

    def test_issue_returns_900_second_token(self):
        issued = shopee_csv_batches.issue_preview([], {"rows": 0}, now_ts=100.0)
        self.assertTrue(issued["token"])
        self.assertEqual(issued["expires_in"], 900)

    def test_peek_does_not_consume_then_consume_succeeds_once(self):
        issued = shopee_csv_batches.issue_preview([{"id": 1}], {"rows": 1}, now_ts=100.0)
        token = issued["token"]

        first = shopee_csv_batches.peek_preview(token, now_ts=101.0)
        second = shopee_csv_batches.peek_preview(token, now_ts=102.0)
        self.assertEqual(first["rows"], [{"id": 1}])
        self.assertEqual(second["summary"], {"rows": 1})

        consumed = shopee_csv_batches.consume_preview(token, now_ts=103.0)
        self.assertEqual(consumed["rows"], [{"id": 1}])
        self.assertIsNone(shopee_csv_batches.peek_preview(token, now_ts=104.0))
        self.assertIsNone(shopee_csv_batches.consume_preview(token, now_ts=105.0))

    def test_expired_batch_is_removed(self):
        issued = shopee_csv_batches.issue_preview([], {}, now_ts=100.0)
        token = issued["token"]
        self.assertIsNotNone(shopee_csv_batches.peek_preview(token, now_ts=999.9))
        self.assertIsNone(shopee_csv_batches.peek_preview(token, now_ts=1000.0))
        self.assertIsNone(shopee_csv_batches.consume_preview(token, now_ts=1001.0))

    def test_unknown_or_blank_token_is_rejected(self):
        self.assertIsNone(shopee_csv_batches.peek_preview("", now_ts=1.0))
        self.assertIsNone(shopee_csv_batches.peek_preview("missing", now_ts=1.0))
        self.assertIsNone(shopee_csv_batches.consume_preview("missing", now_ts=1.0))

    def test_store_copies_mutable_summary_and_rows(self):
        rows = [{"id": 1}]
        summary = {"rows": 1}
        issued = shopee_csv_batches.issue_preview(rows, summary, now_ts=100.0)
        rows[0]["id"] = 99
        summary["rows"] = 99

        stored = shopee_csv_batches.peek_preview(issued["token"], now_ts=101.0)
        self.assertEqual(stored["rows"], [{"id": 1}])
        self.assertEqual(stored["summary"], {"rows": 1})

        stored["rows"][0]["id"] = 55
        self.assertEqual(
            shopee_csv_batches.peek_preview(issued["token"], now_ts=102.0)["rows"],
            [{"id": 1}],
        )


if __name__ == "__main__":
    unittest.main()
