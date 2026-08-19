"""Pure task-intake helpers for ACP Facebook seeding.

This module intentionally has no Flask/SQLite dependencies so parser and comment-plan
validation stay deterministic and easy to test.
"""
from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher

_NEAR_DUPLICATE_RATIO = 0.88


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
            rows.append(
                {
                    "account_slot": account_slot,
                    "comment_type": "MAIN",
                    "item_index": item_index,
                }
            )
        for item_index in range(1, reply_count + 1):
            rows.append(
                {
                    "account_slot": account_slot,
                    "comment_type": "REPLY",
                    "item_index": item_index,
                }
            )
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
                    {
                        "account_slot": slot,
                        "comment_type": kind,
                        "item_index": index,
                        "text": text,
                    }
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
        "post": {
            "url": str(post_url or "").strip(),
            "text": str(post_text or "").strip(),
        },
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
