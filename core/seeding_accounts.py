from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

_ONLINE_WINDOW_SECONDS = 120


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex


def ensure_account_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS seeding_account (
            id                    TEXT PRIMARY KEY,
            platform              TEXT NOT NULL DEFAULT 'facebook',
            label                 TEXT NOT NULL,
            extension_instance_id TEXT NOT NULL UNIQUE,
            enabled               INTEGER NOT NULL DEFAULT 1,
            last_seen_at          TEXT NOT NULL,
            created_at            TEXT NOT NULL,
            updated_at            TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_seed_account_enabled_seen
            ON seeding_account(enabled, last_seen_at);

        CREATE TABLE IF NOT EXISTS seeding_task_account (
            campaign_id  TEXT NOT NULL REFERENCES seeding_campaign(id),
            account_id   TEXT NOT NULL REFERENCES seeding_account(id),
            account_slot INTEGER NOT NULL,
            assigned_at  TEXT NOT NULL,
            PRIMARY KEY (campaign_id, account_slot),
            UNIQUE (campaign_id, account_id)
        );
        """
    )


def register_account(conn, *, instance_id: str, label: str) -> dict:
    ensure_account_schema(conn)
    instance = str(instance_id or "").strip()
    name = str(label or "").strip()
    if not instance:
        raise ValueError("extension_instance_id không được để trống")
    if not name:
        raise ValueError("Tên/nhãn tài khoản Facebook không được để trống")
    stamp = _stamp()
    existing = conn.execute(
        "SELECT id FROM seeding_account WHERE extension_instance_id=?", (instance,)
    ).fetchone()
    if existing:
        account_id = existing["id"]
        conn.execute(
            """UPDATE seeding_account
               SET label=?, enabled=1, last_seen_at=?, updated_at=?
               WHERE id=?""",
            (name, stamp, stamp, account_id),
        )
    else:
        account_id = _new_id()
        conn.execute(
            """INSERT INTO seeding_account
               (id,platform,label,extension_instance_id,enabled,last_seen_at,created_at,updated_at)
               VALUES (?,'facebook',?,?,1,?,?,?)""",
            (account_id, name, instance, stamp, stamp, stamp),
        )
    return dict(
        conn.execute(
            "SELECT * FROM seeding_account WHERE id=?", (account_id,)
        ).fetchone()
    )


def heartbeat_account(conn, *, instance_id: str) -> dict:
    ensure_account_schema(conn)
    instance = str(instance_id or "").strip()
    row = conn.execute(
        "SELECT id FROM seeding_account WHERE extension_instance_id=? AND enabled=1",
        (instance,),
    ).fetchone()
    if row is None:
        raise ValueError("Tài khoản chưa được kết nối")
    stamp = _stamp()
    conn.execute(
        "UPDATE seeding_account SET last_seen_at=?, updated_at=? WHERE id=?",
        (stamp, stamp, row["id"]),
    )
    return dict(
        conn.execute("SELECT * FROM seeding_account WHERE id=?", (row["id"],)).fetchone()
    )


def list_accounts(conn, *, reference_time: datetime | None = None) -> list[dict]:
    ensure_account_schema(conn)
    reference = (reference_time or _now()).astimezone(timezone.utc)
    rows = conn.execute(
        "SELECT * FROM seeding_account WHERE enabled=1 ORDER BY label COLLATE NOCASE, created_at"
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            seen = datetime.fromisoformat(item["last_seen_at"])
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            age = (reference - seen.astimezone(timezone.utc)).total_seconds()
            item["online"] = 0 <= age <= _ONLINE_WINDOW_SECONDS
        except (TypeError, ValueError):
            item["online"] = False
        out.append(item)
    return out


def list_task_accounts(conn, campaign_id: str) -> list[dict]:
    ensure_account_schema(conn)
    return [
        dict(row)
        for row in conn.execute(
            """SELECT m.campaign_id,m.account_id,m.account_slot,m.assigned_at,
                      a.label,a.extension_instance_id,a.enabled,a.last_seen_at
               FROM seeding_task_account m
               JOIN seeding_account a ON a.id=m.account_id
               WHERE m.campaign_id=?
               ORDER BY m.account_slot""",
            (campaign_id,),
        ).fetchall()
    ]


def _max_accounts(conn, campaign_id: str) -> int:
    row = conn.execute(
        "SELECT task_rules FROM seeding_campaign WHERE id=?", (campaign_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Không tìm thấy nhiệm vụ")
    try:
        rules = json.loads(row["task_rules"] or "{}")
    except (TypeError, json.JSONDecodeError):
        rules = {}
    try:
        return max(1, min(10, int(rules.get("max_accounts", 1))))
    except (TypeError, ValueError, AttributeError):
        return 1


def assign_task_accounts(conn, campaign_id: str, account_ids) -> list[dict]:
    ensure_account_schema(conn)
    ids = [
        str(value or "").strip()
        for value in (account_ids or [])
        if str(value or "").strip()
    ]
    if len(ids) != len(set(ids)):
        raise ValueError("Không được chọn trùng tài khoản")
    if len(ids) > _max_accounts(conn, campaign_id):
        raise ValueError("Số tài khoản vượt quá giới hạn của nhiệm vụ")
    if ids:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id FROM seeding_account WHERE enabled=1 AND id IN ({placeholders})",
            ids,
        ).fetchall()
        if {row["id"] for row in rows} != set(ids):
            raise ValueError("Có tài khoản không hợp lệ hoặc đã bị tắt")
    conn.execute("SAVEPOINT seeding_task_accounts")
    try:
        conn.execute(
            "DELETE FROM seeding_task_account WHERE campaign_id=?", (campaign_id,)
        )
        stamp = _stamp()
        for slot, account_id in enumerate(ids, start=1):
            conn.execute(
                """INSERT INTO seeding_task_account(campaign_id,account_id,account_slot,assigned_at)
                   VALUES (?,?,?,?)""",
                (campaign_id, account_id, slot, stamp),
            )
        conn.execute("RELEASE SAVEPOINT seeding_task_accounts")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT seeding_task_accounts")
        conn.execute("RELEASE SAVEPOINT seeding_task_accounts")
        raise
    return list_task_accounts(conn, campaign_id)
