"""Facebook Seeding Assistant domain logic.

This module deliberately owns campaign/queue/risk decisions but knows nothing
about Facebook DOM selectors. Browser automation must treat the decision here as
a maximum permission: any DOM uncertainty can still downgrade AUTO_READY to a
manual review in the extension.
"""
from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit

from .db import audit, now, ulid
from .system_settings import seeding_global_paused

_LLM_FN = None
_MIN_CONFIDENCE_THRESHOLD = 0.85
_DUPLICATE_SIMILARITY = 0.88
_ALLOWED_FACEBOOK_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com"}
_TERMINAL_TARGET_STATUSES = {"POSTED", "UNKNOWN", "SKIPPED", "UNAVAILABLE"}
_ALLOWED_RESULTS = _TERMINAL_TARGET_STATUSES
_ALLOWED_MODES = {"auto", "reviewed"}

MANDATORY_REVIEW = {
    "negative_brand_context",
    "complaint",
    "refund_dispute",
    "legal_threat",
    "medical_complication",
    "fraud_allegation",
    "ambiguous_context",
    "unsupported_claim",
    "personal_experience_required",
    "first_person_testimonial",
    "sensitive_personal_data",
    "model_uncertainty",
    "target_mismatch",
    "dom_uncertainty",
    "duplicate_recent_comment",
    "global_pause",
    "prohibited_topic",
    "missing_disclosure_policy",
}

_HIGH_RISK = {
    "complaint",
    "refund_dispute",
    "legal_threat",
    "medical_complication",
    "fraud_allegation",
    "first_person_testimonial",
    "sensitive_personal_data",
    "target_mismatch",
}


def set_llm(fn) -> None:
    """Set optional ``fn(prompt: str) -> str`` structured-generation callback."""
    global _LLM_FN
    _LLM_FN = fn


def _dict(row):
    return dict(row) if row is not None else None


def _json_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return sorted(value)
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _clean_items(values) -> list[str]:
    out = []
    seen = set()
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _lower(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).lower().strip()


def _normalize_for_similarity(text: str) -> str:
    value = _lower(text)
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def normalize_facebook_url(value: str) -> str | None:
    """Return a safe normalized Facebook HTTPS URL or ``None``."""
    text = str(value or "").strip()
    if not text or any(ord(ch) < 32 for ch in text):
        return None
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or host not in _ALLOWED_FACEBOOK_HOSTS
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    path = parsed.path or "/"
    return urlunsplit(("https", host, path, parsed.query, ""))


def _same_target_url(left: str, right: str) -> bool:
    a = normalize_facebook_url(left)
    b = normalize_facebook_url(right)
    if not a or not b:
        return False
    pa, pb = urlsplit(a), urlsplit(b)
    aliases = {"facebook.com", "www.facebook.com", "m.facebook.com"}
    host_match = pa.hostname == pb.hostname or (
        pa.hostname in aliases and pb.hostname in aliases
    )
    return host_match and pa.path.rstrip("/") == pb.path.rstrip("/") and pa.query == pb.query


def create_campaign(
    conn,
    *,
    name: str,
    brand: str = "",
    brief: str,
    allowed_claims,
    prohibited_topics,
    disclosure_policy: str = "",
    auto_submit: bool = False,
    confidence_threshold: float = 0.90,
) -> dict:
    name = str(name or "").strip()
    brief = str(brief or "").strip()
    threshold = float(confidence_threshold)
    if not name:
        raise ValueError("Tên campaign không được để trống")
    if not brief:
        raise ValueError("Brief campaign không được để trống")
    if not (_MIN_CONFIDENCE_THRESHOLD <= threshold <= 1.0):
        raise ValueError("confidence_threshold phải nằm trong khoảng 0.85..1.00")
    campaign_id = ulid()
    stamp = now()
    conn.execute(
        """INSERT INTO seeding_campaign
           (id,name,brand,brief,allowed_claims,prohibited_topics,disclosure_policy,
            status,auto_submit,confidence_threshold,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,'ACTIVE',?,?,?,?)""",
        (
            campaign_id,
            name,
            str(brand or "").strip(),
            brief,
            json.dumps(_clean_items(allowed_claims), ensure_ascii=False),
            json.dumps(_clean_items(prohibited_topics), ensure_ascii=False),
            str(disclosure_policy or "").strip(),
            1 if auto_submit else 0,
            threshold,
            stamp,
            stamp,
        ),
    )
    audit(conn, "seeding_campaign", campaign_id, "create", actor="operator")
    return _dict(conn.execute("SELECT * FROM seeding_campaign WHERE id=?", (campaign_id,)).fetchone())


