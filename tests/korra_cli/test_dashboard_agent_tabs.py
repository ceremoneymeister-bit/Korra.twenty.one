"""K21-116: shared agent-tab layout with server-side revision conflicts."""

from __future__ import annotations

import asyncio
import copy

import pytest
from fastapi import HTTPException

from korra_cli import web_server as ws
from korra_cli.dashboard_agent_tabs import MAX_AGENT_TABS, preference, updated
from korra_cli.dashboard_auth.public_paths import PUBLIC_API_PATHS
from korra_cli.web_models import AgentTabsSetBody


@pytest.fixture
def tab_state(monkeypatch):
    state = {"dashboard": {"theme": "dark"}, "unrelated": {"keep": True}}
    writes = []
    monkeypatch.setattr(ws, "load_config", lambda: copy.deepcopy(state))

    def save(config, **kwargs):
        writes.append((copy.deepcopy(config), kwargs))
        state.clear()
        state.update(copy.deepcopy(config))

    monkeypatch.setattr(ws, "save_config", save)
    return state, writes


def test_normalization_never_hides_main_or_accepts_invalid_profiles():
    value = preference({
        "dashboard": {
            "agent_tabs": {
                "revision": -4,
                "initialized": True,
                "order": ["writer", "", "../escape", "writer", "analyst"],
                "hidden": ["", "analyst", "ghost", "../escape"],
            }
        }
    })
    assert value == {
        "version": 1,
        "revision": 0,
        "initialized": True,
        "order": ["writer", "", "analyst"],
        "hidden": ["analyst"],
    }


def test_update_adds_main_caps_layout_and_increments_server_revision():
    value = updated(
        {"revision": 12},
        order=[f"agent-{index}" for index in range(20)],
        hidden=["agent-2", "missing", ""],
    )
    assert value["revision"] == 13
    assert value["initialized"] is True
    assert value["order"][0] == ""
    assert len(value["order"]) == MAX_AGENT_TABS
    assert value["hidden"] == ["agent-2"]


def test_endpoint_round_trip_preserves_unrelated_config_and_rejects_stale_writer(tab_state):
    state, writes = tab_state
    initial = asyncio.run(ws.get_dashboard_agent_tabs())
    assert initial["revision"] == 0 and initial["initialized"] is False

    saved = asyncio.run(ws.set_dashboard_agent_tabs(AgentTabsSetBody(
        revision=0,
        order=["writer", "", "analyst"],
        hidden=["analyst"],
    )))
    assert saved == {
        "version": 1,
        "revision": 1,
        "initialized": True,
        "order": ["writer", "", "analyst"],
        "hidden": ["analyst"],
    }
    assert state["dashboard"]["theme"] == "dark"
    assert state["unrelated"] == {"keep": True}
    assert len(writes) == 1

    with pytest.raises(HTTPException) as conflict:
        asyncio.run(ws.set_dashboard_agent_tabs(AgentTabsSetBody(
            revision=0,
            order=["", "analyst", "writer"],
            hidden=[],
        )))
    assert conflict.value.status_code == 409
    assert asyncio.run(ws.get_dashboard_agent_tabs()) == saved
    assert len(writes) == 1


def test_agent_tab_layout_is_not_a_public_dashboard_endpoint():
    assert "/api/dashboard/agent-tabs" not in PUBLIC_API_PATHS
