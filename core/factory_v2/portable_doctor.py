"""Fail-closed readiness checks for portable Account Factory state."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from typing import Callable, Iterator, Mapping

from .account_credentials import CredentialDecryptError, get_account_password
from .avd import AvdManager
from .portable_state import load_machine_state, validate_sqlite


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    code: str


def _check(name: str, ok: bool, code: str = "OK") -> DoctorCheck:
    return DoctorCheck(name=name, ok=bool(ok), code="OK" if ok else code)


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value
    return values


def _read_machine_metadata(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


@contextmanager
def _temporary_environment(values: Mapping[str, str]) -> Iterator[None]:
    previous: dict[str, str | None] = {}
    try:
        for key, value in values.items():
            previous[key] = os.environ.get(key)
            os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _database_path(base: Path, env: Mapping[str, str]) -> Path:
    configured = str(env.get("ACP_DB") or "").strip()
    return Path(configured) if configured else base / "shared" / "var" / "acp-live.db"


def _callback_required(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute(
            """SELECT 1
               FROM factory_account a
               JOIN factory_batch b ON b.id=a.batch_id
               WHERE b.completion_mode='ACP_ACTIVE'
                 AND (
                   a.stage IN ('THREADS_CREATED','ACP_CONNECTING','ACP_ACTIVE')
                   OR a.last_safe_stage='THREADS_CREATED'
                 )
               LIMIT 1"""
        ).fetchone()
    except sqlite3.DatabaseError:
        return False
    return row is not None


def _oauth_config_required(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM factory_batch WHERE completion_mode='ACP_ACTIVE' LIMIT 1"
        ).fetchone()
    except sqlite3.DatabaseError:
        return False
    return row is not None


def _git_state_ok(repo_root: Path, machine_metadata: Mapping[str, object]) -> bool:
    expected_commit = str(machine_metadata.get("source_git_commit") or "").strip()
    expected_branch = str(machine_metadata.get("source_branch") or "").strip()
    if not expected_commit or not expected_branch:
        return False
    try:
        commit = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=False,
        )
        branch = subprocess.run(
            ["git", "-C", str(repo_root), "branch", "--show-current"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    if commit.returncode != 0 or branch.returncode != 0:
        return False
    return commit.stdout.strip() == expected_commit and branch.stdout.strip() == expected_branch


def _github_auth_ok() -> bool:
    try:
        result = subprocess.run(
            ["gh", "auth", "status", "--hostname", "github.com"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def run_portable_doctor(
    base: Path,
    repo_root: Path,
    *,
    avd=None,
    http_probe: Callable[[str], bool] | None = None,
) -> list[DoctorCheck]:
    """Return sanitized readiness results without exposing environment secrets."""
    base = Path(base)
    repo_root = Path(repo_root)
    shared = base / "shared"
    env_path = shared / ".env.local"
    machine_path = shared / "machine.json"
    env = _read_env(env_path)
    machine_metadata = _read_machine_metadata(machine_path)
    checks: list[DoctorCheck] = []

    env_mode_ok = False
    try:
        env_mode_ok = env_path.is_file() and (env_path.stat().st_mode & 0o777) == 0o600
    except OSError:
        env_mode_ok = False
    checks.append(_check("ENV_PERMISSIONS", env_mode_ok, "ENV_PERMISSIONS_INVALID"))

    master_key = str(env.get("ACP_MASTER_KEY") or "")
    checks.append(_check("ACP_MASTER_KEY", bool(master_key), "ACP_MASTER_KEY_MISSING"))

    try:
        machine = load_machine_state(machine_path)
        ownership_ok = machine is not None and machine.ownership == "ACTIVE"
        ownership_code = "MACHINE_HANDED_OFF" if not ownership_ok else "OK"
    except RuntimeError:
        ownership_ok = False
        ownership_code = "INVALID_MACHINE_STATE"
    checks.append(_check("OWNERSHIP", ownership_ok, ownership_code))

    checks.append(_check("GIT_STATE", _git_state_ok(repo_root, machine_metadata), "GIT_STATE_MISMATCH"))
    checks.append(_check("GITHUB_AUTH", _github_auth_ok(), "GITHUB_AUTH_REQUIRED"))

    db_path = _database_path(base, env)
    sqlite_ok = False
    conn: sqlite3.Connection | None = None
    try:
        validate_sqlite(db_path)
        sqlite_ok = True
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
    except (RuntimeError, sqlite3.DatabaseError, OSError):
        sqlite_ok = False
    checks.append(_check("SQLITE", sqlite_ok, "SQLITE_INTEGRITY_FAILED"))

    credential_ok = True
    credential_code = "OK"
    if sqlite_ok and conn is not None:
        try:
            row = conn.execute(
                "SELECT account_id FROM factory_account_credential ORDER BY account_id LIMIT 1"
            ).fetchone()
        except sqlite3.DatabaseError:
            row = None
        if row is not None:
            if not master_key:
                credential_ok = False
                credential_code = "ACP_MASTER_KEY_MISSING"
            else:
                try:
                    with _temporary_environment({"ACP_MASTER_KEY": master_key}):
                        password = get_account_password(conn, str(row["account_id"]))
                    del password
                except CredentialDecryptError:
                    credential_ok = False
                    credential_code = "CREDENTIAL_DECRYPT_FAILED"
                except Exception:
                    credential_ok = False
                    credential_code = "CREDENTIAL_DECRYPT_FAILED"
    checks.append(_check("CREDENTIAL_DECRYPT", credential_ok, credential_code))

    oauth_required = bool(sqlite_ok and conn is not None and _oauth_config_required(conn))
    oauth_ok = True
    if oauth_required:
        oauth_ok = bool(
            str(env.get("THREADS_APP_ID") or "").strip()
            and str(env.get("THREADS_APP_SECRET") or "").strip()
        )
    checks.append(_check("OAUTH_CONFIG", oauth_ok, "OAUTH_CONFIG_MISSING"))

    avd = avd or AvdManager()
    avd_ok = False
    avd_code = "AVD_MISSING"
    try:
        configured = set(avd.list_avds())
        if "acp-worker-01" in configured:
            online = set(avd.list_online_devices())
            if "emulator-5554" not in online or not avd.is_boot_completed("emulator-5554"):
                avd_code = "AVD_NOT_BOOTED"
            else:
                avd_ok = True
                avd_code = "OK"
    except Exception:
        avd_ok = False
        avd_code = "AVD_MISSING"
    checks.append(_check("AVD", avd_ok, avd_code))

    callback_needed = bool(sqlite_ok and conn is not None and _callback_required(conn))
    if callback_needed:
        public_base = str(env.get("ACP_PUBLIC_BASE_URL") or "").strip()
        if not public_base:
            checks.append(_check("CALLBACK", False, "CALLBACK_URL_MISSING"))
        else:
            probe = http_probe or (lambda _url: False)
            try:
                callback_ok = bool(probe(public_base))
            except Exception:
                callback_ok = False
            checks.append(_check("CALLBACK", callback_ok, "CALLBACK_UNREACHABLE"))
    else:
        checks.append(_check("CALLBACK", True))

    if conn is not None:
        conn.close()
    return checks


def require_portable_doctor(
    base: Path,
    repo_root: Path,
    *,
    avd=None,
    http_probe: Callable[[str], bool] | None = None,
) -> None:
    checks = run_portable_doctor(
        base,
        repo_root,
        avd=avd,
        http_probe=http_probe,
    )
    for check in checks:
        if not check.ok:
            raise RuntimeError(f"PORTABLE_DOCTOR_FAILED:{check.code}")
