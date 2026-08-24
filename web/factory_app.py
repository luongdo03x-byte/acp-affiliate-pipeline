"""Minimal Flask application for Account Factory only.

This app deliberately excludes ACP publishing/dashboard routes. Factory V2 and
Account Factory OAuth routes are registered by ``account_factory_server.py``.
"""
import os

from flask import Flask, jsonify, render_template


def create_factory_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def root():
        return jsonify(ok=True, service="account-factory")

    @app.get("/healthz")
    def healthz():
        return jsonify(ok=True, service="account-factory")

    def legal_context():
        return {
            "support_email": os.environ.get("ACP_SUPPORT_EMAIL", "").strip(),
        }

    @app.get("/privacy")
    def privacy_policy():
        return render_template("privacy_policy.html", **legal_context())

    @app.get("/data-deletion")
    def data_deletion():
        return render_template("data_deletion.html", **legal_context())

    return app
