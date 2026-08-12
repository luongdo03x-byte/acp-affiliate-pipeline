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

CREATE TABLE IF NOT EXISTS product_sync_lock (
    name      TEXT PRIMARY KEY,
    locked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  TEXT NOT NULL REFERENCES product(id),
    price       INTEGER NOT NULL,
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pph_product ON product_price_history(product_id, observed_at);

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
    product_id            TEXT REFERENCES product(id),  -- NULL cho bài không bán hàng (post_type='VALUE')
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
    post_type             TEXT NOT NULL DEFAULT 'SALES',  -- SALES | VALUE (bài không bán hàng)
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
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
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


PRODUCT_MIGRATIONS = [
    # Catalog fields are added one at a time so existing product data stays intact.
    ("product", "provider", "ALTER TABLE product ADD COLUMN provider TEXT"),
    ("product", "shop_name", "ALTER TABLE product ADD COLUMN shop_name TEXT"),
    ("product", "detail_link", "ALTER TABLE product ADD COLUMN detail_link TEXT"),
    ("product", "main_image_url", "ALTER TABLE product ADD COLUMN main_image_url TEXT"),
    ("product", "sale_region", "ALTER TABLE product ADD COLUMN sale_region TEXT"),
    ("product", "currency", "ALTER TABLE product ADD COLUMN currency TEXT"),
    ("product", "price_min", "ALTER TABLE product ADD COLUMN price_min INTEGER"),
    ("product", "price_max", "ALTER TABLE product ADD COLUMN price_max INTEGER"),
    ("product", "original_price_min", "ALTER TABLE product ADD COLUMN original_price_min INTEGER"),
    ("product", "original_price_max", "ALTER TABLE product ADD COLUMN original_price_max INTEGER"),
    ("product", "commission_rate_raw", "ALTER TABLE product ADD COLUMN commission_rate_raw INTEGER"),
    ("product", "commission_rate_percent", "ALTER TABLE product ADD COLUMN commission_rate_percent REAL"),
    ("product", "commission_amount", "ALTER TABLE product ADD COLUMN commission_amount INTEGER"),
    ("product", "commission_currency", "ALTER TABLE product ADD COLUMN commission_currency TEXT"),
    ("product", "units_sold", "ALTER TABLE product ADD COLUMN units_sold INTEGER"),
    ("product", "has_inventory", "ALTER TABLE product ADD COLUMN has_inventory INTEGER"),
    ("product", "category_data", "ALTER TABLE product ADD COLUMN category_data TEXT"),
    ("product", "score", "ALTER TABLE product ADD COLUMN score REAL"),
    ("product", "affiliate_url", "ALTER TABLE product ADD COLUMN affiliate_url TEXT"),
    ("product", "affiliate_short_url", "ALTER TABLE product ADD COLUMN affiliate_short_url TEXT"),
    ("product", "affiliate_link_status",
     "ALTER TABLE product ADD COLUMN affiliate_link_status TEXT NOT NULL DEFAULT 'NOT_CREATED'"),
    ("product", "affiliate_link_error", "ALTER TABLE product ADD COLUMN affiliate_link_error TEXT"),
    ("product", "first_seen_at", "ALTER TABLE product ADD COLUMN first_seen_at TEXT"),
    ("product", "last_seen_at", "ALTER TABLE product ADD COLUMN last_seen_at TEXT"),
    ("product", "last_synced_at", "ALTER TABLE product ADD COLUMN last_synced_at TEXT"),
    ("product", "affiliate_link_created_at", "ALTER TABLE product ADD COLUMN affiliate_link_created_at TEXT"),
    ("product", "last_posted_at", "ALTER TABLE product ADD COLUMN last_posted_at TEXT"),
    ("product", "post_count", "ALTER TABLE product ADD COLUMN post_count INTEGER NOT NULL DEFAULT 0"),
]


MIGRATIONS = [
    # (bảng, cột, câu lệnh) -- chạy được nhiều lần, bỏ qua nếu cột đã có.
    ("channel", "niches", "ALTER TABLE channel ADD COLUMN niches TEXT NOT NULL DEFAULT '[]'"),
    ("post", "post_type", "ALTER TABLE post ADD COLUMN post_type TEXT NOT NULL DEFAULT 'SALES'"),
] + PRODUCT_MIGRATIONS


def _rebuild_post_table(conn) -> None:
    """Dựng lại bảng post để bỏ NOT NULL trên product_id -- SQLite không bỏ được
    ràng buộc này bằng ALTER TABLE. Giữ nguyên toàn bộ bản ghi cũ.

    post_metrics và conversion có FK trỏ vào post(id). Theo đúng quy trình SQLite
    khuyến nghị cho kiểu đổi schema này (sqlite.org/lang_altertable.html mục
    "Making Other Kinds Of Table Schema Changes"):
      - legacy_alter_table=ON để RENAME không tự viết lại FK của post_metrics/
        conversion thành "post_old" (mặc định SQLite sẽ làm vậy, và sau khi
        post_old bị DROP thì FK đó trỏ vào một bảng không còn tồn tại).
      - foreign_keys=OFF để DROP TABLE post_old không bị chặn bởi chính FK mà
        post_metrics/conversion đang giữ.
    Bật lại cả hai ngay sau khi xong, kể cả khi có lỗi giữa chừng.

    KHÔNG THỂ HOÀN TÁC: bảng cũ bị DROP ở cuối. Sao lưu var/acp.db trước khi gọi
    init_db() lần đầu trên một CSDL đã có dữ liệu.
    """
    conn.execute("PRAGMA legacy_alter_table = ON")
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("ALTER TABLE post RENAME TO post_old")
        # Tạo lại bảng post đúng theo SCHEMA hiện hành (đã có product_id nullable).
        post_ddl = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS post ("):]
        post_ddl = post_ddl[:post_ddl.index(";") + 1]
        conn.executescript(post_ddl)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(post_old)").fetchall()]
        col_list = ", ".join(cols)
        conn.execute(f"INSERT INTO post ({col_list}) SELECT {col_list} FROM post_old")
        conn.execute("DROP TABLE post_old")  # cũng xoá luôn các index cũ (đi theo bảng khi RENAME)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_post_status ON post(status, scheduled_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_post_product ON post(product_id, published_at)")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA legacy_alter_table = OFF")


def migrate(conn) -> list:
    """Nâng cấp schema cho CSDL đã tồn tại. Không xoá hay ghi đè dữ liệu, trừ
    _rebuild_post_table() -- xem cảnh báo trong docstring của hàm đó."""
    applied = []
    for table, column, sql in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if cols and column not in cols:
            conn.execute(sql)
            applied.append(f"{table}.{column}")

    product_cols = {r[1] for r in conn.execute("PRAGMA table_info(product)").fetchall()}
    if {"provider", "source", "merchant"} <= product_cols:
        conn.execute("""UPDATE product
                        SET provider=COALESCE(
                            provider,
                            'LEGACY_' || length(source) || ':' || source || ':' ||
                            length(merchant) || ':' || merchant
                        )""")
    if {"first_seen_at", "created_at"} <= product_cols:
        conn.execute("UPDATE product SET first_seen_at=COALESCE(first_seen_at, created_at)")
    if {"provider", "external_product_id"} <= product_cols:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_product_provider_external "
                     "ON product(provider, external_product_id)")

    post_cols = conn.execute("PRAGMA table_info(post)").fetchall()
    product_id_col = next((r for r in post_cols if r[1] == "product_id"), None)
    if product_id_col and product_id_col[3]:  # notnull=1 -> bảng cũ, cần dựng lại
        _rebuild_post_table(conn)
        applied.append("post.product_id (bỏ NOT NULL)")
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
