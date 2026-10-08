"""Каталог подключения CRM под общей защитой файлов с ключами установки (K21-322, F1)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import agent.file_safety as fs
from korra_cli import crm_connection as cc
from tools import credential_files as cf
from tools import file_tools as ft

WEBHOOK = "https://synthetic.bitrix24.ru/rest/17/abcdefgh12345678/"
AMO_TOKEN = "synthetic-amo-long-lived-token"

CONNECTIONS = {
    "bitrix24": ({"type": "bitrix24", "webhook_url": WEBHOOK, "settings": {}}, WEBHOOK),
    "amocrm": ({"type": "amocrm", "domain": "synthetic.amocrm.ru", "token": AMO_TOKEN, "settings": {}}, AMO_TOKEN),
}


@pytest.fixture(params=sorted(CONNECTIONS))
def stored(request, tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "_hermes_home_path", lambda: tmp_path)
    monkeypatch.setattr(fs, "_hermes_root_path", lambda: tmp_path)
    monkeypatch.setattr(cf, "_resolve_hermes_home", lambda: tmp_path)
    conn, secret = CONNECTIONS[request.param]
    cc._write(dict(conn), tmp_path)
    return tmp_path / cc.STORE_DIR / cc.STORE_FILE, secret


def test_read_guard_blocks_store_and_directory(stored, tmp_path):
    path, _ = stored
    assert fs.get_read_block_error(str(path))
    assert fs.get_read_block_error(str(path.parent))
    assert fs.get_read_block_error(str(path.parent / "connection.json.tmp"))


def test_read_guard_follows_symlink_into_store(stored, tmp_path):
    path, _ = stored
    link = tmp_path / "innocent.txt"
    link.symlink_to(path)
    assert fs.get_read_block_error(str(link))


def test_read_file_does_not_return_the_key(stored, monkeypatch):
    path, secret = stored
    ops = MagicMock()
    result = SimpleNamespace(content=path.read_text())
    result.to_dict = lambda: {"content": result.content, "total_lines": 1}
    ops.read_file.return_value = result
    monkeypatch.setattr(ft, "_get_file_ops", lambda *a, **kw: ops)
    out = ft.read_file_tool(str(path), task_id="crm-secret-guard")
    assert secret not in out
    ops.read_file.assert_not_called()


def test_search_results_drop_the_store(stored, tmp_path):
    path, secret = stored
    other = tmp_path / "notes.txt"
    other.write_text("ok", encoding="utf-8")
    result = SimpleNamespace(
        matches=[SimpleNamespace(path=str(path), content=secret), SimpleNamespace(path=str(other), content="ok")],
        files=[str(path), str(other)],
        counts={str(path): 1, str(other): 1},
    )
    omitted = ft._filter_read_blocked_search_results(result, "crm-secret-guard")
    assert omitted == 3
    assert [m.path for m in result.matches] == [str(other)]
    assert result.files == [str(other)]
    assert result.counts == {str(other): 1}


def test_search_inside_store_directory_is_refused(stored):
    path, secret = stored
    out = ft.search_tool(pattern=secret, path=str(path.parent), task_id="crm-secret-guard")
    assert secret not in out
    assert "denied" in out.lower()


def test_credential_mount_is_refused(stored, tmp_path):
    cf.clear_credential_files()
    assert cf.register_credential_file(f"{cc.STORE_DIR}/{cc.STORE_FILE}") is False
    assert cf.register_credential_files([f"{cc.STORE_DIR}/{cc.STORE_FILE}"]) == [f"{cc.STORE_DIR}/{cc.STORE_FILE}"]
    assert not any(cc.STORE_DIR in mount["host_path"] for mount in cf.get_credential_file_mounts())


def test_write_patch_delete_are_denied(stored):
    path, _ = stored
    assert fs.is_write_denied(str(path))
    assert fs.is_write_denied(str(path.parent / "new.json"))
    assert fs.get_write_denied_error(str(path), verb="Delete")
    assert json.loads(path.read_text())
