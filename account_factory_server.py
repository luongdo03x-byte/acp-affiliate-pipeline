#!/usr/bin/env python3
"""Run the dedicated Account Factory API/controller service.

The Flask app itself stays side-effect free for tests. When this file is run as
a program, the dedicated Account Factory controller runtime is started in a
daemon thread unless ACP_FACTORY_CONTROLLER=0. The runtime never enables live
Threads publishing and does not register ACP publishing/dashboard routes.
"""
import atexit
import os
from pathlib import Path
import sys
import threading
import types

_REPO_ROOT = Path(__file__).resolve().parent

# Some V2 modules intentionally use top-level imports such as ``core.db`` while
# the web package uses package-relative imports under ``acp``. Make both import
# styles resolve from the repository itself, regardless of the checkout or
# worktree directory name and regardless of the caller's current directory.
_repo_root_str = str(_REPO_ROOT)
if _repo_root_str not in sys.path:
    sys.path.insert(0, _repo_root_str)

# Git worktrees/checkouts are not guaranteed to be named ``acp``, so expose the
# current repository root as that package name before importing application
# modules. This keeps the launcher portable across paths such as
# ``worktrees/account-factory-android`` and CI checkouts.
if "acp" not in sys.modules:
    acp_package = types.ModuleType("acp")
    acp_package.__file__ = str(_REPO_ROOT / "__init__.py")
    acp_package.__package__ = "acp"
    acp_package.__path__ = [_repo_root_str]
    sys.modules["acp"] = acp_package

from acp.core.factory_v2.portable_state import require_active_ownership  # noqa: E402
from acp.core.factory_v2.runtime import build_default_runtime  # noqa: E402
from acp.web.account_factory import register_account_factory_routes  # noqa: E402
from acp.web.factory_app import create_factory_app  # noqa: E402
from acp.web.factory_enrollment import (  # noqa: E402
    install_factory_device_auth,
    register_factory_enrollment_routes,
)
from acp.web.factory_v2 import register_factory_v2_routes  # noqa: E402


def _default_ownership_guard():
    base = Path(os.environ.get("ACP_BASE", str(Path.home() / "Downloads" / "ACP")))
    require_active_ownership(base / "shared" / "machine.json")


def build_app(
    *,
    start_controller=False,
    runtime_factory=build_default_runtime,
    ownership_guard=_default_ownership_guard,
):
    app = create_factory_app()
    install_factory_device_auth()
    register_account_factory_routes(app)
    register_factory_enrollment_routes(app)
    register_factory_v2_routes(app)
    if start_controller:
        ownership_guard()
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
