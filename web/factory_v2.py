"""Safe REST surface for Account Factory V2 controller state."""
from __future__ import annotations

import hmac
import json
import os

from flask import abort, jsonify, request

from core.account_factory import ThreadsOAuthClient
from core.db import connect, now, ulid
from core.factory_v2.oauth_bridge import start_account_oauth, sync_account_from_oauth_session
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.service import FactoryService


FACTORY_KEY_HEADER = "X-ACP-Factory-Key"

_BATCH_FIELDS = (
    "id", "name", "target_count", "status", "created_at", "started_at",
    "completed_at", "paused_at", "desired_max_workers", "reminder_interval_minutes",
    "completion_mode",
)
_ACCOUNT_FIELDS = (
    "id", "batch_id", "sequence", "group_no", "username", "display_name", "bio",
    "gender_profile", "primary_niche", "secondary_interest", "personality_style",
    "content_tone", "avatar_type", "avatar_theme", "avatar_file", "signup_contact_type",
    "phone", "email", "birth_date", "stage", "last_safe_stage", "execution_target",
    "assigned_worker_id", "current_job_id", "threads_user_id", "channel_id", "channel_code",
    "retry_count", "last_error_code", "last_error_message", "created_at", "updated_at",
    "completed_at",
)
_RUNNER_FIELDS = (
    "id", "runner_type", "device_id", "device_name", "avd_name", "state",
    "current_account_id", "current_job_id", "started_at", "last_heartbeat_at",
    "last_progress_at", "processed_count", "recovery_count", "estimated_ram_mb",
    "current_ram_mb", "current_cpu_percent", "draining", "last_error",
)
_CHECKPOINT_FIELDS = (
    "id", "batch_id", "account_id", "worker_id", "type", "status", "message",
    "created_at", "last_reminded_at", "next_reminder_at", "reminder_count",
    "snoozed_until", "resolved_at", "resolution",
)
_HOST_FIELDS = (
    "cpu_percent", "ram_total_mb", "ram_available_mb", "swap_used_mb", "swap_in_rate",
    "load_1m", "load_5m", "avd_total", "avd_running", "avd_waiting_human",
    "capacity_state", "desired_workers", "timestamp",
)
_ALLOWED_RUNNER_RESULT_KEYS = frozenset({
    "package", "activity", "waiting_human", "error_code", "prepared",
})
_ALLOWED_CREATE_ACCOUNT_FIELDS = frozenset({
    "execution_target", "batch_name", "completion_mode", "signup_contact_type",
    "phone", "email", "birth_date", "avatar_file",
})

_WAITING_STAGES = {
    "WAITING_HUMAN", "NEEDS_VERIFICATION", "NEEDS_CONFIRMATION", "USERNAME_UNAVAILABLE",
}
_RUNNING_STAGES = {
    "AVD_ASSIGNED", "RUNNER_ASSIGNED", "IG_READY_FOR_HUMAN",
    "THREADS_READY_FOR_HUMAN", "ACP_CONNECTING",
}
_ACTIVE_WORKER_STATES = {
    "STARTING", "READY", "RUNNING", "WAITING_HUMAN", "RECOVERING", "DRAINING",
}


def _factory_key() -> str:
    return os.environ.get("ACP_FACTORY_API_KEY", "").strip()


def _require_factory_key() -> None:
    expected = _factory_key()
    if not expected:
        abort(503, "ACP_FACTORY_API_KEY chưa được cấu hình")
    received = request.headers.get(FACTORY_KEY_HEADER, "")
    if not received or not hmac.compare_digest(received.encode(), expected.encode()):
        abort(401, "Factory key không hợp lệ")


def _base_url() -> str:
    configured = os.environ.get("ACP_PUBLIC_BASE_URL", "").strip().rstrip("/")
    return configured or request.host_url.rstrip("/")


def _redirect_uri() -> str:
    return _base_url() + "/oauth/account-factory/threads/callback"


def _provider(app):
    factory = app.config.get("ACCOUNT_FACTORY_OAUTH_FACTORY")
    return factory() if factory else ThreadsOAuthClient()


def _pick(row: dict | None, fields) -> dict | None:
    if row is None:
        return None
    return {name: row.get(name) for name in fields if name in row}


