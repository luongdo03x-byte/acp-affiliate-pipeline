"""Controller services for Account Factory V2."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.db import now, transaction, ulid

from .identity import generate_profiles
from .models import AccountStage, BatchStatus
from .state_machine import require_transition, safe_stage_after_transition

_ALLOWED_ERROR_CODES = frozenset({
    "ACCOUNT_MISMATCH",
    "ADB_DISCONNECTED",
    "AVD_BOOT_FAILED",
    "NETWORK_TRANSIENT",
    "OAUTH_FAILED",
    "POSTCHECK_FAILED",
    "USERNAME_UNAVAILABLE",
    "WORKER_TIMEOUT",
})


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


class FactoryService:
    def __init__(self, repository):
        self.repo = repository

    def create_batch(self, name: str, count: int = 50, seed: int | None = None) -> dict:
        if count <= 0:
            raise ValueError("count must be positive")
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
                "created_at": created_at,
                "updated_at": created_at,
            })
        with transaction(self.repo.conn):
            self.repo.create_batch(batch_row)
            self.repo.insert_accounts(account_rows)
        return self.repo.get_batch(batch_id)

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

    def retry_account(self, account_id: str) -> dict:
        return self.transition_account(account_id, AccountStage.RETRY_PENDING)

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
        self.repo.conn.execute(
            """UPDATE factory_worker
               SET state='RECOVERING', draining=0,
                   recovery_count=recovery_count+1,
                   last_error='manual restart requested'
               WHERE id=?""",
            (worker_id,),
        )
        return self.repo.get_worker(worker_id)