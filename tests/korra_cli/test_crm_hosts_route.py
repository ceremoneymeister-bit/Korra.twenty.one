"""Локальный маршрут портала через /etc/hosts (K21-322, доработка № 3).

Неглобальный адрес допустим только для точного имени хоста поставщика, которое
администратор хоста закрепил в файле hosts; сам файл должен быть доверенным.
Сеть не используется: сокеты и DNS подменены, hosts — временный файл.
"""

from __future__ import annotations

import os
import socket
import stat
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from korra_cli import crm_readers as cr

BITRIX = "acme.bitrix24.ru"
AMO = "synthetic.amocrm.ru"
BITRIX_URL = f"https://{BITRIX}/rest/1/abcdefgh12345678/crm.deal.list.json"


@pytest.fixture(autouse=True)
def closed_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("real network forbidden")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


@pytest.fixture
def admin_file(monkeypatch):
    """The process is not root and cannot write to the file, as the engine in a container."""
    monkeypatch.setattr(cr, "_can_write", lambda path, mode: False)


def hosts(tmp_path, text, mode=0o644):
    path = tmp_path / "hosts"
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return str(path)


def resolves_to(monkeypatch, *addresses):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", (a_, 443)) for a_ in addresses])


def connected_to(monkeypatch, address):
    sock = MagicMock()
    sock.getpeername.return_value = (address, 443)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: sock)
    ctx = MagicMock()
    monkeypatch.setattr(cr.ssl, "create_default_context", lambda: ctx)
    return sock, ctx


def amo_addresses(path, domain=AMO):
    return cr.AmoTransport(domain, hosts_file=path)._addresses()


def bitrix_connect(path, host=BITRIX):
    conn = cr._VendorHTTPSConnection(host, 443, timeout=1, context=MagicMock(), hosts_file=path)
    conn.connect()
    return conn


# ---- the pinned name is allowed


def test_amo_pinned_name_resolves_to_the_local_route(tmp_path, monkeypatch, admin_file):
    path = hosts(tmp_path, f"127.0.0.1 localhost\n127.0.0.2 {AMO}\n")
    resolves_to(monkeypatch, "127.0.0.2")
    assert amo_addresses(path) == ["127.0.0.2"]


def test_amo_connects_to_the_pinned_address_with_tls_for_the_portal_name(tmp_path, monkeypatch, admin_file):
    path = hosts(tmp_path, f"127.0.0.2 {AMO}\n")
    sock, ctx = connected_to(monkeypatch, "127.0.0.2")
    cr.AmoTransport(AMO, hosts_file=path)._connect("127.0.0.2")
    ctx.wrap_socket.assert_called_once_with(sock, server_hostname=AMO)


def test_bitrix_pinned_name_connects_with_tls_for_the_portal_name(tmp_path, monkeypatch, admin_file):
    path = hosts(tmp_path, f"127.0.0.2 {BITRIX}\n")
    sock, ctx = connected_to(monkeypatch, "127.0.0.2")
    conn = cr._VendorHTTPSConnection(BITRIX, 443, timeout=1, context=ctx, hosts_file=path)
    conn.connect()
    ctx.wrap_socket.assert_called_once_with(sock, server_hostname=BITRIX)


def test_hosts_syntax_is_read_as_the_system_reads_it(tmp_path, monkeypatch, admin_file):
    path = hosts(
        tmp_path,
        "# 127.0.0.9 commented.bitrix24.ru\n"
        "\n"
        f"127.0.0.2\tfront.example  {BITRIX.upper()}   # xray\n"
        "::ffff:127.0.0.3 other-alias.amocrm.ru\n",
    )
    assert cr.admin_pinned_addresses(BITRIX, path) == {cr._normal_ip("127.0.0.2")}
    assert cr.is_allowed_address("other-alias.amocrm.ru", "127.0.0.3", path)
    assert cr.admin_pinned_addresses("commented.bitrix24.ru", path) == set()


# ---- everything else keeps the old ban


@pytest.mark.parametrize("address", ["127.0.0.2", "10.0.0.5", "169.254.169.254", "192.168.1.9", "::1", "fd00::1"])
def test_unpinned_name_resolved_to_a_non_global_address_is_refused(tmp_path, monkeypatch, admin_file, address):
    path = hosts(tmp_path, "127.0.0.1 localhost\n")
    resolves_to(monkeypatch, address)
    with pytest.raises(cr.CrmError) as caught:
        amo_addresses(path)
    assert caught.value.code == "bad_url"

    sock, ctx = connected_to(monkeypatch, address)
    with pytest.raises(cr.CrmError) as caught:
        bitrix_connect(path)
    assert caught.value.code == "bad_url"
    ctx.wrap_socket.assert_not_called()
    sock.close.assert_called()


