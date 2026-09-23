from pathlib import Path

import pytest

from korra_cli.file_organization import (
    ensure_agent_results,
    ensure_sections,
    rename_agent,
    retire_agent,
    section_snapshot,
)


def test_organization_preserves_legacy_files_and_stable_agent_paths(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    legacy = root / "Старый отчёт.pdf"
    legacy.write_bytes(b"original")
    upload = root / "client" / "inbox" / "2026-09-23" / "package" / "photo.jpg"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"jpg")

    first = ensure_agent_results(root, "designer")
    other = ensure_agent_results(root, "writer")
    assert other != first
    assert other.parent != first.parent
    result = first / "result.pdf"
    result.write_bytes(b"result")
    assert ensure_agent_results(root, "designer") == first
    assert section_snapshot(root)["shared"] == str(root / "shared")
    assert legacy.read_bytes() == b"original"
    assert upload.read_bytes() == b"jpg"
    assert upload == root / "client" / "inbox" / "2026-09-23" / "package" / "photo.jpg"

    rename_agent(root, "designer", "studio")
    assert ensure_agent_results(root, "studio") == first
    retire_agent(root, "studio")
    replacement = ensure_agent_results(root, "studio")
    assert replacement != first
    assert result.read_bytes() == b"result"
    assert section_snapshot(root)["archived"][first.parent.name] == "studio"


def test_existing_user_folder_is_not_adopted(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    existing = root / "shared"
    existing.mkdir()
    file = existing / "contract.pdf"
    file.write_bytes(b"owner")

    with pytest.raises(FileExistsError, match="shared/agents already exist"):
        ensure_sections(root)
    assert file.read_bytes() == b"owner"
    assert not (root / "agents").exists()


def test_symlinked_agent_folder_is_rejected(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    ensure_sections(root)
    (root / "agents").rmdir()
    (root / "agents").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="not a directory"):
        ensure_agent_results(root, "designer")
    assert list(outside.iterdir()) == []


def test_existing_client_file_cannot_leave_partial_sections(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "client").write_bytes(b"owner")

    with pytest.raises(ValueError, match="not a directory"):
        ensure_sections(root)
    assert (root / "client").read_bytes() == b"owner"
    assert not (root / "shared").exists()
    assert not (root / "agents").exists()
