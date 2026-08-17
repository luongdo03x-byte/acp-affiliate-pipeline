"""Fail-closed Instagram UI state machine for REMOTE_AVD."""
from __future__ import annotations

from ..flow_result import FlowResult
from .screens import PACKAGE
from .selectors import BIO_INPUT, CONTINUE, DISPLAY_NAME_INPUT, SIGN_UP, USERNAME_INPUT

_SUCCESS = frozenset({"IG_HOME", "IG_POSTCHECK_OK"})
_CHECKPOINT_SUCCESSORS = frozenset({"IG_PROFILE_SETUP", "IG_HOME", "IG_POSTCHECK_OK"})
_IG_PROTECTED = (
    "PASSWORD_REQUIRED", "OTP_REQUIRED", "CAPTCHA_REQUIRED",
    "EMAIL_OR_PHONE_VERIFICATION", "SELFIE_OR_IDENTITY_CHECK",
    "SECURITY_CHALLENGE", "ACCOUNT_RECOVERY", "CONSENT_WITH_SECURITY_IMPACT",
)
_IG_ERRORS = (
    "NETWORK_ERROR", "RATE_LIMITED", "ACTION_BLOCKED", "ACCOUNT_DISABLED", "APP_CRASH",
)
_AFTER_SIGNUP = _IG_PROTECTED + _IG_ERRORS + (
    "IG_PROFILE_SETUP", "IG_HOME", "IG_POSTCHECK_OK",
)
_AFTER_PROFILE = _IG_PROTECTED + _IG_ERRORS + ("IG_HOME", "IG_POSTCHECK_OK")


class InstagramFlow:
    def __init__(self, driver):
        self.driver = driver

    @staticmethod
    def _attempt(action):
        result = None
        for _ in range(3):
            result = action()
            if result.status in {"completed", "noop"}:
                return result
        return result

    def _detect_bounded(self):
        detected = self.driver.detect_screen()
        for _ in range(2):
            if detected.kind != "UNKNOWN":
                return detected
            detected = self.driver.detect_screen()
        return detected

    def _result_for_error(self, detected):
        if detected.kind in {"RATE_LIMITED", "ACTION_BLOCKED"}:
            return FlowResult("retry_pending", detected.kind, detected.kind)
        if detected.kind == "ACCOUNT_DISABLED":
            return FlowResult("error", detected.kind, "ACCOUNT_DISABLED")
        return None

    def _handle_detected(self, detected, profile: dict, *, crash_reopened: bool = False) -> FlowResult:
        if detected.protected:
            return FlowResult("waiting_human", detected.kind, "HUMAN_VERIFICATION_REQUIRED")
        if detected.kind in _SUCCESS:
            return FlowResult("completed", detected.kind, last_safe_step="IG_POSTCHECK_OK")
        error = self._result_for_error(detected)
        if error is not None:
            return error
        if detected.kind == "UNKNOWN":
            return FlowResult("needs_confirmation", "UNKNOWN", "UI_CHANGED")
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
        if detected.kind == "IG_SIGNUP_ENTRY":
            selector = SIGN_UP if self.driver.find(SIGN_UP) is not None else CONTINUE
            action = self._attempt(
                lambda: self.driver.tap(
                    selector,
                    expected_screens=_AFTER_SIGNUP,
                    timeout=8.0,
                )
            )
            if action.status != "completed":
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            return FlowResult("running", detected.kind, last_safe_step="IG_SIGNUP_ENTRY")
        if detected.kind == "IG_PROFILE_SETUP":
            approved = (
                (USERNAME_INPUT, str(profile.get("username") or "")),
                (DISPLAY_NAME_INPUT, str(profile.get("display_name") or "")),
                (BIO_INPUT, str(profile.get("bio") or "")),
            )
            for selector, value in approved:
                if not value or self.driver.find(selector) is None:
                    continue
                action = self._attempt(
                    lambda selector=selector, value=value: self.driver.set_text(selector, value)
                )
                if action.status not in {"completed", "noop"}:
                    return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            if self.driver.find(CONTINUE) is not None:
                action = self._attempt(
                    lambda: self.driver.tap(
                        CONTINUE,
                        expected_screens=_AFTER_PROFILE,
                        timeout=8.0,
                    )
                )
                if action.status != "completed":
                    return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            return FlowResult("running", detected.kind, last_safe_step="IG_PROFILE_SETUP")
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
