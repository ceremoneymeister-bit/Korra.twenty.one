"""Installation-level CRM connection: check, save, settings, takeover, secrecy."""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from korra_cli import crm_connection as cc
from korra_cli import crm_readers as cr

from .crm_fakes import AMO_TOKEN, WEBHOOK, FakeAmo, FakeBitrix, amo_probe_handlers, bitrix_probe_handlers

SECRETS = ("abcdef1234567890", "sig-secret-xyz", "tok-socket")


@pytest.fixture(autouse=True)
def _isolated(no_real_network):
    cr.reset_pace()
    yield
    cr.reset_pace()


@pytest.fixture
def portal(monkeypatch):
    """Route readers to doubles; ``portal.bitrix`` / ``portal.amo`` are editable."""
    state = SimpleNamespace(bitrix=FakeBitrix(bitrix_probe_handlers()), amo=FakeAmo(amo_probe_handlers()))

    def make(conn):
        if conn["type"] == cr.BITRIX:
            return cr.Bitrix24Reader(conn["webhook_url"], transport=state.bitrix, sleep=lambda s: None)
        return cr.AmoReader(
            conn["domain"], conn["token"], unix_socket=conn.get("unix_socket", ""), transport=state.amo,
            sleep=lambda s: None,
        )

    monkeypatch.setattr(cc, "make_reader", make)
    return state


BITRIX_BODY = {"type": "bitrix24", "webhook_url": WEBHOOK}
AMO_BODY = {"type": "amocrm", "domain": "acme.amocrm.ru", "token": AMO_TOKEN}


def test_check_reports_what_was_found_and_saves_nothing(portal, tmp_path):
    out = cc.check(BITRIX_BODY, root=tmp_path)
    found = out["found"]
    assert out["ok"] is True
    assert (found["portal"], found["user"], found["deals"], found["managers"], found["tasks"]) == (
        "acme.bitrix24.ru", "Анна Миронова", 46, 8, True,
    )
    assert [p["name"] for p in found["pipelines"]] == ["Продажи", "Партнёры"]
    assert found["pipeline_id"] == "0"
    assert not (tmp_path / cc.STORE_DIR).exists()
    assert not any(s in json.dumps(out) for s in SECRETS)


def test_check_amo_resolves_the_token_owner_from_the_jwt(portal, tmp_path):
    found = cc.check(AMO_BODY, root=tmp_path)["found"]
    assert found["user"] == "Ирина"
    assert (found["portal"], found["deals"], found["managers"]) == ("acme.amocrm.ru", 5, 2)
    assert found["pipelines"] == [{"id": "900", "name": "Продажи"}] and found["pipeline_id"] == "900"


def test_amo_open_deals_filter_lists_only_open_statuses(portal, tmp_path):
    cc.check(AMO_BODY, root=tmp_path)
    query = next(q for path, q in portal.amo.calls if path == "leads")
    statuses = {v[0] for k, v in query.items() if k.endswith("[status_id]")}
    assert statuses == {"11", "12"}


def test_check_without_tasks_permission_still_ok(portal, tmp_path):
    portal.bitrix.handlers["tasks.task.list"] = lambda p: {"error": "ACCESS_DENIED", "error_description": "no"}
    found = cc.check(BITRIX_BODY, root=tmp_path)["found"]
    assert found["tasks"] is False


@pytest.mark.parametrize(
    "body, code",
    [
        ({"type": "bitrix24", "webhook_url": "https://evil.example.com/rest/1/abcdefgh12345678/"}, "bad_url"),
        ({"type": "bitrix24", "webhook_url": ""}, "bad_url"),
        ({"type": "amocrm", "domain": "evil.com", "token": "t"}, "bad_url"),
        ({"type": "amocrm", "domain": "acme.amocrm.ru", "token": ""}, "bad_key"),
        ({"type": "amocrm", "domain": "acme.amocrm.ru", "token": "t", "unix_socket": "relative"}, "bad_url"),
    ],
)
def test_check_rejects_bad_input_in_russian(portal, tmp_path, body, code):
    out = cc.check(body, root=tmp_path)
    assert out["ok"] is False and out["error"]["code"] == code
    assert out["error"]["title"] and out["error"]["message"]


