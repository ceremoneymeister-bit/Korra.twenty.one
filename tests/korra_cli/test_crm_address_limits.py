"""Куда может ходить подключение CRM: только домены поставщиков, только глобальные адреса,
сокет — не из HTTP API (K21-322, F3)."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock

import pytest

from korra_cli import crm_connection as cc
from korra_cli import crm_readers as cr

TOKEN = "synthetic-amo-token"


@pytest.fixture(autouse=True)
def closed_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("real network forbidden")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


@pytest.mark.parametrize(
    "host",
    [
        "x.bitrix24.test",
        "x.bitrix24.zz",
        "x.bitrix24.localhost",
        "x.bitrix24.ru.evil.example",
        "bitrix24.ru.evil.example",
        "x.y.bitrix24.ru",
        "x.bitrix24.xyz",
    ],
)
def test_bitrix_host_outside_vendor_domains_is_refused(host):
    with pytest.raises(cr.CrmError) as caught:
        cr.parse_bitrix_webhook(f"https://{host}/rest/1/abcdefgh12345678/")
    assert caught.value.code in {"bad_url", "self_hosted"}


@pytest.mark.parametrize(
    "host",
    [
        "x.bitrix24.ru", "x.bitrix24.com", "x.bitrix24.by", "x.bitrix24.kz", "x.bitrix24.ua",
        "x.bitrix24.de", "x.bitrix24.com.br", "x.bitrix24.co.uk", "x-1.bitrix24.com.tr",
    ],
)
def test_bitrix_cloud_domains_are_accepted(host):
    assert cr.parse_bitrix_webhook(f"https://{host}/rest/1/abcdefgh12345678/")[0] == host


def test_self_hosted_bitrix_gets_its_own_explanation():
    with pytest.raises(cr.CrmError) as caught:
        cr.parse_bitrix_webhook("https://crm.company.example/rest/1/abcdefgh12345678/")
    assert caught.value.code == "self_hosted"
    text = cr.describe_error("self_hosted", cr.BITRIX)
    assert "коробоч" in text["message"].lower()
    assert text["retry"] is False


def test_malformed_address_stays_bad_url():
    with pytest.raises(cr.CrmError) as caught:
        cr.parse_bitrix_webhook("http://x.bitrix24.ru/rest/1/abcdefgh12345678/")
    assert caught.value.code == "bad_url"


@pytest.mark.parametrize("address", ["169.254.169.254", "10.0.0.5", "127.0.0.1", "192.168.1.9", "100.64.0.1", "::1", "fd00::1", "::ffff:10.0.0.5"])
def test_amo_refuses_non_global_resolution(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", (address, 443))])
    with pytest.raises(cr.CrmError) as caught:
        cr.AmoTransport("synthetic.amocrm.ru")._addresses()
    assert caught.value.code == "bad_url"


def test_amo_keeps_only_global_addresses(monkeypatch):
    rows = [(2, 1, 6, "", (a, 443)) for a in ("10.0.0.5", "93.184.216.34")]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: rows)
    assert cr.AmoTransport("synthetic.amocrm.ru")._addresses() == ["93.184.216.34"]


def test_amo_checks_the_address_actually_connected(monkeypatch):
    sock = MagicMock()
    sock.getpeername.return_value = ("169.254.169.254", 443)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: sock)
    ctx = MagicMock()
    monkeypatch.setattr(cr.ssl, "create_default_context", lambda: ctx)
    with pytest.raises(cr.CrmError) as caught:
        cr.AmoTransport("synthetic.amocrm.ru")._connect("93.184.216.34")
    assert caught.value.code == "bad_url"
    ctx.wrap_socket.assert_not_called()
    sock.close.assert_called()


def test_bitrix_transport_checks_the_address_actually_connected(monkeypatch):
    sock = MagicMock()
    sock.getpeername.return_value = ("10.1.2.3", 443)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: sock)
    ctx = MagicMock()
    monkeypatch.setattr(cr.ssl, "create_default_context", lambda: ctx)
    with pytest.raises(cr.CrmError) as caught:
        cr.default_bitrix_transport("POST", "https://x.bitrix24.ru/rest/1/abcdefgh12345678/crm.deal.list.json", {}, b"")
    assert caught.value.code == "bad_url"
    ctx.wrap_socket.assert_not_called()


def test_unix_socket_route_keeps_tls_and_skips_address_check(monkeypatch):
    seen = []
    fake = MagicMock()
    fake.connect.side_effect = seen.append
    monkeypatch.setattr(socket, "socket", lambda *a: fake)
    ctx = MagicMock()
    monkeypatch.setattr(cr.ssl, "create_default_context", lambda: ctx)
    cr.AmoTransport("synthetic.amocrm.ru", "/run/amo/amo.sock")._connect("unix")
    assert seen == ["/run/amo/amo.sock"]
    ctx.wrap_socket.assert_called_once_with(fake, server_hostname="synthetic.amocrm.ru")


def test_http_api_cannot_set_the_socket():
    conn = cc.build({"type": "amocrm", "domain": "synthetic.amocrm.ru", "token": TOKEN, "unix_socket": "/run/evil.sock"})
    assert "unix_socket" not in conn


def test_trusted_route_is_validated():
    payload = {"type": "amocrm", "domain": "synthetic.amocrm.ru", "token": TOKEN}
    assert cc.build(payload, socket_path="/run/amo/amo.sock")["unix_socket"] == "/run/amo/amo.sock"
    for bad in ("relative.sock", "/run/../x.sock", "/run/a\0b"):
        with pytest.raises(cr.CrmError):
            cc.build(payload, socket_path=bad)


def test_save_via_api_drops_socket_but_adopt_keeps_agent_route(tmp_path, monkeypatch):
    probed = []
    monkeypatch.setattr(cc, "probe", lambda conn: probed.append(conn.get("unix_socket")) or {"pipelines": []})
    cc.save({"type": "amocrm", "domain": "synthetic.amocrm.ru", "token": TOKEN, "unix_socket": "/run/evil.sock"}, root=tmp_path)
    assert probed == [None] and "unix_socket" not in cc.load(tmp_path)

    env = {"type": "amocrm", "domain": "synthetic.amocrm.ru", "token": TOKEN, "unix_socket": "/run/amo/amo.sock"}
    agent = MagicMock(profile="sales", label="Нюра")
    monkeypatch.setattr(cc, "_agent_keys", lambda: [(agent, env)])
    cc.adopt("sales", "amocrm", root=tmp_path)
    assert probed[-1] == "/run/amo/amo.sock"
    assert cc.load(tmp_path)["unix_socket"] == "/run/amo/amo.sock"


# --- F10: replacing the token of the same account keeps the adopted route ---

SOCKET = "/run/amo/amo.sock"


def _stored_amo(tmp_path, socket_path=SOCKET):
    record = {"type": "amocrm", "domain": "synthetic.amocrm.ru", "token": "old-token", "settings": {"agents_access": True}}
    if socket_path:
        record["unix_socket"] = socket_path
    cc._write(record, tmp_path)


def test_replacing_token_keeps_route_on_check_and_save(tmp_path, monkeypatch):
    _stored_amo(tmp_path)
    seen = []
    monkeypatch.setattr(cc, "probe", lambda conn: seen.append(conn.get("unix_socket")) or {"pipelines": []})
    payload = {"type": "amocrm", "domain": "synthetic.amocrm.ru", "token": "new-token"}
    assert cc.check(payload, root=tmp_path)["ok"]
    assert seen == [SOCKET]
    out = cc.save(payload, root=tmp_path)
    assert out["ok"] and seen == [SOCKET, SOCKET]
    stored = cc.load(tmp_path)
    assert stored["token"] == "new-token" and stored["unix_socket"] == SOCKET


def test_route_survives_a_network_failure_on_replacement(tmp_path, monkeypatch):
    _stored_amo(tmp_path)

    def down(conn):
        assert conn.get("unix_socket") == SOCKET
        raise cr.CrmError("network")

    monkeypatch.setattr(cc, "probe", down)
    out = cc.save({"type": "amocrm", "domain": "synthetic.amocrm.ru", "token": "new-token"}, root=tmp_path)
    assert out["ok"] and out["warning"]["code"] == "network"
    assert cc.load(tmp_path)["unix_socket"] == SOCKET


def test_route_is_not_carried_to_another_account(tmp_path, monkeypatch):
    _stored_amo(tmp_path)
    seen = []
    monkeypatch.setattr(cc, "probe", lambda conn: seen.append(conn.get("unix_socket")) or {"pipelines": []})
    cc.save({"type": "amocrm", "domain": "other.amocrm.ru", "token": "tok"}, root=tmp_path)
    assert seen == [None] and "unix_socket" not in cc.load(tmp_path)
