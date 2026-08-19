"""Controller services for Account Factory V2."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import re

from core.db import now, transaction, ulid

from .identity import generate_profiles
from .models import AccountStage, BatchStatus, RunnerType, WorkerState
from .state_machine import require_transition, safe_stage_after_transition

_ALLOWED_ERROR_CODES = frozenset({
    "ACCOUNT_DISABLED",
    "ACCOUNT_MISMATCH",
    "ACTION_BLOCKED",
    "ADB_DISCONNECTED",
    "AVD_BOOT_FAILED",
    "NETWORK_TRANSIENT",
    "OAUTH_FAILED",
    "POSTCHECK_FAILED",
    "RATE_LIMITED",
    "UI_CHANGED",
    "USERNAME_UNAVAILABLE",
    "WORKER_TIMEOUT",
})
_ALLOWED_COMPLETION_MODES = frozenset({"ACP_ACTIVE", "SOCIAL_ONLY"})
_ALLOWED_SIGNUP_CONTACT_TYPES = frozenset({"phone", "email"})
_WORKER_USERNAME_RE = re.compile(r"^[a-z0-9._]{1,30}$")
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _clean_error(error_code: str | None, error_message: str | None) -> tuple[str | None, str | None]:
    if error_code is None:
        return None, None if error_message is None else str(error_message)[:500]
    code = str(error_code).strip().upper()
    if code not in _ALLOWED_ERROR_CODES:
        raise ValueError(f"unsupported factory error code: {code}")
    message = None if error_message is None else " ".join(str(error_message).split())[:500]
    return code, message


def _future_iso(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _clean_runner_text(value: str, field: str, *, max_length: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > max_length:
        raise ValueError(f"{field} is too long")
    return text


def _clean_optional_signup_text(value, field: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        raise ValueError(f"{field} is too long")
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise ValueError(f"{field} contains control characters")
    return text


def _validate_completion_mode(value: str | None) -> str:
    mode = str(value or "ACP_ACTIVE").strip().upper()
    if mode not in _ALLOWED_COMPLETION_MODES:
        raise ValueError("completion_mode must be ACP_ACTIVE or SOCIAL_ONLY")
    return mode


def _validate_birth_date(value) -> str | None:
    text = _clean_optional_signup_text(value, "birth_date", max_length=10)
    if text is None:
        return None
    try:
        born = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("birth_date must be YYYY-MM-DD") from exc
    today = date.today()
    if born > today:
        raise ValueError("birth_date cannot be in the future")
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    if age < 18:
        raise ValueError("birth_date must be at least 18 years ago")
    return born.isoformat()


def _validate_avatar_file(value) -> str | None:
    text = _clean_optional_signup_text(value, "avatar_file", max_length=300)
    if text is None:
        return None
    relative = Path(text)
    if relative.is_absolute():
        raise ValueError("avatar_file must be a repository-relative path")
    resolved = (_REPO_ROOT / relative).resolve()
    try:
        resolved.relative_to(_REPO_ROOT)
    except ValueError as exc:
        raise ValueError("avatar_file must stay inside the repository") from exc
    return relative.as_posix()


class FactoryService:
    def __init__(self, repository):
        self.repo = repository

    def create_batch(
        self,
        name: str,
        count: int = 50,
        seed: int | None = None,
        *,
        execution_target: str | None = None,
        completion_mode: str = "ACP_ACTIVE",
    ) -> dict:
        if count <= 0:
            raise ValueError("count must be positive")
        completion_mode = _validate_completion_mode(completion_mode)
        batch_id = ulid()
        created_at = now()
        profiles = generate_profiles(count, seed=seed)
        batch_row = {
            "id": batch_id,
            "name": str(name).strip() or "Account Factory Batch",
            "target_count": count,
            "status": BatchStatus.READY.value,
            "created_at": created_at,
            "reminder_interval_minutes": 10,
            "completion_mode": completion_mode,
        }
        account_rows = []
        for sequence, profile in enumerate(profiles, start=1):
            account_rows.append({
                "id": ulid(),
                "batch_id": batch_id,
                "sequence": sequence,
                "group_no": ((sequence - 1) // 5) + 1,
                "username": profile.username,
                "display_name": profile.display_name,
                "bio": profile.bio,
                "gender_profile": profile.gender_profile,
                "primary_niche": profile.primary_niche,
                "secondary_interest": profile.secondary_interest,
                "personality_style": profile.personality_style,
                "content_tone": profile.content_tone,
                "avatar_type": profile.avatar_type,
                "avatar_theme": profile.avatar_theme,
                "avatar_prompt": profile.avatar_prompt,
                "stage": AccountStage.PROFILE_READY.value,
                "last_safe_stage": AccountStage.PROFILE_READY.value,
                "execution_target": execution_target,
                "created_at": created_at,
                "updated_at": created_at,
            })
        with transaction(self.repo.conn):
            self.repo.create_batch(batch_row)
            self.repo.insert_accounts(account_rows)
        return self.repo.get_batch(batch_id)

    def _validate_execution_target(self, execution_target: str) -> str:
        target = _clean_runner_text(execution_target, "execution_target", max_length=180)
        if target == "AUTO_AVD":
            return target
        if target in {"AUTO", "THIS_PHONE"} or target.startswith("AUTO_AVD:"):
            raise ValueError("unsupported execution_target")

        worker = self.repo.get_worker(target)
        if worker is None:
            raise KeyError(target)
        if worker.get("runner_type") not in {
            RunnerType.LOCAL_DEVICE.value,
            RunnerType.REMOTE_AVD.value,
        }:
            raise ValueError("unsupported runner type")
        if worker.get("state") != WorkerState.READY.value or worker.get("draining"):
            raise ValueError("selected runner is not ready")
        return worker["id"]

    def create_single_account(
        self,
        *,
        execution_target: str,
        batch_name: str = "Phone/AVD Pilot",
        completion_mode: str = "ACP_ACTIVE",
        signup_contact_type: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        birth_date: str | None = None,
        avatar_file: str | None = None,
    ) -> dict:
        target = self._validate_execution_target(execution_target)
        name = " ".join(str(batch_name or "").split()) or "Phone/AVD Pilot"
        if len(name) > 120:
            raise ValueError("batch_name is too long")

        mode = _validate_completion_mode(completion_mode)
        contact_type = None
        if signup_contact_type is not None:
            contact_type = str(signup_contact_type).strip().lower()
            if contact_type not in _ALLOWED_SIGNUP_CONTACT_TYPES:
                raise ValueError("signup_contact_type must be phone or email")

        clean_phone = _clean_optional_signup_text(phone, "phone", max_length=64)
        clean_email = _clean_optional_signup_text(email, "email", max_length=320)
        if contact_type == "phone" and clean_phone is None:
            raise ValueError("phone is required when signup_contact_type=phone")
        if contact_type == "email" and clean_email is None:
            raise ValueError("email is required when signup_contact_type=email")

        clean_birth_date = _validate_birth_date(birth_date)
        clean_avatar_file = _validate_avatar_file(avatar_file)

        batch = self.create_batch(
            name,
            count=1,
            execution_target=target,
            completion_mode=mode,
        )
        account = self.repo.list_accounts(batch["id"])[0]
        self.repo.conn.execute(
            """UPDATE factory_account
               SET signup_contact_type=?, phone=?, email=?, birth_date=?, avatar_file=?, updated_at=?
               WHERE id=?""",
            (
                contact_type,
                clean_phone,
                clean_email,
                clean_birth_date,
                clean_avatar_file,
                now(),
                account["id"],
            ),
        )
        account = self.repo.get_account(account["id"])
        return {"batch": batch, "account": account}

    def register_local_runner(self, device_id: str, device_name: str) -> dict:
        device_id = _clean_runner_text(device_id, "device_id", max_length=160)
        device_name = _clean_runner_text(device_name, "device_name", max_length=160)
        timestamp = now()
        existing = self.repo.get_worker_by_device_id(device_id)
        if existing is not None:
            if existing.get("runner_type") != RunnerType.LOCAL_DEVICE.value:
                raise ValueError("device_id belongs to a non-local runner")
            updates = {
                "device_name": device_name,
                "last_heartbeat_at": timestamp,
            }
            if not existing.get("current_job_id") and existing.get("state") in {
                WorkerState.STOPPED.value,
                WorkerState.ERROR.value,
            }:
                updates.update(state=WorkerState.READY.value, draining=0, last_error=None)
            return self.repo.update_worker_fields(existing["id"], **updates)

        return self.repo.insert_worker({
            "id": ulid(),
            "runner_type": RunnerType.LOCAL_DEVICE.value,
            "device_id": device_id,
            "device_name": device_name,
            "state": WorkerState.READY.value,
            "started_at": timestamp,
            "last_heartbeat_at": timestamp,
        })

    def heartbeat_runner(
        self,
        worker_id: str,
        *,
        current_account_id: str | None,
        current_job_id: str | None,
    ) -> dict:
        worker = self.repo.get_worker(worker_id)
        if worker is None:
            raise KeyError(worker_id)
        if worker.get("runner_type") != RunnerType.LOCAL_DEVICE.value:
            raise ValueError("phone heartbeat is only valid for LOCAL_DEVICE runners")

        expected_account = worker.get("current_account_id")
        expected_job = worker.get("current_job_id")
        if current_account_id != expected_account or current_job_id != expected_job:
            raise ValueError("runner assignment does not match controller lease")

        return self.repo.update_worker_fields(
            worker_id,
            last_heartbeat_at=now(),
        )

    def update_worker_selected_username(
        self,
        account_id: str,
        *,
        job_id: str,
        worker_id: str,
        username: str,
    ) -> dict:
        account = self.repo.get_account(account_id)
        if account is None:
            raise KeyError(account_id)
        if (
            account.get("current_job_id") != job_id
            or account.get("assigned_worker_id") != worker_id
        ):
            raise ValueError("worker profile update binding mismatch")
        value = str(username or "").strip()
        if _WORKER_USERNAME_RE.fullmatch(value) is None:
            raise ValueError("invalid worker-selected username")
        self.repo.conn.execute(
            "UPDATE factory_account SET username=?, updated_at=? WHERE id=?",
            (value, now(), account_id),
        )
        return self.repo.get_account(account_id)

    def transition_account(
        self,
        account_id: str,
        to_stage: AccountStage,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict:
        account = self.repo.get_account(account_id)
        if account is None:
            raise KeyError(account_id)
        current = AccountStage(account["stage"])
        target = AccountStage(to_stage)
        require_transition(current, target)
        previous_safe = AccountStage(account["last_safe_stage"])
        next_safe = safe_stage_after_transition(previous_safe, target)
        code, message = _clean_error(error_code, error_message)
        completed_at = now() if target == AccountStage.ACP_ACTIVE else None
        return self.repo.update_account_stage(
            account_id,
            stage=target.value,
            last_safe_stage=next_safe.value,
            updated_at=now(),
            error_code=code,
            error_message=message,
            completed_at=completed_at,
        )

    def mark_postcheck_result(
        self,
        account_id: str,
        *,
        passed: bool,
        success_stage: AccountStage,
        failure_message: str,
    ) -> dict:
        if passed:
            return self.transition_account(account_id, success_stage)
        return self.transition_account(
            account_id,
            AccountStage.NEEDS_CONFIRMATION,
            error_code="POSTCHECK_FAILED",
            error_message=failure_message,
        )

    def pause_batch(self, batch_id: str) -> dict:
        batch = self.repo.get_batch(batch_id)
        if batch is None:
            raise KeyError(batch_id)
        if batch["status"] not in {BatchStatus.READY.value, BatchStatus.RUNNING.value}:
            raise ValueError(f"batch cannot pause from {batch['status']}")
        paused_at = now()
        self.repo.conn.execute(
            "UPDATE factory_batch SET status='PAUSED', paused_at=? WHERE id=?",
            (paused_at, batch_id),
        )
        return self.repo.get_batch(batch_id)

    def resume_batch(self, batch_id: str) -> dict:
        batch = self.repo.get_batch(batch_id)
        if batch is None:
            raise KeyError(batch_id)
        if batch["status"] != BatchStatus.PAUSED.value:
            raise ValueError(f"batch cannot resume from {batch['status']}")
        started_at = batch["started_at"] or now()
        self.repo.conn.execute(
            "UPDATE factory_batch SET status='RUNNING', paused_at=NULL, started_at=? WHERE id=?",
            (started_at, batch_id),
        )
        return self.repo.get_batch(batch_id)

    def request_checkpoint_verification(self, checkpoint_id: str, *, action: str = "VERIFY_CHECKPOINT") -> dict:
        checkpoint = self.repo.get_checkpoint(checkpoint_id)
        if checkpoint is None:
            raise KeyError(checkpoint_id)
        if checkpoint["status"] not in {"OPEN", "SNOOZED", "VERIFYING"}:
            raise ValueError(f"checkpoint cannot continue from {checkpoint['status']}")
        account = self.repo.get_account(checkpoint["account_id"])
        if account is None:
            raise KeyError(checkpoint["account_id"])
        job = self.repo.get_active_job_for_account(account["id"])
        if job is None:
            if action == "RETRY_CHECKPOINT":
                return self.retry_checkpoint(checkpoint_id)
            raise ValueError("checkpoint has no active worker job")

        command_id = ulid()
        with transaction(self.repo.conn):
            self.repo.conn.execute(
                """UPDATE factory_checkpoint
                   SET status='VERIFYING', snoozed_until=NULL, next_reminder_at=NULL
                   WHERE id=?""",
                (checkpoint_id,),
            )
            self.repo.conn.execute(
                """UPDATE factory_job
                   SET state='RUNNING', desired_action=?, command_id=?, heartbeat_at=?
                   WHERE id=?""",
                (action, command_id, now(), job["id"]),
            )
            if checkpoint["worker_id"]:
                self.repo.conn.execute(
                    "UPDATE factory_worker SET state='RUNNING', last_progress_at=? WHERE id=?",
                    (now(), checkpoint["worker_id"]),
                )
        return {"command_id": command_id, "status": "VERIFYING"}

    def snooze_checkpoint(self, checkpoint_id: str, minutes: int) -> dict:
        minutes = int(minutes)
        if minutes not in {10, 30, 60}:
            raise ValueError("snooze minutes must be one of 10, 30, 60")
        checkpoint = self.repo.get_checkpoint(checkpoint_id)
        if checkpoint is None:
            raise KeyError(checkpoint_id)
        if checkpoint["status"] not in {"OPEN", "SNOOZED"}:
            raise ValueError(f"checkpoint cannot snooze from {checkpoint['status']}")
        until = _future_iso(minutes)
        self.repo.conn.execute(
            """UPDATE factory_checkpoint
               SET status='SNOOZED', snoozed_until=?, next_reminder_at=?
               WHERE id=?""",
            (until, until, checkpoint_id),
        )
        return self.repo.get_checkpoint(checkpoint_id)

    def _resolve_actionable_checkpoints_for_retry(self, account_id: str, *, resolved_at: str) -> None:
        checkpoints = self.repo.conn.execute(
            """SELECT id FROM factory_checkpoint
               WHERE account_id=? AND status IN ('OPEN','SNOOZED','VERIFYING')
               ORDER BY created_at, id""",
            (account_id,),
        ).fetchall()
        for checkpoint in checkpoints:
            self.repo.resolve_checkpoint(
                checkpoint["id"],
                resolved_at=resolved_at,
                resolution="RETRY_REQUESTED",
            )

    def retry_checkpoint(self, checkpoint_id: str) -> dict:
        checkpoint = self.repo.get_checkpoint(checkpoint_id)
        if checkpoint is None:
            raise KeyError(checkpoint_id)
        if checkpoint["status"] not in {"OPEN", "SNOOZED", "VERIFYING"}:
            raise ValueError(f"checkpoint cannot retry from {checkpoint['status']}")
        account = self.repo.get_account(checkpoint["account_id"])
        if account is None:
            raise KeyError(checkpoint["account_id"])

        job = self.repo.get_active_job_for_account(account["id"])
        if job is not None:
            return self.request_checkpoint_verification(
                checkpoint_id,
                action="RETRY_CHECKPOINT",
            )

        command_id = ulid()
        retried_at = now()
        with transaction(self.repo.conn):
            current = self.repo.get_account(account["id"])
            if current["stage"] != AccountStage.RETRY_PENDING.value:
                self.transition_account(account["id"], AccountStage.RETRY_PENDING)
            self.repo.resolve_checkpoint(
                checkpoint_id,
                resolved_at=retried_at,
                resolution="RETRY_REQUESTED",
            )
        return {"command_id": command_id, "status": AccountStage.RETRY_PENDING.value}

    def retry_account(self, account_id: str) -> dict:
        account = self.repo.get_account(account_id)
        if account is None:
            raise KeyError(account_id)
        if account["stage"] == AccountStage.RETRY_PENDING.value:
            if (
                account["last_safe_stage"] == AccountStage.THREADS_CREATED.value
                and account["last_error_code"] == "OAUTH_FAILED"
            ):
                self.repo.conn.execute(
                    """UPDATE factory_account
                       SET last_error_code=NULL, last_error_message=NULL,
                           retry_count=retry_count+1, updated_at=?
                       WHERE id=?""",
                    (now(), account_id),
                )
                return self.repo.get_account(account_id)
            retried_at = now()
            with transaction(self.repo.conn):
                self._resolve_actionable_checkpoints_for_retry(
                    account_id,
                    resolved_at=retried_at,
                )
            return self.repo.get_account(account_id)

        retried_at = now()
        with transaction(self.repo.conn):
            self.transition_account(account_id, AccountStage.RETRY_PENDING)
            self._resolve_actionable_checkpoints_for_retry(
                account_id,
                resolved_at=retried_at,
            )
        return self.repo.get_account(account_id)

    def stop_account(self, account_id: str) -> dict:
        account = self.repo.get_account(account_id)
        if account is None:
            raise KeyError(account_id)
        if account["stage"] == AccountStage.DISABLED.value:
            raise ValueError("account is already disabled")
        stopped_at = now()
        with transaction(self.repo.conn):
            self.repo.conn.execute(
                """UPDATE factory_account
                   SET stage='DISABLED', assigned_worker_id=NULL, current_job_id=NULL, updated_at=?
                   WHERE id=?""",
                (stopped_at, account_id),
            )
            if account["current_job_id"]:
                self.repo.conn.execute(
                    "UPDATE factory_job SET state='CANCELLED', finished_at=? WHERE id=?",
                    (stopped_at, account["current_job_id"]),
                )
            if account["assigned_worker_id"]:
                self.repo.conn.execute(
                    """UPDATE factory_worker
                       SET state='READY', current_account_id=NULL, current_job_id=NULL
                       WHERE id=?""",
                    (account["assigned_worker_id"],),
                )
        return self.repo.get_account(account_id)

    def request_worker_drain(self, worker_id: str) -> dict:
        worker = self.repo.get_worker(worker_id)
        if worker is None:
            raise KeyError(worker_id)
        if worker["state"] == "WAITING_HUMAN":
            raise ValueError("cannot drain worker during human checkpoint")
        if worker["state"] in {"STOPPED", "ERROR"}:
            raise ValueError(f"worker cannot drain from {worker['state']}")
        if worker["current_job_id"]:
            self.repo.conn.execute(
                "UPDATE factory_worker SET draining=1 WHERE id=?",
                (worker_id,),
            )
        else:
            self.repo.conn.execute(
                "UPDATE factory_worker SET state='DRAINING', draining=1 WHERE id=?",
                (worker_id,),
            )
        return self.repo.get_worker(worker_id)

    def request_worker_restart(self, worker_id: str) -> dict:
        worker = self.repo.get_worker(worker_id)
        if worker is None:
            raise KeyError(worker_id)
        if worker["state"] == "WAITING_HUMAN":
            raise ValueError("cannot restart worker during human checkpoint")
        if worker["current_job_id"]:
            raise ValueError("cannot restart worker with an active job")
        if worker["state"] == "STOPPED":
            raise ValueError("cannot restart a stopped worker; start it through pool scaling")
        self.repo.conn.execute(
            """UPDATE factory_worker
               SET state='RECOVERING', draining=0,
                   recovery_count=recovery_count+1,
                   last_error='manual restart requested'
               WHERE id=?""",
            (worker_id,),
        )
        return self.repo.get_worker(worker_id)
