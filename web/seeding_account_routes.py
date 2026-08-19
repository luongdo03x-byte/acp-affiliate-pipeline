"""Account registry, heartbeat and task-account mapping for Seeding."""
from __future__ import annotations

import json

from flask import jsonify, redirect, render_template, request, url_for

from ..core import seeding_accounts, seeding_tasks
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
        item["max_accounts"] = max(1, int(rules.get("max_accounts", 1) or 1))
        out.append(item)
    return out


@bp.get("/seeding/accounts")
def seeding_accounts_page():
    conn = connect()
    try:
        seeding_tasks.ensure_task_schema(conn)
        seeding_accounts.ensure_account_schema(conn)
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


@bp.post("/seeding/campaign/<campaign_id>/accounts")
def seeding_task_accounts_update(campaign_id):
    conn = connect()
    try:
        seeding_tasks.ensure_task_schema(conn)
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