def update_campaign(
    conn,
    campaign_id: str,
    *,
    name: str,
    brand: str = "",
    brief: str,
    allowed_claims,
    prohibited_topics,
    disclosure_policy: str = "",
    auto_submit: bool = False,
    confidence_threshold: float = 0.90,
    status: str = "ACTIVE",
) -> dict:
    threshold = float(confidence_threshold)
    if not (_MIN_CONFIDENCE_THRESHOLD <= threshold <= 1.0):
        raise ValueError("confidence_threshold phải nằm trong khoảng 0.85..1.00")
    if status not in {"ACTIVE", "PAUSED", "ARCHIVED"}:
        raise ValueError("Trạng thái campaign không hợp lệ")
    name = str(name or "").strip()
    brief = str(brief or "").strip()
    if not name or not brief:
        raise ValueError("Tên và brief campaign là bắt buộc")
    cur = conn.execute(
        """UPDATE seeding_campaign
           SET name=?, brand=?, brief=?, allowed_claims=?, prohibited_topics=?,
               disclosure_policy=?, status=?, auto_submit=?, confidence_threshold=?, updated_at=?
           WHERE id=?""",
        (
            name,
            str(brand or "").strip(),
            brief,
            json.dumps(_clean_items(allowed_claims), ensure_ascii=False),
            json.dumps(_clean_items(prohibited_topics), ensure_ascii=False),
            str(disclosure_policy or "").strip(),
            status,
            1 if auto_submit else 0,
            threshold,
            now(),
            campaign_id,
        ),
    )
    if cur.rowcount != 1:
        raise ValueError("Không tìm thấy campaign")
    audit(conn, "seeding_campaign", campaign_id, "update", actor="operator")
    return get_campaign(conn, campaign_id)


def get_campaign(conn, campaign_id: str) -> dict:
    row = conn.execute("SELECT * FROM seeding_campaign WHERE id=?", (campaign_id,)).fetchone()
    if row is None:
        raise ValueError("Không tìm thấy campaign")
    return _dict(row)


def list_campaigns(conn) -> list[dict]:
    return [
        _dict(row)
        for row in conn.execute(
            "SELECT * FROM seeding_campaign ORDER BY created_at DESC"
        ).fetchall()
    ]


def add_template(
    conn,
    campaign_id: str,
    *,
    intent: str,
    source_text: str,
    allowed_claims=(),
) -> dict:
    get_campaign(conn, campaign_id)
    intent = str(intent or "generic").strip() or "generic"
    source_text = str(source_text or "").strip()
    if not source_text:
        raise ValueError("Template không được để trống")
    template_id = ulid()
    stamp = now()
    conn.execute(
        """INSERT INTO seeding_template
           (id,campaign_id,intent,source_text,allowed_claims,enabled,created_at,updated_at)
           VALUES (?,?,?,?,?,1,?,?)""",
        (
            template_id,
            campaign_id,
            intent,
            source_text,
            json.dumps(_clean_items(allowed_claims), ensure_ascii=False),
            stamp,
            stamp,
        ),
    )
    audit(conn, "seeding_template", template_id, "create", actor="operator")
    return _dict(conn.execute("SELECT * FROM seeding_template WHERE id=?", (template_id,)).fetchone())


