"""Dashboard and token-protected API routes for Facebook Seeding Assistant."""
from __future__ import annotations

import hmac
import json
import os

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for

from ..adapters import factory
from ..core import seeding
from ..core.db import connect
from ..core.system_settings import seeding_global_paused, set_seeding_global_paused

bp = Blueprint("seeding", __name__)


def _lines(value: str) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def _json_lines(value) -> str:
    try:
        rows = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        rows = []
    if not isinstance(rows, list):
        rows = []
    return "\n".join(str(item) for item in rows if str(item).strip())


def _redirect_to_campaign(campaign_id=None, *, err=None, message=None):
    values = {}
    if campaign_id:
        values["campaign_id"] = campaign_id
    if err:
        values["err"] = str(err)
    if message:
        values["message"] = str(message)
    return redirect(url_for("seeding.seeding_page", **values))


def _require_extension_token() -> None:
    expected = os.environ.get("ACP_SEEDING_EXTENSION_TOKEN", "")
    given = request.headers.get("X-ACP-Seeding-Token", "")
    if not expected or not given or not hmac.compare_digest(expected, given):
        abort(401)


def _json_body() -> dict:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        abort(400, "JSON object bắt buộc")
    return value


def _find_active_shift(conn, shift_id=None):
    if shift_id:
        return conn.execute(
            "SELECT * FROM seeding_shift WHERE id=? AND status='ACTIVE'",
            (str(shift_id),),
        ).fetchone()
    return conn.execute(
        "SELECT * FROM seeding_shift WHERE status='ACTIVE' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()


def _pending_review_count(conn) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM post WHERE status IN ('PENDING_REVIEW','DRAFT')"
        ).fetchone()[0]
    )


@bp.get("/seeding")
def seeding_page():
    conn = connect()
    try:
        campaigns = seeding.list_campaigns(conn)
        selected = None
        selected_id = request.args.get("campaign_id", "").strip()
        if selected_id:
            selected = next((item for item in campaigns if item["id"] == selected_id), None)
        elif campaigns:
            selected = campaigns[0]

        templates = []
        targets = []
        activities = []
        counts = {}
        active_shift = None
        if selected:
            templates = seeding.list_templates(conn, selected["id"])
            targets = seeding.list_targets(conn, selected["id"], limit=200)
            activities = seeding.recent_activities(conn, selected["id"], limit=30)
            counts = seeding.campaign_status_counts(conn, selected["id"])
            active_shift = conn.execute(
                """SELECT * FROM seeding_shift
                   WHERE campaign_id=? AND status IN ('ACTIVE','PAUSED')
                   ORDER BY started_at DESC LIMIT 1""",
                (selected["id"],),
            ).fetchone()
            selected = dict(selected)
            selected["allowed_claims_text"] = _json_lines(selected["allowed_claims"])
            selected["prohibited_topics_text"] = _json_lines(selected["prohibited_topics"])

        return render_template(
            "seeding.html",
            page="seeding",
            pending_review=_pending_review_count(conn),
            campaigns=campaigns,
            selected=selected,
            templates=templates,
            targets=targets,
            activities=activities,
            counts=counts,
            active_shift=dict(active_shift) if active_shift else None,
            global_paused=seeding_global_paused(conn),
        )
    finally:
        conn.close()


@bp.post("/seeding/campaign")
def seeding_campaign_create():
    conn = connect()
    try:
        campaign = seeding.create_campaign(
            conn,
            name=request.form.get("name", ""),
            brand=request.form.get("brand", ""),
            brief=request.form.get("brief", ""),
            allowed_claims=_lines(request.form.get("allowed_claims", "")),
            prohibited_topics=_lines(request.form.get("prohibited_topics", "")),
            disclosure_policy=request.form.get("disclosure_policy", ""),
            auto_submit=request.form.get("auto_submit") == "1",
            confidence_threshold=float(request.form.get("confidence_threshold", "0.90")),
        )
        return _redirect_to_campaign(campaign["id"], message="Đã tạo campaign")
    except (TypeError, ValueError) as exc:
        return _redirect_to_campaign(err=exc)
    finally:
        conn.close()


@bp.post("/seeding/campaign/<campaign_id>/config")
def seeding_campaign_config(campaign_id):
    conn = connect()
    try:
        seeding.update_campaign(
            conn,
            campaign_id,
            name=request.form.get("name", ""),
            brand=request.form.get("brand", ""),
            brief=request.form.get("brief", ""),
            allowed_claims=_lines(request.form.get("allowed_claims", "")),
            prohibited_topics=_lines(request.form.get("prohibited_topics", "")),
            disclosure_policy=request.form.get("disclosure_policy", ""),
            auto_submit=request.form.get("auto_submit") == "1",
            confidence_threshold=float(request.form.get("confidence_threshold", "0.90")),
            status=request.form.get("status", "ACTIVE"),
        )
        return _redirect_to_campaign(campaign_id, message="Đã cập nhật campaign")
    except (TypeError, ValueError) as exc:
        return _redirect_to_campaign(campaign_id, err=exc)
    finally:
        conn.close()


@bp.post("/seeding/campaign/<campaign_id>/templates")
def seeding_template_create(campaign_id):
    conn = connect()
    try:
        seeding.add_template(
            conn,
            campaign_id,
            intent=request.form.get("intent", "generic"),
            source_text=request.form.get("source_text", ""),
            allowed_claims=_lines(request.form.get("template_claims", "")),
        )
        return _redirect_to_campaign(campaign_id, message="Đã thêm template")
    except ValueError as exc:
        return _redirect_to_campaign(campaign_id, err=exc)
    finally:
        conn.close()


