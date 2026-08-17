"""Lớp truy cập dữ liệu.

Dùng SQLite để prototype chạy được không cần cài đặt gì. Schema viết bám sát
mục 6 của BRD v2 nên port sang PostgreSQL chỉ là đổi kiểu dữ liệu:
    TEXT (ULID)      -> UUID / TEXT
    TEXT (ISO8601)   -> TIMESTAMPTZ
    TEXT (JSON)      -> JSONB
    INTEGER (0/1)    -> BOOLEAN
"""
import json
import os
import sqlite3
import time
import random
import string
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.environ.get("ACP_DB", os.path.join(os.path.dirname(os.path.dirname(__file__)), "var", "acp.db"))

_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid() -> str:
    """ULID rút gọn: 10 ký tự thời gian + 12 ký tự ngẫu nhiên. Sắp xếp được theo thời gian."""
    ts = int(time.time() * 1000)
    head = ""
    for _ in range(10):
        head = _ULID_ALPHABET[ts % 32] + head
        ts //= 32
    tail = "".join(random.choice(_ULID_ALPHABET) for _ in range(12))
    return head + tail


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS campaign (
    id              TEXT PRIMARY KEY,
    code            TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    niche           TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product (
    id                  TEXT PRIMARY KEY,
    source              TEXT NOT NULL,
    merchant            TEXT NOT NULL,
    external_product_id TEXT NOT NULL,
    name                TEXT NOT NULL,
    description         TEXT,
    current_price       INTEGER NOT NULL,
    original_price      INTEGER,
    commission_value    INTEGER NOT NULL,
    commission_rate     REAL,
    category_code       TEXT NOT NULL,
    rating              REAL,
    review_count        INTEGER DEFAULT 0,
    sold_count          INTEGER DEFAULT 0,
    image_url_original  TEXT,
    image_path_local    TEXT,
    product_url         TEXT NOT NULL,
    is_available        INTEGER NOT NULL DEFAULT 1,
    last_seen_at        TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (source, merchant, external_product_id)
);

CREATE TABLE IF NOT EXISTS product_price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  TEXT NOT NULL REFERENCES product(id),
    price       INTEGER NOT NULL,
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pph_product ON product_price_history(product_id, observed_at);

CREATE TABLE IF NOT EXISTS product_facts (
    product_id      TEXT PRIMARY KEY REFERENCES product(id),
    facts_json      TEXT NOT NULL,
    unknown_json    TEXT NOT NULL,
    category        TEXT,
    source_hash     TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    extracted_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channel (
    id                  TEXT PRIMARY KEY,
    code                TEXT UNIQUE NOT NULL,
    platform            TEXT NOT NULL DEFAULT 'threads',
    handle              TEXT NOT NULL,
    external_user_id    TEXT,
    status              TEXT NOT NULL DEFAULT 'ACTIVE',
    token_encrypted     BLOB,
    token_expires_at    TEXT,
    daily_post_cap      INTEGER NOT NULL DEFAULT 12,
    min_gap_minutes     INTEGER NOT NULL DEFAULT 90,
    niches              TEXT NOT NULL DEFAULT '[]',   -- JSON: chủ đề riêng của kênh này
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS caption_template (
    id          TEXT PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    body        TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS post (
    id                    TEXT PRIMARY KEY,
    product_id            TEXT NOT NULL REFERENCES product(id),
    channel_id            TEXT NOT NULL REFERENCES channel(id),
    campaign_id           TEXT NOT NULL REFERENCES campaign(id),
    caption_template_id   TEXT REFERENCES caption_template(id),
    variant_code          TEXT NOT NULL,
    caption_body          TEXT NOT NULL,
    disclosure_text       TEXT NOT NULL,
    caption_final         TEXT NOT NULL,
    image_url_composited  TEXT,
    affiliate_link        TEXT,
    sub_id_payload        TEXT,
    score                 REAL,
    status                TEXT NOT NULL DEFAULT 'DRAFT',
    scheduled_at          TEXT,
    published_at          TEXT,
    thread_id             TEXT,
    reviewed_by           TEXT,
    reviewed_at           TEXT,
    reject_reason         TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    CHECK (length(disclosure_text) > 0),
    CHECK (length(caption_final) <= 500)
);
CREATE INDEX IF NOT EXISTS idx_post_status ON post(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_post_product ON post(product_id, published_at);

CREATE TABLE IF NOT EXISTS post_metrics (
    post_id     TEXT PRIMARY KEY REFERENCES post(id),
    views       INTEGER DEFAULT 0,
    likes       INTEGER DEFAULT 0,
    replies     INTEGER DEFAULT 0,
    reposts     INTEGER DEFAULT 0,
    clicks      INTEGER DEFAULT 0,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS publish_target (
    id                TEXT PRIMARY KEY,
    post_id           TEXT NOT NULL REFERENCES post(id),
    channel_id        TEXT NOT NULL REFERENCES channel(id),
    status            TEXT NOT NULL DEFAULT 'PENDING',
    scheduled_at      TEXT,
    external_post_id  TEXT,
    last_error        TEXT,
    attempt_count     INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_publish_target_post   ON publish_target(post_id);
CREATE INDEX IF NOT EXISTS idx_publish_target_status ON publish_target(status, scheduled_at);

CREATE TABLE IF NOT EXISTS post_channel_selection (
    post_id     TEXT NOT NULL REFERENCES post(id),
    channel_id  TEXT NOT NULL REFERENCES channel(id),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (post_id, channel_id)
);

CREATE TABLE IF NOT EXISTS media_asset (
    id          TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    source      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS post_media (
    post_id         TEXT NOT NULL REFERENCES post(id),
    media_asset_id  TEXT NOT NULL REFERENCES media_asset(id),
    position        INTEGER NOT NULL,
    PRIMARY KEY (post_id, media_asset_id)
);
CREATE INDEX IF NOT EXISTS idx_post_media_post ON post_media(post_id, position);

CREATE TABLE IF NOT EXISTS account_group (
    id          TEXT PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_group_channel (
    group_id    TEXT NOT NULL REFERENCES account_group(id),
    channel_id  TEXT NOT NULL REFERENCES channel(id),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (group_id, channel_id)
);

CREATE TABLE IF NOT EXISTS meta_connection (
    id              TEXT PRIMARY KEY,
    provider        TEXT NOT NULL DEFAULT 'meta',
    token_encrypted BLOB NOT NULL,
    meta_user_id    TEXT,
    status          TEXT NOT NULL DEFAULT 'ACTIVE',
    expires_at      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversion (
    id                  TEXT PRIMARY KEY,
    post_id             TEXT REFERENCES post(id),
    transaction_id      TEXT NOT NULL,
    external_product_id TEXT NOT NULL,
    sale_amount         INTEGER NOT NULL,
    commission          INTEGER NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    converted_at        TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    raw_payload         TEXT,
    UNIQUE (transaction_id, external_product_id)
);
CREATE INDEX IF NOT EXISTS idx_conv_post ON conversion(post_id, status);

CREATE TABLE IF NOT EXISTS job_queue (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type          TEXT NOT NULL,
    payload           TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'READY',
    priority          INTEGER NOT NULL DEFAULT 0,
    attempt_count     INTEGER NOT NULL DEFAULT 0,
    max_attempts      INTEGER NOT NULL DEFAULT 3,
    run_after         TEXT NOT NULL,
    locked_at         TEXT,
    locked_by         TEXT,
    last_error        TEXT,
    idempotency_key   TEXT UNIQUE,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_ready ON job_queue(status, run_after, priority);

CREATE TABLE IF NOT EXISTS scoring_config (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    version     INTEGER NOT NULL,
    weights     TEXT NOT NULL,
    filters     TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 0,
    note        TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity      TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    action      TEXT NOT NULL,
    actor       TEXT,
    detail      TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity, entity_id);

CREATE TABLE IF NOT EXISTS system_setting (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  TEXT
);

CREATE TABLE IF NOT EXISTS content_generation_run (
    id          TEXT PRIMARY KEY,
    post_id     TEXT NOT NULL REFERENCES post(id),
    status      TEXT NOT NULL DEFAULT 'READY',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS content_variant_row (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES content_generation_run(id),
    label           TEXT NOT NULL,
    angle           TEXT NOT NULL,
    hook            TEXT NOT NULL,
    main_message    TEXT NOT NULL,
    body_json       TEXT NOT NULL,
    cta             TEXT NOT NULL,
    structure       TEXT NOT NULL,
    rule_score      REAL,
    hybrid_score    REAL,
    final_score     REAL,
    is_best         INTEGER NOT NULL DEFAULT 0,
    manual_edited   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_content_variant_run ON content_variant_row(run_id);
"""


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE giữ khoá ghi ngay từ đầu -- tương đương SELECT ... FOR UPDATE
    của PostgreSQL, cần thiết để nhiều worker không giành cùng một job."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


MIGRATIONS = [
    # (bảng, cột, câu lệnh) -- chạy được nhiều lần, bỏ qua nếu cột đã có.
    ("channel", "niches", "ALTER TABLE channel ADD COLUMN niches TEXT NOT NULL DEFAULT '[]'"),
    ("channel", "connection_id", "ALTER TABLE channel ADD COLUMN connection_id TEXT REFERENCES meta_connection(id)"),
    ("channel", "external_account_id", "ALTER TABLE channel ADD COLUMN external_account_id TEXT"),
    ("channel", "username", "ALTER TABLE channel ADD COLUMN username TEXT"),
    ("channel", "enabled", "ALTER TABLE channel ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"),
    ("channel", "last_sync_at", "ALTER TABLE channel ADD COLUMN last_sync_at TEXT"),
    ("post", "caption_facebook", "ALTER TABLE post ADD COLUMN caption_facebook TEXT"),
    ("post", "caption_instagram", "ALTER TABLE post ADD COLUMN caption_instagram TEXT"),
    ("publish_target", "caption_override", "ALTER TABLE publish_target ADD COLUMN caption_override TEXT"),
]


def migrate(conn) -> list:
    """Nâng cấp schema cho CSDL đã tồn tại. Không xoá hay ghi đè dữ liệu."""
    applied = []
    for table, column, sql in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if cols and column not in cols:
            conn.execute(sql)
            applied.append(f"{table}.{column}")
    return applied


def init_db() -> None:
    with session() as conn:
        conn.executescript(SCHEMA)
        applied = migrate(conn)
        if applied:
            print(f"  ↑ nâng cấp schema: {', '.join(applied)}")


def audit(conn, entity: str, entity_id: str, action: str, actor: str = "system", detail=None) -> None:
    conn.execute(
        "INSERT INTO audit_log (entity, entity_id, action, actor, detail, created_at) VALUES (?,?,?,?,?,?)",
        (entity, entity_id, action, actor, json.dumps(detail, ensure_ascii=False) if detail else None, now()),
    )
