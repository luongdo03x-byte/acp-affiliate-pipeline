from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

_ONLINE_WINDOW_SECONDS = 120
_TERMINAL_COMMENT_STATUSES = {"POSTED", "SKIPPED", "UNKNOWN"}
_TERMINAL_LIKE_STATUSES = {"DONE", "SKIPPED", "UNKNOWN"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


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
    cols = {row[1] for row in conn.execute("PRAGMA table_info(seeding_task_account)").fetchall()}
    if "like_status" not in cols:
        conn.execute(
            "ALTER TABLE seeding_task_account ADD COLUMN like_status TEXT NOT NULL DEFAULT 'PENDING'"
        )
    if "like_updated_at" not in cols:
        conn.execute("ALTER TABLE seeding_task_account ADD COLUMN like_updated_at TEXT")


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


def resolve_instance_account(conn, instance_id: str) -> dict:
    ensure_account_schema(conn)
    instance = str(instance_id or "").strip()
    if not instance:
        raise ValueError("extension_instance_id không được để trống")
    row = conn.execute(
        "SELECT * FROM seeding_account WHERE extension_instance_id=? AND enabled=1",
        (instance,),
    ).fetchone()
    if row is None:
        raise ValueError("Tài khoản chưa được kết nối")
    return dict(row)


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
                      m.like_status,m.like_updated_at,
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


def _task_rules(conn, campaign_id: str) -> dict:
    row = conn.execute(
        "SELECT task_rules FROM seeding_campaign WHERE id=?", (campaign_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Không tìm thấy nhiệm vụ")
    try:
        value = json.loads(row["task_rules"] or "{}")
    except (TypeError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _mapping_locked(conn, campaign_id: str) -> bool:
    if _table_exists(conn, "seeding_comment_slot"):
        row = conn.execute(
            """SELECT 1 FROM seeding_comment_slot
               WHERE campaign_id=? AND status<>'EMPTY' LIMIT 1""",
            (campaign_id,),
        ).fetchone()
        if row is not None:
            return True
    if _table_exists(conn, "seeding_task_account"):
        cols = {row[1] for row in conn.execute("PRAGMA table_info(seeding_task_account)").fetchall()}
        if "like_status" in cols:
            row = conn.execute(
                """SELECT 1 FROM seeding_task_account
                   WHERE campaign_id=? AND like_status<>'PENDING' LIMIT 1""",
                (campaign_id,),
            ).fetchone()
            if row is not None:
                return True
    return False


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

    current = [
        row["account_id"]
        for row in conn.execute(
            "SELECT account_id FROM seeding_task_account WHERE campaign_id=? ORDER BY account_slot",
            (campaign_id,),
        ).fetchall()
    ]
    if current == ids:
        return list_task_accounts(conn, campaign_id)
    if current and _mapping_locked(conn, campaign_id):
        raise ValueError("Không thể đổi tài khoản sau khi nhiệm vụ đã bắt đầu")

    conn.execute("SAVEPOINT seeding_task_accounts")
    try:
        conn.execute(
            "DELETE FROM seeding_task_account WHERE campaign_id=?", (campaign_id,)
        )
        stamp = _stamp()
        for slot, account_id in enumerate(ids, start=1):
            conn.execute(
                """INSERT INTO seeding_task_account
                   (campaign_id,account_id,account_slot,assigned_at,like_status)
                   VALUES (?,?,?,?,'PENDING')""",
                (campaign_id, account_id, slot, stamp),
            )
        conn.execute("RELEASE SAVEPOINT seeding_task_accounts")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT seeding_task_accounts")
        conn.execute("RELEASE SAVEPOINT seeding_task_accounts")
        raise
    return list_task_accounts(conn, campaign_id)


def _mapped_task(conn, *, instance_id: str, campaign_id: str | None = None):
    account = resolve_instance_account(conn, instance_id)
    params = [account["id"]]
    where = "m.account_id=? AND c.status='ACTIVE'"
    if campaign_id:
        where += " AND c.id=?"
        params.append(str(campaign_id))
    row = conn.execute(
        f"""SELECT m.campaign_id,m.account_id,m.account_slot,m.like_status,
                   a.label AS account_label,a.extension_instance_id,
                   c.name AS task_name,c.task_rules,c.created_at,
                   t.id AS target_id,t.url AS target_url,t.status AS target_status
            FROM seeding_task_account m
            JOIN seeding_account a ON a.id=m.account_id
            JOIN seeding_campaign c ON c.id=m.campaign_id
            JOIN seeding_target t ON t.campaign_id=c.id
            WHERE {where}
            ORDER BY c.created_at DESC,t.position ASC
            LIMIT 1""",
        params,
    ).fetchone()
    return dict(row) if row else None


def record_account_like_result(
    conn,
    *,
    campaign_id: str,
    instance_id: str,
    result: str,
) -> dict:
    ensure_account_schema(conn)
    task = _mapped_task(conn, instance_id=instance_id, campaign_id=campaign_id)
    if task is None:
        raise ValueError("Tài khoản không được gán cho nhiệm vụ")
    state = str(result or "").strip().upper()
    if state not in _TERMINAL_LIKE_STATUSES:
        raise ValueError("Trạng thái LIKE không hợp lệ")
    current = str(task["like_status"] or "PENDING").upper()
    if current in _TERMINAL_LIKE_STATUSES:
        if current != state:
            raise ValueError("Kết quả LIKE đã được ghi trước đó")
        return task
    stamp = _stamp()
    conn.execute(
        """UPDATE seeding_task_account
           SET like_status=?, like_updated_at=?
           WHERE campaign_id=? AND account_id=?""",
        (state, stamp, campaign_id, task["account_id"]),
    )
    return _mapped_task(conn, instance_id=instance_id, campaign_id=campaign_id)


def next_account_work(
    conn,
    *,
    instance_id: str,
    campaign_id: str | None = None,
) -> dict | None:
    """Return only work owned by this connected Chrome profile.

    LIKE is emitted before comments when required. If no mapped comment has been
    generated yet, PREPARE asks this profile to read the supplied post and let
    ACP generate the whole distinct plan once.
    """
    ensure_account_schema(conn)
    task = _mapped_task(conn, instance_id=instance_id, campaign_id=campaign_id)
    if task is None:
        return None
    rules = _task_rules(conn, task["campaign_id"])
    if rules.get("like_required") and task["like_status"] == "PENDING":
        return {
            "work_type": "LIKE",
            **task,
        }

    slot = conn.execute(
        """SELECT id,account_slot,comment_type,item_index,generated_text,final_text,status
           FROM seeding_comment_slot
           WHERE campaign_id=? AND target_id=? AND account_slot=? AND status='GENERATED'
           ORDER BY CASE comment_type WHEN 'MAIN' THEN 0 ELSE 1 END,item_index
           LIMIT 1""",
        (task["campaign_id"], task["target_id"], task["account_slot"]),
    ).fetchone()
    if slot is not None:
        item = dict(slot)
        return {
            "work_type": "COMMENT",
            **task,
            "slot_id": item["id"],
            "comment_type": item["comment_type"],
            "item_index": item["item_index"],
            "text": item["final_text"] or item["generated_text"],
            "slot_status": item["status"],
        }

    # Only mapped slots count. Unused max-account slots remain EMPTY and are not
    # dispatched or reported.
    mapped_count = conn.execute(
        "SELECT COUNT(*) FROM seeding_task_account WHERE campaign_id=?",
        (task["campaign_id"],),
    ).fetchone()[0]
    generated_or_terminal = conn.execute(
        """SELECT COUNT(*) FROM seeding_comment_slot s
           JOIN seeding_task_account m
             ON m.campaign_id=s.campaign_id AND m.account_slot=s.account_slot
           WHERE s.campaign_id=? AND s.target_id=? AND s.status<>'EMPTY'""",
        (task["campaign_id"], task["target_id"]),
    ).fetchone()[0]
    if mapped_count and generated_or_terminal == 0:
        return {"work_type": "PREPARE", **task, "mapped_accounts": int(mapped_count)}
    return None


def record_account_slot_result(
    conn,
    *,
    instance_id: str,
    slot_id: str,
    result: str,
    final_text: str | None,
) -> dict:
    ensure_account_schema(conn)
    account = resolve_instance_account(conn, instance_id)
    row = conn.execute(
        """SELECT s.*,m.account_id,m.account_slot AS mapped_slot
           FROM seeding_comment_slot s
           JOIN seeding_task_account m
             ON m.campaign_id=s.campaign_id AND m.account_slot=s.account_slot
           WHERE s.id=? AND m.account_id=?""",
        (str(slot_id or "").strip(), account["id"]),
    ).fetchone()
    if row is None:
        raise ValueError("Comment slot không thuộc tài khoản này")
    state = str(result or "").strip().upper()
    if state not in _TERMINAL_COMMENT_STATUSES:
        raise ValueError("Kết quả comment không hợp lệ")
    current = str(row["status"] or "").upper()
    text = str(final_text or "").strip() or None
    if state in {"POSTED", "UNKNOWN"} and not text:
        raise ValueError("Cần lưu nội dung comment cuối cùng")
    if current in _TERMINAL_COMMENT_STATUSES:
        existing_text = str(row["final_text"] or "").strip() or None
        if current != state or existing_text != text:
            raise ValueError("Kết quả comment đã được ghi trước đó")
        return dict(row)
    if current != "GENERATED":
        raise ValueError("Comment slot chưa sẵn sàng")
    conn.execute(
        """UPDATE seeding_comment_slot
           SET final_text=?, status=?, updated_at=? WHERE id=?""",
        (text, state, _stamp(), row["id"]),
    )
    return dict(conn.execute("SELECT * FROM seeding_comment_slot WHERE id=?", (row["id"],)).fetchone())


def task_execution_summary(conn, campaign_id: str) -> dict:
    ensure_account_schema(conn)
    mappings = list_task_accounts(conn, campaign_id)
    mapped_slots = [row["account_slot"] for row in mappings]
    summary = {
        "accounts": len(mappings),
        "like": {"PENDING": 0, "DONE": 0, "SKIPPED": 0, "UNKNOWN": 0},
        "comments": {"EMPTY": 0, "GENERATED": 0, "POSTED": 0, "SKIPPED": 0, "UNKNOWN": 0},
        "complete": False,
    }
    for row in mappings:
        state = str(row.get("like_status") or "PENDING").upper()
        summary["like"][state] = summary["like"].get(state, 0) + 1
    if mapped_slots:
        placeholders = ",".join("?" for _ in mapped_slots)
        rows = conn.execute(
            f"""SELECT status,COUNT(*) AS n FROM seeding_comment_slot
                 WHERE campaign_id=? AND account_slot IN ({placeholders})
                 GROUP BY status""",
            [campaign_id, *mapped_slots],
        ).fetchall()
        for row in rows:
            state = str(row["status"] or "EMPTY").upper()
            summary["comments"][state] = int(row["n"])
    rules = _task_rules(conn, campaign_id)
    likes_complete = (
        not rules.get("like_required")
        or summary["like"].get("PENDING", 0) == 0
    )
    comments_complete = (
        summary["comments"].get("EMPTY", 0) == 0
        and summary["comments"].get("GENERATED", 0) == 0
    )
    summary["complete"] = bool(mappings and likes_complete and comments_complete)
    return summary