@bp.post("/seeding/campaign/<campaign_id>/targets/import")
def seeding_targets_import(campaign_id):
    conn = connect()
    try:
        result = seeding.import_targets(
            conn,
            campaign_id,
            _lines(request.form.get("target_urls", "")),
        )
        return _redirect_to_campaign(
            campaign_id,
            message=(
                f"Đã thêm {result['created']} target; "
                f"trùng {result['duplicates']}; không hợp lệ {result['invalid']}"
            ),
        )
    except ValueError as exc:
        return _redirect_to_campaign(campaign_id, err=exc)
    finally:
        conn.close()


@bp.post("/seeding/campaign/<campaign_id>/shift/start")
def seeding_shift_start(campaign_id):
    conn = connect()
    try:
        shift = seeding.start_shift(conn, campaign_id)
        return _redirect_to_campaign(
            campaign_id, message=f"Shift {shift['id'][:8]} đang ACTIVE"
        )
    except ValueError as exc:
        return _redirect_to_campaign(campaign_id, err=exc)
    finally:
        conn.close()


@bp.post("/seeding/shift/<shift_id>/pause")
def seeding_shift_pause(shift_id):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT campaign_id FROM seeding_shift WHERE id=?", (shift_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Không tìm thấy shift")
        seeding.pause_shift(conn, shift_id)
        return _redirect_to_campaign(row["campaign_id"], message="Đã pause shift")
    except ValueError as exc:
        return _redirect_to_campaign(err=exc)
    finally:
        conn.close()


@bp.post("/seeding/shift/<shift_id>/end")
def seeding_shift_end(shift_id):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT campaign_id FROM seeding_shift WHERE id=?", (shift_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Không tìm thấy shift")
        seeding.end_shift(conn, shift_id)
        return _redirect_to_campaign(row["campaign_id"], message="Đã kết thúc shift")
    except ValueError as exc:
        return _redirect_to_campaign(err=exc)
    finally:
        conn.close()


@bp.post("/seeding/global-pause")
def seeding_global_pause_route():
    value = request.form.get("paused", "")
    if value not in {"0", "1"}:
        abort(400, "Giá trị pause không hợp lệ")
    conn = connect()
    try:
        set_seeding_global_paused(conn, value == "1", actor="operator")
    finally:
        conn.close()
    return _redirect_to_campaign(
        request.form.get("campaign_id") or None,
        message="Global pause đã bật" if value == "1" else "Global pause đã tắt",
    )


@bp.get("/api/seeding/status")
def seeding_api_status():
    _require_extension_token()
    conn = connect()
    try:
        shift = _find_active_shift(conn, request.args.get("shift_id"))
        return jsonify(
            ok=True,
            paused=seeding_global_paused(conn),
            active_shift_id=shift["id"] if shift else None,
            campaign_id=shift["campaign_id"] if shift else None,
        )
    finally:
        conn.close()


@bp.post("/api/seeding/next-target")
def seeding_api_next_target():
    _require_extension_token()
    body = _json_body()
    conn = connect()
    try:
        if seeding_global_paused(conn):
            return jsonify(ok=False, paused=True, error="global_pause"), 409
        shift = _find_active_shift(conn, body.get("shift_id"))
        if shift is None:
            return jsonify(ok=False, error="no_active_shift"), 409
        target = seeding.next_target(conn, shift["id"])
        return jsonify(
            ok=True,
            shift_id=shift["id"],
            target=target,
            done=target is None,
        )
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 409
    finally:
        conn.close()


@bp.post("/api/seeding/analyze")
def seeding_api_analyze():
    _require_extension_token()
    body = _json_body()
    target_id = str(body.get("target_id") or "").strip()
    context = body.get("context")
    if not target_id or not isinstance(context, dict):
        abort(400, "target_id và context là bắt buộc")
    conn = connect()
    try:
        shift = _find_active_shift(conn, body.get("shift_id"))
        if shift is None:
            return jsonify(ok=False, error="no_active_shift"), 409
        decision = seeding.prepare_target(
            conn, shift["id"], target_id, context
        )
        return jsonify(ok=True, shift_id=shift["id"], **decision)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 409
    finally:
        conn.close()


def _record_api_result(*, force_reviewed=False):
    _require_extension_token()
    body = _json_body()
    target_id = str(body.get("target_id") or "").strip()
    if not target_id:
        abort(400, "target_id là bắt buộc")
    conn = connect()
    try:
        shift = _find_active_shift(conn, body.get("shift_id"))
        if shift is None:
            return jsonify(ok=False, error="no_active_shift"), 409
        mode = "reviewed" if force_reviewed else str(body.get("mode") or "").strip()
        summary = seeding.record_result(
            conn,
            shift["id"],
            target_id,
            result=str(body.get("result") or "").strip(),
            mode=mode,
            final_text=body.get("final_text"),
            proof_ref=body.get("proof_ref"),
            error_detail=body.get("error_detail"),
        )
        target = conn.execute(
            "SELECT status FROM seeding_target WHERE id=?", (target_id,)
        ).fetchone()
        return jsonify(
            ok=True,
            target_status=target["status"] if target else None,
            summary=summary,
        )
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 409
    finally:
        conn.close()


@bp.post("/api/seeding/result")
def seeding_api_result():
    return _record_api_result(force_reviewed=False)


@bp.post("/api/seeding/review-result")
def seeding_api_review_result():
    return _record_api_result(force_reviewed=True)


def register_seeding(app) -> None:
    """Attach seeding routes and reuse ACP's configured optional LLM callback."""
    seeding.set_llm(factory.get_caption_llm())
    app.register_blueprint(bp)
