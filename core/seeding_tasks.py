"""Task-intake helpers for ACP Facebook seeding.

Parser/comment-plan validation are dependency-light; persistence helpers use only
an existing SQLite connection and additive task schema.
"""
from __future__ import annotations

import json
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit

_NEAR_DUPLICATE_RATIO = 0.88
_ALLOWED_FACEBOOK_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com"}


def _plain(value: str) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower()).replace("đ", "d")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _bounded_int(value, *, default: int, minimum: int = 0, maximum: int = 10) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _first_int(patterns, text: str, default: int) -> int:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.S)
        if match:
            return _bounded_int(match.group(1), default=default)
    return default


def _forbidden_words(instruction: str) -> list[str]:
    results: list[str] = []
    pattern = re.compile(
        r"kh[oô]ng\s+(?:được\s+)?nhắc(?:\s+(?:đến|tới))?\s*[:\-]?\s*([^\.\n;]+)",
        flags=re.I,
    )
    for match in pattern.finditer(str(instruction or "")):
        raw = match.group(1).strip(" \"'“”‘’()[]{}")
        raw = re.split(
            r"\b(?:yêu\s*cầu|yeu\s*cau|like\s+bài|like\s+bai)\b",
            raw,
            maxsplit=1,
            flags=re.I,
        )[0]
        for item in re.split(r"\s*(?:,|/|\+|\bvà\b|\bva\b)\s*", raw, flags=re.I):
            cleaned = item.strip(" \"'“”‘’()[]{}:-")
            if cleaned and _plain(cleaned) and _plain(cleaned) not in {_plain(x) for x in results}:
                results.append(cleaned.lower())
    return results


def parse_task_instruction(instruction: str) -> dict:
    """Parse common Vietnamese seeding-job shorthand into deterministic rules."""
    original = str(instruction or "").strip()
    plain = _plain(original)

    main_count = _first_int(
        [
            r"(\d+)\s*(?:cmt|comment|binh\s*luan)\s*(?:chinh|main)\b",
            r"(?:chinh|main)\s*[:=]?\s*(\d+)\b",
        ],
        plain,
        1,
    )
    reply_count = _first_int(
        [
            r"(\d+)\s*(?:(?:cmt|comment|binh\s*luan)\s*)?(?:reply|rep)\b",
            r"(?:reply|rep)\s*[:=]?\s*(\d+)\b",
        ],
        plain,
        0,
    )
    declared_total = _first_int(
        [
            r"moi\s+(?:acc|account)\b.*?(?:binh\s*luan\s*)?(\d+)\s*(?:cmt|comment)\b",
            r"(\d+)\s*(?:cmt|comment)\s*/\s*(?:acc|account)\b",
        ],
        plain,
        main_count + reply_count,
    )
    max_accounts = _first_int(
        [
            r"(?:toi\s*da|max(?:imum)?)\s*(\d+)\s*(?:acc|account)\b",
            r"(?:acc|account)\s*(?:toi\s*da|max)\s*(\d+)\b",
        ],
        plain,
        1,
    )
    main_count = max(0, main_count)
    reply_count = max(0, reply_count)
    calculated_total = main_count + reply_count
    comments_per_account = calculated_total if calculated_total else max(1, declared_total)

    platforms: list[str] = []
    if re.search(r"\bfb\b|\bfacebook\b", plain):
        platforms.append("facebook")
    if re.search(r"\btt\b|\btiktok\b|\btik\s*tok\b", plain):
        platforms.append("tiktok")

    return {
        "like_required": bool(re.search(r"\blike\b", plain)),
        "main_comments_per_account": main_count or 1,
        "replies_per_account": reply_count,
        "comments_per_account": comments_per_account or 1,
        "max_accounts": max(1, max_accounts),
        "forbidden_words": _forbidden_words(original),
        "platforms": platforms,
    }


def build_slot_blueprint(rules: dict) -> list[dict]:
    account_count = _bounded_int(rules.get("max_accounts"), default=1, minimum=1)
    main_count = _bounded_int(rules.get("main_comments_per_account"), default=1, minimum=0)
    reply_count = _bounded_int(rules.get("replies_per_account"), default=0, minimum=0)
    if main_count == 0 and reply_count == 0:
        main_count = 1

    rows: list[dict] = []
    for account_slot in range(1, account_count + 1):
        for item_index in range(1, main_count + 1):
            rows.append({"account_slot": account_slot, "comment_type": "MAIN", "item_index": item_index})
        for item_index in range(1, reply_count + 1):
            rows.append({"account_slot": account_slot, "comment_type": "REPLY", "item_index": item_index})
    return rows


