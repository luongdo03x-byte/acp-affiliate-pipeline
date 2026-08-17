import unittest

from account_factory_server import build_app


class FakeRuntime:
    def __init__(self):
        self.ran = False
        self.closed = False

    def run_forever(self, *, interval_seconds=2.0):
        self.ran = True

    def close(self):
        self.closed = True


class FactoryV2LauncherRuntimeTests(unittest.TestCase):
    def test_build_app_does_not_start_controller_by_default(self):
        calls = []
        app = build_app(runtime_factory=lambda: calls.append("built"))

        self.assertEqual([], calls)
        self.assertNotIn("factory_v2_runtime", app.extensions)

    def test_build_app_can_start_and_close_controller_runtime_explicitly(self):
        runtime = FakeRuntime()
        app = build_app(start_controller=True, runtime_factory=lambda: runtime)
        thread = app.extensions["factory_v2_controller_thread"]
        thread.join(timeout=1)

        self.assertTrue(runtime.ran)
        self.assertTrue(runtime.closed)
        self.assertIs(runtime, app.extensions["factory_v2_runtime"])
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
