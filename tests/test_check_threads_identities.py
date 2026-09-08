import unittest

from scripts.check_threads_identities import classify


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.ok = status_code < 400
        self.payload = payload

    def json(self):
        return self.payload


class ThreadsIdentityDiagnosticTests(unittest.TestCase):
    def test_reports_matching_identity_without_token_data(self):
        row = {"handle": "@alice", "external_user_id": "uid-1"}
        result = classify(row, _Response(200, {"id": "uid-1", "username": "alice"}))
        self.assertEqual("@alice|HTTP_200|oauth=@alice|MATCH", result)

    def test_reports_sanitized_api_error(self):
        row = {"handle": "@alice", "external_user_id": "uid-1"}
        response = _Response(400, {"error": {"code": 100, "error_subcode": 33}})
        self.assertEqual(
            "@alice|HTTP_400|ERROR|code=100|subcode=33",
            classify(row, response),
        )


if __name__ == "__main__":
    unittest.main()