def validate_comment_plan(plan: dict, rules: dict) -> list[dict]:
    if not isinstance(plan, dict) or not isinstance(plan.get("accounts"), list):
        raise ValueError("comment_plan phải có accounts[]")

    expected_accounts = _bounded_int(rules.get("max_accounts"), default=1, minimum=1)
    main_count = _bounded_int(rules.get("main_comments_per_account"), default=1, minimum=0)
    reply_count = _bounded_int(rules.get("replies_per_account"), default=0, minimum=0)
    accounts = plan["accounts"]
    if len(accounts) != expected_accounts:
        raise ValueError("comment_plan không đủ số account")

    normalized_rows: list[dict] = []
    seen_slots: set[int] = set()
    for account in accounts:
        if not isinstance(account, dict):
            raise ValueError("account plan không hợp lệ")
        slot = _bounded_int(account.get("slot"), default=0, minimum=0)
        if slot < 1 or slot > expected_accounts or slot in seen_slots:
            raise ValueError("account slot không hợp lệ")
        seen_slots.add(slot)
        mains = account.get("main_comments")
        replies = account.get("replies")
        if not isinstance(mains, list) or len(mains) != main_count:
            raise ValueError("sai số lượng comment chính")
        if not isinstance(replies, list) or len(replies) != reply_count:
            raise ValueError("sai số lượng reply")
        for kind, values in (("MAIN", mains), ("REPLY", replies)):
            for index, value in enumerate(values, start=1):
                text = str(value or "").strip()
                if not text:
                    raise ValueError("comment rỗng")
                normalized_rows.append(
                    {"account_slot": slot, "comment_type": kind, "item_index": index, "text": text}
                )

    normalized_rows.sort(
        key=lambda row: (
            row["account_slot"],
            0 if row["comment_type"] == "MAIN" else 1,
            row["item_index"],
        )
    )

    forbidden = [_plain(item) for item in rules.get("forbidden_words") or [] if _plain(item)]
    folded_texts: list[str] = []
    for row in normalized_rows:
        folded = _plain(row["text"])
        if not folded:
            raise ValueError("comment rỗng sau normalize")
        if any(word in folded for word in forbidden):
            raise ValueError("comment chứa từ cấm")
        for previous in folded_texts:
            if folded == previous or SequenceMatcher(None, folded, previous).ratio() >= _NEAR_DUPLICATE_RATIO:
                raise ValueError("comment bị trùng hoặc quá giống nhau")
        folded_texts.append(folded)
    return normalized_rows


def build_comment_plan_prompt(
    *,
    task_name: str,
    instruction: str,
    post_url: str,
    post_text: str,
    rules: dict,
) -> str:
    """Build one JSON-only LLM prompt for all account slots."""
    payload = {
        "task": "Generate a distinct comment plan for a human-reviewed social seeding task. Return JSON only.",
        "task_name": str(task_name or "").strip(),
        "instruction": str(instruction or "").strip(),
        "post": {"url": str(post_url or "").strip(), "text": str(post_text or "").strip()},
        "rules": rules,
        "required_schema": {
            "accounts": [
                {
                    "slot": "1..max_accounts",
                    "main_comments": ["exactly main_comments_per_account strings"],
                    "replies": ["exactly replies_per_account strings"],
                }
            ]
        },
        "constraints": [
            "Every generated text must be materially different from every other generated text.",
            "Do not use any forbidden_words, including close unaccented spelling.",
            "Keep every comment relevant to the supplied post and instruction.",
            "Do not invent first-hand purchase/use/customer experiences that are not present in the instruction.",
            "Do not return markdown or explanations.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_comment_plan_response(text: str, rules: dict) -> list[dict]:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
    try:
        plan = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM trả comment_plan không phải JSON") from exc
    return validate_comment_plan(plan, rules)


def _new_id() -> str:
    try:
        from .db import ulid as _ulid
    except (ImportError, ValueError):
        return uuid.uuid4().hex
    return _ulid()


def _now() -> str:
    try:
        from .db import now as _db_now
    except (ImportError, ValueError):
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    return _db_now()


def _audit(conn, entity: str, entity_id: str, action: str, detail=None) -> None:
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
        ).fetchall()
    }
    if "audit_log" not in tables:
        return
    conn.execute(
        "INSERT INTO audit_log (entity,entity_id,action,actor,detail,created_at) VALUES (?,?,?,?,?,?)",
        (
            entity,
            entity_id,
            action,
            "operator",
            json.dumps(detail, ensure_ascii=False) if detail else None,
            _now(),
        ),
    )


