"""Account Factory V2 account workflow state machine."""
from __future__ import annotations

from types import MappingProxyType

from .models import AccountStage as S

_ALLOWED = MappingProxyType({
    S.NEW: frozenset({S.PROFILE_READY, S.ERROR, S.DISABLED}),
    S.PROFILE_READY: frozenset({S.AVD_ASSIGNED, S.ERROR, S.DISABLED}),
    S.AVD_ASSIGNED: frozenset({S.IG_READY_FOR_HUMAN, S.RETRY_PENDING, S.NEEDS_CONFIRMATION, S.ERROR}),
    S.IG_READY_FOR_HUMAN: frozenset({S.WAITING_HUMAN, S.IG_CREATED, S.NEEDS_VERIFICATION, S.ERROR}),
    S.WAITING_HUMAN: frozenset({
        S.IG_CREATED, S.THREADS_CREATED, S.NEEDS_VERIFICATION,
        S.NEEDS_CONFIRMATION, S.USERNAME_UNAVAILABLE, S.RETRY_PENDING, S.ERROR,
    }),
    S.NEEDS_VERIFICATION: frozenset({
        S.WAITING_HUMAN, S.IG_READY_FOR_HUMAN, S.THREADS_READY_FOR_HUMAN,
        S.NEEDS_CONFIRMATION, S.RETRY_PENDING, S.ERROR,
    }),
    S.NEEDS_CONFIRMATION: frozenset({
        S.WAITING_HUMAN, S.IG_READY_FOR_HUMAN, S.THREADS_READY_FOR_HUMAN,
        S.RETRY_PENDING, S.ERROR,
    }),
    S.USERNAME_UNAVAILABLE: frozenset({S.IG_READY_FOR_HUMAN, S.WAITING_HUMAN, S.RETRY_PENDING, S.ERROR}),
    S.IG_CREATED: frozenset({S.THREADS_READY_FOR_HUMAN, S.RETRY_PENDING, S.ERROR, S.DISABLED}),
    S.THREADS_READY_FOR_HUMAN: frozenset({S.WAITING_HUMAN, S.THREADS_CREATED, S.NEEDS_VERIFICATION, S.ERROR}),
    S.THREADS_CREATED: frozenset({S.ACP_CONNECTING, S.RETRY_PENDING, S.ERROR, S.DISABLED}),
    S.ACP_CONNECTING: frozenset({S.ACP_ACTIVE, S.RETRY_PENDING, S.ERROR}),
    S.ACP_ACTIVE: frozenset({S.DISABLED}),
    S.COOLDOWN: frozenset({S.RETRY_PENDING, S.AVD_ASSIGNED, S.ERROR}),
    S.RETRY_PENDING: frozenset({
        S.AVD_ASSIGNED, S.IG_READY_FOR_HUMAN, S.THREADS_READY_FOR_HUMAN,
        S.ACP_CONNECTING, S.ERROR, S.DISABLED,
    }),
    S.ERROR: frozenset({S.RETRY_PENDING, S.DISABLED}),
    S.DISABLED: frozenset(),
})

_DURABLE_SAFE_STAGES = frozenset({S.PROFILE_READY, S.IG_CREATED, S.THREADS_CREATED, S.ACP_ACTIVE})


def can_transition(from_stage: S, to_stage: S) -> bool:
    return to_stage in _ALLOWED.get(S(from_stage), frozenset())


def require_transition(from_stage: S, to_stage: S) -> None:
    if not can_transition(from_stage, to_stage):
        raise ValueError(f"illegal account transition: {S(from_stage).value} -> {S(to_stage).value}")


def safe_stage_after_transition(previous_safe: S, new_stage: S) -> S:
    stage = S(new_stage)
    return stage if stage in _DURABLE_SAFE_STAGES else S(previous_safe)
