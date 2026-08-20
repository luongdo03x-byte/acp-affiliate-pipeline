"""Narrow browser screen detection for Threads OAuth login."""
from __future__ import annotations

from ..detector import DetectedScreen
from ..selectors import normalize_ui_text

BROWSER_LOGIN = "BROWSER_LOGIN"
OAUTH_CONSENT = "OAUTH_CONSENT"
SECURITY_CHALLENGE = "SECURITY_CHALLENGE"
UNKNOWN = "UNKNOWN"

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

        allow_button = any(
            node.clickable
            and normalize_ui_text(node.text or node.content_desc) in {"allow", "authorize"}
            for node in snapshot.nodes
        )
        consent_context = any(marker in joined for marker in _CONSENT_MARKERS)
        if allow_button or consent_context:
            return DetectedScreen(
                OAUTH_CONSENT,
                0.99,
                ("oauth_consent",),
                True,
            )

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

        return DetectedScreen(UNKNOWN, 0.0, (), False)


def build_browser_detector() -> BrowserScreenDetector:
    return BrowserScreenDetector()