def normalize_task_post_url(value: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = urlsplit(text)
    except ValueError as exc:
        raise ValueError("Link Facebook không hợp lệ") from exc
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or host not in _ALLOWED_FACEBOOK_HOSTS
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("Link nhiệm vụ phải là URL Facebook HTTPS")
    path = parsed.path or "/"
    return urlunsplit(("https", host, path, parsed.query, ""))


def ensure_task_schema(conn) -> None:
    """Add task-intake fields/tables without altering unrelated ACP data."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(seeding_campaign)").fetchall()}
    if not cols:
        raise ValueError("Thiếu bảng seeding_campaign; chạy init_db() trước")
    if "task_rules" not in cols:
        conn.execute("ALTER TABLE seeding_campaign ADD COLUMN task_rules TEXT NOT NULL DEFAULT '{}'")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS seeding_comment_slot (
            id             TEXT PRIMARY KEY,
            campaign_id    TEXT NOT NULL REFERENCES seeding_campaign(id),
            target_id      TEXT NOT NULL REFERENCES seeding_target(id),
            account_slot   INTEGER NOT NULL,
            comment_type   TEXT NOT NULL,
            item_index     INTEGER NOT NULL,
            generated_text TEXT,
            final_text     TEXT,
            status         TEXT NOT NULL DEFAULT 'EMPTY',
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL,
            UNIQUE(campaign_id, target_id, account_slot, comment_type, item_index)
        );
        CREATE INDEX IF NOT EXISTS idx_seed_comment_slot_task
            ON seeding_comment_slot(campaign_id, target_id, account_slot, comment_type, item_index);
        """
    )


def list_comment_slots(conn, campaign_id: str) -> list[dict]:
    ensure_task_schema(conn)
    rows = conn.execute(
        """SELECT * FROM seeding_comment_slot
           WHERE campaign_id=?
           ORDER BY account_slot,
                    CASE comment_type WHEN 'MAIN' THEN 0 ELSE 1 END,
                    item_index""",
        (campaign_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_task_rules(conn, campaign_id: str) -> dict:
    ensure_task_schema(conn)
    row = conn.execute("SELECT task_rules FROM seeding_campaign WHERE id=?", (campaign_id,)).fetchone()
    if row is None:
        raise ValueError("Không tìm thấy nhiệm vụ")
    try:
        value = json.loads(row["task_rules"] or "{}")
    except (TypeError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def create_task(conn, *, name: str, instruction: str, post_url: str) -> dict:
    """Create one manual task instance. Duplicate names are intentionally allowed."""
    ensure_task_schema(conn)
    task_name = str(name or "").strip()
    raw_instruction = str(instruction or "").strip()
    if not task_name:
        raise ValueError("Tên nhiệm vụ không được để trống")
    if not raw_instruction:
        raise ValueError("Nội dung/yêu cầu nhiệm vụ không được để trống")
    normalized_url = normalize_task_post_url(post_url)
    rules = parse_task_instruction(raw_instruction)

    campaign_id = _new_id()
    target_id = _new_id()
    stamp = _now()
    conn.execute(
        """INSERT INTO seeding_campaign
           (id,name,brand,brief,allowed_claims,prohibited_topics,disclosure_policy,
            status,auto_submit,confidence_threshold,created_at,updated_at,task_rules)
           VALUES (?,?,?,?,?,?,?,'ACTIVE',0,0.90,?,?,?)""",
        (
            campaign_id,
            task_name,
            "",
            raw_instruction,
            "[]",
            "[]",
            "promotional",
            stamp,
            stamp,
            json.dumps(rules, ensure_ascii=False),
        ),
    )
    conn.execute(
        """INSERT INTO seeding_target
           (id,campaign_id,url,position,status,risk_labels,created_at,updated_at)
           VALUES (?,?,?,0,'READY','[]',?,?)""",
        (target_id, campaign_id, normalized_url, stamp, stamp),
    )
    for slot in build_slot_blueprint(rules):
        conn.execute(
            """INSERT INTO seeding_comment_slot
               (id,campaign_id,target_id,account_slot,comment_type,item_index,status,created_at,updated_at)
               VALUES (?,?,?,?,?,?,'EMPTY',?,?)""",
            (
                _new_id(),
                campaign_id,
                target_id,
                slot["account_slot"],
                slot["comment_type"],
                slot["item_index"],
                stamp,
                stamp,
            ),
        )

    _audit(conn, "seeding_campaign", campaign_id, "create_task", detail={"post_url": normalized_url, "rules": rules})
    campaign = conn.execute("SELECT * FROM seeding_campaign WHERE id=?", (campaign_id,)).fetchone()
    return {
        "campaign": dict(campaign),
        "target_count": 1,
        "target_id": target_id,
        "slots": list_comment_slots(conn, campaign_id),
        "rules": rules,
    }
