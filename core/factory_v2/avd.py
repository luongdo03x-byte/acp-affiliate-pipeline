"""Android Studio AVD/ADB adapter for Account Factory V2."""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import shlex
import shutil
import subprocess
from urllib.parse import urlsplit

_BROWSER_PACKAGE_RE = re.compile(r"^[A-Za-z0-9_.]+$")


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


def _resolve_android_binary(relative_path: str, fallback: str) -> str:
    home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if home:
        candidate = os.path.join(home, relative_path)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which(fallback) or fallback


def _validate_browser_package(value: str) -> str:
    package = str(value or "").strip()
    if not _BROWSER_PACKAGE_RE.fullmatch(package):
        raise ValueError("invalid browser package")
    return package


class AvdManager:
    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        adb_path: str | None = None,
        emulator_path: str | None = None,
        popen_factory=None,
    ):
        self.runner = runner or CommandRunner()
        self.adb = adb_path or _resolve_android_binary("platform-tools/adb", "adb")
        self.emulator = emulator_path or _resolve_android_binary("emulator/emulator", "emulator")
        self._popen = popen_factory or subprocess.Popen

    def _checked(self, argv: list[str], timeout: int = 20) -> CompletedCommand:
        result = self.runner.run(argv, timeout)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"command failed: {argv[0]}")
        return result

    def list_avds(self) -> list[str]:
        result = self._checked([self.emulator, "-list-avds"], timeout=20)
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def list_online_devices(self) -> list[str]:
        result = self._checked([self.adb, "devices"], timeout=20)
        devices = []
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
        return devices

    def start(self, avd_name: str, port: int):
        if not re.fullmatch(r"[A-Za-z0-9._-]+", avd_name):
            raise ValueError("invalid AVD name")
        if port < 5554 or port > 5680 or port % 2:
            raise ValueError("emulator port must be an even port between 5554 and 5680")
        return self._popen(
            [
                self.emulator,
                "-avd", avd_name,
                "-port", str(port),
                "-memory", "1536",
                "-gpu", "host",
                "-feature", "-Vulkan",
                "-no-snapshot",
                "-noaudio",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def is_boot_completed(self, serial: str) -> bool:
        result = self.runner.run(
            [self.adb, "-s", serial, "shell", "getprop", "sys.boot_completed"],
            15,
        )
        return result.returncode == 0 and result.stdout.strip() == "1"

    def stop(self, serial: str) -> None:
        self._checked([self.adb, "-s", serial, "emu", "kill"], timeout=20)

    def reset_app_session(self, serial: str, app_package: str) -> None:
        package = _validate_browser_package(app_package)
        self._checked(
            [self.adb, "-s", serial, "shell", "pm", "clear", package],
            timeout=20,
        )

    def reset_browser_session(self, serial: str, browser_package: str) -> None:
        self.reset_app_session(serial, browser_package)

    def set_package_enabled(self, serial: str, package: str, enabled: bool) -> None:
        package = _validate_browser_package(package)
        command = "enable" if enabled else "disable-user"
        self._checked(
            [
                self.adb,
                "-s",
                serial,
                "shell",
                "pm",
                command,
                "--user",
                "0",
                package,
            ],
            timeout=20,
        )

    def open_url(
        self,
        serial: str,
        url: str,
        *,
        browser_package: str | None = None,
    ) -> None:
        value = str(url or "").strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("invalid URL")
        try:
            parsed = urlsplit(value)
        except ValueError as exc:
            raise ValueError("invalid URL") from exc
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            raise ValueError("only https URLs are supported")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("URL credentials are not supported")
        argv = [
            self.adb, "-s", serial, "shell", "am", "start",
            "-a", "android.intent.action.VIEW", "-d", shlex.quote(value),
        ]
        if browser_package is not None:
            argv.extend(["-p", _validate_browser_package(browser_package)])
        self._checked(argv, timeout=20)

    def open_package(self, serial: str, package: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.]+", package):
            raise ValueError("invalid Android package")
        self._checked([
            self.adb, "-s", serial, "shell", "monkey", "-p", package,
            "-c", "android.intent.category.LAUNCHER", "1",
        ], timeout=20)
