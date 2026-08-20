import io
import json
import unittest

from core.factory_v2.worker_process import WorkerProcessManager


class FakeStdin(io.StringIO):
    def close(self):
        self.was_closed = True


class FakeProcess:
    def __init__(self):
        self.pid = 9876
        self.stdin = FakeStdin()
        self.stdout = io.StringIO(json.dumps({
            "ok": True,
            "heartbeat": {
                "worker_id": "worker-01",
                "adb_serial": "emulator-5554",
                "state": "READY",
                "current_account_id": None,
                "current_job_id": None,
                "observed_state": None,
                "last_progress_at": "2026-08-17T06:00:00+00:00"
            }
        }) + "\n")
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


class WorkerProcessTransportTests(unittest.TestCase):
    def test_start_uses_safe_env_and_heartbeat_roundtrip(self):
        calls = []
        process = FakeProcess()

        def fake_popen(argv, **kwargs):
            calls.append((argv, kwargs))
            return process

        manager = WorkerProcessManager(
            popen_factory=fake_popen,
            line_reader=lambda stream, timeout: stream.readline(),
            base_env={
                "PATH": "/usr/bin",
                "HOME": "/tmp/home",
                "ANDROID_HOME": "/opt/android",
                "THREADS_APP_SECRET": "must-not-leak",
                "ACP_MASTER_KEY": "must-not-leak",
                "ACP_FACTORY_API_KEY": "must-not-leak",
            },
        )

        manager.start("worker-01", "acp-worker-01", "emulator-5554")
        heartbeat = manager.heartbeat("worker-01")

        argv, kwargs = calls[0]
        self.assertIn("--worker-id", argv)
        self.assertIn("worker-01", argv)
        self.assertIn("--avd-name", argv)
        self.assertIn("acp-worker-01", argv)
        self.assertIn("--serial", argv)
        self.assertIn("emulator-5554", argv)
        self.assertNotIn("THREADS_APP_SECRET", kwargs["env"])
        self.assertNotIn("ACP_MASTER_KEY", kwargs["env"])
        self.assertNotIn("ACP_FACTORY_API_KEY", kwargs["env"])
        self.assertEqual("worker-01", heartbeat["worker_id"])
        self.assertEqual("READY", heartbeat["state"])

        written = process.stdin.getvalue()
        command = json.loads(written.strip())
        self.assertEqual("HEARTBEAT", command["action"])
        self.assertTrue(command["command_id"])

    def test_stop_terminates_worker_process(self):
        process = FakeProcess()
        manager = WorkerProcessManager(
            popen_factory=lambda argv, **kwargs: process,
            line_reader=lambda stream, timeout: stream.readline(),
            base_env={"PATH": "/usr/bin"},
        )
        manager.start("worker-01", "acp-worker-01", "emulator-5554")

        manager.stop("worker-01")

        self.assertTrue(process.terminated)
        self.assertFalse(manager.is_running("worker-01"))


if __name__ == "__main__":
    unittest.main()
