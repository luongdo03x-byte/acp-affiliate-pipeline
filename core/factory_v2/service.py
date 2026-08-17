"""Controller services for Account Factory V2."""
from __future__ import annotations

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
