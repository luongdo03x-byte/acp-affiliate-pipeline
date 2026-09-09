"""Fail-closed acceptance of the Meta Threads tester invitation.

The flow only ever taps controls on screens it has positively identified. Any
protected or unrecognized screen ends the flow and hands the account back to a
human, exactly like the profile flow in ``flow.py``.
"""
from __future__ import annotations

from ..flow_result import FlowResult
from .selectors import TESTER_ACCEPT, TESTER_INVITES, WEBSITE_PERMISSIONS

_CONFIRMED = "THREADS_TESTER_INVITE_CONFIRM"

# screen -> (selector to tap, screens that prove the tap worked)
_STEPS = (
    ("THREADS_SETTINGS", WEBSITE_PERMISSIONS, ("THREADS_WEBSITE_PERMISSIONS",)),
    ("THREADS_WEBSITE_PERMISSIONS", TESTER_INVITES, ("THREADS_TESTER_INVITE_LIST",)),
    ("THREADS_TESTER_INVITE_LIST", TESTER_ACCEPT, (_CONFIRMED,)),
)


class ThreadsTesterFlow:
    def __init__(self, driver):
        self.driver = driver

    def accept_invite(self) -> FlowResult:
        detected = None
        for _ in range(len(_STEPS) + 1):
            detected = self.driver.detect_screen()
            if detected.protected:
                return FlowResult(
                    "waiting_human", detected.kind, "HUMAN_VERIFICATION_REQUIRED"
                )
            if detected.kind == _CONFIRMED:
                return FlowResult(
                    "completed", detected.kind, last_safe_step="THREADS_TESTER_ACCEPTED"
                )
            step = next((item for item in _STEPS if item[0] == detected.kind), None)
            if step is None:
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")

            _, selector, expected = step
            if self.driver.find(selector) is None:
                # An invite list with no Accept control means Meta never sent the
                # invitation. Report it instead of retrying against a dead end.
                reason = (
                    "NO_TESTER_INVITE"
                    if detected.kind == "THREADS_TESTER_INVITE_LIST"
                    else "UI_CHANGED"
                )
                return FlowResult("needs_confirmation", detected.kind, reason)

            action = self.driver.tap(selector, expected_screens=expected, timeout=8.0)
            if action.status != "completed":
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")

        kind = detected.kind if detected is not None else "UNKNOWN"
        return FlowResult("needs_confirmation", kind, "UI_CHANGED")
