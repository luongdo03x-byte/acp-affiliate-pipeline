"""Serial-scoped ADB primitives for one AVD worker."""
from __future__ import annotations

import fcntl
import hashlib
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CompletedCommand:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner:
    def run(self, argv: list[str], timeout: int) -> CompletedCommand:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CompletedCommand(completed.returncode, completed.stdout, completed.stderr)


class AdbClient:
    _AVATAR_DEVICE_DIR = "/sdcard/Pictures/ACP"
    _AVATAR_DEVICE_PATH = "/sdcard/Pictures/ACP/avatar.jpg"
    _AVATAR_DEVICE_PATHS = frozenset({
        "/sdcard/Pictures/ACP/avatar.jpg",
        "/sdcard/Pictures/ACP/avatar.jpeg",
        "/sdcard/Pictures/ACP/avatar.png",
        "/sdcard/Pictures/ACP/avatar.webp",
    })
    _HIERARCHY_DEVICE_PATH = "/sdcard/acp-window.xml"

    def __init__(self, serial: str, *, adb_path: str | None = None, runner=None):
        serial = str(serial or "").strip()
        if not serial or any(character.isspace() for character in serial):
            raise ValueError("invalid ADB serial")
        self.serial = serial
        self.adb = adb_path or shutil.which("adb") or "adb"
        self.runner = runner or CommandRunner()

    def _run(self, args: list[str], timeout: int = 20):
        result = self.runner.run([self.adb, "-s", self.serial, *args], timeout)
        if result.returncode != 0:
            raise RuntimeError(str(result.stderr or "ADB command failed").strip())
        return result

    @staticmethod
    def _foreground_from_output(output: str, patterns: tuple[str, ...]):
        for pattern in patterns:
            match = re.search(pattern, output or "")
            if match:
                return match.group(1), match.group(2)
        return None

    def foreground(self) -> tuple[str | None, str | None]:
        result = self._run(["shell", "dumpsys", "window", "windows"])
        window_match = self._foreground_from_output(
            result.stdout or "",
            (
                r"mCurrentFocus=.*?\s([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)",
                r"mFocusedApp=.*?\s([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)",
            ),
        )
        if window_match is not None:
            return window_match

        result = self._run(["shell", "dumpsys", "activity", "activities"])
        activity_match = self._foreground_from_output(
            result.stdout or "",
            (
                r"topResumedActivity=.*?\s([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)",
                r"mCurrentFocus=.*?\s([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)",
                r"mFocusedApp=.*?\s([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)",
                r"ResumedActivity:.*?\s([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)",
                r"Resumed:.*?\s([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)",
            ),
        )
        if activity_match is not None:
            return activity_match
        return None, None

    @staticmethod
    def _hierarchy_xml_from_output(output: str) -> str | None:
        output = str(output or "")
        start = output.find("<?xml")
        if start < 0:
            start = output.find("<hierarchy")
        if start < 0:
            return None
        end = output.find("</hierarchy>", start)
        if end < 0:
            raise RuntimeError("UI hierarchy XML is incomplete")
        end += len("</hierarchy>")
        return output[start:end]

    def _hierarchy_lock_path(self) -> Path:
        token = hashlib.sha256(self.serial.encode("utf-8")).hexdigest()[:16]
        return Path(tempfile.gettempdir()) / f"acp-uiautomator-{token}.lock"

    def dump_hierarchy(self) -> str:
        with self._hierarchy_lock_path().open("a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                return self._dump_hierarchy_unlocked()
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _dump_hierarchy_unlocked(self) -> str:
        try:
            result = self._run(["exec-out", "uiautomator", "dump", "/dev/tty"], timeout=25)
            xml = self._hierarchy_xml_from_output(result.stdout)
            if xml is not None:
                return xml
        except RuntimeError:
            pass

        path = self._HIERARCHY_DEVICE_PATH
        try:
            self._run(["shell", "uiautomator", "dump", path], timeout=25)
            result = self._run(["exec-out", "cat", path], timeout=20)
            xml = self._hierarchy_xml_from_output(result.stdout)
            if xml is None:
                raise RuntimeError("UI hierarchy XML missing from adb fallback output")
            return xml
        finally:
            try:
                self._run(["shell", "rm", "-f", path], timeout=10)
            except RuntimeError:
                pass

    def tap(self, x: int, y: int) -> None:
        self._run(["shell", "input", "tap", str(int(x)), str(int(y))])

    def set_text(self, text: str) -> None:
        value = str(text)
        if len(value) > 500:
            raise ValueError("input text exceeds 500 characters")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("input text contains control characters")
        encoded = value.replace("%", "%25").replace(" ", "%s")
        self._run(["shell", "input", "text", encoded])

    def keyevent(self, keycode: int) -> None:
        self._run(["shell", "input", "keyevent", str(int(keycode))])

    def back(self) -> None:
        self.keyevent(4)

    def home(self) -> None:
        self.keyevent(3)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        duration = max(1, min(int(duration_ms), 10_000))
        self._run([
            "shell", "input", "swipe",
            str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)), str(duration),
        ])

    def push_file(self, source, destination: str) -> None:
        source_path = Path(source).resolve()
        if not source_path.is_file():
            raise ValueError("ADB push source must be an existing file")
        destination = str(destination or "").strip()
        if destination not in self._AVATAR_DEVICE_PATHS:
            raise ValueError("unsupported ADB push destination")
        self._run(["shell", "mkdir", "-p", self._AVATAR_DEVICE_DIR])
        self._run(["push", str(source_path), destination], timeout=60)
        self._run([
            "shell", "am", "broadcast",
            "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
            "-d", f"file://{destination}",
        ])

    def open_package(self, package: str) -> None:
        package = str(package or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.]+", package):
            raise ValueError("invalid Android package")
        self._run([
            "shell", "monkey", "-p", package,
            "-c", "android.intent.category.LAUNCHER", "1",
        ])
