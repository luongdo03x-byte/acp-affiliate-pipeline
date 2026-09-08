"""Runner-neutral command transport for Account Factory V2."""
from __future__ import annotations

import json
from urllib.parse import urlsplit

from ..db import now, ulid

from .models import RunnerType
from .worker_protocol import WorkerCommand

_LOCAL_ACTIONS = frozenset({
    "PREPARE_INSTAGRAM",
    "AUTOMATE_INSTAGRAM",
    "AUTOMATE_THREADS",
    "OBSERVE_CHECKPOINT",
    "PREPARE_TEXT",
    "OPEN_PACKAGE",
    "OPEN_URL",
    "REPORT_WAITING_HUMAN",
    "OBSERVE_FOREGROUND",
})


def validate_factory_authorization_url(value: str) -> str:
    url = str(value or "").strip()
    if not url or any(ord(character) < 32 for character in url):
        raise ValueError("invalid authorization URL")
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise ValueError("invalid authorization URL") from exc
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError("authorization URL must use https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("authorization URL must not contain credentials")
    return url


class RunnerGateway:
    def __init__(self, repository, worker_processes):
        self.repo = repository
        self.worker_processes = worker_processes

    def _runner_type(self, job: dict) -> str:
        runner_type = job.get("runner_type")
        if runner_type:
            return str(runner_type)
        worker = self.repo.get_worker(job["worker_id"])
        if worker is None:
            raise KeyError(job["worker_id"])
        return worker.get("runner_type") or RunnerType.REMOTE_AVD.value

    def send(self, job: dict, action: str, payload: dict | None = None) -> dict:
        action = str(action).upper()
        payload = {"job_id": job["id"], **(payload or {})}
        if action == "OPEN_URL":
            payload["url"] = validate_factory_authorization_url(payload.get("url"))
        runner_type = self._runner_type(job)

        if runner_type == RunnerType.REMOTE_AVD.value:
            return self.worker_processes.request(
                job["worker_id"],
                WorkerCommand(
                    command_id=ulid(),
                    action=action,
                    account_id=job["account_id"],
                    payload=payload,
                ),
            )

        if runner_type != RunnerType.LOCAL_DEVICE.value:
            raise ValueError(f"unsupported runner type: {runner_type}")
        if action not in _LOCAL_ACTIONS:
            raise ValueError(f"unsupported local runner action: {action}")

        # The Android command DTO deliberately accepts only scalar values. Keep
        # the wire format small and auditable instead of teaching the phone to
        # accept arbitrary nested command objects.
        profile = payload.pop("profile", None)
        if profile is not None:
            if not isinstance(profile, dict):
                raise ValueError("invalid local runner profile")
            allowed_profile = {
                "username", "display_name", "bio", "signup_contact_type",
                "signup_contact", "birth_date", "avatar_file",
            }
            if set(profile) - allowed_profile:
                raise ValueError("unsupported local runner profile field")
            payload.update({key: value for key, value in profile.items() if value is not None})

        correlation = str(job.get("command_id") or job["id"])
        command_id = f"{correlation}:{action}"
        existing = self.repo.get_runner_command(command_id)
        if existing is None:
            self.repo.create_runner_command({
                "id": command_id,
                "worker_id": job["worker_id"],
                "job_id": job["id"],
                "account_id": job["account_id"],
                "runner_type": RunnerType.LOCAL_DEVICE.value,
                "action": action,
                "payload_json": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                "status": "QUEUED",
                "created_at": now(),
            })
            return {"status": "pending", "command_id": command_id}

        if existing["status"] in {"QUEUED", "DELIVERED"}:
            return {"status": "pending", "command_id": command_id}
        if existing["status"] == "FAILED":
            detail = {}
            try:
                detail = json.loads(existing.get("result_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                pass
            raise RuntimeError(str(detail.get("error_code") or "local runner command failed")[:240])
        if existing["status"] != "COMPLETED":
            raise RuntimeError(f"unexpected local runner command state: {existing['status']}")

        try:
            result = json.loads(existing.get("result_json") or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("local runner returned invalid result") from exc
        if not isinstance(result, dict):
            raise RuntimeError("local runner returned invalid result")
        flow_status = result.pop("flow_status", None)
        flow_detail = {
            key: result.pop(key)
            for key in ("screen", "reason", "last_safe_step")
            if key in result
        }
        response = {
            "status": flow_status or "completed",
            "command_id": command_id,
            **result,
        }
        if flow_detail:
            response["result"] = flow_detail
        return response
