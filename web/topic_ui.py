"""Jinja context for Dynamic Topic trees and channel inheritance state."""
from __future__ import annotations

from ..core import topic_engine
from ..core.db import connect


def _channel_state(conn):
    states = {}
    pool_counts = {}
    products = [
        row["id"] for row in conn.execute(
            "SELECT id FROM product WHERE provider='SHOPEE_AFFILIATE' AND is_available=1"
        ).fetchall()
    ]
    for channel in conn.execute("SELECT id FROM channel ORDER BY id").fetchall():
        rules = topic_engine.channel_rules(conn, channel["id"])
        states[channel["id"]] = {
            "includes": [item["code"] for item in rules["includes"]],
            "excludes": [item["code"] for item in rules["excludes"]],
        }
        count = 0
        for product_id in products:
            if topic_engine.channel_accepts_product(conn, channel["id"], product_id):
                count += 1
        pool_counts[channel["id"]] = count
    return states, pool_counts


def register_topic_ui(app):
    @app.context_processor
    def inject_dynamic_topics():
        conn = connect()
        try:
            topic_engine.ensure_system_topics(conn)
            tree = topic_engine.topic_tree(conn)
            states, pool_counts = _channel_state(conn)
            return {
                "dynamic_topic_tree": tree,
                "channel_topic_state": states,
                "channel_topic_pool": pool_counts,
            }
        except Exception:
            # Pages unrelated to topics must remain available while an operator
            # is still applying an additive schema migration.
            return {
                "dynamic_topic_tree": [],
                "channel_topic_state": {},
                "channel_topic_pool": {},
            }
        finally:
            conn.close()
