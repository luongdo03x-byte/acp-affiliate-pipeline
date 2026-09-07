import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from core import db
from core.factory_v2.device_credentials import authenticate_device_token
from core.factory_v2.pairing import create_pairing_code, redeem_pairing_code
from scripts.render_factory_cloud import render


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()

    def test_one_time_grant_and_only_hashed_secrets_are_stored(self):
        code = create_pairing_code(self.conn)
        token = redeem_pairing_code(self.conn, code, "phone-12345678", "Phone")
        self.assertEqual("phone-12345678", authenticate_device_token(self.conn, token)["device_id"])
        self.assertNotIn(code, str(list(self.conn.iterdump())))
        self.assertNotIn(token, str(list(self.conn.iterdump())))
        with self.assertRaises(ValueError):
            redeem_pairing_code(self.conn, code, "phone-other123")
        self.assertIsNotNone(authenticate_device_token(self.conn, token))

    def test_expired_and_bad_codes_do_not_enroll(self):
        with patch("core.factory_v2.pairing.time.time", return_value=100):
            code = create_pairing_code(self.conn, ttl_seconds=60)
        with patch("core.factory_v2.pairing.time.time", return_value=160):
            with self.assertRaises(ValueError):
                redeem_pairing_code(self.conn, code, "phone-12345678")
        with self.assertRaises(ValueError):
            redeem_pairing_code(self.conn, "x" * 32, "phone-12345678")

    def test_failed_token_write_rolls_back_pairing_claim(self):
        code = create_pairing_code(self.conn)
        with patch("core.factory_v2.pairing.issue_device_token", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                redeem_pairing_code(self.conn, code, "phone-12345678")
        token = redeem_pairing_code(self.conn, code, "phone-12345678")
        self.assertIsNotNone(authenticate_device_token(self.conn, token))


class PairingApiTests(unittest.TestCase):
    def setUp(self):
        from account_factory_server import build_app
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db, "DB_PATH", os.path.join(self.tmp.name, "factory.db"))
        self.db_patch.start()
        from core.factory_v2.schema import ensure_schema
        conn = db.connect()
        ensure_schema(conn)
        conn.close()
        self.client = build_app().test_client()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_public_client_pairs_without_operator_key_and_cannot_replay(self):
        conn = db.connect()
        code = create_pairing_code(conn)
        conn.close()
        body = dict(code=code, device_id="phone-12345678", device_name="Phone")
        response = self.client.post("/api/factory/pair", json=body, environ_base={"REMOTE_ADDR": "8.8.8.8"})
        self.assertEqual(201, response.status_code)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertEqual("account-factory", response.json["service"])
        self.assertEqual(2, response.json["api_version"])
        self.assertEqual(400, self.client.post("/api/factory/pair", json=body).status_code)
        auth = self.client.get("/api/factory/v2/dashboard", headers={"X-ACP-Device-Token": response.json["device_token"]})
        self.assertEqual(200, auth.status_code)

    def test_proxy_cannot_turn_public_client_into_lan_enrollment(self):
        with patch.dict(os.environ, {"ACP_FACTORY_LAN_AUTO_ENROLL": "true"}):
            response = self.client.post("/api/factory/enroll", json={"device_id": "phone-12345678"},
                headers={"X-Forwarded-For": "8.8.8.8"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(403, response.status_code)

    def test_pairing_rejects_malformed_body(self):
        for body in ([], {"code": "bad", "device_id": "phone-12345678"}, {"code": "x" * 32, "secret": "never"}):
            self.assertEqual(400, self.client.post("/api/factory/pair", json=body).status_code)

    def test_phone_checkpoints_to_real_callback_with_fake_oauth_provider(self):
        from account_factory_server import build_app
        from core.factory_v2.activation import FactoryActivationService
        from core.factory_v2.runtime import build_default_runtime

        class Phone:
            package = None
            urls = []

            def send(self, job, action, payload=None):
                payload = payload or {}
                if action == "OPEN_PACKAGE":
                    self.package = payload["package"]
                elif action == "OPEN_URL":
                    self.urls.append(payload["url"])
                elif action == "OBSERVE_FOREGROUND":
                    return {"package": self.package}
                return {"ok": True}

        class Provider:
            def authorization_url(self, state, redirect_uri):
                self.state, self.redirect_uri = state, redirect_uri
                return "https://threads.example/authorize"

            def exchange_code(self, code, redirect_uri):
                return {"access_token": "test-short", "user_id": "test-threads-user"}

            def exchange_long_lived(self, token):
                return {"access_token": "test-long", "expires_in": 3600}

            def fetch_profile(self, token):
                return {"id": "test-threads-user", "username": self.username}

        with patch.dict(os.environ, {"ACP_FACTORY_LOCAL_ONLY": "1", "ACP_ENV": "test",
                                      "ACP_PUBLIC_BASE_URL": "https://acp.example"}), \
                patch("acp.web.account_factory.connect", db.connect):
            runtime = build_default_runtime()
            try:
                phone, provider = Phone(), Provider()
                runtime.runner_gateway = phone
                runtime.activation_service = FactoryActivationService(runtime.repo.conn, provider=provider)
                worker = runtime.service.register_local_runner("phone-12345678", "Mock phone")
                batch = runtime.service.create_batch("Single phone mock", count=1, seed=9)
                account = runtime.repo.list_accounts(batch["id"])[0]
                provider.username = account["username"]
                runtime.repo.conn.execute("UPDATE factory_account SET execution_target=? WHERE id=?",
                                         (worker["id"], account["id"]))
                runtime.tick()
                for checkpoint_type in ("IG_POSTCHECK", "THREADS_POSTCHECK"):
                    checkpoint = runtime.repo.conn.execute(
                        "SELECT id FROM factory_checkpoint WHERE account_id=? AND type=? AND status='OPEN'",
                        (account["id"], checkpoint_type),
                    ).fetchone()
                    self.assertIsNotNone(checkpoint, checkpoint_type)
                    runtime.service.request_checkpoint_verification(checkpoint["id"])
                    runtime.tick()
                    runtime.tick()
                self.assertEqual("ACP_CONNECTING", runtime.repo.get_account(account["id"])["stage"])
                self.assertTrue(phone.urls)
                self.assertEqual("https://acp.example/oauth/account-factory/threads/callback", provider.redirect_uri)
                app = build_app()
                app.config["ACCOUNT_FACTORY_OAUTH_FACTORY"] = lambda: provider
                response = app.test_client().get("/oauth/account-factory/threads/callback",
                                                query_string={"code": "test-code", "state": provider.state})
                self.assertEqual(200, response.status_code)
                runtime.tick()
                saved = runtime.repo.get_account(account["id"])
                self.assertEqual("ACP_ACTIVE", saved["stage"])
                self.assertIsNotNone(saved["channel_id"])
                self.assertEqual("READY", runtime.repo.get_worker(worker["id"])["state"])
            finally:
                runtime.close()


class DeploymentTests(unittest.TestCase):
    def test_physical_controller_ticks_without_android_sdk(self):
        from core.factory_v2.runtime import build_default_runtime
        with tempfile.TemporaryDirectory() as tmp, patch.object(db, "DB_PATH", os.path.join(tmp, "factory.db")), \
                patch.dict(os.environ, {"ACP_FACTORY_LOCAL_ONLY": "1"}), \
                patch("core.factory_v2.avd.AvdManager", side_effect=AssertionError("SDK must not be used")):
            runtime = build_default_runtime()
            try:
                runtime.tick()
                self.assertIsNotNone(runtime.last_successful_tick)
                self.assertTrue(runtime.local_only)
            finally:
                runtime.close()

    def test_render_has_shared_database_env_and_same_public_callback_origin(self):
        files = render("operator", "/home/operator/Downloads/ACP", "https://acp.example")
        self.assertIn("/shared/.env.local", files["acp-factory.service"])
        env = files["acp-factory-runtime.env"]
        self.assertIn("ACP_FACTORY_LAN_AUTO_ENROLL=false", env)
        self.assertIn("ACP_FACTORY_LOCAL_ONLY=1", env)
        self.assertNotIn("ACP_DB=", env)
        self.assertIn("ACP_PUBLIC_BASE_URL=https://acp.example", env)
        gateway = files["acp-factory-gateway.conf"]
        self.assertIn("location = /api/factory/enroll { return 403; }", gateway)
        self.assertIn("listen 127.0.0.1:8080", gateway)

    def test_render_rejects_directive_injection(self):
        for user, root, url in (("root\nUser=root", "/app", "https://acp.example"),
                                ("operator", "/app\nExecStart=x", "https://acp.example"),
                                ("operator", "/app", "https://user:pass@acp.example")):
            with self.assertRaises(ValueError):
                render(user, root, url)
