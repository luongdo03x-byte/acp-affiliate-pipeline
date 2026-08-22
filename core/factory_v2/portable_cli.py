"""Portable single-machine handoff orchestration for Account Factory V2."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
from typing import TextIO

from .portable_release import GitHubReleaseTransport
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
