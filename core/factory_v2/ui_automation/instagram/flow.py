"""Fail-closed Instagram UI state machine for REMOTE_AVD."""
from __future__ import annotations

from core.factory_v2.identity import username_fallback_candidates

from ..flow_result import FlowResult
from .screens import PACKAGE
from .selectors import (
    ACCOUNT_SWITCHER,
    ACCOUNTS_CENTER_ALLOW,
    ADD_ACCOUNT,
    ADD_PROFILE_PHOTO,
    ALLOW_LIMITED_PHOTOS,
    AVATAR_CROP_DONE,
    AVATAR_SKIP,
    BIO_INPUT,
    BIRTH_DATE_INPUT,
    CHOOSE_FROM_LIBRARY,
    CONTINUE,
    DISPLAY_NAME_INPUT,
    MEDIA_PICKER_CONFIRM,
    MEDIA_PICKER_PHOTO,
    NAV_TIP_GOT_IT,
    PROFILE,
    SIGN_UP,
    SIGNUP_CONTACT_INPUT,
    USERNAME_ENTRY_INPUT,
    USERNAME_INPUT,
)

_EXISTING_SESSION_HOME = frozenset({"IG_HOME", "IG_POSTCHECK_OK"})
_CHECKPOINT_SUCCESSORS = frozenset({"IG_EXISTING_PROFILE", "IG_HOME", "IG_POSTCHECK_OK"})
_CHECKPOINT_RESUMABLE = frozenset({
    "IG_NAV_TIP",
    "IG_ACCOUNT_SWITCHER",
    "IG_SIGNUP_ENTRY",
    "IG_ACCOUNTS_CENTER_CONSENT",
    "IG_USERNAME_ENTRY",
    "IG_USERNAME_VALID",
    "IG_USERNAME_UNAVAILABLE",
    "IG_CONTACT_ENTRY",
    "IG_BIRTHDAY_ENTRY",
    "IG_PROFILE_SETUP",
    "IG_AVATAR_SETUP",
    "IG_AVATAR_SOURCE_MENU",
    "ANDROID_MEDIA_PERMISSION",
    "ANDROID_MEDIA_PICKER_INITIAL",
    "ANDROID_MEDIA_PICKER",
    "IG_AVATAR_CROP",
})
_IG_PROTECTED = (
    "PASSWORD_REQUIRED",
    "OTP_REQUIRED",
    "CAPTCHA_REQUIRED",
    "IG_FINAL_SIGNUP_SUBMIT",
    "EMAIL_OR_PHONE_VERIFICATION",
    "SELFIE_OR_IDENTITY_CHECK",
    "SECURITY_CHALLENGE",
    "ACCOUNT_RECOVERY",
    "CONSENT_WITH_SECURITY_IMPACT",
)
_IG_ERRORS = (
    "NETWORK_ERROR",
    "RATE_LIMITED",
    "ACTION_BLOCKED",
    "ACCOUNT_DISABLED",
    "APP_CRASH",
)
_USERNAME_VALIDATION_STATES = _IG_PROTECTED + _IG_ERRORS + (
    "IG_USERNAME_VALID",
    "IG_USERNAME_UNAVAILABLE",
)
_AFTER_EXISTING_HOME = _IG_PROTECTED + _IG_ERRORS + (
    "IG_EXISTING_PROFILE",
    "IG_ACCOUNT_SWITCHER",
    "IG_SIGNUP_ENTRY",
)
_AFTER_EXISTING_PROFILE = _IG_PROTECTED + _IG_ERRORS + (
    "IG_ACCOUNT_SWITCHER",
    "IG_SIGNUP_ENTRY",
)
_AFTER_ADD_ACCOUNT = _IG_PROTECTED + _IG_ERRORS + (
    "IG_SIGNUP_ENTRY",
    "IG_ACCOUNTS_CENTER_CONSENT",
    "IG_USERNAME_ENTRY",
    "IG_USERNAME_VALID",
    "IG_USERNAME_UNAVAILABLE",
    "IG_CONTACT_ENTRY",
    "IG_BIRTHDAY_ENTRY",
    "IG_PROFILE_SETUP",
    "IG_AVATAR_SETUP",
)
_AFTER_SIGNUP = _IG_PROTECTED + _IG_ERRORS + (
    "IG_ACCOUNTS_CENTER_CONSENT",
    "IG_USERNAME_ENTRY",
    "IG_USERNAME_VALID",
    "IG_USERNAME_UNAVAILABLE",
    "IG_CONTACT_ENTRY",
    "IG_BIRTHDAY_ENTRY",
    "IG_PROFILE_SETUP",
    "IG_AVATAR_SETUP",
    "IG_HOME",
    "IG_POSTCHECK_OK",
)
_AFTER_ACCOUNTS_CENTER = _IG_PROTECTED + _IG_ERRORS + (
    "IG_USERNAME_ENTRY",
    "IG_USERNAME_VALID",
    "IG_USERNAME_UNAVAILABLE",
    "IG_CONTACT_ENTRY",
    "IG_BIRTHDAY_ENTRY",
    "IG_PROFILE_SETUP",
    "IG_AVATAR_SETUP",
    "IG_HOME",
    "IG_POSTCHECK_OK",
)
_AFTER_USERNAME = _IG_PROTECTED + _IG_ERRORS + (
    "IG_CONTACT_ENTRY",
    "IG_BIRTHDAY_ENTRY",
    "IG_PROFILE_SETUP",
    "IG_AVATAR_SETUP",
    "IG_HOME",
    "IG_POSTCHECK_OK",
)
_AFTER_CONTACT = _IG_PROTECTED + _IG_ERRORS + (
    "IG_BIRTHDAY_ENTRY",
    "IG_PROFILE_SETUP",
    "IG_AVATAR_SETUP",
    "IG_HOME",
    "IG_POSTCHECK_OK",
)
_AFTER_BIRTHDAY = _IG_PROTECTED + _IG_ERRORS + (
    "IG_PROFILE_SETUP",
    "IG_AVATAR_SETUP",
    "IG_HOME",
    "IG_POSTCHECK_OK",
)
_AFTER_PROFILE = _IG_PROTECTED + _IG_ERRORS + (
    "IG_AVATAR_SETUP",
    "IG_HOME",
    "IG_POSTCHECK_OK",
)
_AFTER_AVATAR_CROP = _IG_PROTECTED + _IG_ERRORS + (
    "IG_NAV_TIP",
    "IG_EXISTING_PROFILE",
    "IG_HOME",
    "IG_POSTCHECK_OK",
)
_ALLOWED_MEDIA_CONFIRM_TEXTS = frozenset({
    "Allow (1)",
    "Allow 1 photo",
    "Allow 1 photo and video",
    "Cho phép (1)",
    "Cho phép 1 ảnh",
})


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

    @staticmethod
    def _username_terminal_result(detected):
        if detected.protected:
            return FlowResult(
                "waiting_human",
                detected.kind,
                "HUMAN_VERIFICATION_REQUIRED",
            )
        if detected.kind in {"RATE_LIMITED", "ACTION_BLOCKED"}:
            return FlowResult("retry_pending", detected.kind, detected.kind)
        if detected.kind == "ACCOUNT_DISABLED":
            return FlowResult("error", detected.kind, "ACCOUNT_DISABLED")
        if detected.kind == "NETWORK_ERROR":
            return FlowResult("retry_pending", detected.kind, "NETWORK_ERROR")
        if detected.kind not in {"IG_USERNAME_VALID", "IG_USERNAME_UNAVAILABLE"}:
            return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
        return None

    def _username_validation_result(
        self,
        validation,
        profile: dict,
        account_id: str | None,
        *,
        crash_reopened: bool,
    ) -> FlowResult | None:
        if validation.kind == "APP_CRASH":
            if crash_reopened:
                return FlowResult("needs_confirmation", validation.kind, "APP_CRASH")
            self.driver.open_package(PACKAGE)
            return self._handle_detected(
                self._detect_bounded(),
                profile,
                account_id=account_id,
                crash_reopened=True,
            )
        return self._username_terminal_result(validation)

    def _accept_username(
        self,
        selected: str,
        requested: str,
        original_screen: str,
    ) -> FlowResult:
        if self.driver.find(CONTINUE) is None:
            return FlowResult("needs_confirmation", original_screen, "UI_CHANGED")
        action = self._attempt(
            lambda: self.driver.tap(
                CONTINUE,
                expected_screens=_AFTER_USERNAME,
                timeout=8.0,
            )
        )
        if action.status != "completed":
            return FlowResult("needs_confirmation", original_screen, "UI_CHANGED")
        updates = {"username": selected} if selected != requested else None
        return FlowResult(
            "running",
            "IG_USERNAME_VALID",
            last_safe_step="IG_USERNAME_ENTRY",
            profile_updates=updates,
        )

    def _handle_username(
        self,
        detected,
        profile: dict,
        account_id: str | None,
        *,
        crash_reopened: bool = False,
    ) -> FlowResult:
        requested = str(profile.get("username") or "").strip()
        if not requested:
            return FlowResult("needs_confirmation", detected.kind, "MISSING_USERNAME")

        current = self.driver.find(USERNAME_ENTRY_INPUT)
        if current is None:
            return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
        current_text = str(getattr(current, "text", "") or "")

        if detected.kind != "IG_USERNAME_UNAVAILABLE":
            if current_text != requested:
                action = self._attempt(
                    lambda: self.driver.set_text(USERNAME_ENTRY_INPUT, requested)
                )
                if action.status not in {"completed", "noop"}:
                    return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            validation = self.driver.wait_for(_USERNAME_VALIDATION_STATES, 8.0)
            terminal = self._username_validation_result(
                validation,
                profile,
                account_id,
                crash_reopened=crash_reopened,
            )
            if terminal is not None:
                return terminal
            if validation.kind == "IG_USERNAME_VALID":
                return self._accept_username(requested, requested, detected.kind)

        stable_id = str(account_id or "").strip()
        if not stable_id:
            return FlowResult("needs_confirmation", detected.kind, "MISSING_ACCOUNT_ID")

        for candidate in username_fallback_candidates(
            requested,
            stable_id,
            max_candidates=5,
        ):
            action = self._attempt(
                lambda candidate=candidate: self.driver.set_text(
                    USERNAME_ENTRY_INPUT,
                    candidate,
                )
            )
            if action.status not in {"completed", "noop"}:
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")

            validation = self.driver.wait_for(_USERNAME_VALIDATION_STATES, 8.0)
            terminal = self._username_validation_result(
                validation,
                profile,
                account_id,
                crash_reopened=crash_reopened,
            )
            if terminal is not None:
                return terminal
            if validation.kind == "IG_USERNAME_UNAVAILABLE":
                continue
            if validation.kind == "IG_USERNAME_VALID":
                return self._accept_username(candidate, requested, detected.kind)

        return FlowResult(
            "needs_confirmation",
            "IG_USERNAME_UNAVAILABLE",
            "USERNAME_UNAVAILABLE",
        )

    def _handle_detected(
        self,
        detected,
        profile: dict,
        *,
        account_id: str | None = None,
        crash_reopened: bool = False,
    ) -> FlowResult:
        if detected.protected:
            return FlowResult("waiting_human", detected.kind, "HUMAN_VERIFICATION_REQUIRED")
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
                    return self._handle_detected(
                        current,
                        profile,
                        account_id=account_id,
                        crash_reopened=crash_reopened,
                    )
            return FlowResult("retry_pending", current.kind, "NETWORK_ERROR")
        if detected.kind == "APP_CRASH":
            if crash_reopened:
                return FlowResult("needs_confirmation", detected.kind, "APP_CRASH")
            self.driver.open_package(PACKAGE)
            return self._handle_detected(
                self._detect_bounded(),
                profile,
                account_id=account_id,
                crash_reopened=True,
            )
        if detected.kind == "IG_NAV_TIP":
            if self.driver.find(NAV_TIP_GOT_IT) is None:
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            action = self._attempt(lambda: self.driver.tap(NAV_TIP_GOT_IT))
            if action.status != "completed":
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            return FlowResult("running", detected.kind, last_safe_step="IG_NAV_TIP")
        if detected.kind in _EXISTING_SESSION_HOME:
            if self.driver.find(PROFILE) is None:
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            action = self._attempt(
                lambda: self.driver.tap(
                    PROFILE,
                    expected_screens=_AFTER_EXISTING_HOME,
                    timeout=8.0,
                )
            )
            if action.status != "completed":
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            return FlowResult("running", detected.kind, last_safe_step="IG_EXISTING_SESSION")
        if detected.kind == "IG_EXISTING_PROFILE":
            if self.driver.find(ACCOUNT_SWITCHER) is None:
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            action = self._attempt(
                lambda: self.driver.tap(
                    ACCOUNT_SWITCHER,
                    expected_screens=_AFTER_EXISTING_PROFILE,
                    timeout=8.0,
                )
            )
            if action.status != "completed":
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            return FlowResult("running", detected.kind, last_safe_step="IG_EXISTING_PROFILE")
        if detected.kind == "IG_ACCOUNT_SWITCHER":
            if self.driver.find(ADD_ACCOUNT) is None:
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            action = self._attempt(
                lambda: self.driver.tap(
                    ADD_ACCOUNT,
                    expected_screens=_AFTER_ADD_ACCOUNT,
                    timeout=8.0,
                )
            )
            if action.status != "completed":
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            return FlowResult("running", detected.kind, last_safe_step="IG_ACCOUNT_SWITCHER")
        if detected.kind == "IG_SIGNUP_ENTRY":
            if self.driver.find(SIGN_UP) is None:
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            action = self._attempt(
                lambda: self.driver.tap(
                    SIGN_UP,
                    expected_screens=_AFTER_SIGNUP,
                    timeout=8.0,
                )
            )
            if action.status != "completed":
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            return FlowResult("running", detected.kind, last_safe_step="IG_SIGNUP_ENTRY")
        if detected.kind == "IG_ACCOUNTS_CENTER_CONSENT":
            if self.driver.find(ACCOUNTS_CENTER_ALLOW) is None:
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            action = self._attempt(
                lambda: self.driver.tap(
                    ACCOUNTS_CENTER_ALLOW,
                    expected_screens=_AFTER_ACCOUNTS_CENTER,
                    timeout=8.0,
                )
            )
            if action.status != "completed":
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            return FlowResult(
                "running",
                detected.kind,
                last_safe_step="IG_ACCOUNTS_CENTER_CONSENT",
            )
        if detected.kind in {
            "IG_USERNAME_ENTRY",
            "IG_USERNAME_VALID",
            "IG_USERNAME_UNAVAILABLE",
        }:
            return self._handle_username(
                detected,
                profile,
                account_id,
                crash_reopened=crash_reopened,
            )
        if detected.kind == "IG_CONTACT_ENTRY":
            contact = str(profile.get("signup_contact") or "").strip()
            if not contact:
                return FlowResult(
                    "needs_confirmation",
                    detected.kind,
                    "MISSING_SIGNUP_CONTACT",
                )
            if self.driver.find(SIGNUP_CONTACT_INPUT) is None or self.driver.find(CONTINUE) is None:
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            action = self._attempt(
                lambda: self.driver.set_text(SIGNUP_CONTACT_INPUT, contact)
            )
            if action.status not in {"completed", "noop"}:
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            action = self._attempt(
                lambda: self.driver.tap(
                    CONTINUE,
                    expected_screens=_AFTER_CONTACT,
                    timeout=8.0,
                )
            )
            if action.status != "completed":
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            return FlowResult("running", detected.kind, last_safe_step="IG_CONTACT_ENTRY")
        if detected.kind == "IG_BIRTHDAY_ENTRY":
            birth_date = str(profile.get("birth_date") or "").strip()
            if not birth_date:
                return FlowResult("needs_confirmation", detected.kind, "MISSING_BIRTH_DATE")
            if self.driver.find(BIRTH_DATE_INPUT) is None or self.driver.find(CONTINUE) is None:
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            action = self._attempt(
                lambda: self.driver.set_text(BIRTH_DATE_INPUT, birth_date)
            )
            if action.status not in {"completed", "noop"}:
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            action = self._attempt(
                lambda: self.driver.tap(
                    CONTINUE,
                    expected_screens=_AFTER_BIRTHDAY,
                    timeout=8.0,
                )
            )
            if action.status != "completed":
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            return FlowResult("running", detected.kind, last_safe_step="IG_BIRTHDAY_ENTRY")
        if detected.kind == "IG_AVATAR_SETUP":
            avatar_file = str(profile.get("avatar_file") or "").strip()
            if avatar_file:
                if self.driver.find(ADD_PROFILE_PHOTO) is None:
                    return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
                action = self._attempt(lambda: self.driver.tap(ADD_PROFILE_PHOTO))
            else:
                if self.driver.find(AVATAR_SKIP) is None:
                    return FlowResult("needs_confirmation", detected.kind, "MISSING_AVATAR")
                action = self._attempt(lambda: self.driver.tap(AVATAR_SKIP))
            if action.status != "completed":
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            return FlowResult("running", detected.kind, last_safe_step="IG_AVATAR_SETUP")
        if detected.kind == "IG_AVATAR_SOURCE_MENU":
            if not str(profile.get("avatar_file") or "").strip():
                return FlowResult("needs_confirmation", detected.kind, "MISSING_AVATAR")
            if self.driver.find(CHOOSE_FROM_LIBRARY) is None:
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            action = self._attempt(lambda: self.driver.tap(CHOOSE_FROM_LIBRARY))
            if action.status != "completed":
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            return FlowResult("running", detected.kind, last_safe_step="IG_AVATAR_SOURCE_MENU")
        if detected.kind == "ANDROID_MEDIA_PERMISSION":
            if not str(profile.get("avatar_file") or "").strip():
                return FlowResult("needs_confirmation", detected.kind, "MISSING_AVATAR")
            if self.driver.find(ALLOW_LIMITED_PHOTOS) is None:
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            action = self._attempt(lambda: self.driver.tap(ALLOW_LIMITED_PHOTOS))
            if action.status != "completed":
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            return FlowResult("running", detected.kind, last_safe_step="ANDROID_MEDIA_PERMISSION")
        if detected.kind == "ANDROID_MEDIA_PICKER_INITIAL":
            if not str(profile.get("avatar_file") or "").strip():
                return FlowResult("needs_confirmation", detected.kind, "MISSING_AVATAR")
            if self.driver.find(MEDIA_PICKER_PHOTO) is None:
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            action = self._attempt(lambda: self.driver.tap(MEDIA_PICKER_PHOTO))
            if action.status != "completed":
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            return FlowResult(
                "running",
                detected.kind,
                last_safe_step="ANDROID_MEDIA_PICKER_INITIAL",
            )
        if detected.kind == "ANDROID_MEDIA_PICKER":
            if not str(profile.get("avatar_file") or "").strip():
                return FlowResult("needs_confirmation", detected.kind, "MISSING_AVATAR")

            confirm = None
            confirm_text = ""
            for _ in range(3):
                candidate = self.driver.find(MEDIA_PICKER_CONFIRM)
                if candidate is None:
                    continue
                confirm = candidate
                confirm_text = str(getattr(candidate, "text", "") or "").strip()
                if confirm_text in _ALLOWED_MEDIA_CONFIRM_TEXTS:
                    break
            if confirm is None or confirm_text not in _ALLOWED_MEDIA_CONFIRM_TEXTS:
                return FlowResult("needs_confirmation", detected.kind, "MEDIA_PICKER_NOT_CONFIRMED")

            action = self._attempt(lambda: self.driver.tap(MEDIA_PICKER_CONFIRM))
            if action.status != "completed":
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            return FlowResult("running", detected.kind, last_safe_step="ANDROID_MEDIA_PICKER")
        if detected.kind == "IG_AVATAR_CROP":
            if not str(profile.get("avatar_file") or "").strip():
                return FlowResult("needs_confirmation", detected.kind, "MISSING_AVATAR")
            if self.driver.find(AVATAR_CROP_DONE) is None:
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            action = self._attempt(
                lambda: self.driver.tap(
                    AVATAR_CROP_DONE,
                    expected_screens=_AFTER_AVATAR_CROP,
                    timeout=12.0,
                )
            )
            if action.status != "completed":
                return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
            return FlowResult("completed", detected.kind, last_safe_step="IG_AVATAR_CROP")
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

    def run(self, profile: dict, *, account_id: str | None = None) -> FlowResult:
        return self._handle_detected(
            self._detect_bounded(),
            dict(profile or {}),
            account_id=account_id,
        )

    def observe_checkpoint(self) -> FlowResult:
        detected = self._detect_bounded()
        if detected.protected:
            return FlowResult("waiting_human", detected.kind, "HUMAN_VERIFICATION_REQUIRED")
        if detected.kind in _CHECKPOINT_SUCCESSORS and detected.automation_allowed:
            return FlowResult("completed", detected.kind, last_safe_step=detected.kind)
        if detected.kind in _CHECKPOINT_RESUMABLE and detected.automation_allowed:
            return FlowResult("running", detected.kind, last_safe_step=detected.kind)
        if detected.kind in {"RATE_LIMITED", "ACTION_BLOCKED"}:
            return FlowResult("retry_pending", detected.kind, detected.kind)
        if detected.kind == "ACCOUNT_DISABLED":
            return FlowResult("error", detected.kind, "ACCOUNT_DISABLED")
        if detected.kind == "NETWORK_ERROR":
            return FlowResult("retry_pending", detected.kind, "NETWORK_ERROR")
        if detected.kind == "UNKNOWN":
            return FlowResult("needs_confirmation", detected.kind, "UI_CHANGED")
        return FlowResult("needs_confirmation", detected.kind, "CHECKPOINT_NOT_CONFIRMED")