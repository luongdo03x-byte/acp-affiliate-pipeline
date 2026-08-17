"""Safe REST surface for Account Factory V2 controller state."""
from __future__ import annotations

import hmac
import os

from flask import abort, jsonify, request

from core.db import connect, ulid
from core.factory_v2.repository import FactoryRepository
from core.factory_v2.service import FactoryService


FACTORY_KEY_HEADER = "X-ACP-Factory-Key"

_BATCH_FIELDS = (
    "id", "name", "target_count", "status", "created_at", "started_at",
    "completed_at", "paused_at", "desired_max_workers", "reminder_interval_minutes",
)
_ACCOUNT_FIELDS = (
    "id", "batch_id", "sequence", "group_no", "username", "display_name", "bio",
    "gender_profile", "primary_niche", "secondary_interest", "personality_style",
    "content_tone", "avatar_type", "avatar_theme", "avatar_file", "stage",
    "last_safe_stage", "assigned_worker_id", "current_job_id", "threads_user_id",
    "channel_id", "channel_code", "retry_count", "last_error_code",
    "last_error_message", "created_at", "updated_at", "completed_at",
)
_WORKER_FIELDS = (
    "id", "avd_name", "state", "current_account_id", "current_job_id", "started_at",
    "last_heartbeat_at", "last_progress_at", "processed_count", "recovery_count",
    "estimated_ram_mb", "current_ram_mb", "current_cpu_percent", "draining", "last_error",
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

_WAITING_STAGES = {
    "WAITING_HUMAN", "NEEDS_VERIFICATION", "NEEDS_CONFIRMATION", "USERNAME_UNAVAILABLE",
}
_RUNNING_STAGES = {
    "AVD_ASSIGNED", "IG_READY_FOR_HUMAN", "THREADS_READY_FOR_HUMAN", "ACP_CONNECTING",
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

    active = sum(a["stage"] == "ACP_ACTIVE" for a in accounts)
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

    @app.get("/api/factory/v2/workers")
    def factory_v2_workers():
        _require_factory_key()
        conn, repo = _repo()
        try:
            return jsonify(
                ok=True,
                workers=[_pick(row, _WORKER_FIELDS) for row in repo.list_workers()],
            )
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