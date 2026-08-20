"""Balanced shared-avatar selection and safe avatar path resolution."""
from __future__ import annotations

import os
import random
from collections import Counter
from pathlib import Path

_SUPPORTED_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})


def configured_avatar_root(repo_root: Path, override=None) -> Path:
    raw = override if override is not None else os.environ.get("ACP_AVATAR_DIR")
    if raw is None or not str(raw).strip():
        return (Path(repo_root) / "var" / "factory_avatars").resolve()
    return Path(str(raw)).expanduser().resolve()


def validate_avatar_reference(value, *, repo_root: Path, avatar_root: Path) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 300 or "\\" in text:
        raise ValueError("invalid avatar_file")

    repo_root = Path(repo_root).resolve()
    avatar_root = Path(avatar_root).resolve()
    candidate = Path(text).expanduser()

    if candidate.is_absolute():
        resolved = candidate.resolve()
        try:
            resolved.relative_to(avatar_root)
        except ValueError as exc:
            raise ValueError("avatar_file must stay inside configured avatar directory") from exc
        return str(resolved)

    if text.startswith("./") or ".." in candidate.parts:
        raise ValueError("avatar_file must stay inside repository")

    resolved = (repo_root / candidate).resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError("avatar_file must stay inside repository") from exc
    return candidate.as_posix()


def resolve_avatar_source(reference: str, *, repo_root: Path, avatar_root: Path) -> Path:
    clean = validate_avatar_reference(
        reference,
        repo_root=repo_root,
        avatar_root=avatar_root,
    )
    path = Path(clean)
    if path.is_absolute():
        return path.resolve()
    return (Path(repo_root).resolve() / path).resolve()


class AvatarPool:
    """Choose among the least-used images, avoiding immediate repeats when possible."""

    def __init__(self, connection, avatar_root: Path, *, rng=None):
        self.connection = connection
        self.avatar_root = Path(avatar_root).resolve()
        self.rng = rng or random.SystemRandom()
        self.images = self._scan_images()
        self.usage = self._load_usage()

    def _scan_images(self) -> tuple[str, ...]:
        if not self.avatar_root.is_dir():
            return ()
        images = [
            str(path.resolve())
            for path in self.avatar_root.rglob("*")
            if path.is_file() and path.suffix.lower() in _SUPPORTED_SUFFIXES
        ]
        return tuple(sorted(set(images)))

    def _load_usage(self) -> Counter:
        rows = self.connection.execute(
            """SELECT avatar_file, COUNT(*) AS usage_count
               FROM factory_account
               WHERE avatar_file IS NOT NULL AND avatar_file != ''
               GROUP BY avatar_file"""
        ).fetchall()
        return Counter({str(row[0]): int(row[1]) for row in rows})

    def choose(self, *, avoid: str | None = None) -> str | None:
        if not self.images:
            return None

        candidates = list(self.images)
        if avoid and len(candidates) > 1:
            without_previous = [path for path in candidates if path != avoid]
            if without_previous:
                candidates = without_previous

        minimum = min(self.usage.get(path, 0) for path in candidates)
        least_used = [path for path in candidates if self.usage.get(path, 0) == minimum]
        selected = self.rng.choice(least_used)
        self.usage[selected] += 1
        return selected
