"""Portable single-machine state primitives for Account Factory V2."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tarfile
import tempfile


_ASSET_RE = re.compile(r"^acp-state-g([0-9]{6})\.tar\.gz$")
_ALLOWED_OWNERSHIP = frozenset({"ACTIVE", "HANDED_OFF"})
_FORMAT_VERSION = 1
_REQUIRED_FILES = frozenset({
    "state/manifest.json",
    "state/checksums.sha256",
    "state/shared/.env.local",
    "state/shared/var/acp-live.db",
})
_ALLOWED_DIRS = frozenset({
    "state",
    "state/shared",
    "state/shared/var",
    "state/shared/avatars",
})


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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _avatar_tree_digest(avatar_dir: Path) -> str:
    digest = hashlib.sha256()
    avatar_dir = Path(avatar_dir)
    if not avatar_dir.exists():
        return digest.hexdigest()
    for path in sorted(item for item in avatar_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(avatar_dir).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _payload_files(state_dir: Path) -> list[Path]:
    files = [
        state_dir / "manifest.json",
        state_dir / "shared" / ".env.local",
        state_dir / "shared" / "var" / "acp-live.db",
    ]
    avatar_root = state_dir / "shared" / "avatars"
    if avatar_root.exists():
        files.extend(sorted(item for item in avatar_root.rglob("*") if item.is_file()))
    return files


def build_bundle(
    *,
    snapshot_db: Path,
    env_path: Path,
    avatar_dir: Path,
    output_dir: Path,
    generation: int,
    source_machine_id: str,
    source_git_commit: str,
    source_branch: str,
) -> Path:
    snapshot_db = Path(snapshot_db)
    env_path = Path(env_path)
    avatar_dir = Path(avatar_dir)
    output_dir = Path(output_dir)
    generation = int(generation)
    if generation < 0 or generation > 999999:
        raise ValueError("INVALID_BUNDLE_GENERATION")
    validate_sqlite(snapshot_db)
    if not env_path.is_file():
        raise RuntimeError("BUNDLE_ENV_MISSING")

    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"acp-state-g{generation:06d}.tar.gz"

    with tempfile.TemporaryDirectory(prefix="acp-portable-build-") as tmp_name:
        root = Path(tmp_name)
        state_dir = root / "state"
        shared = state_dir / "shared"
        var_dir = shared / "var"
        avatar_target = shared / "avatars"
        var_dir.mkdir(parents=True)
        avatar_target.mkdir(parents=True)

        shutil.copyfile(snapshot_db, var_dir / "acp-live.db")
        shutil.copyfile(env_path, shared / ".env.local")
        (shared / ".env.local").chmod(0o600)
        if avatar_dir.exists():
            for source in sorted(avatar_dir.rglob("*")):
                relative = source.relative_to(avatar_dir)
                target = avatar_target / relative
                if source.is_symlink():
                    raise RuntimeError("UNSAFE_AVATAR_MEMBER")
                if source.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                elif source.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)

        manifest = {
            "format_version": _FORMAT_VERSION,
            "generation": generation,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_machine_id": str(source_machine_id),
            "source_git_commit": str(source_git_commit),
            "source_branch": str(source_branch),
            "db_relative_path": "shared/var/acp-live.db",
            "db_sha256": _sha256_file(var_dir / "acp-live.db"),
            "env_sha256": _sha256_file(shared / ".env.local"),
            "avatars_digest": _avatar_tree_digest(avatar_target),
            "handoff_state": "READY_FOR_IMPORT",
        }
        (state_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        checksum_lines = []
        for path in _payload_files(state_dir):
            relative = path.relative_to(root).as_posix()
            checksum_lines.append(f"{_sha256_file(path)}  {relative}\n")
        (state_dir / "checksums.sha256").write_text(
            "".join(checksum_lines), encoding="utf-8"
        )

        with tarfile.open(archive, "w:gz") as tar:
            tar.add(state_dir, arcname="state", recursive=True)

    archive.chmod(0o600)
    return archive


def _classify_member(member: tarfile.TarInfo) -> str:
    raw_name = member.name
    path = PurePosixPath(raw_name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise RuntimeError("UNSAFE_BUNDLE_PATH")
    normalized = path.as_posix().rstrip("/")

    if member.issym() or member.islnk() or member.isdev() or member.isfifo():
        raise RuntimeError("UNSAFE_BUNDLE_MEMBER")
    if not (member.isfile() or member.isdir()):
        raise RuntimeError("UNSAFE_BUNDLE_MEMBER")

    avatar_prefix = "state/shared/avatars/"
    allowed = normalized in _REQUIRED_FILES or normalized in _ALLOWED_DIRS
    if normalized.startswith(avatar_prefix) and normalized != "state/shared/avatars":
        allowed = True
    if not allowed:
        raise RuntimeError("BUNDLE_LAYOUT_INVALID")
    return normalized


def _read_member_bytes(tar: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    handle = tar.extractfile(member)
    if handle is None:
        raise RuntimeError("BUNDLE_LAYOUT_INVALID")
    return handle.read()


def _parse_checksums(data: bytes) -> dict[str, str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("BUNDLE_CHECKSUM_INVALID") from exc
    checksums: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            raise RuntimeError("BUNDLE_CHECKSUM_INVALID")
        name = PurePosixPath(parts[1]).as_posix()
        if name in checksums:
            raise RuntimeError("BUNDLE_CHECKSUM_INVALID")
        checksums[name] = parts[0]
    return checksums


def validate_bundle(archive: Path, expected_generation: int) -> dict:
    archive = Path(archive)
    try:
        tar = tarfile.open(archive, "r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise RuntimeError("BUNDLE_INVALID") from exc

    with tar:
        members: dict[str, tarfile.TarInfo] = {}
        for member in tar.getmembers():
            normalized = _classify_member(member)
            if normalized in members:
                raise RuntimeError("BUNDLE_LAYOUT_INVALID")
            members[normalized] = member

        if not _REQUIRED_FILES.issubset(members):
            raise RuntimeError("BUNDLE_LAYOUT_INVALID")
        for required in _REQUIRED_FILES:
            if not members[required].isfile():
                raise RuntimeError("BUNDLE_LAYOUT_INVALID")

        try:
            manifest = json.loads(
                _read_member_bytes(tar, members["state/manifest.json"]).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError("BUNDLE_MANIFEST_INVALID") from exc
        if not isinstance(manifest, dict) or manifest.get("format_version") != _FORMAT_VERSION:
            raise RuntimeError("BUNDLE_FORMAT_UNSUPPORTED")
        try:
            generation = int(manifest.get("generation"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("BUNDLE_MANIFEST_INVALID") from exc
        if generation != int(expected_generation):
            raise RuntimeError("BUNDLE_GENERATION_MISMATCH")
        if manifest.get("handoff_state") != "READY_FOR_IMPORT":
            raise RuntimeError("BUNDLE_MANIFEST_INVALID")

        checksums = _parse_checksums(
            _read_member_bytes(tar, members["state/checksums.sha256"])
        )
        regular_payloads = {
            name for name, member in members.items()
            if member.isfile() and name != "state/checksums.sha256"
        }
        if set(checksums) != regular_payloads:
            raise RuntimeError("BUNDLE_CHECKSUM_MISMATCH")
        for name, expected_digest in checksums.items():
            actual = _sha256_bytes(_read_member_bytes(tar, members[name]))
            if actual != expected_digest:
                raise RuntimeError("BUNDLE_CHECKSUM_MISMATCH")

        db_bytes = _read_member_bytes(tar, members["state/shared/var/acp-live.db"])
        env_bytes = _read_member_bytes(tar, members["state/shared/.env.local"])
        if manifest.get("db_sha256") != _sha256_bytes(db_bytes):
            raise RuntimeError("BUNDLE_CHECKSUM_MISMATCH")
        if manifest.get("env_sha256") != _sha256_bytes(env_bytes):
            raise RuntimeError("BUNDLE_CHECKSUM_MISMATCH")

        with tempfile.TemporaryDirectory(prefix="acp-portable-validate-") as tmp_name:
            db_path = Path(tmp_name) / "acp-live.db"
            db_path.write_bytes(db_bytes)
            validate_sqlite(db_path)

    return manifest


def normalize_portable_env(env_path: Path, base: Path) -> None:
    env_path = Path(env_path)
    base = Path(base)
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError("BUNDLE_ENV_INVALID") from exc

    replacements = {
        "ACP_DB=": f"ACP_DB={base}/shared/var/acp-live.db",
        "ACP_AVATAR_DIR=": f"ACP_AVATAR_DIR={base}/shared/avatars",
    }
    normalized = []
    for line in lines:
        replacement = next(
            (value for prefix, value in replacements.items() if line.startswith(prefix)),
            None,
        )
        normalized.append(replacement if replacement is not None else line)
    env_path.write_text("\n".join(normalized) + "\n", encoding="utf-8")
    env_path.chmod(0o600)


def restore_bundle(archive: Path, *, base: Path, expected_generation: int) -> Path:
    archive = Path(archive)
    base = Path(base)
    validate_bundle(archive, expected_generation=expected_generation)

    base.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="acp-portable-restore-", dir=str(base.parent)
    ) as tmp_name:
        staging_root = Path(tmp_name)
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                normalized = _classify_member(member)
                target = staging_root / normalized
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(_read_member_bytes(tar, member))

        staged_shared = staging_root / "state" / "shared"
        staged_env = staged_shared / ".env.local"
        staged_db = staged_shared / "var" / "acp-live.db"
        validate_sqlite(staged_db)
        normalize_portable_env(staged_env, base)

        live_shared = base / "shared"
        live_var = live_shared / "var"
        live_avatars = live_shared / "avatars"
        live_shared.mkdir(parents=True, exist_ok=True)
        live_var.mkdir(parents=True, exist_ok=True)

        env_tmp = live_shared / ".env.local.import"
        db_tmp = live_var / "acp-live.db.import"
        shutil.copyfile(staged_env, env_tmp)
        shutil.copyfile(staged_db, db_tmp)
        env_tmp.chmod(0o600)
        db_tmp.chmod(0o600)
        os.replace(env_tmp, live_shared / ".env.local")
        os.replace(db_tmp, live_var / "acp-live.db")

        staged_avatars = staged_shared / "avatars"
        avatars_tmp = live_shared / ".avatars.import"
        if avatars_tmp.exists():
            shutil.rmtree(avatars_tmp)
        shutil.copytree(staged_avatars, avatars_tmp)
        if live_avatars.exists():
            shutil.rmtree(live_avatars)
        os.replace(avatars_tmp, live_avatars)

    return base / "shared"