def list_templates(conn, campaign_id: str) -> list[dict]:
    return [
        _dict(row)
        for row in conn.execute(
            "SELECT * FROM seeding_template WHERE campaign_id=? ORDER BY created_at",
            (campaign_id,),
        ).fetchall()
    ]


def import_targets(conn, campaign_id: str, urls: list[str]) -> dict:
    get_campaign(conn, campaign_id)
    row = conn.execute(
        "SELECT COALESCE(MAX(position), -1) FROM seeding_target WHERE campaign_id=?",
        (campaign_id,),
    ).fetchone()
    position = int(row[0]) + 1
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT url FROM seeding_target WHERE campaign_id=?", (campaign_id,)
        ).fetchall()
    }
    seen = set()
    created = duplicates = invalid = 0
    for raw_url in urls or []:
        url = normalize_facebook_url(raw_url)
        if url is None:
            invalid += 1
            continue
        if url in existing or url in seen:
            duplicates += 1
            continue
        target_id = ulid()
        stamp = now()
        conn.execute(
            """INSERT INTO seeding_target
               (id,campaign_id,url,position,status,risk_labels,created_at,updated_at)
               VALUES (?,?,?,?,'READY','[]',?,?)""",
            (target_id, campaign_id, url, position, stamp, stamp),
        )
        audit(conn, "seeding_target", target_id, "import", actor="operator")
        seen.add(url)
        existing.add(url)
        position += 1
        created += 1
    return {"created": created, "duplicates": duplicates, "invalid": invalid}


def list_targets(conn, campaign_id: str, limit: int = 500) -> list[dict]:
    return [
        _dict(row)
        for row in conn.execute(
            "SELECT * FROM seeding_target WHERE campaign_id=? ORDER BY position LIMIT ?",
            (campaign_id, int(limit)),
        ).fetchall()
    ]


def start_shift(conn, campaign_id: str) -> dict:
    get_campaign(conn, campaign_id)
    row = conn.execute(
        """SELECT * FROM seeding_shift
           WHERE campaign_id=? AND status IN ('ACTIVE','PAUSED')
           ORDER BY started_at DESC LIMIT 1""",
        (campaign_id,),
    ).fetchone()
    if row is not None:
        if row["status"] == "PAUSED":
            conn.execute("UPDATE seeding_shift SET status='ACTIVE' WHERE id=?", (row["id"],))
            audit(conn, "seeding_shift", row["id"], "resume", actor="operator")
            return _dict(conn.execute("SELECT * FROM seeding_shift WHERE id=?", (row["id"],)).fetchone())
        return _dict(row)
    target_count = conn.execute(
        "SELECT COUNT(*) FROM seeding_target WHERE campaign_id=? AND status='READY'",
        (campaign_id,),
    ).fetchone()[0]
    shift_id = ulid()
    conn.execute(
        """INSERT INTO seeding_shift
           (id,campaign_id,status,started_at,target_count,posted_count,review_count,
            skipped_count,unknown_count)
           VALUES (?,?,'ACTIVE',?,?,0,0,0,0)""",
        (shift_id, campaign_id, now(), int(target_count)),
    )
    audit(conn, "seeding_shift", shift_id, "start", actor="operator")
    return _dict(conn.execute("SELECT * FROM seeding_shift WHERE id=?", (shift_id,)).fetchone())


def pause_shift(conn, shift_id: str) -> None:
    cur = conn.execute(
        "UPDATE seeding_shift SET status='PAUSED' WHERE id=? AND status='ACTIVE'",
        (shift_id,),
    )
    if cur.rowcount != 1:
        raise ValueError("Shift không ở trạng thái ACTIVE")
    audit(conn, "seeding_shift", shift_id, "pause", actor="operator")


