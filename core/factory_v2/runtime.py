"""Controller runtime that leases accounts and drives safe runner checkpoints.

The runtime never submits signup forms, credentials, OTP/CAPTCHA, identity
checks, or Threads publishing. Runner automation is limited to preparing text,
opening official apps/URLs, observing foreground package state, and waiting for
the operator at explicit checkpoints.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import threading

from core.db import now, transaction, ulid

from .models import AccountStage
from .runner_gateway import RunnerGateway


_LOG = logging.getLogger(__name__)
_INSTAGRAM_PACKAGE = "com.instagram.android"
_THREADS_PACKAGE = "com.instagram.barcelona"


def _pending(result: dict | None) -> bool:
    return isinstance(result, dict) and result.get("status") == "pending"


def _lease_extension(seconds: int = 180) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


class FactoryControllerRuntime:
    def __init__(
        self,
        repository,
        service,
        scheduler,
        supervisor,
        worker_processes,
        *,
        runner_gateway=None,
        activation_service=None,
        owned_connection=None,
    ):
        self.repo = repository
        self.service = service
        self.scheduler = scheduler
        self.supervisor = supervisor
        self.worker_processes = worker_processes
        self.runner_gateway = runner_gateway or RunnerGateway(repository, worker_processes)
        self.activation_service = activation_service
        self.owned_connection = owned_connection

    def _activation(self):
        if self.activation_service is None:
            from .activation import FactoryActivationService
            self.activation_service = FactoryActivationService(self.repo.conn)
        return self.activation_service

    def _command(self, job, action: str, payload: dict | None = None) -> dict:
        return self.runner_gateway.send(job, action, payload)

    def _open_human_checkpoint(self, job, account, *, package: str, checkpoint_type: str) -> None:
        steps = (
            ("PREPARE_TEXT", {"text": f"@{account['username']}"}),
            ("OPEN_PACKAGE", {"package": package}),
            ("REPORT_WAITING_HUMAN", {"checkpoint": checkpoint_type}),
        )
        for action, payload in steps:
            if _pending(self._command(job, action, payload)):
                return

        if checkpoint_type == "IG_POSTCHECK":
            if account["stage"] not in {
                AccountStage.AVD_ASSIGNED.value,
                AccountStage.RUNNER_ASSIGNED.value,
            }:
                raise ValueError(f"cannot prepare Instagram from {account['stage']}")
            message = "Hoàn tất Instagram signup thủ công rồi bấm Continue để chạy post-check."
        else:
            if account["stage"] not in {
                AccountStage.IG_CREATED.value,
                AccountStage.THREADS_READY_FOR_HUMAN.value,
            }:
                raise ValueError(f"cannot prepare Threads from {account['stage']}")
            message = "Hoàn tất Threads profile thủ công rồi bấm Continue để chạy post-check."

        checkpoint_id = ulid()
        timestamp = now()
        with transaction(self.repo.conn):
            if checkpoint_type == "IG_POSTCHECK":
                self.service.transition_account(account["id"], AccountStage.IG_READY_FOR_HUMAN)
            elif account["stage"] == AccountStage.IG_CREATED.value:
                self.service.transition_account(account["id"], AccountStage.THREADS_READY_FOR_HUMAN)
            self.service.transition_account(account["id"], AccountStage.WAITING_HUMAN)

            self.repo.create_checkpoint({
                "id": checkpoint_id,
                "batch_id": account["batch_id"],
                "account_id": account["id"],
                "worker_id": job["worker_id"],
                "type": checkpoint_type,
                "status": "OPEN",
                "message": message,
                "created_at": timestamp,
            })
            self.repo.conn.execute(
                """UPDATE factory_job
                   SET state='WAITING_HUMAN', desired_action='WAITING_HUMAN', heartbeat_at=?
                   WHERE id=?""",
                (timestamp, job["id"]),
            )
            self.repo.conn.execute(
                "UPDATE factory_worker SET state='WAITING_HUMAN', last_progress_at=? WHERE id=?",
                (timestamp, job["worker_id"]),
            )

    def _checkpoint_for_account(self, account_id: str):
        return self.repo.conn.execute(
            """SELECT * FROM factory_checkpoint
               WHERE account_id=? AND status IN ('VERIFYING','OPEN','SNOOZED')
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            (account_id,),
        ).fetchone()

    def _activation_checkpoint(self, account_id: str):
        return self.repo.conn.execute(
            """SELECT * FROM factory_checkpoint
               WHERE account_id=? AND type='ACP_OAUTH' AND status != 'RESOLVED'
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            (account_id,),
        ).fetchone()

    def _ensure_activation_checkpoint(self, job, account) -> None:
        if self._activation_checkpoint(account["id"]) is not None:
            return
        self.repo.create_checkpoint({
            "id": ulid(),
            "batch_id": account["batch_id"],
            "account_id": account["id"],
            "worker_id": job["worker_id"],
            "type": "ACP_OAUTH",
            "status": "WAITING_EXTERNAL",
            "message": "Đang chờ xác nhận OAuth Threads chính thức để active ACP.",
            "created_at": now(),
        })

    def _resolve_activation_checkpoint(self, account_id: str, resolution: str) -> None:
        checkpoint = self._activation_checkpoint(account_id)
        if checkpoint is not None:
            self.repo.resolve_checkpoint(
                checkpoint["id"],
                resolved_at=now(),
                resolution=resolution,
            )

    def _release_preserving_account(self, job_id: str, final_state: str) -> None:
        with transaction(self.repo.conn):
            self.scheduler.release_job_in_transaction(job_id, final_state)

    def _start_activation(self, job, account) -> None:
        try:
            activation = self._activation()
            started = activation.start(account["id"])
        except RuntimeError:
            current = self.repo.get_account(account["id"])
            if current and current["stage"] in {
                AccountStage.THREADS_CREATED.value,
                AccountStage.ACP_CONNECTING.value,
            }:
                if current["stage"] == AccountStage.THREADS_CREATED.value:
                    self.service.transition_account(
                        account["id"],
                        AccountStage.RETRY_PENDING,
                        error_code="OAUTH_FAILED",
                        error_message="ACP activation is not configured",
                    )
            self._release_preserving_account(job["id"], "FAILED")
            return

        current = self.repo.get_account(account["id"])
        self._ensure_activation_checkpoint(job, current)
        opened = self._command(job, "OPEN_URL", {"url": started["authorization_url"]})
        if _pending(opened):
            return
        self.repo.conn.execute(
            """UPDATE factory_job
               SET state='WAITING_HUMAN', desired_action='WAIT_ACP', heartbeat_at=?, lease_expires_at=?
               WHERE id=?""",
            (now(), _lease_extension(), job["id"]),
        )
        self.repo.conn.execute(
            """UPDATE factory_worker
               SET state='WAITING_HUMAN', last_progress_at=?
               WHERE id=?""",
            (now(), job["worker_id"]),
        )

    def _reconcile_activation(self, job, account) -> None:
        self.repo.conn.execute(
            "UPDATE factory_job SET heartbeat_at=?, lease_expires_at=? WHERE id=?",
            (now(), _lease_extension(), job["id"]),
        )
        updated = self._activation().reconcile(account["id"])
        if updated["stage"] == AccountStage.ACP_CONNECTING.value:
            return
        if updated["stage"] == AccountStage.ACP_ACTIVE.value:
            self._resolve_activation_checkpoint(account["id"], "ACP_ACTIVE")
            self._release_preserving_account(job["id"], "COMPLETED")
            return
        if updated["stage"] == AccountStage.RETRY_PENDING.value:
            self._resolve_activation_checkpoint(account["id"], "OAUTH_FAILED")
            self._release_preserving_account(job["id"], "FAILED")
            return
        if updated["stage"] == AccountStage.ERROR.value:
            self._resolve_activation_checkpoint(account["id"], updated.get("last_error_code") or "ERROR")
            self._release_preserving_account(job["id"], "FAILED")

    def _verify_checkpoint(self, job, account) -> None:
        checkpoint = self._checkpoint_for_account(account["id"])
        if checkpoint is None:
            raise ValueError("verification requested without checkpoint")
        expected_package = {
            "IG_POSTCHECK": _INSTAGRAM_PACKAGE,
            "THREADS_POSTCHECK": _THREADS_PACKAGE,
        }.get(checkpoint["type"])
        if expected_package is None:
            raise ValueError(f"unsupported checkpoint type: {checkpoint['type']}")

        observation = self._command(job, "OBSERVE_FOREGROUND")
        if _pending(observation):
            return
        if observation.get("package") != expected_package:
            current = self.repo.get_account(account["id"])
            if current["stage"] == AccountStage.WAITING_HUMAN.value:
                self.service.transition_account(
                    account["id"],
                    AccountStage.NEEDS_CONFIRMATION,
                    error_code="POSTCHECK_FAILED",
                    error_message="Official app post-check did not match expected foreground package",
                )
            self.repo.conn.execute(
                """UPDATE factory_checkpoint
                   SET status='OPEN', message=?
                   WHERE id=?""",
                ("Post-check chưa xác nhận được trạng thái. Kiểm tra thủ công rồi thử Continue lại.", checkpoint["id"]),
            )
            self.repo.conn.execute(
                "UPDATE factory_job SET state='WAITING_HUMAN', desired_action='WAITING_HUMAN' WHERE id=?",
                (job["id"],),
            )
            self.repo.conn.execute(
                "UPDATE factory_worker SET state='WAITING_HUMAN' WHERE id=?",
                (job["worker_id"],),
            )
            return

        current = self.repo.get_account(account["id"])
        if current["stage"] == AccountStage.NEEDS_CONFIRMATION.value:
            self.service.transition_account(account["id"], AccountStage.WAITING_HUMAN)

        if checkpoint["type"] == "IG_POSTCHECK":
            self.service.transition_account(account["id"], AccountStage.IG_CREATED)
            self.repo.resolve_checkpoint(
                checkpoint["id"], resolved_at=now(), resolution="POSTCHECK_OK"
            )
            self.repo.conn.execute(
                """UPDATE factory_job
                   SET state='RUNNING', desired_action='PREPARE_THREADS', command_id=?, heartbeat_at=?
                   WHERE id=?""",
                (ulid(), now(), job["id"]),
            )
            self.repo.conn.execute(
                "UPDATE factory_worker SET state='RUNNING', last_progress_at=? WHERE id=?",
                (now(), job["worker_id"]),
            )
            return

        self.service.transition_account(account["id"], AccountStage.THREADS_CREATED)
        self.repo.resolve_checkpoint(
            checkpoint["id"], resolved_at=now(), resolution="POSTCHECK_OK"
        )
        self.repo.conn.execute(
            """UPDATE factory_job
               SET state='RUNNING', desired_action='START_ACP', command_id=?, heartbeat_at=?
               WHERE id=?""",
            (ulid(), now(), job["id"]),
        )
        self.repo.conn.execute(
            "UPDATE factory_worker SET state='RUNNING', last_progress_at=? WHERE id=?",
            (now(), job["worker_id"]),
        )
        refreshed_job = dict(self.repo.conn.execute(
            "SELECT * FROM factory_job WHERE id=?", (job["id"],)
        ).fetchone())
        self._start_activation(refreshed_job, self.repo.get_account(account["id"]))

    def _drive_job(self, job) -> None:
        account = self.repo.get_account(job["account_id"])
        if account is None:
            return
        action = str(job["desired_action"] or "").upper()
        if action == "PREPARE_INSTAGRAM":
            self._open_human_checkpoint(
                job, account, package=_INSTAGRAM_PACKAGE, checkpoint_type="IG_POSTCHECK"
            )
        elif action == "PREPARE_THREADS":
            self._open_human_checkpoint(
                job, account, package=_THREADS_PACKAGE, checkpoint_type="THREADS_POSTCHECK"
            )
        elif action in {"VERIFY_CHECKPOINT", "RETRY_CHECKPOINT"}:
            self._verify_checkpoint(job, account)
        elif action == "START_ACP":
            self._start_activation(job, account)
        elif action == "WAIT_ACP":
            self._reconcile_activation(job, account)

    def _drive_job_safely(self, job) -> None:
        try:
            self._drive_job(job)
        except Exception as exc:
            self.repo.conn.execute(
                "UPDATE factory_job SET state='RECOVERING' WHERE id=?",
                (job["id"],),
            )
            self.repo.conn.execute(
                """UPDATE factory_worker
                   SET state='RECOVERING', recovery_count=recovery_count+1,
                       last_error='runner command failed'
                   WHERE id=?""",
                (job["worker_id"],),
            )
            _LOG.warning("Factory runner command deferred (%s)", type(exc).__name__)

    def tick(self) -> None:
        self.supervisor.tick()
        self.scheduler.reconcile_expired_leases(now())

        active_jobs = self.repo.conn.execute(
            """SELECT * FROM factory_job
               WHERE state IN ('RUNNING','RECOVERING','WAITING_HUMAN')
                 AND desired_action IN (
                     'PREPARE_INSTAGRAM','PREPARE_THREADS','VERIFY_CHECKPOINT','RETRY_CHECKPOINT',
                     'START_ACP','WAIT_ACP'
                 )
               ORDER BY leased_at, id"""
        ).fetchall()
        for job in active_jobs:
            self._drive_job_safely(dict(job))

        ready_workers = self.repo.conn.execute(
            "SELECT id FROM factory_worker WHERE state='READY' AND draining=0 ORDER BY id"
        ).fetchall()
        for worker in ready_workers:
            job = self.scheduler.assign_next(worker["id"])
            if job is not None:
                self._drive_job_safely(job)

    def close(self) -> None:
        try:
            stop_all = getattr(self.worker_processes, "stop_all", None)
            if stop_all is not None:
                stop_all()
        finally:
            if self.owned_connection is not None:
                self.owned_connection.close()
                self.owned_connection = None

    def run_forever(self, *, interval_seconds: float = 2.0, stop_event=None) -> None:
        stop_event = stop_event or threading.Event()
        interval_seconds = max(0.2, float(interval_seconds))
        try:
            while not stop_event.is_set():
                try:
                    self.tick()
                except Exception as exc:
                    _LOG.warning("Factory controller tick failed (%s)", type(exc).__name__)
                stop_event.wait(interval_seconds)
        finally:
            self.close()


def build_default_runtime():
    """Construct the local controller runtime in the thread that will own SQLite."""
    from core.db import connect

    from .avd import AvdManager
    from .host_metrics import HostMetricsSampler
    from .repository import FactoryRepository
    from .scheduler import Scheduler
    from .schema import ensure_schema
    from .service import FactoryService
    from .supervisor import WorkerSupervisor
    from .worker_process import WorkerProcessManager

    conn = connect()
    ensure_schema(conn)
    repo = FactoryRepository(conn)
    service = FactoryService(repo)
    worker_processes = WorkerProcessManager()
    avd = AvdManager()
    metrics = HostMetricsSampler()
    scheduler = Scheduler(repo, service)
    supervisor = WorkerSupervisor(
        repo,
        avd,
        metrics,
        worker_processes=worker_processes,
    )
    supervisor.reconcile_on_boot()
    return FactoryControllerRuntime(
        repo,
        service,
        scheduler,
        supervisor,
        worker_processes,
        owned_connection=conn,
    )
