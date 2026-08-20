"""Account registry, profile-scoped execution and reporting for Seeding."""
from __future__ import annotations

import csv
import io
import json
import os

from flask import Response, jsonify, redirect, render_template, request, url_for

from ..adapters import factory
from ..core import seeding_accounts, seeding_execution, seeding_reports, seeding_tasks
from ..core.db import connect
from .seeding_routes import bp, _json_body, _require_extension_token, _pending_review_count


def _campaign_rows(conn):
    rows = conn.execute(
        "SELECT id,name,task_rules,created_at FROM seeding_campaign ORDER BY created_at DESC"
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            rules = json.loads(item.get("task_rules") or "{}")
        except (TypeError, json.JSONDecodeError):
            rules = {}
        try:
            item["max_accounts"] = max(1, int(rules.get("max_accounts", 1) or 1))
        except (TypeError, ValueError, AttributeError):
            item["max_accounts"] = 1
        out.append(item)
    return out


def _report_state(conn, campaign_id: str):
    if not campaign_id:
        return None
    try:
        completion = seeding_reports.task_completion(conn, campaign_id)
    except ValueError:
        return None
    row = conn.execute(
        "SELECT * FROM seeding_task_report WHERE campaign_id=?", (campaign_id,)
    ).fetchone()
    unknown_slots = [
        dict(item)
        for item in conn.execute(
            """SELECT s.id,s.account_slot,s.comment_type,s.item_index,
                      s.final_text,s.generated_text,a.label AS account_label
               FROM seeding_comment_slot s
               JOIN seeding_task_account m
                 ON m.campaign_id=s.campaign_id AND m.account_slot=s.account_slot
               JOIN seeding_account a ON a.id=m.account_id
               WHERE s.campaign_id=? AND s.status='UNKNOWN'
               ORDER BY s.account_slot,
                        CASE s.comment_type WHEN 'MAIN' THEN 0 ELSE 1 END,
                        s.item_index""",
            (campaign_id,),
        ).fetchall()
    ]
    return {
        **completion,
        "report": dict(row) if row else None,
        "unknown_slots": unknown_slots,
    }


def _safe_auto_report(conn, campaign_id: str) -> dict:
    try:
        return seeding_reports.maybe_auto_push(conn, campaign_id)
    except Exception as exc:
        # Facebook-side completion has already been persisted. A Sheet failure
        # must never make the extension repeat LIKE/comment work.
        return {"status": "FAILED", "error": str(exc)}


@bp.get("/seeding/accounts")
def seeding_accounts_page():
    conn = connect()
    try:
        seeding_tasks.ensure_task_schema(conn)
        seeding_accounts.ensure_account_schema(conn)
        seeding_execution.ensure_execution_schema(conn)
        seeding_reports.ensure_report_schema(conn)
        campaigns = _campaign_rows(conn)
        selected_id = request.args.get("campaign_id", "").strip()
        if not selected_id and campaigns:
            selected_id = campaigns[0]["id"]
        selected = next((row for row in campaigns if row["id"] == selected_id), None)
        accounts = seeding_accounts.list_accounts(conn)
        mapped = seeding_accounts.list_task_accounts(conn, selected_id) if selected_id else []
        return render_template(
            "seeding_accounts.html",
            page="seeding",
            pending_review=_pending_review_count(conn),
            campaigns=campaigns,
            selected=selected,
            facebook_accounts=accounts,
            task_accounts=mapped,
            task_account_ids={row["account_id"] for row in mapped},
            report_state=_report_state(conn, selected_id),
            sheet_configured=bool(
                os.environ.get("ACP_SEEDING_SHEET_WEBHOOK_URL")
                and os.environ.get("ACP_SEEDING_SHEET_SECRET")
            ),
        )
    finally:
        conn.close()


@bp.post("/api/seeding/account/register")
def seeding_account_register():
    _require_extension_token()
    body = _json_body()
    conn = connect()
    try:
        account = seeding_accounts.register_account(
            conn,
            instance_id=body.get("instance_id"),
            label=body.get("label"),
        )
        seeding_execution.ensure_execution_schema(conn)
        return jsonify(ok=True, account=account)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 409
    finally:
        conn.close()


@bp.post("/api/seeding/account/heartbeat")
def seeding_account_heartbeat():
    _require_extension_token()
    body = _json_body()
    conn = connect()
    try:
        account = seeding_accounts.heartbeat_account(
            conn,
            instance_id=body.get("instance_id"),
        )
        return jsonify(ok=True, account_id=account["id"], last_seen_at=account["last_seen_at"])
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 409
    finally:
        conn.close()


@bp.get("/api/seeding/accounts")
def seeding_accounts_api():
    _require_extension_token()
    conn = connect()
    try:
        return jsonify(ok=True, accounts=seeding_accounts.list_accounts(conn))
    finally:
        conn.close()


@bp.post("/api/seeding/account/next-work")
def seeding_account_next_work():
    _require_extension_token()
    body = _json_body()
    conn = connect()
    try:
        work = seeding_execution.next_account_work(conn, body.get("instance_id"))
        return jsonify(ok=True, work=work)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 409
    finally:
        conn.close()


@bp.post("/api/seeding/account/prepare")
def seeding_account_prepare():
    _require_extension_token()
    body = _json_body()
    context = body.get("context")
    if not isinstance(context, dict):
        return jsonify(ok=False, error="context là bắt buộc"), 400
    conn = connect()
    try:
        rows = seeding_execution.prepare_account_task(
            conn,
            instance_id=body.get("instance_id"),
            campaign_id=str(body.get("campaign_id") or "").strip(),
            target_id=str(body.get("target_id") or "").strip(),
            post_text=context.get("post_text"),
            llm_fn=factory.get_caption_llm(),
        )
        work = seeding_execution.next_account_work(conn, body.get("instance_id"))
        return jsonify(ok=True, generated=len(rows), work=work)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 409
    finally:
        conn.close()


@bp.post("/api/seeding/account/like-result")
def seeding_account_like_result():
    _require_extension_token()
    body = _json_body()
    campaign_id = str(body.get("campaign_id") or "").strip()
    conn = connect()
    try:
        mapping = seeding_execution.record_like(
            conn,
            body.get("instance_id"),
            campaign_id,
            done=bool(body.get("done")),
        )
        report = _safe_auto_report(conn, campaign_id)
        work = seeding_execution.next_account_work(conn, body.get("instance_id"))
        return jsonify(ok=True, mapping=mapping, report=report, work=work)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 409
    finally:
        conn.close()


@bp.post("/api/seeding/account/work-result")
def seeding_account_work_result():
    _require_extension_token()
    body = _json_body()
    conn = connect()
    try:
        slot = seeding_execution.record_comment_result(
            conn,
            instance_id=body.get("instance_id"),
            slot_id=str(body.get("slot_id") or "").strip(),
            result=str(body.get("result") or "").strip(),
            final_text=body.get("final_text"),
            proof_ref=body.get("proof_ref"),
        )
        report = _safe_auto_report(conn, slot["campaign_id"])
        work = seeding_execution.next_account_work(conn, body.get("instance_id"))
        return jsonify(ok=True, slot=slot, report=report, work=work)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 409
    finally:
        conn.close()


@bp.post("/seeding/campaign/<campaign_id>/accounts")
def seeding_task_accounts_update(campaign_id):
    conn = connect()
    try:
        seeding_tasks.ensure_task_schema(conn)
        seeding_execution.ensure_execution_schema(conn)
        mapped = seeding_accounts.assign_task_accounts(
            conn,
            campaign_id,
            request.form.getlist("account_ids"),
        )
        return redirect(
            url_for(
                "seeding.seeding_accounts_page",
                campaign_id=campaign_id,
                message=f"Đã gán {len(mapped)} tài khoản Facebook",
            )
        )
    except ValueError as exc:
        return redirect(
            url_for("seeding.seeding_accounts_page", campaign_id=campaign_id, err=str(exc))
        )
    finally:
        conn.close()


@bp.post("/seeding/comment/<slot_id>/reset-unknown")
def seeding_comment_reset_unknown(slot_id):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT campaign_id FROM seeding_comment_slot WHERE id=?", (slot_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Không tìm thấy comment slot")
        seeding_execution.reset_unknown_comment(conn, slot_id)
        return redirect(
            url_for(
                "seeding.seeding_accounts_page",
                campaign_id=row["campaign_id"],
                message="Đã reset UNKNOWN; profile được gán có thể thử lại slot này",
            )
        )
    except ValueError as exc:
        return redirect(url_for("seeding.seeding_accounts_page", err=str(exc)))
    finally:
        conn.close()


