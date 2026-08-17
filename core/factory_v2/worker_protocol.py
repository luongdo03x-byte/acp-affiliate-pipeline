"""Serializable worker protocol and idempotent command ledger."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from threading import Lock
from typing import Any, Callable


@dataclass(frozen=True)
class WorkerCommand:
    command_id: str
    action: str
    account_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkerHeartbeat:
    worker_id: str
    adb_serial: str
    state: str
    current_account_id: str | None = None
    current_job_id: str | None = None
    observed_state: str | None = None
    last_progress_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CommandLedger:
    """Small in-memory at-most-once ledger for idempotent worker commands."""

    def __init__(self, *, max_entries: int = 512):
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self.max_entries = max_entries
        self._results: OrderedDict[str, Any] = OrderedDict()
        self._lock = Lock()

    def execute(self, command_id: str, action: Callable[[], Any]) -> Any:
        command_id = str(command_id).strip()
        if not command_id:
            raise ValueError("command_id is required")
        with self._lock:
            if command_id in self._results:
                result = self._results.pop(command_id)
                self._results[command_id] = result
                return result
            result = action()
            self._results[command_id] = result
            while len(self._results) > self.max_entries:
                self._results.popitem(last=False)
            return result
