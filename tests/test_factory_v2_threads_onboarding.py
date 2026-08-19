import sqlite3
import unittest

from core.factory_v2.threads_onboarding import (
    list_onboarding_accounts,
    mark_tester_accepted,
    mark_tester_invited,
    onboarding_status,
)


class ThreadsOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE factory_account (
                id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                username TEXT NOT NULL,
                stage TEXT NOT NULL,
                last_safe_stage TEXT NOT NULL,
                last_error_code TEXT,
                tester_invited_at TEXT,
                tester_accepted_at TEXT
            );
        """)

    def tearDown(self):
        self.conn.close()

    def insert_account(
        self,
        account_id,
        username,
        *,
        stage="THREADS_CREATED",
        last_safe_stage="THREADS_CREATED",
        last_error_code=None,
        invited_at=None,
        accepted_at=None,
        sequence=1,
    ):
        self.conn.execute(
            """INSERT INTO factory_account
               (id,batch_id,sequence,username,stage,last_safe_stage,last_error_code,
                tester_invited_at,tester_accepted_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                account_id,
                "batch-1",
                sequence,
                username,
                stage,
                last_safe_stage,
                last_error_code,
                invited_at,
                accepted_at,
            ),
        )
        return dict(self.conn.execute(
            "SELECT * FROM factory_account WHERE id=?", (account_id,)
        ).fetchone())

    def test_status_progresses_from_invite_to_accept_to_oauth(self):
        account = self.insert_account("a1", "alpha")
        self.assertEqual("NEEDS_TESTER_INVITE", onboarding_status(account))

        account["tester_invited_at"] = "2026-08-19T03:00:00+00:00"
        self.assertEqual("NEEDS_TESTER_ACCEPT", onboarding_status(account))

        account["tester_accepted_at"] = "2026-08-19T03:01:00+00:00"
        self.assertEqual("READY_FOR_OAUTH", onboarding_status(account))

    def test_runtime_stages_map_to_in_progress_active_and_disabled(self):
        base = self.insert_account(
            "a1", "alpha", invited_at="t1", accepted_at="t2"
        )
        base["stage"] = "ACP_CONNECTING"
        self.assertEqual("OAUTH_IN_PROGRESS", onboarding_status(base))
        base["stage"] = "ACP_ACTIVE"
        self.assertEqual("ACTIVE", onboarding_status(base))
        base["stage"] = "DISABLED"
        self.assertEqual("DISABLED", onboarding_status(base))

    def test_only_oauth_retry_returns_to_tester_ready_state(self):
        oauth_retry = self.insert_account(
            "a1",
            "alpha",
            stage="RETRY_PENDING",
            last_safe_stage="THREADS_CREATED",
            last_error_code="OAUTH_FAILED",
            invited_at="t1",
            accepted_at="t2",
        )
        self.assertEqual("READY_FOR_OAUTH", onboarding_status(oauth_retry))

        other_retry = dict(oauth_retry)
        other_retry["last_error_code"] = "NETWORK_TRANSIENT"
        self.assertIsNone(onboarding_status(other_retry))

        too_early = dict(oauth_retry)
        too_early["last_safe_stage"] = "IG_CREATED"
        self.assertIsNone(onboarding_status(too_early))

    def test_accept_backfills_invite_and_is_idempotent(self):
        self.insert_account("a1", "alpha")

        first = mark_tester_accepted(
            self.conn, "a1", timestamp="2026-08-19T03:05:00+00:00"
        )
        second = mark_tester_accepted(
            self.conn, "a1", timestamp="2026-08-19T03:06:00+00:00"
        )

        self.assertEqual("2026-08-19T03:05:00+00:00", first["tester_invited_at"])
        self.assertEqual("2026-08-19T03:05:00+00:00", first["tester_accepted_at"])
        self.assertEqual(first["tester_invited_at"], second["tester_invited_at"])
        self.assertEqual(first["tester_accepted_at"], second["tester_accepted_at"])
        self.assertEqual("READY_FOR_OAUTH", onboarding_status(second))

    def test_mark_invited_is_idempotent(self):
        self.insert_account("a1", "alpha")
        first = mark_tester_invited(
            self.conn, "a1", timestamp="2026-08-19T03:10:00+00:00"
        )
        second = mark_tester_invited(
            self.conn, "a1", timestamp="2026-08-19T03:11:00+00:00"
        )
        self.assertEqual("2026-08-19T03:10:00+00:00", first["tester_invited_at"])
        self.assertEqual(first["tester_invited_at"], second["tester_invited_at"])

    def test_markers_reject_accounts_not_ready_for_threads_oauth(self):
        self.insert_account(
            "a1",
            "alpha",
            stage="IG_CREATED",
            last_safe_stage="IG_CREATED",
        )
        with self.assertRaises(ValueError):
            mark_tester_invited(self.conn, "a1", timestamp="t1")
        with self.assertRaises(ValueError):
            mark_tester_accepted(self.conn, "a1", timestamp="t2")

    def test_list_returns_only_accounts_relevant_to_onboarding_in_sequence_order(self):
        self.insert_account("a2", "second", sequence=2)
        self.insert_account("a1", "first", sequence=1)
        self.insert_account(
            "early",
            "early",
            stage="IG_CREATED",
            last_safe_stage="IG_CREATED",
            sequence=3,
        )

        rows = list_onboarding_accounts(self.conn)

        self.assertEqual(["a1", "a2"], [row["id"] for row in rows])
        self.assertEqual(
            ["NEEDS_TESTER_INVITE", "NEEDS_TESTER_INVITE"],
            [row["onboarding_status"] for row in rows],
        )

    def test_unknown_account_raises_key_error(self):
        with self.assertRaises(KeyError):
            mark_tester_invited(self.conn, "missing", timestamp="t1")
        with self.assertRaises(KeyError):
            mark_tester_accepted(self.conn, "missing", timestamp="t2")


if __name__ == "__main__":
    unittest.main()
