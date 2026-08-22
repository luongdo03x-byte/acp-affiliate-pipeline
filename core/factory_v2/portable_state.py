"""Portable single-machine state primitives for Account Factory V2."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sqlite3


_ASSET_RE = re.compile(r"^acp-state-g([0-9]{6})\.tar\.gz$")
_ALLOWED_OWNERSHIP = frozenset({"ACTIVE", "HANDED_OFF"})


@dataclass(frozen=True)
class MachineState:
    machine_id: str
    last_imported_generation: int
    ownership: str


def generation_from_asset(name: str) -> int | None:
    match = _ASSET_RE.fullmatch(str(name or ""))
    return int(match.group(1)) if match else None


def next_generation(remote_names: list[str], local_generation: int) -> int:
    generations = [
        generation
        for name in remote_names
        if (generation := generation_from_asset(name)) is not None
    ]
    return max([int(local_generation), *generations], default=int(local_generation)) + 1


def _validate_machine_state(state: MachineState) -> MachineState:
    if state.ownership not in _ALLOWED_OWNERSHIP:
        raise ValueError("INVALID_MACHINE_OWNERSHIP")
    if not str(state.machine_id or "").strip():
        raise ValueError("INVALID_MACHINE_ID")
    if int(state.last_imported_generation) < 0:
        raise ValueError("INVALID_MACHINE_GENERATION")
    return state


def load_machine_state(path: Path) -> MachineState | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        state = MachineState(
            machine_id=str(raw["machine_id"]),
            last_imported_generation=int(raw["last_imported_generation"]),
            ownership=str(raw["ownership"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("INVALID_MACHINE_STATE") from exc
    try:
        return _validate_machine_state(state)
    except ValueError as exc:
        raise RuntimeError("INVALID_MACHINE_STATE") from exc


def write_machine_state(path: Path, state: MachineState) -> None:
    state = _validate_machine_state(state)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(asdict(state), ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.chmod(0o600)
    tmp.replace(path)


def require_active_ownership(path: Path) -> MachineState:
    state = load_machine_state(Path(path))
    if state is None or state.ownership != "ACTIVE":
        raise RuntimeError("MACHINE_HANDED_OFF")
    return state


def validate_sqlite(path: Path) -> None:
    path = Path(path)
    conn = None
    try:
        conn = sqlite3.connect(str(path))
        integrity_row = conn.execute("PRAGMA integrity_check").fetchone()
        integrity = integrity_row[0] if integrity_row else None
        foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    except (sqlite3.DatabaseError, OSError) as exc:
        raise RuntimeError("SQLITE_INTEGRITY_FAILED") from exc
    finally:
        if conn is not None:
            conn.close()

    if integrity != "ok" or foreign_key_errors:
        raise RuntimeError("SQLITE_INTEGRITY_FAILED")


def snapshot_sqlite(source: Path, destination: Path) -> None:
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    src = None
    dst = None
    try:
        src = sqlite3.connect(str(source))
        dst = sqlite3.connect(str(destination))
        src.backup(dst)
    except (sqlite3.DatabaseError, OSError) as exc:
        raise RuntimeError("SQLITE_INTEGRITY_FAILED") from exc
    finally:
        if dst is not None:
            dst.close()
        if src is not None:
            src.close()

    validate_sqlite(destination)
