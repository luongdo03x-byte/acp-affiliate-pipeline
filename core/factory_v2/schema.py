"""SQLite schema for Account Factory V2 controller state."""

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS factory_batch (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    target_count INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    paused_at TEXT,
    desired_max_workers INTEGER,
    reminder_interval_minutes INTEGER NOT NULL DEFAULT 10,
    created_by_device_id TEXT
);

CREATE TABLE IF NOT EXISTS factory_worker (
    id TEXT PRIMARY KEY,
    runner_type TEXT NOT NULL DEFAULT 'REMOTE_AVD',
    avd_name TEXT UNIQUE,
    adb_serial TEXT UNIQUE,
    device_id TEXT UNIQUE,
    device_name TEXT,
    state TEXT NOT NULL,
    current_account_id TEXT,
    current_job_id TEXT,
    pid INTEGER,
    started_at TEXT,
    last_heartbeat_at TEXT,
    last_progress_at TEXT,
    processed_count INTEGER NOT NULL DEFAULT 0,
    recovery_count INTEGER NOT NULL DEFAULT 0,
    estimated_ram_mb INTEGER,
    current_ram_mb INTEGER,
    current_cpu_percent REAL,
    draining INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS factory_account (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES factory_batch(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    group_no INTEGER NOT NULL,
    username TEXT NOT NULL,
    display_name TEXT NOT NULL,
    bio TEXT,
    gender_profile TEXT,
    primary_niche TEXT,
    secondary_interest TEXT,
    personality_style TEXT,
    content_tone TEXT,
    avatar_type TEXT,
    avatar_theme TEXT,
    avatar_prompt TEXT,
    avatar_file TEXT,
    stage TEXT NOT NULL,
    last_safe_stage TEXT NOT NULL,
    execution_target TEXT,
    assigned_worker_id TEXT REFERENCES factory_worker(id),
    current_job_id TEXT,
    oauth_session_id TEXT,
    threads_user_id TEXT,
    channel_id TEXT,
    channel_code TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error_code TEXT,
    last_error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(batch_id, sequence),
    UNIQUE(batch_id, username)
);

CREATE TABLE IF NOT EXISTS factory_job (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES factory_account(id) ON DELETE CASCADE,
    worker_id TEXT NOT NULL REFERENCES factory_worker(id),
    runner_type TEXT,
    lease_token TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    desired_action TEXT,
    observed_state TEXT,
    command_id TEXT,
    leased_at TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    heartbeat_at TEXT,
    attempt INTEGER NOT NULL DEFAULT 1,
    started_at TEXT,
    finished_at TEXT,
    error_code TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_factory_job_active_account
ON factory_job(account_id)
WHERE state IN ('LEASED','RUNNING','WAITING_HUMAN','RECOVERING');
CREATE INDEX IF NOT EXISTS idx_factory_job_worker_state ON factory_job(worker_id, state);
CREATE INDEX IF NOT EXISTS idx_factory_account_batch_stage ON factory_account(batch_id, stage);

CREATE TABLE IF NOT EXISTS factory_checkpoint (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES factory_batch(id) ON DELETE CASCADE,
    account_id TEXT NOT NULL REFERENCES factory_account(id) ON DELETE CASCADE,
    worker_id TEXT REFERENCES factory_worker(id),
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    created_at TEXT NOT NULL,
    last_reminded_at TEXT,
    next_reminder_at TEXT,
    reminder_count INTEGER NOT NULL DEFAULT 0,
    snoozed_until TEXT,
    resolved_at TEXT,
    resolved_by_device_id TEXT,
    resolution TEXT
);
CREATE INDEX IF NOT EXISTS idx_factory_checkpoint_status ON factory_checkpoint(status, next_reminder_at);

CREATE TABLE IF NOT EXISTS factory_resource_sample (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    cpu_percent REAL NOT NULL,
    ram_total_mb INTEGER NOT NULL,
    ram_available_mb INTEGER NOT NULL,
    swap_used_mb INTEGER NOT NULL,
    swap_in_rate REAL NOT NULL DEFAULT 0,
    load_1m REAL NOT NULL DEFAULT 0,
    load_5m REAL NOT NULL DEFAULT 0,
    avd_total INTEGER NOT NULL DEFAULT 0,
    avd_running INTEGER NOT NULL DEFAULT 0,
    avd_waiting_human INTEGER NOT NULL DEFAULT 0,
    capacity_state TEXT NOT NULL,
    desired_workers INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_factory_resource_timestamp ON factory_resource_sample(timestamp);
"""


_WORKER_COLUMNS = (
    "id",
    "runner_type",
    "avd_name",
    "adb_serial",
    "device_id",
    "device_name",
    "state",
    "current_account_id",
    "current_job_id",
    "pid",
    "started_at",
    "last_heartbeat_at",
    "last_progress_at",
    "processed_count",
    "recovery_count",
    "estimated_ram_mb",
    "current_ram_mb",
    "current_cpu_percent",
    "draining",
    "last_error",
)


def _table_info(conn, table: str) -> dict[str, object]:
    return {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _worker_table_needs_rebuild(conn) -> bool:
    columns = _table_info(conn, "factory_worker")
    if not columns:
        return False
    avd_name = columns.get("avd_name")
    return (
        "runner_type" not in columns
        or "device_id" not in columns
        or "device_name" not in columns
        or (avd_name is not None and bool(avd_name[3]))
    )


def _rebuild_worker_table(conn) -> None:
    old_columns = _table_info(conn, "factory_worker")
    if not old_columns:
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("DROP TABLE IF EXISTS factory_worker_new")
        conn.execute(
            """CREATE TABLE factory_worker_new (
                id TEXT PRIMARY KEY,
                runner_type TEXT NOT NULL DEFAULT 'REMOTE_AVD',
                avd_name TEXT UNIQUE,
                adb_serial TEXT UNIQUE,
                device_id TEXT UNIQUE,
                device_name TEXT,
                state TEXT NOT NULL,
                current_account_id TEXT,
                current_job_id TEXT,
                pid INTEGER,
                started_at TEXT,
                last_heartbeat_at TEXT,
                last_progress_at TEXT,
                processed_count INTEGER NOT NULL DEFAULT 0,
                recovery_count INTEGER NOT NULL DEFAULT 0,
                estimated_ram_mb INTEGER,
                current_ram_mb INTEGER,
                current_cpu_percent REAL,
                draining INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            )"""
        )

        expressions = []
        for column in _WORKER_COLUMNS:
            if column == "runner_type":
                if column in old_columns:
                    expressions.append(
                        "COALESCE(runner_type, CASE WHEN avd_name IS NOT NULL THEN 'REMOTE_AVD' ELSE 'LOCAL_DEVICE' END)"
                    )
                else:
                    expressions.append("'REMOTE_AVD'")
            elif column in old_columns:
                expressions.append(column)
            elif column in {"processed_count", "recovery_count", "draining"}:
                expressions.append("0")
            else:
                expressions.append("NULL")

        conn.execute(
            f"""INSERT INTO factory_worker_new ({','.join(_WORKER_COLUMNS)})
                SELECT {','.join(expressions)} FROM factory_worker"""
        )
        conn.execute("DROP TABLE factory_worker")
        conn.execute("ALTER TABLE factory_worker_new RENAME TO factory_worker")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _add_column_if_missing(conn, table: str, column: str, ddl: str) -> None:
    if column not in _table_info(conn, table):
        conn.execute(ddl)


def _migrate_runner_columns(conn) -> None:
    if _worker_table_needs_rebuild(conn):
        _rebuild_worker_table(conn)

    _add_column_if_missing(
        conn,
        "factory_account",
        "execution_target",
        "ALTER TABLE factory_account ADD COLUMN execution_target TEXT",
    )
    _add_column_if_missing(
        conn,
        "factory_job",
        "runner_type",
        "ALTER TABLE factory_job ADD COLUMN runner_type TEXT",
    )

    conn.execute(
        """UPDATE factory_worker
           SET runner_type=CASE
               WHEN runner_type IS NOT NULL AND runner_type != '' THEN runner_type
               WHEN avd_name IS NOT NULL THEN 'REMOTE_AVD'
               ELSE 'LOCAL_DEVICE'
           END"""
    )
    conn.execute(
        """UPDATE factory_job
           SET runner_type=COALESCE(
               runner_type,
               (SELECT w.runner_type FROM factory_worker w WHERE w.id=factory_job.worker_id),
               'REMOTE_AVD'
           )"""
    )


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)
    _migrate_runner_columns(conn)
