"""Minimal ACP channel schema required by Account Factory OAuth activation."""

CHANNEL_SCHEMA = """
CREATE TABLE IF NOT EXISTS channel (
    id                  TEXT PRIMARY KEY,
    code                TEXT UNIQUE NOT NULL,
    platform            TEXT NOT NULL DEFAULT 'threads',
    handle              TEXT NOT NULL,
    external_user_id    TEXT,
    status              TEXT NOT NULL DEFAULT 'ACTIVE',
    token_encrypted     BLOB,
    token_expires_at    TEXT,
    daily_post_cap      INTEGER NOT NULL DEFAULT 3,
    min_gap_minutes     INTEGER NOT NULL DEFAULT 90,
    niches              TEXT NOT NULL DEFAULT '[]',
    created_at          TEXT NOT NULL
);
"""


def ensure_factory_channel_schema(conn) -> None:
    conn.executescript(CHANNEL_SCHEMA)