@pytest.mark.parametrize("address", ["10.0.0.5", "169.254.169.254", "127.0.0.3"])
def test_pin_does_not_open_other_addresses_for_the_same_name(tmp_path, monkeypatch, admin_file, address):
    path = hosts(tmp_path, f"127.0.0.2 {AMO} {BITRIX}\n")
    resolves_to(monkeypatch, address)
    with pytest.raises(cr.CrmError):
        amo_addresses(path)
    sock, ctx = connected_to(monkeypatch, address)
    with pytest.raises(cr.CrmError):
        bitrix_connect(path)
    ctx.wrap_socket.assert_not_called()


def test_pinned_and_global_addresses_are_kept_the_rest_dropped(tmp_path, monkeypatch, admin_file):
    path = hosts(tmp_path, f"127.0.0.2 {AMO}\n")
    resolves_to(monkeypatch, "10.0.0.5", "127.0.0.2", "93.184.216.34")
    assert amo_addresses(path) == ["127.0.0.2", "93.184.216.34"]


@pytest.mark.parametrize(
    "line",
    [
        "127.0.0.2 evil.example",
        "127.0.0.2 other.bitrix24.ru",
        "127.0.0.2 other.amocrm.ru",
        f"127.0.0.2 x.{BITRIX}",
        f"127.0.0.2 {BITRIX}.evil.example",
        f"127.0.0.2 x.{AMO}",
        "127.0.0.2 bitrix24.ru",
        "127.0.0.2 amocrm.ru",
        "127.0.0.2 .bitrix24.ru",
        "127.0.0.2 *.bitrix24.ru",
        "127.0.0.2 *.amocrm.ru",
        f"127.0.0.2 xacme.bitrix24.ru",
        f"127.0.0.2 {BITRIX[1:]}",
    ],
)
def test_a_pin_for_another_name_or_a_superset_or_suffix_allows_nothing(tmp_path, monkeypatch, admin_file, line):
    path = hosts(tmp_path, line + "\n")
    resolves_to(monkeypatch, "127.0.0.2")
    with pytest.raises(cr.CrmError):
        amo_addresses(path)
    connected_to(monkeypatch, "127.0.0.2")
    with pytest.raises(cr.CrmError):
        bitrix_connect(path)
    assert cr.admin_pinned_addresses(BITRIX, path) == set()
    assert cr.admin_pinned_addresses(AMO, path) == set()


# ---- the file must be the administrator's


def test_a_file_the_process_can_write_is_not_trusted(tmp_path, monkeypatch):
    path = hosts(tmp_path, f"127.0.0.2 {AMO}\n")
    monkeypatch.setattr(cr, "_can_write", lambda p, mode: True)
    resolves_to(monkeypatch, "127.0.0.2")
    with pytest.raises(cr.CrmError):
        amo_addresses(path)


def test_a_file_the_process_owns_is_not_trusted_without_any_patching(tmp_path, monkeypatch):
    path = hosts(tmp_path, f"127.0.0.2 {AMO}\n")
    resolves_to(monkeypatch, "127.0.0.2")
    with pytest.raises(cr.CrmError):
        amo_addresses(path)


@pytest.mark.parametrize("mode", [0o664, 0o666, 0o646, 0o622, 0o777])
def test_a_file_writable_by_group_or_others_is_not_trusted(tmp_path, monkeypatch, admin_file, mode):
    path = hosts(tmp_path, f"127.0.0.2 {AMO}\n", mode=mode)
    assert os.stat(path).st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    resolves_to(monkeypatch, "127.0.0.2")
    with pytest.raises(cr.CrmError):
        amo_addresses(path)


def test_a_file_not_owned_by_root_is_not_trusted(tmp_path, monkeypatch, admin_file):
    path = hosts(tmp_path, f"127.0.0.2 {AMO}\n")
    real = os.stat(path)
    monkeypatch.setattr(cr, "_stat_file", lambda p: SimpleNamespace(st_mode=real.st_mode, st_uid=10000, st_gid=10000))
    resolves_to(monkeypatch, "127.0.0.2")
    with pytest.raises(cr.CrmError):
        amo_addresses(path)


def test_a_root_owned_file_of_the_administrator_is_trusted(tmp_path, monkeypatch, admin_file):
    path = hosts(tmp_path, f"127.0.0.2 {AMO}\n")
    real = os.stat(path)
    monkeypatch.setattr(cr, "_stat_file", lambda p: SimpleNamespace(st_mode=real.st_mode, st_uid=0, st_gid=0))
    assert cr.is_allowed_address(AMO, "127.0.0.2", path)


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink-to-nothing"])
def test_an_unreadable_hosts_path_allows_nothing(tmp_path, kind):
    path = tmp_path / "hosts"
    if kind == "directory":
        path.mkdir()
    if kind == "symlink-to-nothing":
        path.symlink_to(tmp_path / "nowhere")
    assert cr.admin_pinned_addresses(AMO, str(path)) == set()
    assert not cr.is_allowed_address(AMO, "127.0.0.2", str(path))


def test_global_addresses_still_need_no_pin(tmp_path, monkeypatch):
    resolves_to(monkeypatch, "93.184.216.34")
    assert amo_addresses(str(tmp_path / "no-such-file")) == ["93.184.216.34"]
