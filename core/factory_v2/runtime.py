"""Controller runtime that leases accounts and drives safe worker checkpoints.

The runtime never submits signup forms, credentials, OTP/CAPTCHA, identity
checks, or Threads publishing. Worker automation is limited to preparing text,
opening official apps, observing foreground package state, and waiting for the
operator at explicit checkpoints.
"""
from __future__ import annotations

import logging
import threading

from core.db import now, ulid

from .models import AccountStage
from .worker_protocol import WorkerCommand


_LOG = logging.getLogger(__name__)
_INSTAGRAM_PACKAGE = "com.instagram.android"
_THREADS_PACKAGE = "com.instagram.barcelona"


class FactoryControllerRuntime:
    def __init__(
        self,
        repository,
        service,
        scheduler,
        supervisor,
        worker_processes,
        *,
        owned_connection=None,
    ):
        self.repo = repository
        self.service = service
        self.scheduler = scheduler
        self.supervisor = supervisor
        self.worker_processes = worker_processes
        self.owned_connection = owned_connection

    def _command(self, job, action: str, payload: dict | None = None) -> dict:
        return self.worker_processes.request(
            job["worker_id"],
            WorkerCommand(
                command_id=ulid(),
                action=action,
                account_id=job["account_id"],
                payload={"job_id": job["id"], **(payload or {})},
            ),
        )

    def _open_human_checkpoint(self, job, account, *, package: str, checkpoint_type: str) -> None:
        self._command(job, "PREPARE_TEXT", {"text": f"@{account['username']}"})
        self._command(job, "OPEN_PACKAGE", {"package": package})
        self._command(job, "REPORT_WAITING_HUMAN", {"checkpoint": checkpoint_type})

        if checkpoint_type == "IG_POSTCHECK":
            if account["stage"] != AccountStage.AVD_ASSIGNED.value:
                raise ValueError(f"cannot prepare Instagram from {account['stage']}")
            self.service.transition_account(account["id"], AccountStage.IG_READY_FOR_HUMAN)
            self.service.transition_account(account["id"], AccountStage.WAITING_HUMAN)
            message = "Hoàn tất Instagram signup thủ công rồi bấm Continue để chạy post-check."
        else:
            if account["stage"] != AccountStage.IG_CREATED.value:
                raise ValueError(f"cannot prepare Threads from {account['stage']}")
            self.service.transition_account(account["id"], AccountStage.THREADS_READY_FOR_HUMAN)
            self.service.transition_account(account["id"], AccountStage.WAITING_HUMAN)
            message = "Hoàn tất Threads profile thủ công rồi bấm Continue để chạy post-check."

        checkpoint_id = ulid()
        self.repo.create_checkpoint({
            "id": checkpoint_id,
            "batch_id": account["batch_id"],
            "account_id": account["id"],
            "worker_id": job["worker_id"],
            "type": checkpoint_type,
            "status": "OPEN",
            "message": message,
            "created_at": now(),
        })
        self.repo.conn.execute(
            """UPDATE factory_job
               SET state='WAITING_HUMAN', desired_action='WAITING_HUMAN', heartbeat_at=?
               WHERE id=?""",
            (now(), job["id"]),
        )
        self.repo.conn.execute(
            "UPDATE factory_worker SET state='WAITING_HUMAN', last_progress_at=? WHERE id=?",
            (now(), job["worker_id"]),
        )

    def _checkpoint_for_account(self, account_id: str):
        return self.repo.conn.execute(
            """SELECT * FROM factory_checkpoint
               WHERE account_id=? AND status IN ('VERIFYING','OPEN','SNOOZED')
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            (account_id,),
        ).fetchone()

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
                   SET state='RUNNING', desired_action='PREPARE_THREADS', heartbeat_at=?
                   WHERE id=?""",
                (now(), job["id"]),
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
        self.scheduler.release_job(job["id"], "COMPLETED")

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
                       last_error='worker command failed'
                   WHERE id=?""",
                (job["worker_id"],),
            )
            _LOG.warning("Factory worker command deferred (%s)", type(exc).__name__)

    def tick(self) -> None:
        self.supervisor.tick()
        self.scheduler.reconcile_expired_leases(now())

        active_jobs = self.repo.conn.execute(
            """SELECT * FROM factory_job
               WHERE state IN ('RUNNING','RECOVERING')
                 AND desired_action IN ('PREPARE_INSTAGRAM','PREPARE_THREADS','VERIFY_CHECKPOINT','RETRY_CHECKPOINT')
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
