"""Local stdio transport for one independent Account Factory worker per AVD."""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import secrets
import subprocess
import sys
from typing import Callable, Mapping

from .worker_protocol import WorkerCommand


_ALLOWED_ENV = (
    "PATH", "HOME", "LANG", "LC_ALL", "ANDROID_HOME", "ANDROID_SDK_ROOT",
    "ACP_AVATAR_DIR",
)
_UI_ACTIONS = frozenset({
    "PREPARE_INSTAGRAM",
    "AUTOMATE_INSTAGRAM",
    "OBSERVE_CHECKPOINT",
    "AUTOMATE_THREADS",
    "TRANSIENT_BROWSER_LOGIN",
    "OPEN_URL",
})
_UI_RESPONSE_TIMEOUT_SECONDS = 60.0


def _readline_with_timeout(stream, timeout: float) -> str:
    ready, _, _ = select.select([stream], [], [], timeout)
    if not ready:
        raise TimeoutError("worker response timed out")
    return stream.readline()


class WorkerProcessManager:
    """Own worker child processes without forwarding ACP/provider secrets."""

    def __init__(
        self,
        *,
        popen_factory=subprocess.Popen,
        line_reader: Callable | None = None,
        base_env: Mapping[str, str] | None = None,
        response_timeout_seconds: float = 10.0,
    ):
        self.popen_factory = popen_factory
        self.line_reader = line_reader or _readline_with_timeout
        self.base_env = dict(os.environ if base_env is None else base_env)
        self.response_timeout_seconds = max(0.1, float(response_timeout_seconds))
        self.repo_root = Path(__file__).resolve().parents[2]
        self.worker_script = self.repo_root / "workers" / "account_factory_worker.py"
        self.processes: dict[str, object] = {}

    def _worker_env(self) -> dict[str, str]:
        env = {name: self.base_env[name] for name in _ALLOWED_ENV if self.base_env.get(name)}
        existing_pythonpath = self.base_env.get("PYTHONPATH", "")
        safe_paths = [str(self.repo_root)]
        if existing_pythonpath:
            safe_paths.extend(
                path for path in existing_pythonpath.split(os.pathsep)
                if path and path.startswith(str(self.repo_root))
            )
        env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(safe_paths))
        return env

    def is_running(self, worker_id: str) -> bool:
        process = self.processes.get(worker_id)
        return process is not None and process.poll() is None

    def start(self, worker_id: str, avd_name: str, serial: str):
        if self.is_running(worker_id):
            return self.processes[worker_id]
        argv = [
            sys.executable,
            str(self.worker_script),
            "--worker-id", worker_id,
            "--avd-name", avd_name,
            "--serial", serial,
        ]
        process = self.popen_factory(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            cwd=str(self.repo_root),
            env=self._worker_env(),
        )
        if process.stdin is None or process.stdout is None:
            try:
                process.terminate()
            finally:
                raise RuntimeError("worker stdio transport unavailable")
        self.processes[worker_id] = process
        return process

    def request(self, worker_id: str, command: WorkerCommand) -> dict:
        process = self.processes.get(worker_id)
        if process is None or process.poll() is not None:
            raise RuntimeError("worker process is not running")
        payload = json.dumps(command.to_dict(), ensure_ascii=False, separators=(",", ":"))
        process.stdin.write(payload + "\n")
        process.stdin.flush()
        timeout = self.response_timeout_seconds
        if str(command.action or "").upper() in _UI_ACTIONS:
            timeout = max(timeout, _UI_RESPONSE_TIMEOUT_SECONDS)
        try:
            line = self.line_reader(process.stdout, timeout)
        except TimeoutError:
            self.stop(worker_id)
            raise
        if not line:
            raise RuntimeError("worker process closed its response stream")
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError("worker returned invalid JSON") from exc
        if not isinstance(response, dict):
            raise RuntimeError("worker returned invalid response")
        if not response.get("ok"):
            error = str(response.get("error") or "worker command failed")[:240]
            raise RuntimeError(error)
        return response

    def heartbeat(self, worker_id: str) -> dict:
        response = self.request(
            worker_id,
            WorkerCommand(
                command_id=f"heartbeat-{secrets.token_urlsafe(12)}",
                action="HEARTBEAT",
            ),
        )
        heartbeat = response.get("heartbeat")
        if not isinstance(heartbeat, dict):
            raise RuntimeError("worker heartbeat payload missing")
        return heartbeat

    def stop(self, worker_id: str) -> None:
        process = self.processes.pop(worker_id, None)
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except Exception:
            pass
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
