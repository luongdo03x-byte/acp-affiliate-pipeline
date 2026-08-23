"""Fail-closed browser credential flow for Threads OAuth."""
from __future__ import annotations

from ..flow_result import FlowResult
from .screens import (
    BROWSER_LOGIN,
    CHROME_FIRST_RUN,
    OAUTH_CONSENT,
    SECURITY_CHALLENGE,
    UNKNOWN,
)


class BrowserLoginFlow:
    def __init__(self, driver, *, load_timeout: float = 8.0, post_login_timeout: float = 10.0):
        self.driver = driver
        self.load_timeout = max(0.0, float(load_timeout))
        self.post_login_timeout = max(0.0, float(post_login_timeout))

    def prepare_browser(self) -> FlowResult:
        wait_for = getattr(self.driver, "wait_for", None)
        if wait_for is None:
            screen = self.driver.detect_screen()
        else:
            # reset_browser_session clears Chrome app data, so a fresh OAuth browser
            # must positively reach the first-run screen before we trust it. Do not
            # treat a transient UNKNOWN while Chrome is still starting as ready.
            screen = wait_for(
                (CHROME_FIRST_RUN,),
                self.load_timeout,
            )
        if screen.kind != CHROME_FIRST_RUN:
            return FlowResult(
                "needs_confirmation",
                screen.kind or UNKNOWN,
                "CHROME_FIRST_RUN_UNVERIFIED",
            )

        tap_skip = getattr(self.driver, "tap_use_without_account", None)
        if tap_skip is None or tap_skip().status != "completed":
            return FlowResult(
                "needs_confirmation",
                CHROME_FIRST_RUN,
                "CHROME_FIRST_RUN_UNVERIFIED",
            )

        # The tap itself is not enough: Chrome can keep rendering the welcome page
        # for a short time. Verify that first-run really disappeared before opening
        # the OAuth URL, otherwise the intent can be swallowed by the welcome UI.
        if wait_for is None:
            after = self.driver.detect_screen()
        else:
            after = wait_for(
                (UNKNOWN,),
                min(self.load_timeout, 3.0),
            )
        if after.kind == CHROME_FIRST_RUN:
            return FlowResult(
                "needs_confirmation",
                CHROME_FIRST_RUN,
                "CHROME_FIRST_RUN_UNVERIFIED",
            )
        return FlowResult("running", "BROWSER_READY")

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
