"""Sanitized result returned by platform UI flows."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FlowResult:
    status: str
    screen: str
    reason: str | None = None
    last_safe_step: str | None = None
    profile_updates: dict[str, str] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "screen": self.screen,
            "reason": self.reason,
            "last_safe_step": self.last_safe_step,
            "profile_updates": self.profile_updates,
        }
