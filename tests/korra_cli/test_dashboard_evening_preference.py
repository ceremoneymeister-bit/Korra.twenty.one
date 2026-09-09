"""Explicit evening choices use the existing durable dashboard preference API."""
import asyncio
from copy import deepcopy
from unittest.mock import patch
import pytest
from korra_cli import web_server as ws
from korra_cli.web_models import ThemeSetBody

@pytest.fixture
def state(monkeypatch):
    config = {"dashboard": {"theme": "light"}}
    writes = []
    monkeypatch.setattr(ws, "load_config", lambda: deepcopy(config))
    def save(value, **_kwargs):
        writes.append(deepcopy(value)); config.clear(); config.update(deepcopy(value))
    monkeypatch.setattr(ws, "save_config", save)
    monkeypatch.setattr(ws, "get_install_id", lambda: "a" * 32)
    return config, writes

@pytest.mark.parametrize("action", ["later", "disable"])
def test_explicit_action_has_durable_ack_and_preserves_palette(state, action):
    before = asyncio.run(ws.get_dashboard_themes())["preference"]
    response = asyncio.run(ws.set_dashboard_theme(ThemeSetBody(name="light", revision=before["revision"], evening_action=action)))
    ack = response["preference"]
    assert ack["evening"]["disabled"] is (action == "disable")
    assert (ack["evening"]["snooze_until"] > 0) is (action == "later")
    assert ack == asyncio.run(ws.get_dashboard_themes())["preference"]
    assert len(state[1]) == 1


def test_later_is_fourteen_days_using_server_time(state):
    with patch("time.time", return_value=1788956000):
        ack = asyncio.run(ws.set_dashboard_theme(ThemeSetBody(name="light", evening_action="later")))["preference"]
    assert ack["evening"]["snooze_until"] == 1788956000 + 14 * 86400


def test_explicit_dark_disables_offer_until_explicit_reset(state):
    asyncio.run(ws.set_dashboard_theme(ThemeSetBody(name="dark")))
    asyncio.run(ws.set_dashboard_theme(ThemeSetBody(name="light")))
    assert asyncio.run(ws.get_dashboard_themes())["preference"]["evening"]["disabled"] is True
    asyncio.run(ws.set_dashboard_theme(ThemeSetBody(name="light", evening_action="enable")))
    assert asyncio.run(ws.get_dashboard_themes())["preference"]["evening"] == {"disabled": False, "snooze_until": 0}


def test_failed_save_cannot_ack_or_change_durable_state(state, monkeypatch):
    def fail(_, **_kwargs): raise OSError("synthetic write failure")
    monkeypatch.setattr(ws, "save_config", fail)
    with pytest.raises(OSError):
        asyncio.run(ws.set_dashboard_theme(ThemeSetBody(name="light", evening_action="disable")))
    assert asyncio.run(ws.get_dashboard_themes())["preference"]["evening"]["disabled"] is False


def test_reset_while_dark_survives_a_later_light_choice(state):
    asyncio.run(ws.set_dashboard_theme(ThemeSetBody(name="dark")))
    asyncio.run(ws.set_dashboard_theme(ThemeSetBody(evening_action="enable")))
    asyncio.run(ws.set_dashboard_theme(ThemeSetBody(name="light")))
    assert asyncio.run(ws.get_dashboard_themes())["preference"]["evening"]["disabled"] is False


def test_silently_skipped_managed_save_cannot_ack(state, monkeypatch):
    monkeypatch.setattr(ws, "save_config", lambda _, **_kwargs: None)
    with pytest.raises(ws.HTTPException) as error:
        asyncio.run(ws.set_dashboard_theme(ThemeSetBody(name="dark")))
    assert error.value.status_code == 409
    assert state[0]["dashboard"]["theme"] == "light"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_native_file_write_is_read_back_before_ack(tmp_path, monkeypatch, theme):
    import yaml
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(ws, "get_install_id", lambda: "a" * 32)
    ack = asyncio.run(ws.set_dashboard_theme(ThemeSetBody(name=theme)))["preference"]
    persisted = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert persisted["dashboard"]["theme"] == theme
    assert persisted["dashboard"]["evening_prompt"]["disabled"] is (theme == "dark")
    assert ack == asyncio.run(ws.get_dashboard_themes())["preference"]
