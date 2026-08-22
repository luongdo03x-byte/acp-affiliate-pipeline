"""Portable single-machine handoff orchestration for Account Factory V2."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import TextIO

from .portable_doctor import require_portable_doctor
from .portable_release import GitHubReleaseTransport
from .portable_resume import reconcile_for_portable_resume
from .portable_state import (
    MachineState,
    build_bundle,
    generation_from_asset,
    load_machine_state,
    next_generation,
    require_active_ownership,
    restore_bundle,
    snapshot_sqlite,
    write_machine_state,
)


def _output_stream(out: TextIO | None) -> TextIO:
    return out if out is not None else sys.stdout


def _generation_assets(assets: list[dict]) -> list[int]:
    generations: list[int] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        generation = generation_from_asset(str(asset.get("name") or ""))
        if generation is not None:
            generations.append(generation)
    return generations


def handoff_out(
    *,
    base: Path,
    repo: str,
    git_commit: str,
    git_branch: str,
    transport=None,
    out: TextIO | None = None,
) -> int:
    """Publish one immutable state generation, then relinquish local ownership."""
    base = Path(base)
    shared = base / "shared"
    machine_path = shared / "machine.json"
    state = require_active_ownership(machine_path)
    transport = transport or GitHubReleaseTransport(repo)
    stream = _output_stream(out)

    transport.assert_authenticated()
    assets = transport.list_assets()
    generation = next_generation(
        [str(asset.get("name") or "") for asset in assets if isinstance(asset, dict)],
        state.last_imported_generation,
    )
    transport.ensure_release()

    with tempfile.TemporaryDirectory(prefix="acp-portable-out-") as tmp_name:
        temp_root = Path(tmp_name)
        snapshot = temp_root / "acp-live.db"
        snapshot_sqlite(shared / "var" / "acp-live.db", snapshot)
        archive = build_bundle(
            snapshot_db=snapshot,
            env_path=shared / ".env.local",
            avatar_dir=shared / "avatars",
            output_dir=temp_root / "bundle",
            generation=generation,
            source_machine_id=state.machine_id,
            source_git_commit=git_commit,
            source_branch=git_branch,
        )
        transport.upload(archive)
        transport.verify_remote_asset(archive)
        prune = getattr(transport, "prune_keep_latest", None)
        if callable(prune):
            prune(keep=5)

    write_machine_state(
        machine_path,
        MachineState(state.machine_id, generation, "HANDED_OFF"),
    )
    stream.write(f"HANDOFF_OK generation={generation}\n")
    return generation


def handoff_in(
    *,
    base: Path,
    repo: str,
    transport=None,
    out: TextIO | None = None,
    machine_id: str,
) -> int:
    """Restore the newest remote generation and claim this machine ACTIVE."""
    base = Path(base)
    machine_path = base / "shared" / "machine.json"
    transport = transport or GitHubReleaseTransport(repo)
    stream = _output_stream(out)

    transport.assert_authenticated()
    assets = transport.list_assets()
    remote_generations = _generation_assets(assets)
    if not remote_generations:
        raise RuntimeError("REMOTE_STATE_MISSING")
    remote_generation = max(remote_generations)

    local_state = load_machine_state(machine_path)
    if local_state is not None:
        local_generation = int(local_state.last_imported_generation)
        if local_generation > remote_generation:
            raise RuntimeError("REMOTE_STATE_OLDER_THAN_LOCAL")
        if local_generation == remote_generation and local_state.ownership == "ACTIVE":
            stream.write(f"IMPORT_OK generation={remote_generation} already-current\n")
            return remote_generation

    with tempfile.TemporaryDirectory(prefix="acp-portable-in-") as tmp_name:
        archive = transport.download_generation(remote_generation, Path(tmp_name))
        restore_bundle(
            archive,
            base=base,
            expected_generation=remote_generation,
        )

    write_machine_state(
        machine_path,
        MachineState(str(machine_id), remote_generation, "ACTIVE"),
    )
    stream.write(f"IMPORT_OK generation={remote_generation}\n")
    return remote_generation


def doctor(
    *,
    base: Path,
    repo_root: Path,
    checker=require_portable_doctor,
    out: TextIO | None = None,
) -> None:
    checker(Path(base), Path(repo_root))
    _output_stream(out).write("DOCTOR_OK\n")


def resume(
    *,
    base: Path,
    now_iso: str | None = None,
    reconciler=reconcile_for_portable_resume,
    out: TextIO | None = None,
) -> dict[str, int | str]:
    base = Path(base)
    if now_iso is None:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = sqlite3.connect(str(base / "shared" / "var" / "acp-live.db"), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        result = reconciler(conn, now_iso)
    finally:
        conn.close()
    _output_stream(out).write(
        "RESUME_RECONCILED leases={} oauth={} gated={}\n".format(
            int(result.get("leases_reconciled", 0)),
            int(result.get("oauth_reconciled", 0)),
            int(result.get("oauth_gated", 0)),
        )
    )
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="portable_cli")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor_parser = commands.add_parser("doctor")
    doctor_parser.add_argument("--base", type=Path, required=True)
    doctor_parser.add_argument("--repo-root", type=Path, required=True)
    resume_parser = commands.add_parser("resume")
    resume_parser.add_argument("--base", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "doctor":
        doctor(base=args.base, repo_root=args.repo_root)
    else:
        resume(base=args.base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
