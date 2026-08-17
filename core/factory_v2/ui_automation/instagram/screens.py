"""Instagram screen signatures ordered by safety priority."""
from __future__ import annotations

from ..detector import ScreenDetector, ScreenSignature
from ..selectors import Selector
from .selectors import BIO_INPUT, CONTINUE, DISPLAY_NAME_INPUT, HOME, PROFILE, SIGN_UP, USERNAME_INPUT

PACKAGE = "com.instagram.android"


def _text(semantic: str, *values: str) -> Selector:
    return Selector(semantic=semantic, texts=tuple(values))


def _protected(kind: str, semantic: str, *texts: str, priority: int) -> ScreenSignature:
    return ScreenSignature(kind, PACKAGE, (_text(semantic, *texts),), 1, 0.82, True, priority)


def build_instagram_detector() -> ScreenDetector:
    return ScreenDetector([
        _protected("PASSWORD_REQUIRED", "password_marker", "Password", "Create a password", "Mật khẩu", priority=10),
        _protected("OTP_REQUIRED", "otp_marker", "Enter confirmation code", "Enter code", "Verification code", "Mã xác nhận", "Mã xác minh", priority=11),
        _protected("CAPTCHA_REQUIRED", "captcha_marker", "CAPTCHA", "I'm not a robot", "Verify you're human", priority=12),
        _protected("EMAIL_OR_PHONE_VERIFICATION", "contact_verification", "Confirm your email", "Confirm your phone number", "Verify your email", "Verify your phone number", "Mobile number or email", "Email address", "Phone number", priority=13),
        _protected("SELFIE_OR_IDENTITY_CHECK", "identity_check", "Take a selfie", "Video selfie", "Confirm your identity", priority=14),
        _protected("SECURITY_CHALLENGE", "security_challenge", "Security check", "Suspicious login attempt", "Help us confirm it's you", priority=15),
        _protected("ACCOUNT_RECOVERY", "account_recovery", "Recover your account", "Account recovery", priority=16),
        _protected("CONSENT_WITH_SECURITY_IMPACT", "security_consent", "Two-factor authentication", "Review security settings", priority=17),
        ScreenSignature("NETWORK_ERROR", PACKAGE, (_text("network_error", "No internet connection", "Network error", "Couldn't refresh feed"),), 1, 0.95, False, 30),
        ScreenSignature("RATE_LIMITED", PACKAGE, (_text("rate_limited", "Try again later", "Please wait a few minutes"),), 1, 0.95, False, 31),
        ScreenSignature("ACTION_BLOCKED", PACKAGE, (_text("action_blocked", "Action blocked", "We restrict certain activity"),), 1, 0.95, False, 32),
        ScreenSignature("ACCOUNT_DISABLED", PACKAGE, (_text("account_disabled", "Your account has been disabled", "Account disabled"),), 1, 0.99, False, 33),
        ScreenSignature("APP_CRASH", "*", (_text("app_crash", "Instagram keeps stopping", "Instagram has stopped"),), 1, 0.99, False, 34),
        ScreenSignature("IG_POSTCHECK_OK", PACKAGE, (HOME, PROFILE), 2, 0.99, False, 60),
        ScreenSignature("IG_PROFILE_SETUP", PACKAGE, (USERNAME_INPUT, DISPLAY_NAME_INPUT, BIO_INPUT, CONTINUE), 2, 0.96, False, 80),
        ScreenSignature("IG_SIGNUP_ENTRY", PACKAGE, (SIGN_UP, CONTINUE), 1, 0.94, False, 81),
        ScreenSignature("IG_HOME", PACKAGE, (HOME,), 1, 0.96, False, 82),
    ])
