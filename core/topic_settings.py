"""Runtime-configurable thresholds for Dynamic Topic discovery.

Defaults remain the approved 5 / 0.80 / 0.92 behavior, but operators can tune
future discovery through ``system_setting`` without a code deploy.
"""
from __future__ import annotations

from . import topic_engine

_INSTALLED = False

_DEFAULTS = {
    "topic.auto_cluster_min": 5,
    "topic.auto_confidence_min": 0.80,
    "topic.auto_merge_similarity": 0.92,
    "topic.duplicate_hint_similarity": 0.80,
}


def _setting(conn, key: str, default):
    row = conn.execute("SELECT value FROM system_setting WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _bounded_int(value, default: int, minimum: int = 2, maximum: int = 100) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, number))


def _bounded_float(value, default: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, number))


def apply(conn) -> dict:
    values = {
        "cluster_min": _bounded_int(
            _setting(conn, "topic.auto_cluster_min", _DEFAULTS["topic.auto_cluster_min"]),
            _DEFAULTS["topic.auto_cluster_min"],
        ),
        "confidence_min": _bounded_float(
            _setting(conn, "topic.auto_confidence_min", _DEFAULTS["topic.auto_confidence_min"]),
            _DEFAULTS["topic.auto_confidence_min"],
        ),
        "merge_similarity": _bounded_float(
            _setting(conn, "topic.auto_merge_similarity", _DEFAULTS["topic.auto_merge_similarity"]),
            _DEFAULTS["topic.auto_merge_similarity"],
        ),
        "duplicate_hint_similarity": _bounded_float(
            _setting(conn, "topic.duplicate_hint_similarity", _DEFAULTS["topic.duplicate_hint_similarity"]),
            _DEFAULTS["topic.duplicate_hint_similarity"],
        ),
    }
    topic_engine.AUTO_CLUSTER_MIN = values["cluster_min"]
    topic_engine.AUTO_CONFIDENCE_MIN = values["confidence_min"]
    topic_engine.AUTO_MERGE_SIMILARITY = values["merge_similarity"]
    topic_engine.DUPLICATE_HINT_SIMILARITY = values["duplicate_hint_similarity"]
    return values


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_discover = topic_engine.discover_dynamic_topics
    original_find_or_create = topic_engine.find_or_create_dynamic_topic

    def discover_dynamic_topics(conn):
        apply(conn)
        return original_discover(conn)

    def find_or_create_dynamic_topic(conn, **kwargs):
        apply(conn)
        return original_find_or_create(conn, **kwargs)

    topic_engine.discover_dynamic_topics = discover_dynamic_topics
    topic_engine.find_or_create_dynamic_topic = find_or_create_dynamic_topic
    _INSTALLED = True
