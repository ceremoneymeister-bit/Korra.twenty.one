"""Agent tool ``crm_sales``: owner turns only, the connection's switch, no key in the chat."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from gateway.principal import Principal
from korra_cli import crm_connection as cc
from korra_cli import crm_readers as cr
from korra_cli import crm_sales as cs
from tools import crm_sales_tool as tool

from .crm_fakes import AMO_TOKEN, WEBHOOK, FakeAmo, FakeBitrix, amo_probe_handlers
from .test_crm_sales import NOW, amo_handlers, bitrix_handlers

SECRETS = ("abcdef1234567890", "/rest/17/")


@pytest.fixture
def desk(no_real_network, tmp_path, monkeypatch):
    cr.reset_pace()
    cs.reset()
    monkeypatch.setattr(cc, "get_default_hermes_root", lambda: tmp_path)
    monkeypatch.setattr(cc, "candidates", lambda: [])
    monkeypatch.setattr(cs, "_spawn", lambda target: target())
    monkeypatch.setattr(cs, "_now", lambda tz: NOW)
    monkeypatch.setattr(tool, "current_principal", lambda: Principal("owner", owner=True, live=True))
    state = SimpleNamespace(bitrix=FakeBitrix(bitrix_handlers()), amo=FakeAmo(amo_handlers()), root=tmp_path)

    def make(conn):
        if conn["type"] == cr.BITRIX:
            return cr.Bitrix24Reader(conn["webhook_url"], transport=state.bitrix, sleep=lambda s: None)
        return cr.AmoReader(conn["domain"], conn["token"], transport=state.amo, sleep=lambda s: None)

    monkeypatch.setattr(cc, "make_reader", make)
    yield state
    cr.reset_pace()
    cs.reset()


def run(**args) -> dict:
    return json.loads(tool._handle(args))


def connect(desk):
    assert cc.save({"type": "bitrix24", "webhook_url": WEBHOOK}, root=desk.root)["ok"]
    desk.bitrix.calls.clear()


def test_owner_reads_overview_and_stuck(desk):
    connect(desk)
    overview = run()
    assert overview["ok"] and overview["won"]["amount"] == 1400000 and overview["stuck"]["count"] == 2
    assert overview["river"][1] == {"stage": "КП отправлено", "deals": 3, "amount": 1000000, "stuck": 2}
    stuck = run(action="stuck", limit=1)
    assert [d["id"] for d in stuck["deals"]] == ["12"] and stuck["deals"][0]["stage"] == "КП отправлено"
    assert stuck["deals"][0]["url"].startswith("https://acme.bitrix24.ru/")


def test_one_deal_is_read_live_and_only_by_number(desk):
    connect(desk)
    desk.bitrix.handlers["crm.deal.get"] = lambda p: {
        "result": {"ID": p["id"], "TITLE": "Склад", "OPPORTUNITY": "500000", "STAGE_ID": "PROPOSAL", "CLOSED": "N"}
    }
    deal = run(action="deal", deal_id="12")["deal"]
    assert deal["title"] == "Склад" and deal["url"].endswith("/crm/deal/details/12/")
    for bad in ("12; DROP", "", "x" * 20):
        assert run(action="deal", deal_id=bad)["error"] == "invalid_request"


def test_amo_deal_read(desk):
    desk.amo.handlers = amo_probe_handlers()
    cc.save({"type": "amocrm", "domain": "acme.amocrm.ru", "token": AMO_TOKEN}, root=desk.root)
    desk.amo.handlers["leads/102"] = lambda q: {"id": 102, "name": "Склад", "price": 500000, "status_id": 12,
                                                "responsible_user_id": 8, "created_at": 1, "updated_at": 2}
    deal = run(action="deal", deal_id="102")["deal"]
    assert deal["title"] == "Склад" and deal["url"] == "https://acme.amocrm.ru/leads/detail/102"


def test_visitor_of_a_public_bot_gets_nothing_and_crm_is_not_asked(desk, monkeypatch):
    connect(desk)
    monkeypatch.setattr(tool, "current_principal", lambda: Principal("outsider", owner=False, live=True))
    assert run()["error"] == "owner_only"
    assert run(action="deal", deal_id="12")["error"] == "owner_only"
    assert tool._crm_available() is False
    assert desk.bitrix.calls == []


def test_background_run_for_the_owner_may_read(desk, monkeypatch):
    connect(desk)
    monkeypatch.setattr(tool, "current_principal", lambda: Principal("cron", owner=True, live=False))
    assert run()["ok"] is True and tool._crm_available() is True


def test_switch_off_denies_the_next_call_even_mid_conversation(desk):
    connect(desk)
    assert run()["ok"] is True
    cc.update_settings({"agents_access": False}, root=desk.root)
    denied = run()
    assert denied["error"] == "agents_off" and "won" not in denied
    cc.update_settings({"agents_access": True}, root=desk.root)
    assert run()["ok"] is True


def test_not_connected_points_to_the_window_and_forbids_asking_for_a_key(desk):
    answer = run()
    assert answer["error"] == "not_connected"
    assert "/?crm=connect" in answer["next_step"] and "ключ" in answer["next_step"].lower()
    assert "не просите" in answer["next_step"].lower()
    assert desk.bitrix.calls == []


def test_rejected_key_gives_owner_steps_not_the_key(desk):
    connect(desk)
    desk.bitrix.handlers["profile"] = lambda p: {"error": "INVALID_CREDENTIALS"}
    cc.recheck(root=desk.root)
    answer = run()
    assert answer["error"] == "bad_key" and "/?crm=connect" in answer["next_step"]
    assert not any(secret in json.dumps(answer) for secret in SECRETS)


def test_no_answer_carries_the_secret(desk):
    connect(desk)
    desk.bitrix.handlers["crm.deal.get"] = lambda p: OSError(WEBHOOK)
    blob = json.dumps([run(), run(action="stuck"), run(action="deal", deal_id="5"), run(action="nope")])
    assert not any(secret in blob for secret in SECRETS)


def test_tool_is_registered_as_a_configurable_toolset():
    from korra_cli.tools_config import CONFIGURABLE_TOOLSETS
    from tools.registry import registry
    from toolsets import TOOLSETS

    assert registry.get_entry("crm_sales").toolset == "crm_sales"
    assert any(key == "crm_sales" for key, *_ in CONFIGURABLE_TOOLSETS)
    assert TOOLSETS["crm_sales"]["tools"] == ["crm_sales"]


def _stale_desk(desk, monkeypatch):
    connect(desk)
    assert run()["ok"] is True
    clock = [time.time() + 400]
    monkeypatch.setattr(cs, "_clock", lambda: clock[0])
    desk.bitrix.handlers["crm.deal.list"] = lambda p: OSError("down")


@pytest.mark.parametrize("action", ["overview", "stuck"])
def test_every_summary_action_reports_a_failed_refresh(desk, monkeypatch, action):
    _stale_desk(desk, monkeypatch)
    run(action=action)  # the refresh fails in the background
    answer = run(action=action)
    assert answer["ok"] is True and answer["stale"] is True
    assert answer["error"]["code"] == "network" and answer["as_of"] and "последн" in answer["note"]


@pytest.mark.parametrize("action", ["overview", "stuck"])
def test_fresh_data_has_no_failure_marks(desk, action):
    connect(desk)
    answer = run(action=action)
    assert answer["stale"] is False and "error" not in answer and "note" not in answer
