from __future__ import annotations

from pathlib import Path

import pytest

from metal_calc.errors import Conflict, PathEscape
from metal_calc.securefs import SecureRoot


def test_atomic_write_is_no_replace(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    root = SecureRoot(tmp_path, writable=True)
    try:
        root.atomic_write("out/result", b"one")
        with pytest.raises(Conflict):
            root.atomic_write("out/result", b"two")
        assert root.read_bytes("out/result") == b"one"
    finally:
        root.close()


@pytest.mark.parametrize("value", ["../etc/passwd", "/etc/passwd", "a\\b", "./x"])
def test_relative_path_escape_is_rejected(tmp_path: Path, value: str) -> None:
    root = SecureRoot(tmp_path, writable=True)
    try:
        with pytest.raises(PathEscape):
            root.read_bytes(value)
    finally:
        root.close()


def test_parent_symlink_is_rejected_by_openat2(tmp_path: Path) -> None:
    (tmp_path / "link").symlink_to("/etc")
    root = SecureRoot(tmp_path, writable=False)
    try:
        with pytest.raises(OSError):
            root.read_bytes("link/passwd")
    finally:
        root.close()