def end_shift(conn, shift_id: str) -> dict:
    row = conn.execute("SELECT * FROM seeding_shift WHERE id=?", (shift_id,)).fetchone()
    if row is None:
        raise ValueError("Không tìm thấy shift")
    if row["status"] != "ENDED":
        conn.execute(
            "UPDATE seeding_shift SET status='ENDED', ended_at=? WHERE id=?",
            (now(), shift_id),
        )
        audit(conn, "seeding_shift", shift_id, "end", actor="operator")
    return shift_summary(conn, shift_id)


def _active_shift(conn, shift_id: str):
    row = conn.execute("SELECT * FROM seeding_shift WHERE id=?", (shift_id,)).fetchone()
    if row is None:
        raise ValueError("Không tìm thấy shift")
    if row["status"] != "ACTIVE":
        raise ValueError("Shift không ở trạng thái ACTIVE")
    return row


def next_target(conn, shift_id: str) -> dict | None:
    shift = _active_shift(conn, shift_id)
    row = conn.execute(
        """SELECT * FROM seeding_target
           WHERE campaign_id=? AND status='READY'
           ORDER BY position LIMIT 1""",
        (shift["campaign_id"],),
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE seeding_target SET status='OPENING', updated_at=? WHERE id=? AND status='READY'",
        (now(), row["id"]),
    )
    return _dict(conn.execute("SELECT * FROM seeding_target WHERE id=?", (row["id"],)).fetchone())


def _local_intent(post_text: str) -> str:
    text = _lower(post_text)
    if any(term in text for term in ("bao nhiêu", "giá", "chi phí", "price", "cost")):
        return "price_question"
    if any(term in text for term in ("địa chỉ", "chỗ", "ở đâu", "uy tín", "tham khảo", "recommend")):
        return "recommendation_request"
    if any(term in text for term in ("dịch vụ", "tư vấn", "service", "consult")):
        return "service_question"
    return "generic"


def _local_risk_labels(post_text: str, draft: str, prohibited_topics: list[str]) -> set[str]:
    context = _lower(post_text)
    answer = _lower(draft)
    labels = set()
    if any(term in context for term in ("khiếu nại", "bóc phốt", "phốt", "không hài lòng", "quá tệ", "complaint")):
        labels.add("complaint")
    if any(term in context for term in ("hoàn tiền", "refund", "chargeback")):
        labels.add("refund_dispute")
    if any(term in context for term in ("lừa đảo", "scam", "fraud")):
        labels.add("fraud_allegation")
    if any(term in context for term in ("tai biến", "biến chứng", "adverse event", "nhiễm trùng")):
        labels.add("medical_complication")
    if any(term in context for term in ("khởi kiện", "kiện", "luật sư", "legal action")):
        labels.add("legal_threat")
    if re.search(r"\b(mình|tôi|em)\s+(đã|từng)\b", answer) and any(
        term in answer for term in ("làm", "dùng", "mua", "trải nghiệm", "sử dụng", "đến")
    ):
        labels.add("first_person_testimonial")
    for topic in prohibited_topics:
        if _lower(topic) and (_lower(topic) in context or _lower(topic) in answer):
            labels.add("prohibited_topic")
            break
    return labels


def _select_template(conn, campaign_id: str, intent: str, requested_id=None):
    if requested_id:
        row = conn.execute(
            """SELECT * FROM seeding_template
               WHERE id=? AND campaign_id=? AND enabled=1""",
            (requested_id, campaign_id),
        ).fetchone()
        if row is not None:
            return row
    row = conn.execute(
        """SELECT * FROM seeding_template
           WHERE campaign_id=? AND enabled=1 AND intent=?
           ORDER BY created_at LIMIT 1""",
        (campaign_id, intent),
    ).fetchone()
    if row is None and intent != "generic":
        row = conn.execute(
            """SELECT * FROM seeding_template
               WHERE campaign_id=? AND enabled=1 AND intent='generic'
               ORDER BY created_at LIMIT 1""",
            (campaign_id,),
        ).fetchone()
    return row


