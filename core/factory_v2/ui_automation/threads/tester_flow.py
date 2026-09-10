"""Fail-closed acceptance of the Meta Threads tester invitation.

One step per call, mirroring the Kotlin runner: the controller calls again on the
next tick until the invitation is accepted. The ladder walks from the deepest
screen outwards and keys off nodes rather than screen kinds, because adding a
profile screen kind to the detector would stop ``ThreadsFlow`` from recognising
the profile page as ``THREADS_HOME``.

Every branch acts only on a control it has positively found. Protected screens
end the flow and hand the account back to a human.
"""
from __future__ import annotations

from ..flow_result import FlowResult
from .selectors import (
    APPS_AND_WEBSITES,
    INVITES_TAB_HINT,
    PROFILE,
    PROFILE_SETTINGS_ICON,
    TESTER_ACCEPT,
    TESTER_INVITES,
    WEBSITE_PERMISSIONS,
)

_INVITE_LIST = "THREADS_TESTER_INVITE_LIST"
_RETRYABLE = ("RATE_LIMITED", "ACTION_BLOCKED", "NETWORK_ERROR")


class ThreadsTesterFlow:
    def __init__(self, driver):
        self.driver = driver

    def _tap(self, selector, screen: str, expected: tuple[str, ...] = ()) -> FlowResult:
        action = self.driver.tap(selector, expected_screens=expected, timeout=8.0)
        if action.status != "completed":
            return FlowResult("needs_confirmation", screen, "UI_CHANGED")
        return FlowResult("running", screen, last_safe_step=screen)

    def accept_invite(self) -> FlowResult:
        detected = self.driver.detect_screen()
        if detected.protected:
            return FlowResult("waiting_human", detected.kind, "HUMAN_VERIFICATION_REQUIRED")

        if self.driver.find(TESTER_ACCEPT) is not None:
            action = self.driver.tap(TESTER_ACCEPT)
            if action.status != "completed":
                return FlowResult("needs_confirmation", _INVITE_LIST, "ACCEPT_TAP_FAILED")
            # Tapping a positively identified Accept control is the completion
            # signal. Do not guess at whatever screen Meta renders afterwards.
            return FlowResult("completed", _INVITE_LIST, last_safe_step="THREADS_TESTER_ACCEPTED")

        if self.driver.find(APPS_AND_WEBSITES) is not None:
            if self.driver.find(INVITES_TAB_HINT) is not None:
                # Standing on the Invites tab with no Accept control means Meta
                # never sent the invitation to this account.
                return FlowResult("needs_confirmation", _INVITE_LIST, "NO_TESTER_INVITE")
            return self._tap(TESTER_INVITES, "THREADS_APPS_AND_WEBSITES")

        if self.driver.find(WEBSITE_PERMISSIONS) is not None:
            return self._tap(WEBSITE_PERMISSIONS, "THREADS_SETTINGS")

        if self.driver.find(PROFILE_SETTINGS_ICON) is not None:
            return self._tap(PROFILE_SETTINGS_ICON, "THREADS_PROFILE")

        if self.driver.find(PROFILE) is not None:
            return self._tap(PROFILE, "THREADS_HOME")

        if detected.kind in _RETRYABLE:
            return FlowResult("retry_pending", detected.kind, detected.kind)
        if detected.kind == "ACCOUNT_DISABLED":
            return FlowResult("error", detected.kind, "ACCOUNT_DISABLED")
        return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
