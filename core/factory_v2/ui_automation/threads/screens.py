"""Threads screen signatures ordered by safety priority."""
from __future__ import annotations

from ..detector import ScreenDetector, ScreenSignature
from ..selectors import Selector
from .selectors import (
    ACCOUNT_PICKER_MARKER,
    BIO_INPUT,
    CONTINUE,
    CONTINUE_WITH_INSTAGRAM,
    DISPLAY_NAME_INPUT,
    FOLLOW_SUGGESTIONS_CLOSE,
    FOLLOW_SUGGESTIONS_MARKER,
    HOME,
    JOIN_THREADS,
    NOTIFICATION_DENY,
    NOTIFICATION_PROMPT,
    PROFILE,
    THREADS_TERMS_MARKER,
)

PACKAGE = "com.instagram.barcelona"
_PERMISSION_PACKAGES = (
    "com.android.permissioncontroller",
    "com.google.android.permissioncontroller",
)


def _text(semantic: str, *values: str) -> Selector:
    return Selector(semantic=semantic, texts=tuple(values))


def _protected(kind: str, semantic: str, *texts: str, priority: int) -> ScreenSignature:
    return ScreenSignature(kind, PACKAGE, (_text(semantic, *texts),), 1, 0.82, True, priority)


def build_threads_detector() -> ScreenDetector:
    signatures = [
        _protected("PASSWORD_REQUIRED", "password_marker", "Password", "Mật khẩu", priority=10),
        _protected("OTP_REQUIRED", "otp_marker", "Enter confirmation code", "Enter code", "Verification code", "Mã xác nhận", "Mã xác minh", priority=11),
        _protected("CAPTCHA_REQUIRED", "captcha_marker", "CAPTCHA", "I'm not a robot", "Verify you're human", priority=12),
        ScreenSignature("THREADS_LEGAL_CONSENT", PACKAGE, (JOIN_THREADS,), 1, 0.99, True, 13),
        ScreenSignature("THREADS_LEGAL_CONSENT", PACKAGE, (THREADS_TERMS_MARKER, CONTINUE), 2, 0.99, True, 14),
        _protected("EMAIL_OR_PHONE_VERIFICATION", "contact_verification", "Confirm your email", "Confirm your phone number", "Verify your email", "Verify your phone number", priority=15),
        _protected("SELFIE_OR_IDENTITY_CHECK", "identity_check", "Take a selfie", "Video selfie", "Confirm your identity", priority=16),
        _protected("SECURITY_CHALLENGE", "security_challenge", "Security check", "Suspicious login attempt", "Help us confirm it's you", priority=17),
        _protected("ACCOUNT_RECOVERY", "account_recovery", "Recover your account", "Account recovery", priority=18),
        _protected("CONSENT_WITH_SECURITY_IMPACT", "security_consent", "Two-factor authentication", "Review security settings", priority=19),
        ScreenSignature("NETWORK_ERROR", PACKAGE, (_text("network_error", "No internet connection", "Network error", "Couldn't refresh"),), 1, 0.95, False, 30),
        ScreenSignature("RATE_LIMITED", PACKAGE, (_text("rate_limited", "Try again later", "Please wait a few minutes"),), 1, 0.95, False, 31),
        ScreenSignature("ACTION_BLOCKED", PACKAGE, (_text("action_blocked", "Action blocked", "We restrict certain activity"),), 1, 0.95, False, 32),
        ScreenSignature("ACCOUNT_DISABLED", PACKAGE, (_text("account_disabled", "Your account has been disabled", "Account disabled"),), 1, 0.99, False, 33),
        ScreenSignature("APP_CRASH", "*", (_text("app_crash", "Threads keeps stopping", "Threads has stopped"),), 1, 0.99, False, 34),
        ScreenSignature("THREADS_ACCOUNT_PICKER", PACKAGE, (ACCOUNT_PICKER_MARKER,), 1, 0.99, False, 50),
        ScreenSignature("THREADS_FOLLOW_SUGGESTIONS", PACKAGE, (FOLLOW_SUGGESTIONS_MARKER, FOLLOW_SUGGESTIONS_CLOSE), 2, 0.99, False, 51),
        ScreenSignature("THREADS_POSTCHECK_OK", PACKAGE, (HOME, PROFILE), 2, 0.99, False, 60),
        ScreenSignature("THREADS_PROFILE_SETUP", PACKAGE, (DISPLAY_NAME_INPUT, BIO_INPUT, CONTINUE), 2, 0.96, False, 80),
        ScreenSignature("THREADS_ONBOARDING", PACKAGE, (CONTINUE_WITH_INSTAGRAM, CONTINUE), 1, 0.94, False, 81),
        ScreenSignature("THREADS_HOME", PACKAGE, (HOME,), 1, 0.96, False, 82),
    ]
    for index, package in enumerate(_PERMISSION_PACKAGES):
        signatures.append(
            ScreenSignature(
                "THREADS_NOTIFICATION_PERMISSION",
                package,
                (NOTIFICATION_PROMPT, NOTIFICATION_DENY),
                2,
                0.99,
                False,
                48 + index,
            )
        )
    return ScreenDetector(signatures)
