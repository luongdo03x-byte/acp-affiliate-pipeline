import os
import tempfile
import unittest

from flask import Flask

from core import db
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.schema import ensure_schema
from core.factory_v2.service import FactoryService
from web.factory_v2 import register_factory_v2_routes


class FactoryV2RunnerApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_factory_key = os.environ.get("ACP_FACTORY_API_KEY")
        db.DB_PATH = os.path.join(self.tmp.name, "factory-v2.db")
        os.environ["ACP_FACTORY_API_KEY"] = "test-key"

        self.conn = db.connect()
        ensure_schema(self.conn)
        self.repo = FactoryRepository(self.conn)
        self.service = FactoryService(self.repo)

        app = Flask(__name__)
        app.testing = True
        register_factory_v2_routes(app)
        self.client = app.test_client()
        self.auth = {"X-ACP-Factory-Key": "test-key"}

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self.old_db_path
        if self.old_factory_key is None:
            os.environ.pop("ACP_FACTORY_API_KEY", None)
        else:
            os.environ["ACP_FACTORY_API_KEY"] = self.old_factory_key
        self.tmp.cleanup()

    def test_register_local_runner(self):
        res = self.client.post(
            "/api/factory/v2/runners/local/register",
            headers=self.auth,
            json={"device_id": "android-id-1", "device_name": "Pixel 8"},
        )
        self.assertEqual(201, res.status_code)
        body = res.get_json()
        self.assertEqual("LOCAL_DEVICE", body["runner"]["runner_type"])
        self.assertNotIn("adb_serial", body["runner"])
        self.assertNotIn("pid", body["runner"])

    def test_heartbeat_rejects_wrong_assignment(self):
        worker = self.service.register_local_runner("android-id-2", "Pixel")
        self.repo.update_worker_fields(
            worker["id"], current_account_id="a1", current_job_id="j1", state="RUNNING"
        )
        res = self.client.post(
            f"/api/factory/v2/runners/{worker['id']}/heartbeat",
            headers=self.auth,
            json={"current_account_id": "a2", "current_job_id": "j2"},
        )
        self.assertEqual(409, res.status_code)

    def test_runners_list_exposes_safe_metadata(self):
        self.service.register_local_runner("android-id-3", "Phone")
        res = self.client.get("/api/factory/v2/runners", headers=self.auth)
        self.assertEqual(200, res.status_code)
        runner = res.get_json()["runners"][0]
        self.assertIn("runner_type", runner)
        self.assertNotIn("adb_serial", runner)
        self.assertNotIn("pid", runner)


if __name__ == "__main__":
    unittest.main()
