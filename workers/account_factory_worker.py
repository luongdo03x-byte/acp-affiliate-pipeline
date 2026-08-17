#!/usr/bin/env python3
"""Minimal Account Factory V2 worker agent using local JSON-lines IPC.

The worker only exposes safe orchestration primitives. It does not capture
credentials, submit platform security challenges, solve OTP/CAPTCHA, or alter
fingerprints/network identity.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone

from core.factory_v2.avd import AvdManager
from core.factory_v2.worker_protocol import CommandLedger, WorkerCommand, WorkerHeartbeat


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class WorkerAgent:
    def __init__(self, worker_id: str, avd_name: str, serial: str, *, avd: AvdManager | None = None):
        self.worker_id = worker_id
        self.avd_name = avd_name
        self.serial = serial
        self.avd = avd or AvdManager()
        self.ledger = CommandLedger()
        self.state = "READY"
        self.current_account_id = None
        self.current_job_id = None
        self.observed_state = None
        self.last_progress_at = _now()
        self.prepared_text = None

    def heartbeat(self) -> dict:
        return WorkerHeartbeat(
            worker_id=self.worker_id,
            adb_serial=self.serial,
            state=self.state,
            current_account_id=self.current_account_id,
            current_job_id=self.current_job_id,
            observed_state=self.observed_state,
            last_progress_at=self.last_progress_at,
        ).to_dict()

    def _foreground_package(self) -> str | None:
        result = self.avd.runner.run(
            [self.avd.adb, "-s", self.serial, "shell", "dumpsys", "window", "windows"],
            20,
        )
        if result.returncode != 0:
            return None
        match = re.search(r"mCurrentFocus=.*? ([A-Za-z0-9_.]+)/", result.stdout)
        return match.group(1) if match else None

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
