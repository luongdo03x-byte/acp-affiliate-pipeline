from __future__ import annotations

import json
from datetime import datetime, timezone

from . import seeding_accounts, seeding_tasks

_TERMINAL_SLOT_STATUSES = {"DONE", "SKIPPED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def ensure_execution_schema(conn) -> None:
    seeding_accounts.ensure_account_schema(conn)
    if _table_exists(conn, "seeding_task_account"):
        cols = _columns(conn, "seeding_task_account")
        if "like_status" not in cols:
            conn.execute(
                "ALTER TABLE seeding_task_account ADD COLUMN like_status TEXT NOT NULL DEFAULT 'PENDING'"
            )
        if "like_completed_at" not in cols:
            conn.execute(
                "ALTER TABLE seeding_task_account ADD COLUMN like_completed_at TEXT"
            )
    if _table_exists(conn, "seeding_comment_slot"):
        cols = _columns(conn, "seeding_comment_slot")
        if "proof_ref" not in cols:
            conn.execute("ALTER TABLE seeding_comment_slot ADD COLUMN proof_ref TEXT")
        if "completed_at" not in cols:
            conn.execute("ALTER TABLE seeding_comment_slot ADD COLUMN completed_at TEXT")


def _account_for_instance(conn, instance_id: str):
    ensure_execution_schema(conn)
    instance = str(instance_id or "").strip()
    if not instance:
        raise ValueError("extension_instance_id không được để trống")
    row = conn.execute(
        "SELECT * FROM seeding_account WHERE extension_instance_id=? AND enabled=1",
        (instance,),
    ).fetchone()
    if row is None:
        raise ValueError("Tài khoản Facebook chưa được kết nối")
    return row


def _rules(value) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _mapped_tasks(conn, account_id: str):
    return conn.execute(
        """SELECT m.campaign_id,m.account_id,m.account_slot,m.like_status,m.like_completed_at,
                  c.name AS campaign_name,c.brief,c.task_rules,c.status AS campaign_status,
                  t.id AS target_id,t.url AS target_url,t.status AS target_status
           FROM seeding_task_account m
           JOIN seeding_campaign c ON c.id=m.campaign_id
           JOIN seeding_target t ON t.campaign_id=c.id
           WHERE m.account_id=? AND c.status='ACTIVE'
           ORDER BY c.created_at, m.account_slot, t.rowid""",
        (account_id,),
    ).fetchall()


def _target_payload(row) -> dict:
    return {"id": row["target_id"], "url": row["target_url"], "status": row["target_status"]}


def next_account_work(conn, instance_id: str) -> dict:
    """Return only work mapped to the requesting Chrome Profile."""
    account = _account_for_instance(conn, instance_id)
    for row in _mapped_tasks(conn, account["id"]):
        rules = _rules(row["task_rules"])
        base = {
            "done": False,
            "campaign_id": row["campaign_id"],
            "campaign_name": row["campaign_name"],
            "account_id": row["account_id"],
            "account_slot": row["account_slot"],
            "target": _target_payload(row),
        }
        if bool(rules.get("like_required")) and row["like_status"] != "DONE":
            return {**base, "action": "LIKE"}

        slots = conn.execute(
            """SELECT * FROM seeding_comment_slot
               WHERE campaign_id=? AND target_id=? AND account_slot=?
               ORDER BY CASE comment_type WHEN 'MAIN' THEN 0 ELSE 1 END,item_index""",
            (row["campaign_id"], row["target_id"], row["account_slot"]),
        ).fetchall()
        pending_generated = next((slot for slot in slots if slot["status"] == "GENERATED"), None)
        if pending_generated is not None:
            return {**base, "action": "COMMENT", "slot": dict(pending_generated)}
        if any(slot["status"] == "EMPTY" for slot in slots):
            return {**base, "action": "NEEDS_CONTEXT"}
        if slots and all(slot["status"] in _TERMINAL_SLOT_STATUSES for slot in slots):
            continue
    return {
        "done": True,
        "action": "IDLE",
        "account_id": account["id"],
        "account_label": account["label"],
    }


def _assert_account_mapped(conn, instance_id: str, campaign_id: str):
    account = _account_for_instance(conn, instance_id)
    row = conn.execute(
        """SELECT m.*,c.name AS campaign_name,c.brief,c.task_rules
           FROM seeding_task_account m
           JOIN seeding_campaign c ON c.id=m.campaign_id
           WHERE m.campaign_id=? AND m.account_id=?""",
        (campaign_id, account["id"]),
    ).fetchone()
    if row is None:
        raise ValueError("Tài khoản này không được gán vào nhiệm vụ")
    return account, row


def _mapped_slot_rows(conn, campaign_id: str, target_id: str, mapped_slots: list[int]):
    return conn.execute(
        f"""SELECT * FROM seeding_comment_slot
            WHERE campaign_id=? AND target_id=?
              AND account_slot IN ({','.join('?' for _ in mapped_slots)})
            ORDER BY account_slot,CASE comment_type WHEN 'MAIN' THEN 0 ELSE 1 END,item_index""",
        (campaign_id, target_id, *mapped_slots),
    ).fetchall()


def prepare_account_task(
    conn,
    *,
    instance_id: str,
    campaign_id: str,
    target_id: str,
    post_text: str,
    llm_fn,
) -> list[dict]:
    """Generate one validated plan for the accounts actually mapped to the task."""
    ensure_execution_schema(conn)
    _assert_account_mapped(conn, instance_id, campaign_id)
    if not callable(llm_fn):
        raise ValueError("Chưa cấu hình LLM để sinh comment")
    context = str(post_text or "").strip()
    if not context:
        raise ValueError("Không đọc được nội dung bài Facebook")

    campaign = conn.execute(
        "SELECT id,name,brief,task_rules FROM seeding_campaign WHERE id=? AND status='ACTIVE'",
        (campaign_id,),
    ).fetchone()
    target = conn.execute(
        "SELECT id,url FROM seeding_target WHERE id=? AND campaign_id=?",
        (target_id, campaign_id),
    ).fetchone()
    if campaign is None or target is None:
        raise ValueError("Nhiệm vụ hoặc target không hợp lệ")

    mappings = conn.execute(
        "SELECT account_slot FROM seeding_task_account WHERE campaign_id=? ORDER BY account_slot",
        (campaign_id,),
    ).fetchall()
    mapped_slots = [int(row["account_slot"]) for row in mappings]
    if not mapped_slots:
        raise ValueError("Nhiệm vụ chưa chọn tài khoản Facebook")
    if mapped_slots != list(range(1, len(mapped_slots) + 1)):
        raise ValueError("Account slot của nhiệm vụ không liên tục")

    existing_rows = _mapped_slot_rows(conn, campaign_id, target_id, mapped_slots)
    if existing_rows and all(row["status"] != "EMPTY" for row in existing_rows):
        return [dict(row) for row in existing_rows]
    if any(row["status"] != "EMPTY" for row in existing_rows):
        raise ValueError("Comment plan đang ở trạng thái dở dang; cần operator kiểm tra")

    rules = _rules(campaign["task_rules"])
    execution_rules = dict(rules)
    execution_rules["max_accounts"] = len(mapped_slots)
    prompt = seeding_tasks.build_comment_plan_prompt(
        task_name=campaign["name"],
        instruction=campaign["brief"],
        post_url=target["url"],
        post_text=context,
        rules=execution_rules,
    )
    generated = seeding_tasks.parse_comment_plan_response(llm_fn(prompt), execution_rules)
    expected = {
        (int(row["account_slot"]), row["comment_type"], int(row["item_index"]))
        for row in existing_rows
    }
    incoming = {
        (int(row["account_slot"]), row["comment_type"], int(row["item_index"]))
        for row in generated
    }
    if incoming != expected:
        raise ValueError("Comment plan không khớp các account đã chọn")

    stamp = _now()
    conn.execute("SAVEPOINT seeding_mapped_plan")
    try:
        for row in generated:
            cur = conn.execute(
                """UPDATE seeding_comment_slot
                   SET generated_text=?,status='GENERATED',updated_at=?
                   WHERE campaign_id=? AND target_id=? AND account_slot=?
                     AND comment_type=? AND item_index=? AND status='EMPTY'""",
                (
                    row["text"], stamp, campaign_id, target_id, row["account_slot"],
                    row["comment_type"], row["item_index"],
                ),
            )
            if cur.rowcount != 1:
                raise ValueError("comment_plan_conflict")
        conn.execute("RELEASE SAVEPOINT seeding_mapped_plan")
    except Exception as exc:
        conn.execute("ROLLBACK TO SAVEPOINT seeding_mapped_plan")
        conn.execute("RELEASE SAVEPOINT seeding_mapped_plan")
        current = _mapped_slot_rows(conn, campaign_id, target_id, mapped_slots)
        if current and len(current) == len(existing_rows) and all(row["status"] != "EMPTY" for row in current):
            return [dict(row) for row in current]
        raise exc

    return [dict(row) for row in _mapped_slot_rows(conn, campaign_id, target_id, mapped_slots)]


def record_like(conn, instance_id: str, campaign_id: str, *, done: bool) -> dict:
    ensure_execution_schema(conn)
    account, mapping = _assert_account_mapped(conn, instance_id, campaign_id)
    status = "DONE" if done else "SKIPPED"
    completed_at = _now()
    conn.execute(
        """UPDATE seeding_task_account
           SET like_status=?,like_completed_at=?
           WHERE campaign_id=? AND account_id=?""",
        (status, completed_at, campaign_id, account["id"]),
    )
    result = dict(mapping)
    result.update(like_status=status, like_completed_at=completed_at)
    return result


def _validate_final_comment(conn, row, text: str) -> None:
    campaign = conn.execute(
        "SELECT task_rules FROM seeding_campaign WHERE id=?", (row["campaign_id"],)
    ).fetchone()
    if campaign is None:
        raise ValueError("Không tìm thấy nhiệm vụ")
    mappings = conn.execute(
        "SELECT account_slot FROM seeding_task_account WHERE campaign_id=? ORDER BY account_slot",
        (row["campaign_id"],),
    ).fetchall()
    mapped_slots = [int(item["account_slot"]) for item in mappings]
    if not mapped_slots:
        raise ValueError("Nhiệm vụ chưa gán account")
    slots = _mapped_slot_rows(conn, row["campaign_id"], row["target_id"], mapped_slots)
    accounts = []
    by_slot = {slot: {"slot": slot, "main_comments": [], "replies": []} for slot in mapped_slots}
    for slot in slots:
        candidate = text if slot["id"] == row["id"] else str(slot["final_text"] or slot["generated_text"] or "").strip()
        if not candidate:
            raise ValueError("Comment plan chưa đầy đủ")
        target_list = by_slot[int(slot["account_slot"])][
            "main_comments" if slot["comment_type"] == "MAIN" else "replies"
        ]
        target_list.append(candidate)
    accounts.extend(by_slot[slot] for slot in mapped_slots)
    rules = _rules(campaign["task_rules"])
    rules["max_accounts"] = len(mapped_slots)
    seeding_tasks.validate_comment_plan({"accounts": accounts}, rules)


def record_comment_result(
    conn,
    *,
    instance_id: str,
    slot_id: str,
    result: str,
    final_text: str | None = None,
    proof_ref: str | None = None,
) -> dict:
    ensure_execution_schema(conn)
    account = _account_for_instance(conn, instance_id)
    outcome = str(result or "").strip().upper()
    if outcome not in _TERMINAL_SLOT_STATUSES:
        raise ValueError("Kết quả comment phải là DONE hoặc SKIPPED")
    row = conn.execute(
        """SELECT s.*,m.account_id
           FROM seeding_comment_slot s
           JOIN seeding_task_account m
             ON m.campaign_id=s.campaign_id AND m.account_slot=s.account_slot
           WHERE s.id=?""",
        (str(slot_id or "").strip(),),
    ).fetchone()
    if row is None:
        raise ValueError("Không tìm thấy comment slot")
    if row["account_id"] != account["id"]:
        raise ValueError("Comment slot không thuộc tài khoản này")
    if row["status"] in _TERMINAL_SLOT_STATUSES:
        return dict(row)
    if row["status"] != "GENERATED":
        raise ValueError("Comment slot chưa sẵn sàng")

    text = str(final_text or row["generated_text"] or "").strip()
    if outcome == "DONE":
        if not text:
            raise ValueError("Nội dung comment cuối không được để trống")
        _validate_final_comment(conn, row, text)
    stamp = _now()
    conn.execute(
        """UPDATE seeding_comment_slot
           SET final_text=?,status=?,proof_ref=?,completed_at=?,updated_at=?
           WHERE id=?""",
        (text or None, outcome, proof_ref, stamp, stamp, row["id"]),
    )
    return dict(conn.execute("SELECT * FROM seeding_comment_slot WHERE id=?", (row["id"],)).fetchone())
