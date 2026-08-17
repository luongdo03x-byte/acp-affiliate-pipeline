import os
import tempfile
import unittest

from flask import Flask

from core import db
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService
from web.factory_v2 import register_factory_v2_routes


class FactoryV2ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_factory_key = os.environ.get("ACP_FACTORY_API_KEY")
        db.DB_PATH = os.path.join(self.tmp.name, "factory-v2.db")
        os.environ["ACP_FACTORY_API_KEY"] = "test-key"

        conn = db.connect()
        ensure_schema(conn)
        self.repo = FactoryRepository(conn)
        self.service = FactoryService(self.repo)
        self.batch = self.service.create_batch("Batch 01", count=3, seed=7)
        self.repo.insert_worker({
            "id": "worker-01",
            "avd_name": "acp-worker-01",
            "adb_serial": "emulator-5554",
            "state": "READY",
        })
        self.repo.insert_resource_sample({
            "timestamp": "2026-08-17T06:00:00+00:00",
            "cpu_percent": 58.0,
            "ram_total_mb": 16384,
            "ram_available_mb": 8400,
            "swap_used_mb": 200,
            "swap_in_rate": 0.0,
            "load_1m": 1.2,
            "load_5m": 1.0,
            "avd_total": 1,
            "avd_running": 0,
            "avd_waiting_human": 0,
            "capacity_state": "YELLOW",
            "desired_workers": 1,
        })
        conn.close()

        app = Flask(__name__)
        app.testing = True
        register_factory_v2_routes(app)
        self.client = app.test_client()
        self.auth = {"X-ACP-Factory-Key": "test-key"}

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        if self.old_factory_key is None:
            os.environ.pop("ACP_FACTORY_API_KEY", None)
        else:
            os.environ["ACP_FACTORY_API_KEY"] = self.old_factory_key
        self.tmp.cleanup()

    def test_dashboard_requires_factory_key(self):
        res = self.client.get("/api/factory/v2/dashboard")
        self.assertEqual(401, res.status_code)

    def test_dashboard_returns_controller_counts(self):
        res = self.client.get("/api/factory/v2/dashboard", headers=self.auth)
        self.assertEqual(200, res.status_code)
        body = res.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(self.batch["id"], body["batch"]["id"])
        self.assertEqual(3, body["accounts"]["total"])
        self.assertEqual(3, body["accounts"]["queued"])
        self.assertEqual(1, body["workers"]["total"])
        self.assertEqual("YELLOW", body["host"]["capacity_state"])
        self.assertNotIn("adb_serial", str(body))

    def test_read_endpoints_return_controller_rows(self):
        batch = self.client.get(
            f"/api/factory/v2/batches/{self.batch['id']}", headers=self.auth
        )
        accounts = self.client.get("/api/factory/v2/accounts", headers=self.auth)
        workers = self.client.get("/api/factory/v2/workers", headers=self.auth)
        checkpoints = self.client.get("/api/factory/v2/checkpoints", headers=self.auth)
        self.assertEqual(200, batch.status_code)
        self.assertEqual(3, len(accounts.get_json()["accounts"]))
        self.assertEqual(1, len(workers.get_json()["workers"]))
        self.assertEqual([], checkpoints.get_json()["checkpoints"])
        self.assertNotIn("adb_serial", workers.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()