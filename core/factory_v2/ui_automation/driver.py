"""Verified, fail-closed UI actions built on sanitized snapshots."""
from __future__ import annotations

from dataclasses import dataclass
import time

from .detector import DetectedScreen, ScreenDetector
from .hierarchy import UiHierarchyReader, UiNode, UiSnapshot
from .selectors import Selector

_PROTECTED_TEXT_SEMANTICS = frozenset({"password", "otp", "verification_code", "recovery_code"})
_APPROVED_TEXT_SEMANTICS = frozenset({"username", "display_name", "bio", "signup_contact"})
_APPROVED_PACKAGES = frozenset({"com.instagram.android", "com.instagram.barcelona"})


@dataclass(frozen=True)
class ActionResult:
    status: str
    before: str | None = None
    after: str | None = None


class SafeUiDriver:
    def __init__(self, adb, detector: ScreenDetector, *, hierarchy_reader: UiHierarchyReader | None = None, poll_interval: float = 0.5, monotonic=None, sleeper=None):
        self.adb = adb
        self.detector = detector
        self.hierarchy_reader = hierarchy_reader or UiHierarchyReader()
        self.poll_interval = max(0.0, float(poll_interval))
        self._monotonic = monotonic or time.monotonic
        self._sleep = sleeper or time.sleep

    def snapshot(self) -> UiSnapshot:
        package, activity = self.adb.foreground()
        return self.hierarchy_reader.parse(self.adb.dump_hierarchy(), package=package, activity=activity)

    def detect_screen(self) -> DetectedScreen:
        return self.detector.detect(self.snapshot())

    def find(self, selector: Selector) -> UiNode | None:
        return selector.find(self.snapshot())

    def tap(self, selector: Selector, *, expected_screens: tuple[str, ...] = (), timeout: float = 8.0) -> ActionResult:
        snapshot = self.snapshot()
        before = self.detector.detect(snapshot).kind
        node = selector.find(snapshot)
        if node is None:
            return ActionResult("not_found", before=before)
        self.adb.tap(*node.bounds.center)
        if not expected_screens:
            return ActionResult("completed", before=before)
        detected = self.wait_for(expected_screens, timeout)
        if detected.kind in expected_screens:
            return ActionResult("completed", before=before, after=detected.kind)
        return ActionResult("postcondition_failed", before=before, after=detected.kind)

    def set_text(self, selector: Selector, value: str) -> ActionResult:
        semantic = str(selector.semantic or "").strip().lower()
        if semantic in _PROTECTED_TEXT_SEMANTICS:
            raise ValueError("protected field automation is disabled")
        if semantic not in _APPROVED_TEXT_SEMANTICS:
            raise ValueError("text field automation is not approved")
        value = str(value)
        snapshot = self.snapshot()
        before = self.detector.detect(snapshot).kind
        node = selector.find(snapshot)
        if node is None:
            return ActionResult("not_found", before=before)
        if node.text == value:
            return ActionResult("noop", before=before, after=before)
        self.adb.tap(*node.bounds.center)
        self.adb.keyevent(123)
        for _ in range(min(len(node.text), 500)):
            self.adb.keyevent(67)
        self.adb.set_text(value)
        after_snapshot = self.snapshot()
        after_node = selector.find(after_snapshot)
        after_kind = self.detector.detect(after_snapshot).kind
        if after_node is not None and after_node.text == value:
            return ActionResult("completed", before=before, after=after_kind)
        return ActionResult("postcondition_failed", before=before, after=after_kind)

    def wait_for(self, screens: tuple[str, ...], timeout: float) -> DetectedScreen:
        expected = frozenset(screens)
        deadline = self._monotonic() + max(0.0, float(timeout))
        last = DetectedScreen("UNKNOWN", 0.0, (), False)
        while True:
            last = self.detect_screen()
            if last.kind in expected:
                return last
            if self._monotonic() >= deadline:
                return last
            self._sleep(self.poll_interval)

    def open_package(self, package: str) -> None:
        package = str(package or "").strip()
        if package not in _APPROVED_PACKAGES:
            raise ValueError("unsupported UI automation package")
        self.adb.open_package(package)
