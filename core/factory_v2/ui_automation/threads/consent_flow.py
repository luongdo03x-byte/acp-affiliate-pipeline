"""Fail-closed confirmation of the Threads OAuth authorization window.

The flow taps the approval control only on a screen the detector positively
identified as the authorization window. Anything else, including every
protected screen, ends the flow and leaves the account for a human.
"""
from __future__ import annotations

from ..flow_result import FlowResult
from .selectors import OAUTH_CONSENT_ALLOW

_CONSENT = "THREADS_OAUTH_CONSENT"
_MAX_POLLS = 3


class ThreadsConsentFlow:
    def __init__(self, driver):
        self.driver = driver

    def confirm(self) -> FlowResult:
        detected = None
        for _ in range(_MAX_POLLS):
            detected = self.driver.detect_screen()
            if detected.protected:
                return FlowResult(
                    "waiting_human", detected.kind, "HUMAN_VERIFICATION_REQUIRED"
                )
            if detected.kind != _CONSENT:
                continue
            if self.driver.find(OAUTH_CONSENT_ALLOW) is None:
                return FlowResult(
                    "needs_confirmation", detected.kind, "CONSENT_NOT_DETECTED"
                )
            action = self.driver.tap(OAUTH_CONSENT_ALLOW)
            if action.status != "completed":
                return FlowResult(
                    "needs_confirmation", detected.kind, "CONSENT_TAP_FAILED"
                )
            return FlowResult("completed", detected.kind, last_safe_step=_CONSENT)

        kind = detected.kind if detected is not None else "UNKNOWN"
        return FlowResult("needs_confirmation", kind, "CONSENT_NOT_DETECTED")
