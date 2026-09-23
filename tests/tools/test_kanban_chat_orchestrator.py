"""The main agent plans on the board from chat (K21-142)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def chat_env(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    (home / "profiles" / "pm").mkdir(parents=True)
    (home / "profiles" / "pm" / "config.yaml").write_text("{}\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("KORRA_KANBAN_TASK", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from korra_cli import kanban_db as kb
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    return home


def _mode(monkeypatch, value, profile="default"):
    from tools import kanban_tools as kt
    monkeypatch.setattr(kt, "load_config", lambda: {"kanban": {"chat_tools": value}}, raising=False)
    monkeypatch.setattr("korra_cli.config.load_config", lambda: {"kanban": {"chat_tools": value}})
    monkeypatch.setattr("korra_cli.profiles.get_active_profile_name", lambda: profile)
    return kt


def test_main_agent_gets_the_board_by_default(chat_env, monkeypatch):
    kt = _mode(monkeypatch, "main")
    assert kt._profile_has_kanban_toolset() is True
    assert kt._check_kanban_orchestrator_mode() is True


def test_specialist_profile_does_not_by_default(chat_env, monkeypatch):
    kt = _mode(monkeypatch, "main", profile="designer")
    assert kt._profile_has_kanban_toolset() is False


@pytest.mark.parametrize("value,profile,expected", [
    ("off", "default", False), ("all", "designer", True),
])
def test_chat_tools_setting(chat_env, monkeypatch, value, profile, expected):
    kt = _mode(monkeypatch, value, profile=profile)
    assert kt._profile_has_kanban_toolset() is expected


def test_unknown_agent_returns_the_list_of_agents(chat_env, monkeypatch):
    from tools import kanban_tools as kt
    monkeypatch.setattr(kt, "_known_profiles", lambda: [("default", "Корра"), ("pm", "Продакт-менеджер")])
    out = json.loads(kt._handle_create({"title": "t", "assignee": "manager"}))
    assert "unknown assignee" in out["error"]
    assert "pm (Продакт-менеджер)" in out["error"]
    ok = json.loads(kt._handle_create({"title": "t", "assignee": "pm", "plan_title": "План"}))
    assert ok["ok"] is True


def test_agent_cannot_mark_the_owners_step_done(chat_env):
    from tools import kanban_tools as kt
    from korra_cli import kanban_db as kb
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="Обучить команду", actor_kind="human")
    finally:
        conn.close()
    out = json.loads(kt._handle_complete({"task_id": tid, "summary": "обучил"}))
    assert "owner's own step" in out["error"]


def test_chat_agent_gets_chat_guidance_not_worker_protocol(monkeypatch):
    from agent.prompt_builder import KANBAN_CHAT_GUIDANCE, KANBAN_GUIDANCE
    assert "Plan in one pass" in KANBAN_CHAT_GUIDANCE
    assert "ONE task" not in KANBAN_CHAT_GUIDANCE
    assert len(KANBAN_CHAT_GUIDANCE) < 3000
    assert "full deliverable text in `result`" in KANBAN_GUIDANCE
