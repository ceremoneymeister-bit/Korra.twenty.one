"""HTTP surface of the CRM connection: session auth, shape of answers, no secret out."""

from __future__ import annotations

import json
import logging
import stat
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from korra_cli import crm_connection as cc
from korra_cli import crm_readers as cr
from korra_cli import crm_sales as cs

from .crm_fakes import AMO_TOKEN, WEBHOOK, FakeAmo, FakeBitrix, amo_probe_handlers, bitrix_probe_handlers

SECRETS = ("abcdef1234567890", "sig-secret-xyz", "/rest/17/")


@pytest.fixture
def api(no_real_network, tmp_path, monkeypatch):
    from korra_cli import web_server

    cr.reset_pace()
    cs.reset()
    monkeypatch.setattr(cc, "get_default_hermes_root", lambda: tmp_path)
    monkeypatch.setattr(cc, "candidates", lambda: [])
    monkeypatch.setattr(cs, "_spawn", lambda target: target())
    state = SimpleNamespace(bitrix=FakeBitrix(bitrix_probe_handlers()), amo=FakeAmo(amo_probe_handlers()), root=tmp_path)

    def make(conn):
        if conn["type"] == cr.BITRIX:
            return cr.Bitrix24Reader(conn["webhook_url"], transport=state.bitrix, sleep=lambda s: None)
        return cr.AmoReader(conn["domain"], conn["token"], transport=state.amo, sleep=lambda s: None)

    monkeypatch.setattr(cc, "make_reader", make)
    client = TestClient(web_server.app)
    client.headers["X-Hermes-Session-Token"] = web_server._SESSION_TOKEN
    state.client = client
    yield state
    cr.reset_pace()
    cs.reset()


BITRIX = {"type": "bitrix24", "webhook_url": WEBHOOK}


def test_every_route_needs_the_session(api):
    from korra_cli import web_server

    anonymous = TestClient(web_server.app)
    calls = [
        ("GET", "/api/dashboard/crm", None),
        ("PUT", "/api/dashboard/crm", BITRIX),
        ("PATCH", "/api/dashboard/crm", {"stuck_days": 3}),
        ("DELETE", "/api/dashboard/crm", None),
        ("POST", "/api/dashboard/crm/check", BITRIX),
        ("POST", "/api/dashboard/crm/adopt", {"profile": "default", "type": "bitrix24"}),
    ]
    for method, path, body in calls:
        assert anonymous.request(method, path, json=body).status_code == 401, (method, path)
    assert cc.load(api.root) is None


def test_state_before_and_after_saving(api):
    assert api.client.get("/api/dashboard/crm").json() == {"state": "not_connected", "candidates": []}
    saved = api.client.put("/api/dashboard/crm", json=BITRIX)
    assert saved.status_code == 200 and saved.json()["ok"] is True
    state = api.client.get("/api/dashboard/crm").json()
    assert state["state"] == "connected" and state["connection"]["portal"] == "acme.bitrix24.ru"
    path = api.root / "crm-connection" / "connection.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_check_does_not_save(api):
    answer = api.client.post("/api/dashboard/crm/check", json=BITRIX).json()
    assert answer["ok"] is True and answer["found"]["deals"] == 46
    assert cc.load(api.root) is None


def test_crm_errors_are_ok_false_with_russian_text(api):
    api.bitrix.handlers["profile"] = lambda p: {"error": "INVALID_CREDENTIALS"}
    response = api.client.post("/api/dashboard/crm/check", json=BITRIX)
    body = response.json()
    assert response.status_code == 200 and body["ok"] is False and body["error"]["code"] == "bad_key"
    assert "ключ" in body["error"]["message"].lower()
    assert api.client.put("/api/dashboard/crm", json=BITRIX).json()["ok"] is False
    assert cc.load(api.root) is None


def test_settings_and_disconnect(api):
    api.client.put("/api/dashboard/crm", json=BITRIX)
    patched = api.client.patch("/api/dashboard/crm", json={"stuck_days": 3, "agents_access": False})
    assert patched.status_code == 200 and patched.json()["settings"]["stuck_days"] == 3
    bad = api.client.patch("/api/dashboard/crm", json={"stuck_days": 500})
    assert bad.status_code == 422 and bad.json()["ok"] is False and "дней" in bad.json()["error"]["message"]
    assert api.client.delete("/api/dashboard/crm").json() == {"state": "not_connected"}
    assert cc.load(api.root) is None
    assert api.client.patch("/api/dashboard/crm", json={"stuck_days": 3}).status_code == 404


def test_adopt_unknown_key_is_404(api):
    response = api.client.post("/api/dashboard/crm/adopt", json={"profile": "ghost", "type": "bitrix24"})
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"


def test_replacing_the_key_restarts_the_card_cache(api):
    api.client.put("/api/dashboard/crm", json=BITRIX)
    assert cs.sales_section(root=api.root)["status"] in {"ok", "error"}
    api.client.delete("/api/dashboard/crm")
    assert cs.sales_section(root=api.root)["status"] == "not_connected"


def test_garbage_body_does_not_echo_the_key(api):
    response = api.client.put(
        "/api/dashboard/crm", content=b'{"type": "bitrix24", "webhook_url": ["' + WEBHOOK.encode() + b'"]',
        headers={"content-type": "application/json"},
    )
    assert not any(secret in response.text for secret in SECRETS)
    response = api.client.put("/api/dashboard/crm", json={"type": "bitrix24", "webhook_url": 12})
    assert response.json()["ok"] is False


def test_no_answer_and_no_log_carries_the_secret(api, caplog):
    caplog.set_level(logging.DEBUG)
    texts = [
        api.client.post("/api/dashboard/crm/check", json=BITRIX).text,
        api.client.put("/api/dashboard/crm", json=BITRIX).text,
        api.client.get("/api/dashboard/crm").text,
        api.client.patch("/api/dashboard/crm", json={"stuck_days": 5}).text,
        json.dumps(api.client.get("/api/dashboard/state").json().get("sales")),
    ]
    api.bitrix.handlers["profile"] = lambda p: OSError(f"boom {WEBHOOK}")
    texts.append(api.client.post("/api/dashboard/crm/check", json=BITRIX).text)
    amo = {"type": "amocrm", "domain": "acme.amocrm.ru", "token": AMO_TOKEN}
    texts.append(api.client.put("/api/dashboard/crm", json=amo).text)
    blob = "\n".join(texts) + "\n" + caplog.text
    assert not any(secret in blob for secret in SECRETS)


def test_state_route_carries_the_sales_section(api):
    assert api.client.get("/api/dashboard/state").json()["sales"] == {"status": "not_connected", "candidates": []}
    api.client.put("/api/dashboard/crm", json=BITRIX)
    sales = api.client.get("/api/dashboard/state").json()["sales"]
    assert sales["status"] in {"ok", "error"} and sales["connection"]["source_label"] == "Битрикс24"


def test_checking_unsaved_credentials_keeps_the_card_cache(api):
    assert api.client.put("/api/dashboard/crm", json=BITRIX).json()["ok"] is True
    cs.sales_section(root=api.root)
    assert cs._state
    other = {"type": "bitrix24", "webhook_url": "https://other.bitrix24.ru/rest/5/zzzzzzzzzzzz1234/"}
    api.client.post("/api/dashboard/crm/check", json=other)
    api.client.post("/api/dashboard/crm/check", json={})
    assert cs._state
