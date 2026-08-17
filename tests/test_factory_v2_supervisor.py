import unittest

from core.factory_v2.worker_protocol import CommandLedger


class WorkerProtocolTests(unittest.TestCase):
    def test_duplicate_command_id_returns_stored_result_without_rerun(self):
        ledger = CommandLedger(max_entries=10)
        calls = []

        def action():
            calls.append("ran")
            return {"ok": True, "value": 7}

        first = ledger.execute("cmd-1", action)
        second = ledger.execute("cmd-1", action)
        self.assertEqual(first, second)
        self.assertEqual(["ran"], calls)


if __name__ == "__main__":
    unittest.main()
