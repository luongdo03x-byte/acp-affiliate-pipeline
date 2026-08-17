#!/usr/bin/env python3
"""Run ACP dashboard with Account Factory OAuth and V2 controller routes.

The Flask app itself stays side-effect free for tests. When this file is run as
a program, the dedicated Account Factory controller runtime is started in a
daemon thread unless ACP_FACTORY_CONTROLLER=0. The runtime never enables live
Threads publishing.
"""
import atexit
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from acp.core.factory_v2.runtime import build_default_runtime  # noqa: E402
from acp.web.account_factory import register_account_factory_routes  # noqa: E402
from acp.web.factory_v2 import register_factory_v2_routes  # noqa: E402
from acp.web.server import create_app  # noqa: E402


def build_app(*, start_controller=False, runtime_factory=build_default_runtime):
    app = create_app()
    register_account_factory_routes(app)
    register_factory_v2_routes(app)
    if start_controller:
        interval = float(os.environ.get("ACP_FACTORY_TICK_SECONDS", "2"))

        def run_controller():
            runtime = runtime_factory()
            app.extensions["factory_v2_runtime"] = runtime
            try:
                runtime.run_forever(interval_seconds=interval)
            finally:
                runtime.close()

        thread = threading.Thread(
            target=run_controller,
            name="factory-v2-controller",
            daemon=True,
        )
        app.extensions["factory_v2_controller_thread"] = thread

        def close_runtime():
            runtime = app.extensions.get("factory_v2_runtime")
            if runtime is not None:
                runtime.close()

        atexit.register(close_runtime)
        thread.start()
    return app


if __name__ == "__main__":
    host = os.environ.get("ACP_HOST", "127.0.0.1")
    port = int(os.environ.get("ACP_PORT", "5000"))
    start_controller = os.environ.get("ACP_FACTORY_CONTROLLER", "1").strip().lower() not in {"0", "false", "no"}
    build_app(start_controller=start_controller).run(host=host, port=port, debug=False)