"""Private GitHub Release transport for portable Account Factory state."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Callable, Sequence

from .portable_state import generation_from_asset


_RELEASE_TAG = "acp-portable-state"
_RELEASE_TITLE = "ACP Portable State"
_RELEASE_NOTES = "Portable Account Factory state generations"


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def _default_runner(argv: Sequence[str]) -> CommandResult:
    try:
        completed = subprocess.run(
            list(argv),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError("GITHUB_CLI_UNAVAILABLE") from exc
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


class GitHubReleaseTransport:
    """Small fail-closed wrapper around ``gh release`` commands.

    Error messages are intentionally domain-only so auth metadata, release URLs,
    or other command output never gets reflected back to the operator.
    """

    def __init__(
        self,
        repo: str,
        *,
        runner: Callable[[Sequence[str]], CommandResult] | None = None,
    ) -> None:
        self.repo = str(repo)
        self.runner = runner or _default_runner

    def _run(self, argv: Sequence[str]) -> CommandResult:
        result = self.runner(list(argv))
        if not isinstance(result, CommandResult):
            raise RuntimeError("GITHUB_COMMAND_FAILED")
        return result

    def _view_assets(self) -> CommandResult:
        return self._run(
            [
                "gh",
                "release",
                "view",
                _RELEASE_TAG,
                "--repo",
                self.repo,
                "--json",
                "assets",
            ]
        )

    def assert_authenticated(self) -> None:
        result = self._run(["gh", "auth", "status", "--hostname", "github.com"])
        if result.returncode != 0:
            raise RuntimeError("GITHUB_AUTH_REQUIRED")

    def list_assets(self) -> list[dict]:
        result = self._view_assets()
        if result.returncode != 0:
            raise RuntimeError("GITHUB_RELEASE_UNAVAILABLE")
        try:
            payload = json.loads(result.stdout or "{}")
            raw_assets = payload.get("assets", [])
        except (json.JSONDecodeError, AttributeError, TypeError) as exc:
            raise RuntimeError("GITHUB_RELEASE_RESPONSE_INVALID") from exc
        if not isinstance(raw_assets, list):
            raise RuntimeError("GITHUB_RELEASE_RESPONSE_INVALID")

        assets: list[dict] = []
        for raw in raw_assets:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "")
            if generation_from_asset(name) is None:
                continue
            assets.append(dict(raw))
        return assets

    def ensure_release(self) -> None:
        result = self._view_assets()
        if result.returncode == 0:
            return
        created = self._run(
            [
                "gh",
                "release",
                "create",
                _RELEASE_TAG,
                "--repo",
                self.repo,
                "--title",
                _RELEASE_TITLE,
                "--notes",
                _RELEASE_NOTES,
            ]
        )
        if created.returncode != 0:
            raise RuntimeError("GITHUB_RELEASE_CREATE_FAILED")

    def upload(self, path: Path) -> None:
        path = Path(path)
        if generation_from_asset(path.name) is None:
            raise RuntimeError("INVALID_STATE_ASSET_NAME")
        if not path.is_file():
            raise RuntimeError("STATE_ASSET_MISSING")
        result = self._run(
            [
                "gh",
                "release",
                "upload",
                _RELEASE_TAG,
                str(path),
                "--repo",
                self.repo,
            ]
        )
        if result.returncode != 0:
            raise RuntimeError("GITHUB_RELEASE_UPLOAD_FAILED")

    def download_generation(self, generation: int, destination: Path) -> Path:
        generation = int(generation)
        if generation < 0 or generation > 999999:
            raise RuntimeError("INVALID_STATE_GENERATION")
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        name = f"acp-state-g{generation:06d}.tar.gz"
        result = self._run(
            [
                "gh",
                "release",
                "download",
                _RELEASE_TAG,
                "--pattern",
                name,
                "--dir",
                str(destination),
                "--repo",
                self.repo,
            ]
        )
        if result.returncode != 0:
            raise RuntimeError("GITHUB_RELEASE_DOWNLOAD_FAILED")
        path = destination / name
        if not path.is_file():
            raise RuntimeError("GITHUB_RELEASE_DOWNLOAD_FAILED")
        return path

    def verify_remote_asset(self, path: Path) -> None:
        path = Path(path)
        if not path.is_file():
            raise RuntimeError("REMOTE_ASSET_VERIFICATION_FAILED")
        local_size = path.stat().st_size
        matching = [
            asset
            for asset in self.list_assets()
            if str(asset.get("name") or "") == path.name
        ]
        if len(matching) != 1:
            raise RuntimeError("REMOTE_ASSET_VERIFICATION_FAILED")
        try:
            remote_size = int(matching[0].get("size"))
        except (TypeError, ValueError):
            raise RuntimeError("REMOTE_ASSET_VERIFICATION_FAILED") from None
        if remote_size != local_size:
            raise RuntimeError("REMOTE_ASSET_VERIFICATION_FAILED")

    def prune_keep_latest(self, keep: int = 5) -> None:
        keep = int(keep)
        if keep < 1:
            raise RuntimeError("INVALID_RELEASE_RETENTION")

        generations: list[tuple[int, str]] = []
        for asset in self.list_assets():
            name = str(asset.get("name") or "")
            generation = generation_from_asset(name)
            if generation is not None:
                generations.append((generation, name))

        generations.sort(key=lambda item: item[0])
        stale = generations[:-keep] if len(generations) > keep else []
        for _, name in stale:
            result = self._run(
                [
                    "gh",
                    "release",
                    "delete-asset",
                    _RELEASE_TAG,
                    name,
                    "--repo",
                    self.repo,
                    "--yes",
                ]
            )
            if result.returncode != 0:
                raise RuntimeError("GITHUB_RELEASE_PRUNE_FAILED")
