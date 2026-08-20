"""Secret-aware browser driver isolated from the general SafeUiDriver."""
from __future__ import annotations

import time

from ..driver import ActionResult
from ..hierarchy import UiHierarchyReader
from ..selectors import normalize_ui_text
from .screens import BROWSER_LOGIN

_USERNAME_HINTS = (
    "username",
    "user name",
    "phone or email",
    "email or phone",
    "email",
)
_LOGIN_BUTTONS = frozenset({"log in", "login", "sign in"})
_CLEAR_KEYSTROKES = 96


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


class BrowserSecretDriver:
    """Types credentials only on a positively recognized two-field login form."""

    def __init__(
        self,
        adb,
        detector,
        *,
        hierarchy_reader=None,
        poll_interval: float = 0.5,
        monotonic=None,
        sleeper=None,
    ):
        self.adb = adb
        self.detector = detector
        self.hierarchy_reader = hierarchy_reader or UiHierarchyReader()
        self.poll_interval = max(0.0, float(poll_interval))
        self._monotonic = monotonic or time.monotonic
        self._sleep = sleeper or time.sleep

    def snapshot(self):
        package, activity = self.adb.foreground()
        return self.hierarchy_reader.parse(
            self.adb.dump_hierarchy(),
            package=package,
            activity=activity,
        )

    def detect_screen(self):
        return self.detector.detect(self.snapshot())

    def wait_for(self, screens, timeout: float):
        expected = frozenset(screens)
        deadline = self._monotonic() + max(0.0, float(timeout))
        last = self.detect_screen()
        while last.kind not in expected and self._monotonic() < deadline:
            self._sleep(self.poll_interval)
            last = self.detect_screen()
        return last

    def _login_nodes(self):
        snapshot = self.snapshot()
        detected = self.detector.detect(snapshot)
        if detected.kind != BROWSER_LOGIN:
            return detected, None, None, None

        edit_nodes = tuple(
            node
            for node in snapshot.nodes
            if node.class_name.endswith("EditText") and node.enabled
        )
        if len(edit_nodes) != 2:
            return detected, None, None, None

        username_candidates = tuple(
            node
            for node in edit_nodes
            if any(hint in _node_text(node) for hint in _USERNAME_HINTS)
        )
        if len(username_candidates) != 1:
            return detected, None, None, None
        username_node = username_candidates[0]
        password_candidates = tuple(node for node in edit_nodes if node is not username_node)
        if len(password_candidates) != 1:
            return detected, None, None, None

        login_buttons = tuple(
            node
            for node in snapshot.nodes
            if node.clickable
            and normalize_ui_text(node.text or node.content_desc) in _LOGIN_BUTTONS
        )
        if len(login_buttons) != 1:
            return detected, None, None, None
        return detected, username_node, password_candidates[0], login_buttons[0]

    def _replace_text(self, node, value: str) -> None:
        self.adb.tap(*node.bounds.center)
        self.adb.keyevent(123)
        for _ in range(_CLEAR_KEYSTROKES):
            self.adb.keyevent(67)
        self.adb.set_text(value)

    def set_username(self, value: str) -> ActionResult:
        detected, username_node, _, _ = self._login_nodes()
        if username_node is None:
            return ActionResult("postcondition_failed", before=detected.kind)
        self._replace_text(username_node, str(value))
        return ActionResult("completed", before=detected.kind, after=BROWSER_LOGIN)

    def set_password(self, value: str) -> ActionResult:
        detected, _, password_node, _ = self._login_nodes()
        if password_node is None:
            return ActionResult("postcondition_failed", before=detected.kind)
        # Never let a low-level ADB exception carry password-related command
        # context back across the worker protocol.
        try:
            self._replace_text(password_node, str(value))
        except (RuntimeError, ValueError):
            return ActionResult("postcondition_failed", before=detected.kind)
        return ActionResult("completed", before=detected.kind, after=BROWSER_LOGIN)

    def tap_login(self) -> ActionResult:
        detected, _, _, login_button = self._login_nodes()
        if login_button is None:
            return ActionResult("postcondition_failed", before=detected.kind)
        self.adb.tap(*login_button.bounds.center)
        return ActionResult("completed", before=detected.kind)