def _repo():
    conn = connect()
    return conn, FactoryRepository(conn)


def _dashboard(repo: FactoryRepository) -> dict:
    batch = repo.latest_batch()
    accounts = repo.query_accounts(batch_id=batch["id"]) if batch else []
    workers = [w for w in repo.list_workers() if w["state"] in _ACTIVE_WORKER_STATES]
    host = repo.latest_resource_sample()

    completion_mode = str((batch or {}).get("completion_mode") or "ACP_ACTIVE").upper()
    active = sum(
        a["stage"] == "ACP_ACTIVE"
        or (completion_mode == "SOCIAL_ONLY" and a["stage"] == "THREADS_CREATED")
        for a in accounts
    )
    running = sum(a["stage"] in _RUNNING_STAGES for a in accounts)
    waiting = sum(a["stage"] in _WAITING_STAGES for a in accounts)
    errors = sum(a["stage"] == "ERROR" for a in accounts)
    queued = max(0, len(accounts) - active - running - waiting - errors)

    return {
        "ok": True,
        "batch": _pick(batch, _BATCH_FIELDS),
        "accounts": {
            "total": len(accounts),
            "active": active,
            "running": running,
            "waiting_human": waiting,
            "error": errors,
            "queued": queued,
        },
        "workers": {
            "total": len(workers),
            "running": sum(w["state"] == "RUNNING" for w in workers),
            "waiting_human": sum(w["state"] == "WAITING_HUMAN" for w in workers),
            "starting": sum(w["state"] == "STARTING" for w in workers),
        },
        "host": _pick(host, _HOST_FIELDS),
    }


def _accepted(status: str, command_id: str | None = None):
    return jsonify(ok=True, command_id=command_id or ulid(), status=status), 202


def _public_runner_command(row: dict | None) -> dict | None:
    if row is None:
        return None
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "id": row["id"],
        "job_id": row["job_id"],
        "account_id": row["account_id"],
        "action": row["action"],
        "payload": payload,
        "created_at": row["created_at"],
    }


