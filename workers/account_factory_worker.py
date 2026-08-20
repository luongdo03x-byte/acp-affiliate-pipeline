#!/usr/bin/env python3
"""Account Factory V2 AVD worker agent using local JSON-lines IPC.

The worker exposes only fail-closed orchestration/UI primitives. It never
captures or automates passwords, OTP/CAPTCHA, identity/security challenges,
or Threads publishing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from core.factory_v2.avatar_pool import (
    configured_avatar_root,
    resolve_avatar_source,
    validate_avatar_reference,
)
from core.factory_v2.avd import AvdManager
from core.factory_v2.ui_automation.adb import AdbClient
from core.factory_v2.ui_automation.driver import SafeUiDriver
from core.factory_v2.ui_automation.instagram.flow import InstagramFlow
from core.factory_v2.ui_automation.instagram.screens import build_instagram_detector
from core.factory_v2.ui_automation.threads.flow import ThreadsFlow
from core.factory_v2.ui_automation.threads.screens import build_threads_detector
from core.factory_v2.worker_protocol import CommandLedger, WorkerCommand, WorkerHeartbeat


_INSTAGRAM_PACKAGE = "com.instagram.android"
_THREADS_PACKAGE = "com.instagram.barcelona"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_AVATAR_DEVICE_PATH = "/sdcard/Pictures/ACP/avatar.jpg"
_APPROVED_PROFILE_KEYS = (
    "username",
    "display_name",
    "bio",
    "signup_contact_type",
    "signup_contact",
    "birth_date",
    "avatar_file",
)
_ALLOWED_CONTACT_TYPES = frozenset({"phone", "email"})
_USERNAME_UPDATE_RE = re.compile(r"^[a-z0-9._]{1,30}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_text(value, *, max_length: int = 120) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:max_length] if text else None


def _clean_profile_text(value, key: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        raise ValueError(f"invalid profile field: {key}")
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise ValueError(f"invalid profile field: {key}")
    return text


def _safe_birth_date(value) -> str | None:
    text = _clean_profile_text(value, "birth_date", max_length=10)
    if text is None:
        return None
    try:
        born = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("invalid profile field: birth_date") from exc
    today = date.today()
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    if born > today or age < 18:
        raise ValueError("invalid profile field: birth_date")
    return born.isoformat()


def _avatar_root() -> Path:
    return configured_avatar_root(_REPO_ROOT)


def _safe_avatar_file(value) -> str | None:
    text = _clean_profile_text(value, "avatar_file", max_length=300)
    if text is None:
        return None
    try:
        return validate_avatar_reference(
            text,
            repo_root=_REPO_ROOT,
            avatar_root=_avatar_root(),
        )
    except ValueError as exc:
        raise ValueError("invalid profile field: avatar_file") from exc


def _safe_profile(payload: dict) -> dict[str, str]:
    profile = payload.get("profile")
    if not isinstance(profile, dict):
        return {}

    clean: dict[str, str] = {}
    for key in ("username", "display_name", "bio"):
        text = _clean_profile_text(profile.get(key), key, max_length=500)
        if text is not None:
            clean[key] = text

    contact_type = _clean_profile_text(
        profile.get("signup_contact_type"), "signup_contact_type", max_length=5
    )
    if contact_type is not None:
        contact_type = contact_type.lower()
        if contact_type not in _ALLOWED_CONTACT_TYPES:
            raise ValueError("invalid profile field: signup_contact_type")
        contact = _clean_profile_text(
            profile.get("signup_contact"), "signup_contact", max_length=320
        )
        if contact is None:
            raise ValueError("invalid profile field: signup_contact")
        clean["signup_contact_type"] = contact_type
        clean["signup_contact"] = contact
    elif profile.get("signup_contact") not in {None, ""}:
        raise ValueError("invalid profile field: signup_contact_type")

    birth_date = _safe_birth_date(profile.get("birth_date"))
    if birth_date is not None:
        clean["birth_date"] = birth_date

    avatar_file = _safe_avatar_file(profile.get("avatar_file"))
    if avatar_file is not None:
        clean["avatar_file"] = avatar_file

    return clean


def _safe_profile_updates(value) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"username"}:
        raise ValueError("invalid profile_updates")
    username = _clean_profile_text(value.get("username"), "username", max_length=30)
    if username is None or _USERNAME_UPDATE_RE.fullmatch(username) is None:
        raise ValueError("invalid profile_updates")
    return {"username": username}


class WorkerAgent:
    def __init__(
        self,
        worker_id: str,
        avd_name: str,
        serial: str,
        *,
        avd: AvdManager | None = None,
        instagram_flow=None,
        threads_flow=None,
        adb_client=None,
    ):
        self.worker_id = worker_id
        self.avd_name = avd_name
        self.serial = serial
        self.avd = avd or AvdManager()
        self.adb_client = adb_client or AdbClient(
            serial, adb_path=self.avd.adb, runner=self.avd.runner
        )
        self.ledger = CommandLedger()
        self.state = "READY"
        self.current_account_id = None
        self.current_job_id = None
        self.observed_state = None
        self.last_progress_at = _now()
        self.prepared_text = None
        self.flow = None
        self.last_known_screen = None
        self.last_safe_step = None

        if instagram_flow is None:
            instagram_flow = InstagramFlow(
                SafeUiDriver(self.adb_client, build_instagram_detector())
            )
        if threads_flow is None:
            threads_flow = ThreadsFlow(
                SafeUiDriver(self.adb_client, build_threads_detector())
            )
        self.instagram_flow = instagram_flow
        self.threads_flow = threads_flow

    def heartbeat(self) -> dict:
        heartbeat = WorkerHeartbeat(
            worker_id=self.worker_id,
            adb_serial=self.serial,
            state=self.state,
            current_account_id=self.current_account_id,
            current_job_id=self.current_job_id,
            observed_state=self.last_known_screen or self.observed_state,
            last_progress_at=self.last_progress_at,
        ).to_dict()
        heartbeat.update({
            "flow": self.flow,
            "last_known_screen": self.last_known_screen,
            "last_safe_step": self.last_safe_step,
        })
        return heartbeat

    def _foreground_package(self) -> str | None:
        result = self.avd.runner.run(
            [self.avd.adb, "-s", self.serial, "shell", "dumpsys", "window", "windows"],
            20,
        )
        if result.returncode != 0:
            return None
        match = re.search(r"mCurrentFocus=.*? ([A-Za-z0-9_.]+)/", result.stdout)
        return match.group(1) if match else None

    def _stage_avatar(self, profile: dict[str, str]) -> None:
        avatar_file = profile.get("avatar_file")
        if not avatar_file:
            return
        try:
            source = resolve_avatar_source(
                avatar_file,
                repo_root=_REPO_ROOT,
                avatar_root=_avatar_root(),
            )
        except ValueError as exc:
            raise ValueError("invalid profile field: avatar_file") from exc
        if not source.is_file():
            raise ValueError("invalid profile field: avatar_file")
        self.adb_client.push_file(source, _AVATAR_DEVICE_PATH)

    def _flow_response(self, flow_name: str, result) -> dict:
        screen = _safe_text(getattr(result, "screen", None)) or "UNKNOWN"
        reason = _safe_text(getattr(result, "reason", None))
        safe_step = _safe_text(getattr(result, "last_safe_step", None))
        profile_updates = _safe_profile_updates(getattr(result, "profile_updates", None))
        status = str(getattr(result, "status", "needs_confirmation"))
        if status not in {
            "running", "waiting_human", "completed",
            "needs_confirmation", "retry_pending", "error",
        }:
            status = "needs_confirmation"
            reason = "INVALID_FLOW_RESULT"

        self.flow = flow_name
        self.last_known_screen = screen
        if safe_step:
            self.last_safe_step = safe_step
        self.observed_state = screen
        if status == "waiting_human":
            self.state = "WAITING_HUMAN"
        elif status == "error":
            self.state = "ERROR"
        elif status in {"needs_confirmation", "retry_pending"}:
            self.state = "RECOVERING"
        else:
            self.state = "RUNNING"
        self.last_progress_at = _now()
        return {
            "ok": True,
            "status": status,
            "result": {
                "screen": screen,
                "reason": reason,
                "last_safe_step": self.last_safe_step,
                "profile_updates": profile_updates,
            },
        }

    def _prepare_instagram(self) -> dict:
        self.instagram_flow.driver.open_package(_INSTAGRAM_PACKAGE)
        detected = self.instagram_flow.driver.detect_screen()
        self.flow = "instagram"
        self.last_known_screen = _safe_text(getattr(detected, "kind", None)) or "UNKNOWN"
        self.observed_state = self.last_known_screen
        self.state = "RUNNING"
        self.last_progress_at = _now()
        return {
            "ok": True,
            "status": "completed",
            "result": {
                "screen": self.last_known_screen,
                "reason": None,
                "last_safe_step": self.last_safe_step,
            },
        }

    def execute(self, command: WorkerCommand) -> dict:
        def run_action():
            action = command.action.upper()
            self.current_account_id = command.account_id or self.current_account_id
            if command.payload.get("job_id"):
                self.current_job_id = str(command.payload["job_id"])

            if action == "HEARTBEAT":
                return {"ok": True, "heartbeat": self.heartbeat()}
            if action == "OPEN_URL":
                self.avd.open_url(self.serial, str(command.payload["url"]))
                self.last_progress_at = _now()
                return {"ok": True}
            if action == "OPEN_PACKAGE":
                self.avd.open_package(self.serial, str(command.payload["package"]))
                self.last_progress_at = _now()
                return {"ok": True}
            if action == "PREPARE_TEXT":
                text = str(command.payload.get("text") or "")
                if len(text) > 500:
                    raise ValueError("prepared text exceeds 500 characters")
                self.prepared_text = text
                self.last_progress_at = _now()
                return {"ok": True, "prepared": True}
            if action == "REPORT_WAITING_HUMAN":
                self.state = "WAITING_HUMAN"
                self.observed_state = str(command.payload.get("checkpoint") or "WAITING_HUMAN")
                self.last_progress_at = _now()
                return {"ok": True, "heartbeat": self.heartbeat()}
            if action == "OBSERVE_FOREGROUND":
                package = self._foreground_package()
                self.observed_state = package
                self.last_progress_at = _now()
                return {"ok": True, "package": package}

            if action == "PREPARE_INSTAGRAM":
                return self._prepare_instagram()
            if action == "AUTOMATE_INSTAGRAM":
                profile = _safe_profile(command.payload)
                self._stage_avatar(profile)
                return self._flow_response(
                    "instagram",
                    self.instagram_flow.run(profile, account_id=command.account_id),
                )
            if action == "AUTOMATE_THREADS":
                self.threads_flow.driver.open_package(_THREADS_PACKAGE)
                return self._flow_response(
                    "threads",
                    self.threads_flow.run(_safe_profile(command.payload)),
                )
            if action == "OBSERVE_CHECKPOINT":
                flow_name = str(command.payload.get("flow") or self.flow or "").strip().lower()
                if flow_name == "instagram":
                    result = self.instagram_flow.observe_checkpoint()
                elif flow_name == "threads":
                    result = self.threads_flow.observe_checkpoint()
                else:
                    raise ValueError("unsupported checkpoint flow")
                return self._flow_response(flow_name, result)

            raise ValueError(f"unsupported worker action: {command.action}")

        return self.ledger.execute(command.command_id, run_action)


def _parse_command(line: str) -> WorkerCommand:
    data = json.loads(line)
    return WorkerCommand(
        command_id=str(data["command_id"]),
        action=str(data["action"]),
        account_id=data.get("account_id"),
        payload=dict(data.get("payload") or {}),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--avd-name", required=True)
    parser.add_argument("--serial", required=True)
    args = parser.parse_args()
    agent = WorkerAgent(args.worker_id, args.avd_name, args.serial)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            response = agent.execute(_parse_command(line))
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