@bp.get("/seeding/campaign/<campaign_id>/report.tsv")
def seeding_report_tsv(campaign_id):
    conn = connect()
    try:
        rows = seeding_reports.build_sheet_rows(conn, campaign_id)
        campaign = conn.execute(
            "SELECT name FROM seeding_campaign WHERE id=?", (campaign_id,)
        ).fetchone()
        if campaign is None:
            raise ValueError("Không tìm thấy nhiệm vụ")
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter="\t", lineterminator="\n")
        writer.writerows(rows)
        safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in campaign["name"])
        return Response(
            buffer.getvalue(),
            mimetype="text/tab-separated-values; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_name or "seeding-report"}.tsv"'},
        )
    except ValueError as exc:
        return redirect(url_for("seeding.seeding_accounts_page", campaign_id=campaign_id, err=str(exc)))
    finally:
        conn.close()


@bp.post("/seeding/campaign/<campaign_id>/report/sheet")
def seeding_report_sheet(campaign_id):
    conn = connect()
    try:
        result = seeding_reports.push_to_sheet(
            conn,
            campaign_id,
            webhook_url=os.environ.get("ACP_SEEDING_SHEET_WEBHOOK_URL", ""),
            secret=os.environ.get("ACP_SEEDING_SHEET_SECRET", ""),
        )
        return redirect(
            url_for(
                "seeding.seeding_accounts_page",
                campaign_id=campaign_id,
                message=f"Đã ghi Sheet: {result.get('sheet_ref') or 'OK'}",
            )
        )
    except Exception as exc:
        return redirect(
            url_for("seeding.seeding_accounts_page", campaign_id=campaign_id, err=str(exc))
        )
    finally:
        conn.close()


@bp.app_context_processor
def inject_seeding_account_context():
    if request.path != "/seeding":
        return {}
    conn = connect()
    try:
        seeding_tasks.ensure_task_schema(conn)
        seeding_accounts.ensure_account_schema(conn)
        selected_id = request.args.get("campaign_id", "").strip()
        if not selected_id:
            row = conn.execute(
                "SELECT id FROM seeding_campaign ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            selected_id = row["id"] if row else ""
        accounts = seeding_accounts.list_accounts(conn)
        mapped = (
            seeding_accounts.list_task_accounts(conn, selected_id)
            if selected_id
            else []
        )
        return {
            "facebook_accounts": accounts,
            "task_accounts": mapped,
            "task_account_ids": {row["account_id"] for row in mapped},
        }
    except ValueError:
        return {
            "facebook_accounts": [],
            "task_accounts": [],
            "task_account_ids": set(),
        }
    finally:
        conn.close()
