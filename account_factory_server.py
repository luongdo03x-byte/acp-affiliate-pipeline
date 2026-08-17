#!/usr/bin/env python3
"""Run ACP dashboard with Account Factory OAuth routes registered.

This launcher deliberately reuses the existing ACP Flask app and database. It
adds onboarding routes only; it does not enable live publishing or seed data.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from acp.web.account_factory import register_account_factory_routes  # noqa: E402
from acp.web.server import create_app  # noqa: E402


def build_app():
    app = create_app()
    register_account_factory_routes(app)
    return app


if __name__ == "__main__":
    host = os.environ.get("ACP_HOST", "127.0.0.1")
    port = int(os.environ.get("ACP_PORT", "5000"))
    build_app().run(host=host, port=port, debug=False)