def _clean_runner_result(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("result phải là object")
    unknown = set(value) - _ALLOWED_RUNNER_RESULT_KEYS
    if unknown:
        raise ValueError(f"runner result chứa field không hợp lệ: {sorted(unknown)}")
    clean = {}
    for key, child in value.items():
        if key in {"package", "activity", "error_code"}:
            clean[key] = None if child is None else str(child)[:240]
        elif key in {"waiting_human", "prepared"}:
            clean[key] = bool(child)
    return clean


def register_factory_v2_routes(app):
    @app.get("/api/factory/v2/dashboard")
    def factory_v2_dashboard():
        _require_factory_key()
        conn, repo = _repo()
        try:
            return jsonify(_dashboard(repo))
        finally:
            conn.close()

    @app.get("/api/factory/v2/batches/<batch_id>")
    def factory_v2_batch(batch_id):
        _require_factory_key()
        conn, repo = _repo()
        try:
            batch = repo.get_batch(batch_id)
            if batch is None:
                return jsonify(ok=False, error="Batch không tồn tại"), 404
            return jsonify(ok=True, batch=_pick(batch, _BATCH_FIELDS))
        finally:
            conn.close()

    @app.get("/api/factory/v2/accounts")
    def factory_v2_accounts():
        _require_factory_key()
        conn, repo = _repo()
        try:
            rows = repo.query_accounts(
                batch_id=request.args.get("batch_id") or None,
                stage=(request.args.get("stage") or "").strip().upper() or None,
            )
            return jsonify(ok=True, accounts=[_pick(row, _ACCOUNT_FIELDS) for row in rows])
        finally:
            conn.close()

    @app.post("/api/factory/v2/accounts")
    def factory_v2_create_account():
        _require_factory_key()
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(ok=False, error="Body JSON không hợp lệ"), 400
        unknown = set(data) - _ALLOWED_CREATE_ACCOUNT_FIELDS
        if unknown:
            return jsonify(ok=False, error=f"Field không được phép: {sorted(unknown)}"), 400
        execution_target = str(data.get("execution_target") or "").strip()
        if not execution_target:
            return jsonify(ok=False, error="Thiếu execution_target"), 400
        batch_name = " ".join(str(data.get("batch_name") or "Phone/AVD Pilot").split())
        if len(batch_name) > 120:
            return jsonify(ok=False, error="batch_name quá dài"), 400

        conn, repo = _repo()
        try:
            service = FactoryService(repo)
            try:
                result = service.create_single_account(
                    execution_target=execution_target,
                    batch_name=batch_name,
                    completion_mode=data.get("completion_mode") or "ACP_ACTIVE",
                    signup_contact_type=data.get("signup_contact_type"),
                    phone=data.get("phone"),
                    email=data.get("email"),
                    birth_date=data.get("birth_date"),
                    avatar_file=data.get("avatar_file"),
                )
            except KeyError:
                return jsonify(ok=False, error="Runner không tồn tại"), 404
            except ValueError as exc:
                return jsonify(ok=False, error=str(exc)), 409
            return jsonify(
                ok=True,
                batch=_pick(result["batch"], _BATCH_FIELDS),
                account=_pick(result["account"], _ACCOUNT_FIELDS),
            ), 201
        finally:
            conn.close()

    @app.get("/api/factory/v2/accounts/<account_id>")
    def factory_v2_account(account_id):
        _require_factory_key()
        conn, repo = _repo()
        try:
            account = repo.get_account(account_id)
            if account is None:
                return jsonify(ok=False, error="Account không tồn tại"), 404
            return jsonify(ok=True, account=_pick(account, _ACCOUNT_FIELDS))
        finally:
            conn.close()

    @app.post("/api/factory/v2/accounts/<account_id>/oauth/start")
    def factory_v2_oauth_start(account_id):
        _require_factory_key()
        conn, repo = _repo()
        try:
            if repo.get_account(account_id) is None:
                return jsonify(ok=False, error="Account không tồn tại"), 404
            try:
                result = start_account_oauth(
                    conn,
                    account_id,
                    _redirect_uri(),
                    _provider(app),
                )
            except KeyError:
                return jsonify(ok=False, error="Account không tồn tại"), 404
            except ValueError as exc:
                return jsonify(ok=False, error=str(exc)), 409
            except RuntimeError:
                return jsonify(ok=False, error="Threads OAuth chưa được cấu hình trên ACP"), 503
            return jsonify(ok=True, **result), 201
        finally:
            conn.close()

    @app.get("/api/factory/v2/accounts/<account_id>/oauth/status")
    def factory_v2_oauth_status(account_id):
        _require_factory_key()
        conn, repo = _repo()
        try:
            account = repo.get_account(account_id)
            if account is None:
                return jsonify(ok=False, error="Account không tồn tại"), 404
            session_id = account.get("oauth_session_id")
            if not session_id:
                return jsonify(ok=False, error="Account chưa có OAuth session"), 409
            try:
                updated = sync_account_from_oauth_session(conn, session_id)
            except KeyError:
                return jsonify(ok=False, error="OAuth session không tồn tại"), 404
            except ValueError as exc:
                return jsonify(ok=False, error=str(exc)), 409
            return jsonify(ok=True, account=_pick(updated, _ACCOUNT_FIELDS))
        finally:
            conn.close()

    @app.get("/api/factory/v2/workers")
    def factory_v2_workers():
        _require_factory_key()
        conn, repo = _repo()
        try:
            return jsonify(
                ok=True,
                workers=[_pick(row, _RUNNER_FIELDS) for row in repo.list_workers()],
            )
        finally:
            conn.close()

    @app.get("/api/factory/v2/runners")
    def factory_v2_runners():
        _require_factory_key()
        conn, repo = _repo()
        try:
            return jsonify(
                ok=True,
                runners=[_pick(row, _RUNNER_FIELDS) for row in repo.list_workers()],
            )
        finally:
            conn.close()

    @app.post("/api/factory/v2/runners/local/register")
    def factory_v2_register_local_runner():
        _require_factory_key()
        data = request.get_json(silent=True) or {}
        conn, repo = _repo()
        try:
            service = FactoryService(repo)
            try:
                runner = service.register_local_runner(
                    data.get("device_id"), data.get("device_name")
                )
            except ValueError as exc:
                return jsonify(ok=False, error=str(exc)), 400
            return jsonify(ok=True, runner=_pick(runner, _RUNNER_FIELDS)), 201
        finally:
            conn.close()

    @app.post("/api/factory/v2/runners/<worker_id>/heartbeat")
    def factory_v2_runner_heartbeat(worker_id):
        _require_factory_key()
        data = request.get_json(silent=True) or {}
        conn, repo = _repo()
        try:
            service = FactoryService(repo)
            try:
                runner = service.heartbeat_runner(
                    worker_id,
                    current_account_id=data.get("current_account_id"),
                    current_job_id=data.get("current_job_id"),
                )
            except KeyError:
                return jsonify(ok=False, error="Runner không tồn tại"), 404
            except ValueError as exc:
                return jsonify(ok=False, error=str(exc)), 409
            return jsonify(ok=True, runner=_pick(runner, _RUNNER_FIELDS)), 201
        finally:
            conn.close()

    @app.get("/api/factory/v2/runners/<worker_id>/commands/next")
    def factory_v2_next_runner_command(worker_id):
        _require_factory_key()
        conn, repo = _repo()
        try:
            runner = repo.get_worker(worker_id)
            if runner is None:
                return jsonify(ok=False, error="Runner không tồn tại"), 404
            if runner.get("runner_type") != "LOCAL_DEVICE":
                return jsonify(ok=False, error="Command polling chỉ dành cho LOCAL_DEVICE"), 409
            command = repo.claim_next_runner_command(worker_id, delivered_at=now())
            return jsonify(ok=True, command=_public_runner_command(command))
        finally:
            conn.close()

    @app.post("/api/factory/v2/runners/<worker_id>/commands/<command_id>/result")
    def factory_v2_runner_command_result(worker_id, command_id):
        _require_factory_key()
        data = request.get_json(silent=True) or {}
        status = str(data.get("status") or "").upper()
        if status not in {"COMPLETED", "FAILED"}:
            return jsonify(ok=False, error="status phải là COMPLETED hoặc FAILED"), 400
        try:
            result = _clean_runner_result(data.get("result"))
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400

        conn, repo = _repo()
        try:
            runner = repo.get_worker(worker_id)
            if runner is None:
                return jsonify(ok=False, error="Runner không tồn tại"), 404
            if runner.get("runner_type") != "LOCAL_DEVICE":
                return jsonify(ok=False, error="Command result chỉ dành cho LOCAL_DEVICE"), 409
            try:
                command = repo.complete_runner_command(
                    worker_id,
                    command_id,
                    status=status,
                    result_json=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    completed_at=now(),
                )
            except KeyError:
                return jsonify(ok=False, error="Command không tồn tại"), 404
            except ValueError as exc:
                return jsonify(ok=False, error=str(exc)), 409
            repo.update_worker_fields(worker_id, last_progress_at=now())
            return _accepted(command["status"], command_id)
        finally:
            conn.close()

    @app.get("/api/factory/v2/checkpoints")
    def factory_v2_checkpoints():
        _require_factory_key()
        conn, repo = _repo()
        try:
            rows = repo.list_checkpoints(
                batch_id=request.args.get("batch_id") or None,
                status=(request.args.get("status") or "").strip().upper() or None,
            )
            return jsonify(
                ok=True,
                checkpoints=[_pick(row, _CHECKPOINT_FIELDS) for row in rows],
            )
        finally:
            conn.close()

    @app.post("/api/factory/v2/batches/<batch_id>/pause")
    def factory_v2_pause_batch(batch_id):
        _require_factory_key()
        conn, repo = _repo()
        try:
            service = FactoryService(repo)
            try:
                batch = service.pause_batch(batch_id)
            except KeyError:
                return jsonify(ok=False, error="Batch không tồn tại"), 404
            except ValueError as exc:
                return jsonify(ok=False, error=str(exc)), 409
            return _accepted(batch["status"])
        finally:
            conn.close()

    @app.post("/api/factory/v2/batches/<batch_id>/resume")
    def factory_v2_resume_batch(batch_id):
        _require_factory_key()
        conn, repo = _repo()
        try:
            service = FactoryService(repo)
            try:
                batch = service.resume_batch(batch_id)
            except KeyError:
                return jsonify(ok=False, error="Batch không tồn tại"), 404
            except ValueError as exc:
                return jsonify(ok=False, error=str(exc)), 409
            return _accepted(batch["status"])
        finally:
            conn.close()

    @app.post("/api/factory/v2/checkpoints/<checkpoint_id>/continue")
    def factory_v2_continue_checkpoint(checkpoint_id):
        _require_factory_key()
        conn, repo = _repo()
        try:
            service = FactoryService(repo)
            try:
                result = service.request_checkpoint_verification(checkpoint_id)
            except KeyError:
                return jsonify(ok=False, error="Checkpoint không tồn tại"), 404
            except ValueError as exc:
                return jsonify(ok=False, error=str(exc)), 409
            return _accepted(result["status"], result["command_id"])
        finally:
            conn.close()

    @app.post("/api/factory/v2/checkpoints/<checkpoint_id>/retry")
    def factory_v2_retry_checkpoint(checkpoint_id):
        _require_factory_key()
        conn, repo = _repo()
        try:
            service = FactoryService(repo)
            try:
                result = service.request_checkpoint_verification(
                    checkpoint_id, action="RETRY_CHECKPOINT"
                )
            except KeyError:
                return jsonify(ok=False, error="Checkpoint không tồn tại"), 404
            except ValueError as exc:
                return jsonify(ok=False, error=str(exc)), 409
            return _accepted(result["status"], result["command_id"])
        finally:
            conn.close()

    @app.post("/api/factory/v2/checkpoints/<checkpoint_id>/snooze")
    def factory_v2_snooze_checkpoint(checkpoint_id):
        _require_factory_key()
        data = request.get_json(silent=True) or {}
        try:
            minutes = int(data.get("minutes"))
        except (TypeError, ValueError):
            return jsonify(ok=False, error="minutes phải là 10, 30 hoặc 60"), 400
        if minutes not in {10, 30, 60}:
            return jsonify(ok=False, error="minutes phải là 10, 30 hoặc 60"), 400
        conn, repo = _repo()
        try:
            service = FactoryService(repo)
            try:
                checkpoint = service.snooze_checkpoint(checkpoint_id, minutes)
            except KeyError:
                return jsonify(ok=False, error="Checkpoint không tồn tại"), 404
            except ValueError as exc:
                return jsonify(ok=False, error=str(exc)), 409
            return _accepted(checkpoint["status"])
        finally:
            conn.close()

    @app.post("/api/factory/v2/accounts/<account_id>/stop")
    def factory_v2_stop_account(account_id):
        _require_factory_key()
        conn, repo = _repo()
        try:
            service = FactoryService(repo)
            try:
                account = service.stop_account(account_id)
            except KeyError:
                return jsonify(ok=False, error="Account không tồn tại"), 404
            except ValueError as exc:
                return jsonify(ok=False, error=str(exc)), 409
            return _accepted(account["stage"])
        finally:
            conn.close()

    @app.post("/api/factory/v2/accounts/<account_id>/retry")
    def factory_v2_retry_account(account_id):
        _require_factory_key()
        conn, repo = _repo()
        try:
            service = FactoryService(repo)
            try:
                account = service.retry_account(account_id)
            except KeyError:
                return jsonify(ok=False, error="Account không tồn tại"), 404
            except ValueError as exc:
                return jsonify(ok=False, error=str(exc)), 409
            return _accepted(account["stage"])
        finally:
            conn.close()

    @app.post("/api/factory/v2/workers/<worker_id>/drain")
    def factory_v2_drain_worker(worker_id):
        _require_factory_key()
        conn, repo = _repo()
        try:
            service = FactoryService(repo)
            try:
                worker = service.request_worker_drain(worker_id)
            except KeyError:
                return jsonify(ok=False, error="Worker không tồn tại"), 404
            except ValueError as exc:
                return jsonify(ok=False, error=str(exc)), 409
            return _accepted(worker["state"])
        finally:
            conn.close()

    @app.post("/api/factory/v2/workers/<worker_id>/restart")
    def factory_v2_restart_worker(worker_id):
        _require_factory_key()
        conn, repo = _repo()
        try:
            service = FactoryService(repo)
            try:
                worker = service.request_worker_restart(worker_id)
            except KeyError:
                return jsonify(ok=False, error="Worker không tồn tại"), 404
            except ValueError as exc:
                return jsonify(ok=False, error=str(exc)), 409
            return _accepted(worker["state"])
        finally:
            conn.close()

    return app