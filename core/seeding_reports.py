from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import seeding_execution


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_report_schema(conn) -> None:
    seeding_execution.ensure_execution_schema(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS seeding_task_report (
            campaign_id TEXT PRIMARY KEY REFERENCES seeding_campaign(id),
            status      TEXT NOT NULL DEFAULT 'PENDING',
            last_error  TEXT,
            sheet_ref   TEXT,
            pushed_at   TEXT,
            updated_at  TEXT NOT NULL
        );
        """
    )


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


def task_completion(conn, campaign_id: str) -> dict:
    ensure_report_schema(conn)
    mappings = conn.execute(
        "SELECT account_slot,like_status FROM seeding_task_account WHERE campaign_id=? ORDER BY account_slot",
        (campaign_id,),
    ).fetchall()
    account_count = len(mappings)
    if not account_count:
        return {
            "complete": False,
            "account_count": 0,
            "comment_total": 0,
            "comment_done": 0,
            "like_required": bool(_task_rules(conn, campaign_id).get("like_required")),
            "like_done": 0,
        }

    slots = conn.execute(
        """SELECT s.status
           FROM seeding_comment_slot s
           JOIN seeding_task_account m
             ON m.campaign_id=s.campaign_id AND m.account_slot=s.account_slot
           WHERE s.campaign_id=?""",
        (campaign_id,),
    ).fetchall()
    comment_total = len(slots)
    comment_done = sum(1 for row in slots if row["status"] == "DONE")
    rules = _task_rules(conn, campaign_id)
    like_required = bool(rules.get("like_required"))
    like_done = sum(1 for row in mappings if row["like_status"] == "DONE")
    complete = (
        comment_total > 0
        and comment_done == comment_total
        and (not like_required or like_done == account_count)
    )
    return {
        "complete": complete,
        "account_count": account_count,
        "comment_total": comment_total,
        "comment_done": comment_done,
        "like_required": like_required,
        "like_done": like_done,
    }


def build_sheet_rows(conn, campaign_id: str) -> list[list[str]]:
    ensure_report_schema(conn)
    campaign = conn.execute(
        "SELECT name FROM seeding_campaign WHERE id=?", (campaign_id,)
    ).fetchone()
    target = conn.execute(
        "SELECT url FROM seeding_target WHERE campaign_id=? ORDER BY rowid LIMIT 1",
        (campaign_id,),
    ).fetchone()
    if campaign is None or target is None:
        raise ValueError("Nhiệm vụ hoặc link bài không tồn tại")

    comments = conn.execute(
        """SELECT s.account_slot,s.comment_type,s.item_index,s.generated_text,s.final_text,s.status
           FROM seeding_comment_slot s
           JOIN seeding_task_account m
             ON m.campaign_id=s.campaign_id AND m.account_slot=s.account_slot
           WHERE s.campaign_id=? AND s.status='DONE'
           ORDER BY s.account_slot,
                    CASE s.comment_type WHEN 'MAIN' THEN 0 ELSE 1 END,
                    s.item_index""",
        (campaign_id,),
    ).fetchall()
    mains = [
        str(row["final_text"] or row["generated_text"] or "").strip()
        for row in comments if row["comment_type"] == "MAIN"
    ]
    replies = [
        str(row["final_text"] or row["generated_text"] or "").strip()
        for row in comments if row["comment_type"] == "REPLY"
    ]
    row_count = max(2, len(mains), len(replies))
    rows: list[list[str]] = []
    for index in range(row_count):
        col_b = campaign["name"] if index == 0 else target["url"] if index == 1 else ""
        col_c = mains[index] if index < len(mains) else ""
        col_d = replies[index] if index < len(replies) else ""
        rows.append([col_b, col_c, col_d])
    return rows


def _default_sender(url: str, payload: dict) -> dict:
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Google Sheets webhook phải là HTTPS URL")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError("Không gửi được báo cáo tới Google Sheets") from exc
    try:
        result = json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Google Sheets webhook trả dữ liệu không hợp lệ") from exc
    if not isinstance(result, dict) or not result.get("ok"):
        raise ValueError(str(result.get("error") if isinstance(result, dict) else "Google Sheets webhook lỗi"))
    return result


def _reserve_push(conn, campaign_id: str, stamp: str) -> tuple[bool, dict]:
    cur = conn.execute(
        """INSERT OR IGNORE INTO seeding_task_report(campaign_id,status,updated_at)
           VALUES (?,'PUSHING',?)""",
        (campaign_id, stamp),
    )
    if cur.rowcount == 1:
        row = conn.execute(
            "SELECT * FROM seeding_task_report WHERE campaign_id=?", (campaign_id,)
        ).fetchone()
        return True, dict(row)

    row = conn.execute(
        "SELECT * FROM seeding_task_report WHERE campaign_id=?", (campaign_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Không tạo được trạng thái report")
    if row["status"] in {"PUSHING", "PUSHED"}:
        return False, dict(row)
    cur = conn.execute(
        """UPDATE seeding_task_report
           SET status='PUSHING',last_error=NULL,updated_at=?
           WHERE campaign_id=? AND status IN ('FAILED','PENDING','READY')""",
        (stamp, campaign_id),
    )
    current = conn.execute(
        "SELECT * FROM seeding_task_report WHERE campaign_id=?", (campaign_id,)
    ).fetchone()
    return cur.rowcount == 1, dict(current)


def push_to_sheet(
    conn,
    campaign_id: str,
    *,
    webhook_url: str,
    secret: str,
    sender=None,
) -> dict:
    ensure_report_schema(conn)
    completion = task_completion(conn, campaign_id)
    if not completion["complete"]:
        raise ValueError("Nhiệm vụ chưa hoàn thành đủ LIKE/comment để ghi Sheet")

    url = str(webhook_url or "").strip()
    token = str(secret or "").strip()
    if not url or not token:
        raise ValueError("Chưa cấu hình Google Sheets webhook/secret")

    stamp = _now()
    acquired, reservation = _reserve_push(conn, campaign_id, stamp)
    if not acquired:
        return reservation

    campaign = conn.execute(
        "SELECT name FROM seeding_campaign WHERE id=?", (campaign_id,)
    ).fetchone()
    target = conn.execute(
        "SELECT url FROM seeding_target WHERE campaign_id=? ORDER BY rowid LIMIT 1",
        (campaign_id,),
    ).fetchone()
    if campaign is None or target is None:
        raise ValueError("Không tìm thấy nhiệm vụ hoặc link bài")

    payload = {
        "secret": token,
        "campaign_id": campaign_id,
        "task_name": campaign["name"],
        "post_url": target["url"],
        "rows": build_sheet_rows(conn, campaign_id),
    }
    send = sender or _default_sender
    try:
        response = send(url, payload)
        if not isinstance(response, dict) or not response.get("ok"):
            raise ValueError("Google Sheets webhook không xác nhận thành công")
        sheet_ref = str(response.get("sheet_ref") or response.get("range") or "").strip() or None
        conn.execute(
            """UPDATE seeding_task_report
               SET status='PUSHED',last_error=NULL,sheet_ref=?,pushed_at=?,updated_at=?
               WHERE campaign_id=? AND status='PUSHING'""",
            (sheet_ref, stamp, stamp, campaign_id),
        )
    except Exception as exc:
        conn.execute(
            """UPDATE seeding_task_report
               SET status='FAILED',last_error=?,updated_at=?
               WHERE campaign_id=? AND status='PUSHING'""",
            (str(exc), stamp, campaign_id),
        )
        raise
    return dict(
        conn.execute("SELECT * FROM seeding_task_report WHERE campaign_id=?", (campaign_id,)).fetchone()
    )


def maybe_auto_push(conn, campaign_id: str, *, sender=None) -> dict:
    ensure_report_schema(conn)
    completion = task_completion(conn, campaign_id)
    if not completion["complete"]:
        return {"status": "PENDING", "complete": False}
    url = os.environ.get("ACP_SEEDING_SHEET_WEBHOOK_URL", "").strip()
    secret = os.environ.get("ACP_SEEDING_SHEET_SECRET", "").strip()
    if not url or not secret:
        return {"status": "READY", "complete": True, "configured": False}
    result = push_to_sheet(
        conn,
        campaign_id,
        webhook_url=url,
        secret=secret,
        sender=sender,
    )
    return {**result, "complete": True, "configured": True}
