"""Подключение CRM принадлежит установке: не клонируется в профили и не уходит в экспорт (K21-322, F2)."""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from korra_cli import crm_connection as cc
from korra_cli.profiles import create_profile, export_profile

WEBHOOK = "https://synthetic.bitrix24.ru/rest/17/abcdefgh12345678/"
CONN = {"type": "bitrix24", "webhook_url": WEBHOOK, "settings": {}}


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    default_home = tmp_path / ".hermes"
    default_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    (default_home / "config.yaml").write_text("model: synthetic\n", encoding="utf-8")
    cc._write(dict(CONN), default_home)
    return default_home


def _archive_members(archive: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if member.isfile():
                out[member.name] = tar.extractfile(member).read().decode("utf-8", "replace")
    return out


def test_clone_all_does_not_copy_the_connection(home):
    clone = create_profile("synthetic-clone", clone_from="default", clone_all=True, no_alias=True)
    assert (clone / "config.yaml").exists()
    assert not (clone / cc.STORE_DIR).exists()
    assert (home / cc.STORE_DIR / cc.STORE_FILE).exists()


def test_clone_disconnect_export_leaves_no_key(home, tmp_path):
    clone = create_profile("synthetic-clone", clone_from="default", clone_all=True, no_alias=True)
    cc.disconnect(root=home)
    archive = export_profile("synthetic-clone", str(tmp_path / "clone.tar.gz"))
    members = _archive_members(archive)
    assert any(name.endswith("config.yaml") for name in members)
    assert not any(cc.STORE_DIR in name for name in members)
    assert not any(WEBHOOK in text for text in members.values())
    assert not (clone / cc.STORE_DIR).exists()


def test_export_skips_store_already_copied_into_a_clone(home, tmp_path):
    clone = create_profile("synthetic-clone", clone_from="default", clone_all=True, no_alias=True)
    cc._write(dict(CONN), clone)
    cc.disconnect(root=home)
    archive = export_profile("synthetic-clone", str(tmp_path / "old-clone.tar.gz"))
    members = _archive_members(archive)
    assert not any(cc.STORE_DIR in name for name in members)
    assert not any(WEBHOOK in text for text in members.values())
    assert (clone / cc.STORE_DIR / cc.STORE_FILE).exists()


def test_default_export_has_no_key(home, tmp_path):
    archive = export_profile("default", str(tmp_path / "default.tar.gz"))
    members = _archive_members(archive)
    assert not any(cc.STORE_DIR in name for name in members)
    assert not any(WEBHOOK in text for text in members.values())


def test_nested_directory_with_the_same_name_is_kept(home, tmp_path):
    clone = create_profile("synthetic-clone", clone_from="default", clone_all=True, no_alias=True)
    notes = clone / "skills" / "mine" / cc.STORE_DIR
    notes.mkdir(parents=True)
    (notes / "notes.md").write_text("user data", encoding="utf-8")
    members = _archive_members(export_profile("synthetic-clone", str(tmp_path / "n.tar.gz")))
    assert any(name.endswith(f"{cc.STORE_DIR}/notes.md") for name in members)
