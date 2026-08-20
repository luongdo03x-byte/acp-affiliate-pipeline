"""Fail-closed browser credential flow for Threads OAuth."""
from __future__ import annotations

from ..flow_result import FlowResult
from .screens import BROWSER_LOGIN, OAUTH_CONSENT, SECURITY_CHALLENGE


class BrowserLoginFlow:
    def __init__(self, driver, *, load_timeout: float = 8.0, post_login_timeout: float = 10.0):
        self.driver = driver
        self.load_timeout = max(0.0, float(load_timeout))
        self.post_login_timeout = max(0.0, float(post_login_timeout))

    def _wait_for_browser_state(self, timeout: float):
        wait_for = getattr(self.driver, "wait_for", None)
        if wait_for is None:
            return self.driver.detect_screen()
        return wait_for(
            (BROWSER_LOGIN, OAUTH_CONSENT, SECURITY_CHALLENGE),
            timeout,
        )

    def run(self, username: str, password: str) -> FlowResult:
        screen = self._wait_for_browser_state(self.load_timeout)
        if screen.kind == OAUTH_CONSENT:
            return FlowResult(
                "waiting_human",
                OAUTH_CONSENT,
                "HUMAN_CONSENT_REQUIRED",
            )
        if screen.kind == SECURITY_CHALLENGE:
            return FlowResult(
                "waiting_human",
                SECURITY_CHALLENGE,
                "HUMAN_VERIFICATION_REQUIRED",
            )
        if screen.kind != BROWSER_LOGIN:
            return FlowResult(
                "needs_confirmation",
                screen.kind or "UNKNOWN",
                "UI_CHANGED",
            )

        if self.driver.set_username(username).status not in {"completed", "noop"}:
            return FlowResult(
                "needs_confirmation",
                BROWSER_LOGIN,
                "USERNAME_FIELD_UNVERIFIED",
            )
        if self.driver.set_password(password).status not in {"completed", "noop"}:
            return FlowResult(
                "needs_confirmation",
                BROWSER_LOGIN,
                "PASSWORD_FIELD_UNVERIFIED",
            )
        if self.driver.tap_login().status != "completed":
            return FlowResult(
                "needs_confirmation",
                BROWSER_LOGIN,
                "LOGIN_SUBMIT_UNVERIFIED",
            )

        after = self._wait_for_browser_state(self.post_login_timeout)
        if after.kind == OAUTH_CONSENT:
            return FlowResult(
                "running",
                "LOGIN_SUCCEEDED",
                "OAUTH_CONSENT_REACHED",
            )
        if after.kind == SECURITY_CHALLENGE:
            return FlowResult(
                "waiting_human",
                SECURITY_CHALLENGE,
                "HUMAN_VERIFICATION_REQUIRED",
            )
        return FlowResult(
            "needs_confirmation",
            after.kind or "UNKNOWN",
            "LOGIN_POSTCHECK_FAILED",
        )
