#!/usr/bin/env python3
"""Run Docker boot-time config migrations safely."""
from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from korra_cli.config import (
    _raw_config_has_explicit_version,
    check_config_version,
    get_config_path,
    get_env_path,
    migrate_config,
)
from korra_cli.config_migrations import (
    SUPPORT_FLOOR_VERSION,
    support_floor_message,
)
from utils import atomic_roundtrip_yaml_update, env_var_enabled, fast_safe_load


def _backup_path(path: Path, stamp: str) -> Path:
    base = path.with_name(f"{path.name}.bak-{stamp}")
    if not base.exists():
        return base
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.name}.bak-{stamp}.{index}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not choose a backup path for {path}")


def _backup_existing(paths: Iterable[Path]) -> dict[Path, Path]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backups: dict[Path, Path] = {}
    for path in paths:
        if not path.is_file():
            continue
        dest = _backup_path(path, stamp)
        shutil.copy2(path, dest)
        backups[path] = dest
    return backups


def _restore_backups(backups: dict[Path, Path]) -> list[Path]:
    restored: list[Path] = []
    for original, backup in backups.items():
        if not backup.is_file():
            continue
        shutil.copy2(backup, original)
        restored.append(original)
    return restored


def main() -> int:
    if env_var_enabled("HERMES_SKIP_CONFIG_MIGRATION"):
        print("[config-migrate] HERMES_SKIP_CONFIG_MIGRATION is set; skipping config migration")
        return 0

    current_ver, latest_ver = check_config_version()
    if current_ver >= latest_ver:
        return 0

    # Native core distinguishes a fresh hand-written seed from an explicit
    # unsupported version. Validate the raw shape as well: null/scalar/invalid
    # documents are not empty seeds and must never be replaced with defaults.
    if not _raw_config_has_explicit_version():
        config_path = get_config_path()
        try:
            raw = fast_safe_load(config_path.read_text(encoding="utf-8"))
        except Exception:
            return 0  # check_config_version already reports malformed YAML
        if not isinstance(raw, dict):
            print("[config-migrate] WARNING: настройки должны быть YAML-словарём; файл сохранён без изменений", file=sys.stderr)
            return 0
        # The seed already uses current keys. Reuse the native single-key
        # round-trip writer so comments, active keys, quoting and modes survive;
        # the migration ladder's bulk save would materialise unrelated text.
        atomic_roundtrip_yaml_update(config_path, "_config_version", latest_ver)
        print(f"[config-migrate] Fresh config schema stamped: {latest_ver}")
        return 0

    # Below the auto-migration support floor: migrate_config() refuses (and
    # leaves the file untouched), so don't run the backup/verify dance that
    # would raise "did not advance config version" and block the boot.
    # Warn-and-continue matches the CLI's fail-safe posture.
    if current_ver < SUPPORT_FLOOR_VERSION:
        print(
            f"[config-migrate] WARNING: {support_floor_message()}",
            file=sys.stderr,
        )
        return 0

    backups = _backup_existing((get_config_path(), get_env_path()))
    backup_text = ", ".join(str(path) for path in backups.values()) if backups else "none"
    print(
        f"[config-migrate] Migrating config schema {current_ver} -> {latest_ver}; "
        f"backups: {backup_text}"
    )
    try:
        migrate_config(interactive=False, quiet=False)
    except Exception:
        restored = _restore_backups(backups)
        if restored:
            print(
                "[config-migrate] Migration failed; restored "
                + ", ".join(str(path) for path in restored)
            )
        raise

    post_ver, _ = check_config_version()
    if post_ver < latest_ver:
        restored = _restore_backups(backups)
        restored_text = ", ".join(str(path) for path in restored) if restored else "none"
        raise RuntimeError(
            f"migration did not advance config version to {latest_ver} "
            f"(still {post_ver}); restored: {restored_text}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[config-migrate] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
