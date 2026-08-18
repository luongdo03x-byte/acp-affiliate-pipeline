"""Controller runtime for dual-runner Account Factory V2.

The controller remains authoritative for business stages. REMOTE_AVD workers may
perform only the fail-closed UI actions defined by ui_automation; LOCAL_DEVICE
keeps the explicit human-checkpoint flow.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import threading

from core.db import now, transaction, ulid

from .models import AccountStage, RunnerType
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

    def _runner_type(self, job) -> str:
        value = job.get("runner_type")
        if value:
            return str(value)
        worker = self.repo.get_worker(job["worker_id"])
        if worker is None:
            raise KeyError(job["worker_id"])
        return worker.get("runner_type") or RunnerType.REMOTE_AVD.value

    def _is_remote(self, job) -> bool:
        return self._runner_type(job) == RunnerType.REMOTE_AVD.value

    def _completion_mode(self, account) -> str:
        batch = self.repo.get_batch(account["batch_id"])
        if batch is None:
            return "ACP_ACTIVE"
        return str(batch.get("completion_mode") or "ACP_ACTIVE").strip().upper()

    def _complete_social_only_job(self, job, account) -> None:
        timestamp = now()
        self.repo.conn.execute(
            """UPDATE factory_account
               SET completed_at=COALESCE(completed_at, ?), updated_at=?
               WHERE id=?""",
            (timestamp, timestamp, account["id"]),
        )
        self.scheduler.release_job_in_transaction(job["id"], "COMPLETED")

    @staticmethod
    def _profile_payload(account) -> dict:
        profile = {
            "username": str(account.get("username") or ""),
            "display_name": str(account.get("display_name") or ""),
            "bio": str(account.get("bio") or ""),
        }
        contact_type = str(account.get("signup_contact_type") or "").strip().lower()
        if contact_type in {"phone", "email"}:
            selected = account.get(contact_type)
            if selected:
                profile["signup_contact_type"] = contact_type
                profile["signup_contact"] = str(selected)
        if account.get("birth_date"):
            profile["birth_date"] = str(account["birth_date"])
        if account.get("avatar_file"):
            profile["avatar_file"] = str(account["avatar_file"])
        return {"profile": profile}

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

    def _remote_flow_for_checkpoint(self, checkpoint) -> str:
        if checkpoint["type"] == "IG_POSTCHECK":
            return "instagram"
        if checkpoint["type"] == "THREADS_POSTCHECK":
            return "threads"
        raise ValueError(f"unsupported checkpoint type: {checkpoint['type']}")

    def _set_remote_running(self, job, desired_action: str) -> None:
        timestamp = now()
        self.repo.conn.execute(
            """UPDATE factory_job
               SET state='RUNNING', desired_action=?, command_id=?, heartbeat_at=?, lease_expires_at=?
               WHERE id=?""",
            (desired_action, ulid(), timestamp, _lease_extension(), job["id"]),
        )
        self.repo.conn.execute(
            "UPDATE factory_worker SET state='RUNNING', last_progress_at=? WHERE id=?",
            (timestamp, job["worker_id"]),
        )

    def _set_remote_waiting(self, job) -> None:
        timestamp = now()
        self.repo.conn.execute(
            """UPDATE factory_job
               SET state='WAITING_HUMAN', desired_action='OBSERVE_CHECKPOINT', heartbeat_at=?, lease_expires_at=?
               WHERE id=?""",
            (timestamp, _lease_extension(), job["id"]),
        )
        self.repo.conn.execute(
            "UPDATE factory_worker SET state='WAITING_HUMAN', last_progress_at=? WHERE id=?",
            (timestamp, job["worker_id"]),
        )

    def _refresh_remote_waiting(self, job) -> None:
        self._set_remote_waiting(job)

    def _ensure_remote_checkpoint(
        self,
        job,
        account,
        *,
        flow: str,
        screen: str,
        confirmation: bool,
    ) -> None:
        checkpoint_type = "IG_POSTCHECK" if flow == "instagram" else "THREADS_POSTCHECK"
        checkpoint = self._checkpoint_for_account(account["id"])
        if checkpoint is not None and checkpoint["type"] != checkpoint_type:
            raise ValueError("active checkpoint does not match AVD flow")
        if confirmation:
            message = (
                f"AVD chưa nhận diện chắc chắn UI ({screen}). Kiểm tra thủ công; "
                "hệ thống chỉ tự tiếp tục khi thấy màn hình hợp lệ."
            )
        else:
            message = (
                f"AVD đang chờ thao tác thủ công tại {screen}. "
                "Hệ thống sẽ tự tiếp tục khi nhận diện màn hình hợp lệ."
            )

        with transaction(self.repo.conn):
            current = self.repo.get_account(account["id"])
            if flow == "instagram":
                if current["stage"] in {
                    AccountStage.AVD_ASSIGNED.value,
                    AccountStage.RUNNER_ASSIGNED.value,
                }:
                    self.service.transition_account(account["id"], AccountStage.IG_READY_FOR_HUMAN)
                    current = self.repo.get_account(account["id"])
                if current["stage"] == AccountStage.IG_READY_FOR_HUMAN.value:
                    self.service.transition_account(account["id"], AccountStage.WAITING_HUMAN)
                    current = self.repo.get_account(account["id"])
            else:
                if current["stage"] == AccountStage.IG_CREATED.value:
                    self.service.transition_account(account["id"], AccountStage.THREADS_READY_FOR_HUMAN)
                    current = self.repo.get_account(account["id"])
                if current["stage"] == AccountStage.THREADS_READY_FOR_HUMAN.value:
                    self.service.transition_account(account["id"], AccountStage.WAITING_HUMAN)
                    current = self.repo.get_account(account["id"])

            if confirmation and current["stage"] == AccountStage.WAITING_HUMAN.value:
                self.service.transition_account(
                    account["id"],
                    AccountStage.NEEDS_CONFIRMATION,
                    error_code="UI_CHANGED",
                    error_message=f"Unrecognized {flow} UI: {screen}",
                )

            if checkpoint is None:
                self.repo.create_checkpoint({
                    "id": ulid(),
                    "batch_id": account["batch_id"],
                    "account_id": account["id"],
                    "worker_id": job["worker_id"],
                    "type": checkpoint_type,
                    "status": "OPEN",
                    "message": message,
                    "created_at": now(),
                })
            else:
                self.repo.conn.execute(
                    "UPDATE factory_checkpoint SET status='OPEN', message=? WHERE id=?",
                    (message, checkpoint["id"]),
                )
            self._set_remote_waiting(job)

    def _resolve_remote_checkpoint(self, account_id: str, resolution: str) -> None:
        checkpoint = self._checkpoint_for_account(account_id)
        if checkpoint is not None:
            self.repo.resolve_checkpoint(
                checkpoint["id"], resolved_at=now(), resolution=resolution
            )

    def _complete_remote_flow(self, job, account, *, flow: str) -> None:
        with transaction(self.repo.conn):
            current = self.repo.get_account(account["id"])
            if current["stage"] == AccountStage.NEEDS_CONFIRMATION.value:
                self.service.transition_account(account["id"], AccountStage.WAITING_HUMAN)
                current = self.repo.get_account(account["id"])

            if flow == "instagram":
                if current["stage"] in {
                    AccountStage.AVD_ASSIGNED.value,
                    AccountStage.RUNNER_ASSIGNED.value,
                }:
                    self.service.transition_account(account["id"], AccountStage.IG_READY_FOR_HUMAN)
                    current = self.repo.get_account(account["id"])
                if current["stage"] in {
                    AccountStage.IG_READY_FOR_HUMAN.value,
                    AccountStage.WAITING_HUMAN.value,
                }:
                    self.service.transition_account(account["id"], AccountStage.IG_CREATED)
                elif current["stage"] != AccountStage.IG_CREATED.value:
                    raise ValueError(f"cannot complete Instagram from {current['stage']}")
                self._resolve_remote_checkpoint(account["id"], "POSTCHECK_OK")
                self._set_remote_running(job, "PREPARE_THREADS")
                return

            if current["stage"] == AccountStage.IG_CREATED.value:
                self.service.transition_account(account["id"], AccountStage.THREADS_READY_FOR_HUMAN)
                current = self.repo.get_account(account["id"])
            if current["stage"] in {
                AccountStage.THREADS_READY_FOR_HUMAN.value,
                AccountStage.WAITING_HUMAN.value,
            }:
                self.service.transition_account(account["id"], AccountStage.THREADS_CREATED)
            elif current["stage"] != AccountStage.THREADS_CREATED.value:
                raise ValueError(f"cannot complete Threads from {current['stage']}")
            self._resolve_remote_checkpoint(account["id"], "POSTCHECK_OK")

            refreshed = self.repo.get_account(account["id"])
            if self._completion_mode(refreshed) == "SOCIAL_ONLY":
                self._complete_social_only_job(job, refreshed)
                return

            self._set_remote_running(job, "START_ACP")

        refreshed_row = self.repo.conn.execute(
            "SELECT * FROM factory_job WHERE id=?", (job["id"],)
        ).fetchone()
        refreshed_job = dict(refreshed_row) if refreshed_row is not None else dict(job)
        self._start_activation(refreshed_job, self.repo.get_account(account["id"]))

    def _transition_remote_terminal(
        self,
        job,
        account,
        *,
        stage: AccountStage,
        error_code: str,
        message: str,
    ) -> None:
        with transaction(self.repo.conn):
            self.service.transition_account(
                account["id"], stage, error_code=error_code, error_message=message
            )
            self.scheduler.release_job_in_transaction(job["id"], "FAILED")

    def _handle_remote_result(self, job, account, *, flow: str, response: dict) -> None:
        status = str(response.get("status") or "needs_confirmation").lower()
        detail = response.get("result") if isinstance(response.get("result"), dict) else {}
        screen = str(detail.get("screen") or "UNKNOWN")[:120]
        reason = str(detail.get("reason") or screen)[:120]

        if status == "running":
            self._set_remote_running(
                job,
                "AUTOMATE_INSTAGRAM" if flow == "instagram" else "AUTOMATE_THREADS",
            )
            return
        if status == "waiting_human":
            self._ensure_remote_checkpoint(
                job, account, flow=flow, screen=screen, confirmation=False
            )
            return
        if status == "needs_confirmation":
            self._ensure_remote_checkpoint(
                job, account, flow=flow, screen=screen, confirmation=True
            )
            return
        if status == "completed":
            self._complete_remote_flow(job, account, flow=flow)
            return
        if status == "retry_pending":
            if "RATE_LIMITED" in {reason, screen}:
                code = "RATE_LIMITED"
            elif "ACTION_BLOCKED" in {reason, screen}:
                code = "ACTION_BLOCKED"
            elif "NETWORK_ERROR" in {reason, screen}:
                code = "NETWORK_TRANSIENT"
            else:
                code = "UI_CHANGED"
            self._transition_remote_terminal(
                job,
                account,
                stage=AccountStage.RETRY_PENDING,
                error_code=code,
                message=reason,
            )
            return
        if status == "error":
            code = (
                "ACCOUNT_DISABLED"
                if "ACCOUNT_DISABLED" in {reason, screen}
                else "UI_CHANGED"
            )
            self._transition_remote_terminal(
                job,
                account,
                stage=AccountStage.ERROR,
                error_code=code,
                message=reason,
            )
            return

        self._ensure_remote_checkpoint(
            job, account, flow=flow, screen=screen, confirmation=True
        )

    def _drive_remote_instagram(self, job, account, *, prepare: bool) -> None:
        if prepare:
            prepared = self._command(job, "PREPARE_INSTAGRAM")
            if _pending(prepared):
                return
            if str(prepared.get("status") or "completed").lower() != "completed":
                self._handle_remote_result(
                    job, account, flow="instagram", response=prepared
                )
                return
            self._set_remote_running(job, "AUTOMATE_INSTAGRAM")

        result = self._command(
            job, "AUTOMATE_INSTAGRAM", self._profile_payload(account)
        )
        if _pending(result):
            return
        self._handle_remote_result(job, account, flow="instagram", response=result)

    def _drive_remote_threads(self, job, account) -> None:
        result = self._command(job, "AUTOMATE_THREADS", self._profile_payload(account))
        if _pending(result):
            return
        self._handle_remote_result(job, account, flow="threads", response=result)

    def _observe_remote_checkpoint(self, job, account) -> None:
        checkpoint = self._checkpoint_for_account(account["id"])
        if checkpoint is None:
            raise ValueError("observation requested without checkpoint")
        flow = self._remote_flow_for_checkpoint(checkpoint)
        result = self._command(job, "OBSERVE_CHECKPOINT", {"flow": flow})
        if _pending(result):
            return
        observed_status = str(result.get("status") or "").lower()
        if observed_status in {"waiting_human", "running"}:
            self._refresh_remote_waiting(job)
            return
        self._handle_remote_result(job, account, flow=flow, response=result)

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
            self._resolve_activation_checkpoint(
                account["id"], updated.get("last_error_code") or "ERROR"
            )
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
        refreshed_account = self.repo.get_account(account["id"])
        if self._completion_mode(refreshed_account) == "SOCIAL_ONLY":
            with transaction(self.repo.conn):
                self._complete_social_only_job(job, refreshed_account)
            return

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
        self._start_activation(refreshed_job, refreshed_account)

    def _drive_job(self, job) -> None:
        account = self.repo.get_account(job["account_id"])
        if account is None:
            return
        action = str(job["desired_action"] or "").upper()
        remote = self._is_remote(job)

        if action == "PREPARE_INSTAGRAM":
            if remote:
                self._drive_remote_instagram(job, account, prepare=True)
            else:
                self._open_human_checkpoint(
                    job, account, package=_INSTAGRAM_PACKAGE, checkpoint_type="IG_POSTCHECK"
                )
        elif action == "AUTOMATE_INSTAGRAM" and remote:
            self._drive_remote_instagram(job, account, prepare=False)
        elif action == "PREPARE_THREADS":
            if remote:
                self._drive_remote_threads(job, account)
            else:
                self._open_human_checkpoint(
                    job, account, package=_THREADS_PACKAGE, checkpoint_type="THREADS_POSTCHECK"
                )
        elif action == "AUTOMATE_THREADS" and remote:
            self._drive_remote_threads(job, account)
        elif action == "OBSERVE_CHECKPOINT" and remote:
            self._observe_remote_checkpoint(job, account)
        elif action in {"VERIFY_CHECKPOINT", "RETRY_CHECKPOINT"}:
            if remote:
                self._observe_remote_checkpoint(job, account)
            else:
                self._verify_checkpoint(job, account)
        elif action == "START_ACP":
            if self._completion_mode(account) == "SOCIAL_ONLY":
                if account["stage"] == AccountStage.THREADS_CREATED.value:
                    with transaction(self.repo.conn):
                        self._complete_social_only_job(job, account)
                else:
                    raise ValueError("SOCIAL_ONLY activation requested before Threads completion")
            else:
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
                     'PREPARE_INSTAGRAM','AUTOMATE_INSTAGRAM',
                     'PREPARE_THREADS','AUTOMATE_THREADS','OBSERVE_CHECKPOINT',
                     'VERIFY_CHECKPOINT','RETRY_CHECKPOINT','START_ACP','WAIT_ACP'
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