def test_check_maps_crm_answers(portal, tmp_path):
    portal.bitrix.handlers["profile"] = lambda p: {"error": "INVALID_CREDENTIALS"}
    assert cc.check(BITRIX_BODY, root=tmp_path)["error"]["code"] == "bad_key"
    portal.bitrix.handlers["profile"] = lambda p: {"error": "ACCESS_DENIED", "error_description": "commercial plan"}
    out = cc.check(BITRIX_BODY, root=tmp_path)
    assert out["error"]["code"] == "plan_closed" and "Маркетплейс" in out["error"]["message"]
    portal.bitrix.handlers["profile"] = lambda p: OSError("down")
    out = cc.check(BITRIX_BODY, root=tmp_path)
    assert out["error"]["code"] == "network" and out["error"]["retry"] is True
    assert "acme.bitrix24.ru" in out["error"]["message"]


def test_save_writes_private_file_outside_profiles(portal, tmp_path):
    out = cc.save({**BITRIX_BODY}, root=tmp_path)
    assert out["ok"] is True
    path = tmp_path / "crm-connection" / "connection.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    stored = json.loads(path.read_text())
    assert stored["webhook_url"] == WEBHOOK and stored["settings"] == {
        "pipeline_id": "0", "stuck_days": 7, "agents_access": True,
    }
    assert not any(s in json.dumps(out) for s in SECRETS)
    assert not (tmp_path / "config.yaml").exists()


def test_save_amo_keeps_socket_route_and_hides_it(portal, tmp_path):
    out = cc.save({**AMO_BODY, "unix_socket": "/run/tok-socket/amo.sock"}, root=tmp_path)
    assert out["connection"]["route"] == "socket"
    assert cc.load(tmp_path)["unix_socket"] == "/run/tok-socket/amo.sock"
    assert not any(s in json.dumps(out) for s in SECRETS)


def test_rejected_key_is_not_saved(portal, tmp_path):
    portal.bitrix.handlers["profile"] = lambda p: {"error": "INVALID_CREDENTIALS"}
    out = cc.save(BITRIX_BODY, root=tmp_path)
    assert out["ok"] is False and out["error"]["code"] == "bad_key" and out["error"]["saved"] is False
    assert cc.load(tmp_path) is None


def test_network_failure_keeps_the_key_and_says_so(portal, tmp_path):
    portal.amo.handlers["account"] = lambda q: OSError("down")
    out = cc.save(AMO_BODY, root=tmp_path)
    assert out["ok"] is True and out["warning"]["code"] == "network" and out["warning"]["saved"] is True
    assert cc.load(tmp_path)["token"] == AMO_TOKEN
    assert cc.public(cc.load(tmp_path))["last_check"] == {
        "ok": False, "at": cc.public(cc.load(tmp_path))["last_check"]["at"], "code": "network",
    }


def test_status_never_contains_the_secret(portal, tmp_path):
    cc.save({**AMO_BODY, "unix_socket": "/run/tok-socket/amo.sock"}, root=tmp_path)
    text = json.dumps(cc.status(root=tmp_path))
    assert not any(s in text for s in SECRETS)
    assert json.loads(text)["connection"]["portal"] == "acme.amocrm.ru"


