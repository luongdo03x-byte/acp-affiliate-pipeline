"""Minimal Flask application for Account Factory only.

This app deliberately excludes ACP publishing/dashboard routes. Factory V2 and
Account Factory OAuth routes are registered by ``account_factory_server.py``.
"""
from flask import Flask, jsonify


def create_factory_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def root():
        return jsonify(ok=True, service="account-factory")

    @app.get("/healthz")
    def healthz():
        return jsonify(ok=True, service="account-factory")

    return app
