"""Stable, additive folders for owner files in a hosted Korra workspace.

This module never moves existing files or changes chat upload destinations.
The small registry belongs to the installation, not to a cloneable profile.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from utils import atomic_write_text


_PROFILE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_KEY = re.compile(r"^[0-9a-f]{16}$")
_REGISTRY = "file-organization-v1.json"


def _directory(path: Path, *, mode: int = 0o755) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError(f"File organization path is not a directory: {path}")
    path.mkdir(mode=mode, parents=True, exist_ok=True)


def _check_directory(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError(f"File organization path is not a directory: {path}")


@contextmanager
def _locked(root: Path) -> Iterator[Path]:
    # Hosted fleet is Linux. Keep the lock across both registry mutation and
    # directory provisioning so two profile gateways cannot choose two keys.
    import fcntl

    _directory(root)
    index = root / ".index"
    _directory(index, mode=0o700)
    lock_path = index / "file-organization.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield index / _REGISTRY
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read(registry: Path) -> dict:
    if registry.is_symlink():
        raise ValueError("File organization registry must not be a symlink")
    if not registry.exists():
        # A pre-existing user folder cannot silently become product-owned.
        if any((registry.parent.parent / name).exists() or (registry.parent.parent / name).is_symlink() for name in ("shared", "agents")):
            raise FileExistsError("shared/agents already exist; inspect before enabling file organization")
        return {"version": 1, "profiles": {}, "archived": {}}
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("File organization registry is unreadable") from exc
    if (not isinstance(data, dict) or data.get("version") != 1
            or not isinstance(data.get("profiles"), dict)
            or not isinstance(data.get("archived"), dict)):
        raise ValueError("File organization registry has an unknown format")
    keys = list(data["profiles"].values()) + list(data["archived"])
    if (any(not isinstance(key, str) or not _KEY.fullmatch(key) for key in keys)
            or len(keys) != len(set(keys))):
        raise ValueError("File organization registry contains duplicate or invalid keys")
    return data


def _write(registry: Path, data: dict) -> None:
    atomic_write_text(registry, json.dumps(data, ensure_ascii=False, sort_keys=True), create_mode=0o600)


def _sections(root: Path, registry: Path) -> dict:
    existed = registry.exists()
    data = _read(registry)
    # Validate every reserved path before writing the registry or creating any
    # section. A pre-existing `client` file must not leave half a new layout.
    for path in (root / "shared", root / "agents", root / "client", root / "client" / "inbox"):
        _check_directory(path)
    if not existed:
        # Commit ownership before creating directories. A crash after this
        # point is repaired by the next call; no existing content is moved.
        _write(registry, data)
    _directory(root / "shared")
    _directory(root / "agents")
    _directory(root / "client")
    _directory(root / "client" / "inbox")
    return data


def ensure_sections(root: Path) -> dict:
    """Create only empty product folders; fail on a pre-existing collision."""
    with _locked(root) as registry:
        return _sections(root, registry)


def ensure_agent_results(root: Path, profile: str) -> Path:
    """Return a stable result folder; an agent rename preserves this path."""
    if not _PROFILE.fullmatch(profile):
        raise ValueError("Invalid profile name")
    with _locked(root) as registry:
        data = _sections(root, registry)
        key = data["profiles"].get(profile)
        if key is None:
            key = secrets.token_hex(8)
            while key in data["profiles"].values() or key in data["archived"]:
                key = secrets.token_hex(8)
            data["profiles"][profile] = key
            _write(registry, data)
        path = root / "agents" / key / "results"
        _directory(path)
        return path


def section_snapshot(root: Path) -> dict:
    """UI paths and labels, without exposing the private registry file."""
    with _locked(root) as registry:
        data = _sections(root, registry)
        return {
            "shared": str(root / "shared"),
            "agents": str(root / "agents"),
            "uploads": str(root / "client" / "inbox"),
            "profiles": dict(data["profiles"]),
            "archived": dict(data["archived"]),
        }


def is_managed_structure(root: Path, path: Path) -> bool:
    """Protect section roots and agent folders; their contents remain editable."""
    if not (root / ".index" / _REGISTRY).is_file():
        return False
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return False
    return (parts in {("shared",), ("agents",), ("client",), ("client", "inbox")}
            or len(parts) == 2 and parts[0] == "agents" and bool(_KEY.fullmatch(parts[1]))
            or len(parts) == 3 and parts[0] == "agents" and bool(_KEY.fullmatch(parts[1])) and parts[2] == "results")


def rename_agent(root: Path, old: str, new: str) -> None:
    if not _PROFILE.fullmatch(old) or not _PROFILE.fullmatch(new):
        raise ValueError("Invalid profile name")
    if not ((root / ".index" / _REGISTRY).exists() or (root / ".index" / _REGISTRY).is_symlink()):
        return
    with _locked(root) as registry:
        if not registry.exists():
            return
        data = _read(registry)
        key = data["profiles"].pop(old, None)
        if key is None:
            return
        if new in data["profiles"]:
            data["profiles"][old] = key
            raise ValueError("Target profile already has a result folder")
        data["profiles"][new] = key
        _write(registry, data)


def retire_agent(root: Path, profile: str) -> None:
    """Keep output bytes visible as an archive after profile deletion."""
    if not _PROFILE.fullmatch(profile):
        raise ValueError("Invalid profile name")
    if not ((root / ".index" / _REGISTRY).exists() or (root / ".index" / _REGISTRY).is_symlink()):
        return
    with _locked(root) as registry:
        if not registry.exists():
            return
        data = _read(registry)
        key = data["profiles"].pop(profile, None)
        if key is not None:
            data["archived"][key] = profile
            _write(registry, data)
