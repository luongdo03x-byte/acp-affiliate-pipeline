"""Fail-closed Threads UI state machine for REMOTE_AVD."""
from __future__ import annotations

from ..flow_result import FlowResult
from .screens import PACKAGE
from .selectors import BIO_INPUT, CONTINUE, DISPLAY_NAME_INPUT, JOIN_THREADS

_SUCCESS = frozenset({"THREADS_HOME", "THREADS_POSTCHECK_OK"})
_CHECKPOINT_SUCCESSORS = frozenset({"THREADS_PROFILE_SETUP", "THREADS_HOME", "THREADS_POSTCHECK_OK"})


class ThreadsFlow:
    def __init__(self, driver):
        self.driver = driver

    def _detect_bounded(self):
        detected = self.driver.detect_screen()
        for _ in range(2):
            if detected.kind != "UNKNOWN":
                return detected
            detected = self.driver.detect_screen()
        return detected

    def _handle_detected(self, detected, profile: dict, *, crash_reopened: bool = False) -> FlowResult:
        if detected.protected:
            return FlowResult("waiting_human", detected.kind, "HUMAN_VERIFICATION_REQUIRED")
        if detected.kind in _SUCCESS:
            return FlowResult("completed", detected.kind, last_safe_step="THREADS_POSTCHECK_OK")
        if detected.kind in {"RATE_LIMITED", "ACTION_BLOCKED"}:
            return FlowResult("retry_pending", detected.kind, detected.kind)
        if detected.kind == "ACCOUNT_DISABLED":
            return FlowResult("error", detected.kind, "ACCOUNT_DISABLED")
        if detected.kind == "UNKNOWN":
            return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
        if detected.kind == "NETWORK_ERROR":
            current = detected
            for _ in range(2):
                current = self.driver.detect_screen()
                if current.kind != "NETWORK_ERROR":
                    return self._handle_detected(current, profile, crash_reopened=crash_reopened)
            return FlowResult("retry_pending", current.kind, "NETWORK_ERROR")
        if detected.kind == "APP_CRASH":
            if crash_reopened:
                return FlowResult("needs_confirmation", detected.kind, "APP_CRASH")
            self.driver.open_package(PACKAGE)
            return self._handle_detected(self._detect_bounded(), profile, crash_reopened=True)
        if detected.kind == "THREADS_ONBOARDING":
            selector = JOIN_THREADS if self.driver.find(JOIN_THREADS) is not None else CONTINUE
            action = self.driver.tap(selector)
            if action.status != "completed":
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            return FlowResult("running", detected.kind, last_safe_step="THREADS_ONBOARDING")
        if detected.kind == "THREADS_PROFILE_SETUP":
            approved = ((DISPLAY_NAME_INPUT, str(profile.get("display_name") or "")), (BIO_INPUT, str(profile.get("bio") or "")))
            for selector, value in approved:
                if not value or self.driver.find(selector) is None:
                    continue
                action = self.driver.set_text(selector, value)
                if action.status not in {"completed", "noop"}:
                    return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            if self.driver.find(CONTINUE) is not None:
                action = self.driver.tap(CONTINUE)
                if action.status != "completed":
                    return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            return FlowResult("running", detected.kind, last_safe_step="THREADS_PROFILE_SETUP")
        return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")

    def run(self, profile: dict) -> FlowResult:
        return self._handle_detected(self._detect_bounded(), dict(profile or {}))

    def observe_checkpoint(self) -> FlowResult:
        detected = self._detect_bounded()
        if detected.protected:
            return FlowResult("waiting_human", detected.kind, "HUMAN_VERIFICATION_REQUIRED")
        if detected.kind in _CHECKPOINT_SUCCESSORS and detected.automation_allowed:
            return FlowResult("completed", detected.kind, last_safe_step=detected.kind)
        if detected.kind in {"RATE_LIMITED", "ACTION_BLOCKED"}:
            return FlowResult("retry_pending", detected.kind, detected.kind)
        if detected.kind == "ACCOUNT_DISABLED":
            return FlowResult("error", detected.kind, "ACCOUNT_DISABLED")
        if detected.kind == "NETWORK_ERROR":
            return FlowResult("retry_pending", detected.kind, "NETWORK_ERROR")
        if detected.kind == "UNKNOWN":
            return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
        return FlowResult("needs_confirmation", detected.kind, "CHECKPOINT_NOT_CONFIRMED")
