"""Account registry, heartbeat and task-account mapping for Seeding."""
from __future__ import annotations

from flask import jsonify, redirect, request, url_for

from ..core import seeding_accounts, seeding_tasks
from ..core.db import connect
from .seeding_routes import bp, _json_body, _require_extension_token


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
                "seeding.seeding_page",
                campaign_id=campaign_id,
                message=f"Đã gán {len(mapped)} tài khoản Facebook",
            )
        )
    except ValueError as exc:
        return redirect(
            url_for("seeding.seeding_page", campaign_id=campaign_id, err=str(exc))
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