def _generation_prompt(campaign, templates, context) -> str:
    payload = {
        "task": "Prepare one transparent promotional Facebook comment. Return JSON only.",
        "required_schema": {
            "intent": "string",
            "draft": "string",
            "confidence": "0..1 number",
            "risk_labels": ["string"],
            "template_id": "string|null",
            "claims_used": ["string"],
        },
        "rules": [
            "Do not invent personal experience or customer testimonials.",
            "Use only allowed_claims for factual/brand claims.",
            "Flag complaint, refund, legal, medical, fraud, ambiguity, or sensitive context.",
            "Prefer a supplied template when it matches the intent.",
        ],
        "campaign": {
            "brand": campaign["brand"],
            "brief": campaign["brief"],
            "allowed_claims": _json_list(campaign["allowed_claims"]),
            "prohibited_topics": _json_list(campaign["prohibited_topics"]),
            "disclosure_policy": campaign["disclosure_policy"],
        },
        "templates": [
            {
                "id": row["id"],
                "intent": row["intent"],
                "text": row["source_text"],
                "allowed_claims": _json_list(row["allowed_claims"]),
            }
            for row in templates
            if row["enabled"]
        ],
        "target": {
            "url": context.get("url"),
            "post_text": context.get("post_text"),
            "surface_name": context.get("surface_name"),
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def _parse_llm_result(text: str) -> dict:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("LLM phải trả JSON object")
    draft = str(parsed.get("draft") or "").strip()
    if not draft:
        raise ValueError("LLM trả draft rỗng")
    confidence = float(parsed.get("confidence", 0))
    return {
        "intent": str(parsed.get("intent") or "generic").strip() or "generic",
        "draft": draft,
        "confidence": max(0.0, min(1.0, confidence)),
        "risk_labels": _clean_items(parsed.get("risk_labels") or []),
        "template_id": parsed.get("template_id"),
        "claims_used": _clean_items(parsed.get("claims_used") or []),
    }


def _is_duplicate(conn, campaign_id: str, draft: str) -> bool:
    normalized = _normalize_for_similarity(draft)
    if not normalized:
        return False
    rows = conn.execute(
        """SELECT a.final_text
           FROM seeding_activity a
           JOIN seeding_target t ON t.id=a.target_id
           WHERE t.campaign_id=? AND a.result='POSTED' AND a.final_text IS NOT NULL
           ORDER BY a.created_at DESC LIMIT 100""",
        (campaign_id,),
    ).fetchall()
    for row in rows:
        previous = _normalize_for_similarity(row["final_text"])
        if previous and SequenceMatcher(None, normalized, previous).ratio() >= _DUPLICATE_SIMILARITY:
            return True
    return False


def _risk_level(labels: set[str]) -> str:
    if labels.intersection(_HIGH_RISK):
        return "HIGH"
    if labels:
        return "MEDIUM"
    return "LOW"


def prepare_target(conn, shift_id: str, target_id: str, context: dict) -> dict:
    shift = _active_shift(conn, shift_id)
    target = conn.execute("SELECT * FROM seeding_target WHERE id=?", (target_id,)).fetchone()
    if target is None or target["campaign_id"] != shift["campaign_id"]:
        raise ValueError("Target không thuộc shift hiện tại")
    if target["status"] in _TERMINAL_TARGET_STATUSES:
        raise ValueError("Target đã ở trạng thái terminal")
    campaign = conn.execute(
        "SELECT * FROM seeding_campaign WHERE id=?", (shift["campaign_id"],)
    ).fetchone()
    if campaign is None:
        raise ValueError("Không tìm thấy campaign")

    post_text = str((context or {}).get("post_text") or "").strip()
    context_url = str((context or {}).get("url") or "").strip()
    local_intent = _local_intent(post_text)
    templates = conn.execute(
        "SELECT * FROM seeding_template WHERE campaign_id=? AND enabled=1 ORDER BY created_at",
        (campaign["id"],),
    ).fetchall()

    labels = set()
    generation = None
    if not post_text:
        labels.add("dom_uncertainty")
    if not _same_target_url(target["url"], context_url):
        labels.add("target_mismatch")

    if _LLM_FN is not None:
        try:
            generation = _parse_llm_result(_LLM_FN(_generation_prompt(campaign, templates, context or {})))
        except Exception:
            labels.add("model_uncertainty")
    if generation is None:
        template = _select_template(conn, campaign["id"], local_intent)
        if template is not None:
            generation = {
                "intent": local_intent,
                "draft": template["source_text"],
                "confidence": 0.80,
                "risk_labels": ["model_uncertainty"],
                "template_id": template["id"],
                "claims_used": _json_list(template["allowed_claims"]),
            }
        else:
            generation = {
                "intent": local_intent,
                "draft": "",
                "confidence": 0.0,
                "risk_labels": ["model_uncertainty"],
                "template_id": None,
                "claims_used": [],
            }

    intent = generation["intent"] or local_intent
    template = _select_template(conn, campaign["id"], intent, generation.get("template_id"))
    template_id = template["id"] if template is not None else None
    draft = generation["draft"]
    confidence = float(generation["confidence"])
    labels.update(generation.get("risk_labels") or [])
    labels.update(
        _local_risk_labels(
            post_text,
            draft,
            _json_list(campaign["prohibited_topics"]),
        )
    )

    allowed_claims = set(_json_list(campaign["allowed_claims"]))
    used_claims = set(generation.get("claims_used") or [])
    if not used_claims.issubset(allowed_claims):
        labels.add("unsupported_claim")
    if confidence < float(campaign["confidence_threshold"]):
        labels.add("model_uncertainty")
    if _is_duplicate(conn, campaign["id"], draft):
        labels.add("duplicate_recent_comment")
    if seeding_global_paused(conn):
        labels.add("global_pause")
    if not str(campaign["disclosure_policy"] or "").strip():
        labels.add("missing_disclosure_policy")

    risk_level = _risk_level(labels)
    auto_allowed = (
        campaign["status"] == "ACTIVE"
        and bool(campaign["auto_submit"])
        and shift["status"] == "ACTIVE"
        and bool(draft)
        and risk_level == "LOW"
        and confidence >= float(campaign["confidence_threshold"])
        and not labels.intersection(MANDATORY_REVIEW)
    )
    decision = "AUTO_READY" if auto_allowed else "REVIEW_REQUIRED"
    old_status = target["status"]
    conn.execute(
        """UPDATE seeding_target
           SET status=?, context_summary=?, intent=?, risk_level=?, risk_labels=?,
               confidence=?, updated_at=? WHERE id=?""",
        (
            decision,
            post_text[:1000],
            intent,
            risk_level,
            json.dumps(sorted(labels), ensure_ascii=False),
            confidence,
            now(),
            target_id,
        ),
    )
    if decision == "REVIEW_REQUIRED" and old_status != "REVIEW_REQUIRED":
        conn.execute(
            "UPDATE seeding_shift SET review_count=review_count+1 WHERE id=?",
            (shift_id,),
        )
    activity_id = ulid()
    conn.execute(
        """INSERT INTO seeding_activity
           (id,target_id,shift_id,action,intent,template_id,generated_text,mode,result,created_at)
           VALUES (?,?,?,?,?,?,?,NULL,?,?)""",
        (
            activity_id,
            target_id,
            shift_id,
            "prepare",
            intent,
            template_id,
            draft or None,
            decision,
            now(),
        ),
    )
    return {
        "target_id": target_id,
        "decision": decision,
        "drafts": [draft] if draft else [],
        "confidence": confidence,
        "risk_level": risk_level,
        "risk_labels": sorted(labels),
        "template_id": template_id,
        "claims_used": sorted(used_claims),
    }


def record_result(
    conn,
    shift_id: str,
    target_id: str,
    *,
    result: str,
    mode: str,
    final_text: str | None = None,
    proof_ref: str | None = None,
    error_detail: str | None = None,
) -> dict:
    if result not in _ALLOWED_RESULTS:
        raise ValueError("Kết quả target không hợp lệ")
    if mode not in _ALLOWED_MODES:
        raise ValueError("mode phải là auto hoặc reviewed")
    shift = conn.execute("SELECT * FROM seeding_shift WHERE id=?", (shift_id,)).fetchone()
    target = conn.execute("SELECT * FROM seeding_target WHERE id=?", (target_id,)).fetchone()
    if shift is None or target is None or target["campaign_id"] != shift["campaign_id"]:
        raise ValueError("Target/shift không hợp lệ")
    current = target["status"]
    if current in _TERMINAL_TARGET_STATUSES:
        if current == result:
            return shift_summary(conn, shift_id)
        raise ValueError("Không thể đổi một target đã terminal sang kết quả khác")

    completed_at = now()
    conn.execute(
        "UPDATE seeding_target SET status=?, completed_at=?, updated_at=?, last_error=? WHERE id=?",
        (result, completed_at, completed_at, error_detail, target_id),
    )
    if result == "POSTED":
        conn.execute("UPDATE seeding_shift SET posted_count=posted_count+1 WHERE id=?", (shift_id,))
    elif result == "UNKNOWN":
        conn.execute("UPDATE seeding_shift SET unknown_count=unknown_count+1 WHERE id=?", (shift_id,))
    else:
        conn.execute("UPDATE seeding_shift SET skipped_count=skipped_count+1 WHERE id=?", (shift_id,))

    conn.execute(
        """INSERT INTO seeding_activity
           (id,target_id,shift_id,action,intent,generated_text,final_text,mode,result,
            proof_ref,error_detail,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            ulid(),
            target_id,
            shift_id,
            "submit_result",
            target["intent"],
            None,
            str(final_text or "").strip() or None,
            mode,
            result,
            str(proof_ref or "").strip() or None,
            str(error_detail or "").strip() or None,
            completed_at,
        ),
    )
    return shift_summary(conn, shift_id)


def shift_summary(conn, shift_id: str) -> dict:
    row = conn.execute("SELECT * FROM seeding_shift WHERE id=?", (shift_id,)).fetchone()
    if row is None:
        raise ValueError("Không tìm thấy shift")
    summary = _dict(row)
    counts = conn.execute(
        """SELECT
             SUM(CASE WHEN result='POSTED' AND mode='auto' THEN 1 ELSE 0 END) AS auto_posted,
             SUM(CASE WHEN result='POSTED' AND mode='reviewed' THEN 1 ELSE 0 END) AS reviewed_posted
           FROM seeding_activity WHERE shift_id=? AND action='submit_result'""",
        (shift_id,),
    ).fetchone()
    summary["auto_posted_count"] = int(counts["auto_posted"] or 0)
    summary["reviewed_posted_count"] = int(counts["reviewed_posted"] or 0)
    return summary


def recent_activities(conn, campaign_id: str, limit: int = 50) -> list[dict]:
    return [
        _dict(row)
        for row in conn.execute(
            """SELECT a.*, t.url
               FROM seeding_activity a
               JOIN seeding_target t ON t.id=a.target_id
               WHERE t.campaign_id=? ORDER BY a.created_at DESC LIMIT ?""",
            (campaign_id, int(limit)),
        ).fetchall()
    ]


def campaign_status_counts(conn, campaign_id: str) -> dict:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM seeding_target WHERE campaign_id=? GROUP BY status",
        (campaign_id,),
    ).fetchall()
    return {row["status"]: int(row["n"]) for row in rows}
