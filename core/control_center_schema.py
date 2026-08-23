"""Schema registrations for Dynamic Topics and Auto Posting Control Center.

`core.db` remains the only schema/migration runner. This module appends additive
DDL at package import time, following the same pattern as `shopee_schema.py`.
"""
from . import db


_DDL = """

CREATE TABLE IF NOT EXISTS topic (
    id                      TEXT PRIMARY KEY,
    code                    TEXT UNIQUE NOT NULL,
    name                    TEXT NOT NULL,
    topic_type              TEXT NOT NULL DEFAULT 'AUTO',
    parent_id               TEXT REFERENCES topic(id),
    status                  TEXT NOT NULL DEFAULT 'ACTIVE',
    confidence              REAL,
    product_count           INTEGER NOT NULL DEFAULT 0,
    duplicate_candidate_of  TEXT REFERENCES topic(id),
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_topic_parent_status
    ON topic(parent_id, status, name);
CREATE INDEX IF NOT EXISTS idx_topic_type_status
    ON topic(topic_type, status, name);

CREATE TABLE IF NOT EXISTS topic_alias (
    alias_normalized  TEXT PRIMARY KEY,
    alias_display     TEXT NOT NULL,
    topic_id          TEXT NOT NULL REFERENCES topic(id),
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_topic_alias_topic ON topic_alias(topic_id);

CREATE TABLE IF NOT EXISTS product_topic (
    product_id   TEXT NOT NULL REFERENCES product(id),
    topic_id     TEXT NOT NULL REFERENCES topic(id),
    confidence   REAL NOT NULL DEFAULT 1.0,
    source       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (product_id, topic_id)
);
CREATE INDEX IF NOT EXISTS idx_product_topic_topic
    ON product_topic(topic_id, product_id);

CREATE TABLE IF NOT EXISTS channel_topic_rule (
    channel_id   TEXT NOT NULL REFERENCES channel(id),
    topic_id     TEXT NOT NULL REFERENCES topic(id),
    rule_mode    TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (channel_id, topic_id, rule_mode)
);
CREATE INDEX IF NOT EXISTS idx_channel_topic_rule_channel
    ON channel_topic_rule(channel_id, rule_mode);

CREATE TABLE IF NOT EXISTS auto_post_plan (
    id                      TEXT PRIMARY KEY,
    channel_id              TEXT NOT NULL REFERENCES channel(id),
    scheduled_at            TEXT NOT NULL,
    product_id              TEXT REFERENCES product(id),
    post_id                 TEXT REFERENCES post(id),
    publish_target_id       TEXT REFERENCES publish_target(id),
    state                   TEXT NOT NULL DEFAULT 'PLANNED',
    content_revision        INTEGER NOT NULL DEFAULT 1,
    generated_at            TEXT NOT NULL,
    last_reconciled_at      TEXT,
    replacement_count       INTEGER NOT NULL DEFAULT 0,
    last_change_reason      TEXT,
    product_price_snapshot  INTEGER,
    product_image_snapshot  TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_auto_post_plan_target
    ON auto_post_plan(publish_target_id)
    WHERE publish_target_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_auto_post_plan_window
    ON auto_post_plan(channel_id, scheduled_at, state);
"""


def register() -> None:
    if "CREATE TABLE IF NOT EXISTS topic (" not in db.SCHEMA:
        db.SCHEMA += _DDL


register()
