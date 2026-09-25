"""Who may change agents from chat: the main agent, for the owner speaking live."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def tool(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / ".hermes"
    root.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.delenv("KORRA_SINGLE_QUERY_SESSION", raising=False)
    monkeypatch.delenv("HERMES_SINGLE_QUERY_SESSION", raising=False)
    for name in ("HERMES_KANBAN_TASK", "KORRA_KANBAN_TASK"):
        monkeypatch.delenv(name, raising=False)
    from korra_cli.profiles import create_profile

    create_profile("lawyer", no_alias=True, no_skills=True, display_name="Юрист", soul="Ты юрист.\n")
    config: dict = {}
    monkeypatch.setattr("korra_cli.config.load_config", lambda: config)
    active = {"name": "default"}
    monkeypatch.setattr("korra_cli.profiles.get_active_profile_name", lambda: active["name"])
    from tools import manage_agents_tool as mat

    return mat, root, config, active


def _turn(session: dict):
    from gateway.session_context import set_session_vars

    return set_session_vars(cron_session=session.pop("cron_session", ""), **session)


OWNER_DM = {"platform": "telegram", "chat_type": "dm", "chat_id": "42", "user_id": "42", "owner_principal": "live"}
OUTSIDER = {"platform": "telegram", "chat_type": "dm", "chat_id": "777", "user_id": "777"}
GROUP = {"platform": "telegram", "chat_type": "group", "chat_id": "-1", "user_id": "42", "owner_principal": "live"}
SYSTEM_IN_OWNER_DM = {"platform": "telegram", "chat_type": "dm", "chat_id": "42", "user_id": "42",
                      "owner_principal": "delegated"}


@pytest.mark.parametrize(
    "session,allowed",
    [
        ({"platform": "api_server"}, True),  # the owner's cabinet chat
        ({}, True),  # the owner's own computer (CLI/TUI)
        (OWNER_DM, True),
        (OUTSIDER, False),
        (GROUP, False),
        (SYSTEM_IN_OWNER_DM, False),  # acts for the owner, but nobody is asking
        ({"platform": "webhook", "chat_id": "hook"}, False),
        ({"cron_session": "1"}, False),
    ],
)
def test_only_the_owner_speaking_live_gets_the_tool(tool, session, allowed):
    from gateway.session_context import clear_session_vars

    mat, root, _, _ = tool
    tokens = _turn(dict(session))
    try:
        assert mat._available() is allowed
        answer = json.loads(mat._handle({"action": "update_role", "agent": "lawyer", "content": "Взлом.\n"}))
    finally:
        clear_session_vars(tokens)
    if not allowed:
        assert "owner" in answer["error"]
    assert ((root / "profiles" / "lawyer" / "SOUL.md").read_text(encoding="utf-8") == "Взлом.\n") is allowed


def test_other_agents_one_shot_runs_children_and_the_off_switch_are_refused(tool, monkeypatch):
    from agent.delegation_context import delegated_child_context
    from gateway.session_context import clear_session_vars

    mat, _, config, active = tool
    tokens = _turn({"platform": "api_server"})
    try:
        assert mat._available() is True
        active["name"] = "lawyer"
        assert mat._available() is False
        active["name"] = "default"
        with delegated_child_context():
            assert mat._available() is False
        config["agent"] = {"manage_profiles": "off"}
        assert mat._available() is False
        assert "turned off" in json.loads(mat._handle({"action": "list"}))["error"]
    finally:
        clear_session_vars(tokens)
    config.clear()
    monkeypatch.setenv("KORRA_SINGLE_QUERY_SESSION", "1")
    assert mat._available() is False


def test_owner_edits_an_agent_through_the_tool_and_can_undo(tool):
    from gateway.session_context import clear_session_vars

    mat, root, _, _ = tool
    tokens = _turn({"platform": "api_server"})
    try:
        shown = json.loads(mat._handle({"action": "show", "agent": "Юрист"}))
        done = json.loads(mat._handle({"action": "update_role", "agent": "Юрист", "old_text": "Ты юрист.",
                                       "new_text": "Ты юрист «Награды».", "version": shown["role_version"],
                                       "reason": "уточнить компанию"}))
        assert done["ok"] and (root / "profiles" / "lawyer" / "SOUL.md").read_text(encoding="utf-8") == "Ты юрист «Награды».\n"
        stale = json.loads(mat._handle({"action": "update_role", "agent": "lawyer", "content": "Другое.\n",
                                        "version": shown["role_version"]}))
        assert stale["error"] == "conflict"
        json.loads(mat._handle({"action": "undo", "change_id": done["change_id"]}))
        history = json.loads(mat._handle({"action": "history"}))["changes"]
    finally:
        clear_session_vars(tokens)
    assert (root / "profiles" / "lawyer" / "SOUL.md").read_text(encoding="utf-8") == "Ты юрист.\n"
    assert [c["kind"] for c in history] == ["undo", "role"]


def test_the_tool_is_wired_for_chat_surfaces_and_never_for_delegated_children():
    from tools.delegate_tool import DELEGATE_BLOCKED_TOOLS
    from toolsets import _HERMES_CORE_TOOLS, resolve_toolset

    assert "manage_agents" in DELEGATE_BLOCKED_TOOLS
    assert "manage_agents" in _HERMES_CORE_TOOLS
    assert "manage_agents" in resolve_toolset("hermes-api-server")
    assert resolve_toolset("agent_profiles") == ["manage_agents"]
