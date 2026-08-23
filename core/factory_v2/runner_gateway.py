"""Runner-neutral command transport for Account Factory V2."""
from __future__ import annotations

import json
from urllib.parse import urlsplit

from core.db import now, ulid

from .account_credentials import CredentialDecryptError, get_account_password
from .models import RunnerType
from .worker_protocol import WorkerCommand

_LOCAL_ACTIONS = frozenset({
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

    def send_transient_login_secret(
        self,
        job: dict,
        *,
        username: str,
        password: str,
    ) -> dict:
        if self._runner_type(job) != RunnerType.REMOTE_AVD.value:
            raise ValueError("transient login secret is REMOTE_AVD only")
        username = str(username or "").strip()
        password = str(password or "")
        if not username or not password:
            raise ValueError("login credential is incomplete")
        return self.worker_processes.request(
            job["worker_id"],
            WorkerCommand(
                command_id=ulid(),
                action="TRANSIENT_BROWSER_LOGIN",
                account_id=job["account_id"],
                payload={
                    "job_id": job["id"],
                    "username": username,
                    "password": password,
                },
            ),
        )

    def _restore_oauth_apps_after_open_failure(self, job: dict) -> None:
        self.worker_processes.request(
            job["worker_id"],
            WorkerCommand(
                command_id=ulid(),
                action="RESTORE_OAUTH_APPS",
                account_id=job["account_id"],
                payload={"job_id": job["id"]},
            ),
        )

    def _remote_oauth_login_after_open(self, job: dict, opened: dict) -> dict:
        if not isinstance(opened, dict) or opened.get("ok") is False:
            return opened
        status = str(opened.get("status") or "").strip().lower()
        if status in {"waiting_human", "needs_confirmation", "retry_pending", "error"}:
            return opened
        account = self.repo.get_account(job["account_id"])
        if account is None:
            return opened
        try:
            password = get_account_password(self.repo.conn, account["id"])
        except CredentialDecryptError:
            self._restore_oauth_apps_after_open_failure(job)
            raise
        if password is None:
            return opened
        return self.send_transient_login_secret(
            job,
            username=account["username"],
            password=password,
        )

    def send(self, job: dict, action: str, payload: dict | None = None) -> dict:
        action = str(action).upper()
        payload = {"job_id": job["id"], **(payload or {})}
        if action == "OPEN_URL":
            payload["url"] = validate_factory_authorization_url(payload.get("url"))
        runner_type = self._runner_type(job)

        if runner_type == RunnerType.REMOTE_AVD.value:
            response = self.worker_processes.request(
                job["worker_id"],
                WorkerCommand(
                    command_id=ulid(),
                    action=action,
                    account_id=job["account_id"],
                    payload=payload,
                ),
            )
            if action == "OPEN_URL":
                return self._remote_oauth_login_after_open(job, response)
            return response

        if runner_type != RunnerType.LOCAL_DEVICE.value:
            raise ValueError(f"unsupported runner type: {runner_type}")
        if action not in _LOCAL_ACTIONS:
            raise ValueError(f"unsupported local runner action: {action}")

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
        return {"status": "completed", "command_id": command_id, **result}
