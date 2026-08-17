"""Serial-scoped ADB primitives for one AVD worker."""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass


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

    def foreground(self) -> tuple[str | None, str | None]:
        result = self._run(["shell", "dumpsys", "window", "windows"])
        patterns = (
            r"mCurrentFocus=.*?\s([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)",
            r"mFocusedApp=.*?\s([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)",
        )
        for pattern in patterns:
            match = re.search(pattern, result.stdout or "")
            if match:
                return match.group(1), match.group(2)
        return None, None

    def dump_hierarchy(self) -> str:
        result = self._run(["exec-out", "uiautomator", "dump", "/dev/tty"], timeout=25)
        output = str(result.stdout or "")
        start = output.find("<?xml")
        if start < 0:
            start = output.find("<hierarchy")
        if start < 0:
            raise RuntimeError("UI hierarchy XML missing from adb output")
        end = output.find("</hierarchy>", start)
        if end < 0:
            raise RuntimeError("UI hierarchy XML is incomplete")
        end += len("</hierarchy>")
        return output[start:end]

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

    def open_package(self, package: str) -> None:
        package = str(package or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.]+", package):
            raise ValueError("invalid Android package")
        self._run([
            "shell", "monkey", "-p", package,
            "-c", "android.intent.category.LAUNCHER", "1",
        ])
