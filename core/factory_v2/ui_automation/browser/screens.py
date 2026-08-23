"""Narrow browser screen detection for Threads OAuth login."""
from __future__ import annotations

from ..detector import DetectedScreen
from ..selectors import normalize_ui_text

CHROME_FIRST_RUN = "CHROME_FIRST_RUN"
CHROME_AD_PRIVACY = "CHROME_AD_PRIVACY"
BROWSER_LOGIN = "BROWSER_LOGIN"
OAUTH_CONSENT = "OAUTH_CONSENT"
SECURITY_CHALLENGE = "SECURITY_CHALLENGE"
UNKNOWN = "UNKNOWN"

_BROWSER_PACKAGE = "com.android.chrome"
_CHROME_FIRST_RUN_TITLE = "welcome to chrome"
_CHROME_FIRST_RUN_SKIP = "use without an account"
_CHROME_AD_PRIVACY_TITLE = "enhanced ad privacy in chrome"
_CHROME_AD_PRIVACY_CONFIRM = "got it"
_USERNAME_HINTS = (
    "username",
    "user name",
    "phone or email",
    "email or phone",
    "email",
)
_LOGIN_BUTTONS = frozenset({"log in", "login", "sign in"})
_CHALLENGE_MARKERS = (
    "confirm it's you",
    "confirm it’s you",
    "verify it's you",
    "verify it’s you",
    "security check",
    "suspicious login",
)
_CONSENT_MARKERS = (
    "allow acp",
    "allow access",
    "authorize",
    "permissions",
)


def _node_text(node) -> str:
    return " ".join(
        part
        for part in (
            normalize_ui_text(node.text),
            normalize_ui_text(node.content_desc),
            normalize_ui_text(node.resource_id),
        )
        if part
    )


class BrowserScreenDetector:
    def detect(self, snapshot) -> DetectedScreen:
        if str(snapshot.package or "").strip() != _BROWSER_PACKAGE:
            return DetectedScreen(UNKNOWN, 0.0, (), False)

        node_texts = tuple(_node_text(node) for node in snapshot.nodes)
        joined = " | ".join(node_texts)

        for marker in _CHALLENGE_MARKERS:
            if marker in joined:
                return DetectedScreen(
                    SECURITY_CHALLENGE,
                    0.99,
                    ("security_challenge",),
                    True,
                )

        first_run_skip = tuple(
            node
            for node in snapshot.nodes
            if node.clickable
            and node.enabled
            and normalize_ui_text(node.text or node.content_desc) == _CHROME_FIRST_RUN_SKIP
        )
        first_run_context = any(
            normalize_ui_text(node.text or node.content_desc) == _CHROME_FIRST_RUN_TITLE
            for node in snapshot.nodes
        )
        if first_run_context and len(first_run_skip) == 1:
            return DetectedScreen(
                CHROME_FIRST_RUN,
                0.99,
                ("welcome_to_chrome", "use_without_account"),
                False,
            )

        privacy_confirm = tuple(
            node
            for node in snapshot.nodes
            if node.clickable
            and node.enabled
            and normalize_ui_text(node.text or node.content_desc) == _CHROME_AD_PRIVACY_CONFIRM
        )
        privacy_context = any(
            normalize_ui_text(node.text or node.content_desc) == _CHROME_AD_PRIVACY_TITLE
            for node in snapshot.nodes
        )
        if privacy_context and len(privacy_confirm) == 1:
            return DetectedScreen(
                CHROME_AD_PRIVACY,
                0.99,
                ("enhanced_ad_privacy", "got_it"),
                False,
            )

        # The login form has a much stronger signature than generic consent text:
        # exactly two enabled EditTexts plus a username hint and an exact login
        # button. Check it before consent so harmless Chrome/page metadata such as
        # resource ids containing "permissions" cannot suppress credential entry.
        edit_nodes = tuple(
            node
            for node in snapshot.nodes
            if node.class_name.endswith("EditText") and node.enabled
        )
        username_hint = any(
            any(hint in text for hint in _USERNAME_HINTS)
            for text in node_texts
        )
        login_button = any(
            node.clickable
            and normalize_ui_text(node.text or node.content_desc) in _LOGIN_BUTTONS
            for node in snapshot.nodes
        )
        if len(edit_nodes) == 2 and username_hint and login_button:
            return DetectedScreen(
                BROWSER_LOGIN,
                0.99,
                ("username_field", "password_field", "login_button"),
                False,
            )

        allow_button = any(
            node.clickable
            and node.enabled
            and normalize_ui_text(node.text or node.content_desc) in {"allow", "authorize"}
            for node in snapshot.nodes
        )
        consent_context = any(marker in joined for marker in _CONSENT_MARKERS)
        if allow_button and consent_context:
            return DetectedScreen(
                OAUTH_CONSENT,
                0.99,
                ("oauth_consent",),
                True,
            )

        return DetectedScreen(UNKNOWN, 0.0, (), False)


def build_browser_detector() -> BrowserScreenDetector:
    return BrowserScreenDetector()