def test_failures_do_not_log_secrets(portal, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    portal.bitrix.handlers["profile"] = lambda p: {"error": "INVALID_CREDENTIALS"}
    cc.save(BITRIX_BODY, root=tmp_path)
    portal.amo.handlers["account"] = lambda q: OSError("down")
    cc.save(AMO_BODY, root=tmp_path)
    assert not any(s in caplog.text for s in SECRETS)


def test_replacing_the_key_keeps_settings_of_the_same_portal(portal, tmp_path):
    cc.save(BITRIX_BODY, root=tmp_path)
    cc.update_settings({"pipeline_id": "4", "stuck_days": 14, "agents_access": False}, root=tmp_path)
    new = WEBHOOK.replace("abcdef1234567890", "zyxwvu9876543210")
    cc.save({"type": "bitrix24", "webhook_url": new}, root=tmp_path)
    assert cc.load(tmp_path)["webhook_url"] == new
    assert cc.public(cc.load(tmp_path))["settings"] == {"pipeline_id": "4", "stuck_days": 14, "agents_access": False}


def test_switching_crm_resets_settings(portal, tmp_path):
    cc.save(BITRIX_BODY, root=tmp_path)
    cc.update_settings({"stuck_days": 14}, root=tmp_path)
    cc.save(AMO_BODY, root=tmp_path)
    assert cc.public(cc.load(tmp_path))["settings"] == {"pipeline_id": "900", "stuck_days": 7, "agents_access": True}


@pytest.mark.parametrize(
    "patch",
    [{"stuck_days": 0}, {"stuck_days": 91}, {"stuck_days": True}, {"stuck_days": "7"}, {"agents_access": "yes"},
     {"pipeline_id": "abc"}, {"pipeline_id": "777"}],
)
def test_settings_validation(portal, tmp_path, patch):
    cc.save(BITRIX_BODY, root=tmp_path)
    before = cc.load(tmp_path)["settings"]
    with pytest.raises(cc.CrmConnectionError) as caught:
        cc.update_settings(patch, root=tmp_path)
    assert caught.value.status_code == 422
    assert cc.load(tmp_path)["settings"] == before


def test_settings_without_connection(tmp_path):
    with pytest.raises(cc.CrmConnectionError) as caught:
        cc.update_settings({"stuck_days": 3}, root=tmp_path)
    assert caught.value.status_code == 404


def test_recheck_records_outcome_and_failure_keeps_key(portal, tmp_path):
    cc.save(BITRIX_BODY, root=tmp_path)
    portal.bitrix.handlers["profile"] = lambda p: {"error": "INVALID_CREDENTIALS"}
    out = cc.recheck(root=tmp_path)
    assert out["ok"] is False and out["error"]["saved"] is True
    assert cc.public(cc.load(tmp_path))["last_check"]["code"] == "bad_key"
    portal.bitrix.handlers["profile"] = bitrix_probe_handlers()["profile"]
    assert cc.recheck(root=tmp_path)["ok"] is True
    assert cc.public(cc.load(tmp_path))["last_check"]["ok"] is True


def test_check_with_empty_body_checks_the_saved_connection(portal, tmp_path):
    cc.save(BITRIX_BODY, root=tmp_path)
    assert cc.check({}, root=tmp_path)["ok"] is True


def test_disconnect_removes_the_file(portal, tmp_path):
    cc.save(BITRIX_BODY, root=tmp_path)
    assert cc.disconnect(root=tmp_path) == {"state": "not_connected"}
    assert cc.load(tmp_path) is None and not (tmp_path / cc.STORE_DIR).exists()
    cc.disconnect(root=tmp_path)


def test_symlinked_storage_is_refused(portal, tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    (tmp_path / cc.STORE_DIR).symlink_to(target)
    with pytest.raises(cc.CrmConnectionError) as caught:
        cc.save(BITRIX_BODY, root=tmp_path)
    assert caught.value.code == "unsafe_storage"
    assert list(target.iterdir()) == []


def test_corrupt_file_is_an_error_not_a_crash_of_the_secret(tmp_path):
    folder = tmp_path / cc.STORE_DIR
    folder.mkdir()
    (folder / cc.STORE_FILE).write_text("{not json")
    with pytest.raises(cc.CrmConnectionError):
        cc.load(tmp_path)


def test_agent_access_follows_the_switch(portal, tmp_path):
    assert cc.agent_access(tmp_path) is None
    cc.save(BITRIX_BODY, root=tmp_path)
    assert cc.agent_access(tmp_path)["type"] == "bitrix24"
    cc.update_settings({"agents_access": False}, root=tmp_path)
    assert cc.agent_access(tmp_path) is None


def test_old_file_without_settings_gets_defaults(tmp_path):
    folder = tmp_path / cc.STORE_DIR
    folder.mkdir()
    (folder / cc.STORE_FILE).write_text(json.dumps({"type": "bitrix24", "webhook_url": WEBHOOK}))
    view = cc.public(cc.load(tmp_path))
    assert view["settings"] == {"pipeline_id": "", "stuck_days": 7, "agents_access": True}


# ---------------------------------------------------------------- taking over an agent's key


def _agents(monkeypatch, tmp_path, envs: dict[str, str]):
    agents = []
    for name, text in envs.items():
        home = tmp_path / "profiles" / name
        home.mkdir(parents=True)
        (home / ".env").write_text(text)
        agents.append(SimpleNamespace(profile="" if name == "default" else name, name=name, home=home, label=name.title()))
    from korra_cli import dashboard_state

    monkeypatch.setattr(dashboard_state, "list_agents", lambda: agents)
    return agents


def test_candidates_list_agent_keys_without_secrets(monkeypatch, tmp_path):
    _agents(
        monkeypatch,
        tmp_path,
        {
            "default": "OPENAI_API_KEY=x\n",
            "crm": f'BITRIX24_WEBHOOK_URL="{WEBHOOK}"\n',
            "amo": f"export AMOCRM_DOMAIN=acme.amocrm.ru\nAMOCRM_LONG_TERM_TOKEN={AMO_TOKEN}\n",
            "junk": "BITRIX24_WEBHOOK_URL=https://evil.example/rest/1/abcdefgh12345678/\nAMOCRM_DOMAIN=acme.amocrm.ru\n",
        },
    )
    found = cc.candidates()
    assert [(c["profile"], c["type"], c["portal"]) for c in found] == [
        ("crm", "bitrix24", "acme.bitrix24.ru"),
        ("amo", "amocrm", "acme.amocrm.ru"),
    ]
    assert not any(s in json.dumps(found) for s in SECRETS)
    assert cc.status(root=tmp_path / "root")["candidates"] == found


def test_adopt_copies_key_and_socket_and_leaves_env_untouched(monkeypatch, portal, tmp_path):
    agents = _agents(
        monkeypatch,
        tmp_path,
        {
            "amo": (
                "AMOCRM_DOMAIN=acme.amocrm.ru\n"
                f"AMOCRM_LONG_TERM_TOKEN={AMO_TOKEN}\n"
                "AMOCRM_UNIX_SOCKET=/run/tok-socket/amo.sock\n"
            )
        },
    )
    env = agents[0].home / ".env"
    before = (env.read_bytes(), env.stat().st_mtime_ns)
    root = tmp_path / "root"
    out = cc.adopt("amo", "amocrm", root=root)
    assert out["ok"] is True and out["connection"]["route"] == "socket"
    stored = cc.load(root)
    assert (stored["domain"], stored["token"], stored["unix_socket"]) == (
        "acme.amocrm.ru", AMO_TOKEN, "/run/tok-socket/amo.sock",
    )
    assert (env.read_bytes(), env.stat().st_mtime_ns) == before
    assert not any(s in json.dumps(out) for s in SECRETS)


def test_adopt_unknown_profile_or_type(monkeypatch, portal, tmp_path):
    _agents(monkeypatch, tmp_path, {"crm": f"BITRIX24_WEBHOOK_URL={WEBHOOK}\n"})
    for profile, kind in (("nope", "bitrix24"), ("crm", "amocrm"), ("../x", "bitrix24")):
        with pytest.raises(cc.CrmConnectionError) as caught:
            cc.adopt(profile, kind, root=tmp_path / "root")
        assert caught.value.status_code == 404
