import sqlite3
import unittest

from core.factory_v2.repository import FactoryRepository
from core.factory_v2.runner_gateway import RunnerGateway
from core.factory_v2.schema import ensure_schema


class FakeProcesses:
    def __init__(self):
        self.commands = []

    def request(self, worker_id, command):
        self.commands.append((worker_id, command))
        return {
            "ok": True,
            "status": "waiting_human",
            "result": {"screen": "OAUTH_CONSENT"},
        }


class TransientLoginSecretTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.processes = FakeProcesses()
        self.gateway = RunnerGateway(self.repo, self.processes)

    def tearDown(self):
        self.conn.close()

    def test_remote_secret_bypasses_persisted_runner_commands(self):
        job = {
            "id": "j1",
            "account_id": "a1",
            "worker_id": "w1",
            "runner_type": "REMOTE_AVD",
        }
        response = self.gateway.send_transient_login_secret(
            job,
            username="user1",
            password="example-secret",
        )
        self.assertEqual("waiting_human", response["status"])
        self.assertEqual(
            0,
            self.conn.execute(
                "SELECT COUNT(*) FROM factory_runner_command"
            ).fetchone()[0],
        )
        command = self.processes.commands[0][1]
        self.assertEqual("TRANSIENT_BROWSER_LOGIN", command.action)
        self.assertEqual("user1", command.payload["username"])
        self.assertEqual("example-secret", command.payload["password"])
        self.assertNotIn("example-secret", repr(response))

    def test_local_runner_cannot_receive_transient_login_secret(self):
        job = {
            "id": "j1",
            "account_id": "a1",
            "worker_id": "w1",
            "runner_type": "LOCAL_DEVICE",
        }
        with self.assertRaisesRegex(
            ValueError,
            "transient login secret is REMOTE_AVD only",
        ):
            self.gateway.send_transient_login_secret(
                job,
                username="user1",
                password="example-secret",
            )
        self.assertEqual([], self.processes.commands)


if __name__ == "__main__":
    unittest.main()
