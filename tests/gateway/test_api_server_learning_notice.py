"""K21-294: итог фонового разбора и /refine виден в истории веб-сессии."""

import datetime as dt
import json
import threading
from unittest.mock import MagicMock

import pytest

import run_agent as run_agent_module
from agent.learning_receipt import DISPLAY_KIND, RECEIPT_KEY
from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from korra_state import SessionDB
from run_agent import AIAgent

SKILL = """---
name: weekly-report
description: weekly report
---

# Weekly report

Body.
"""


class _ImmediateThread:
    def __init__(self, *, target, daemon=None, name=None):
        self._target = target

    def start(self):
        self._target()


@pytest.fixture
def env(tmp_path, monkeypatch):
    from agent import skill_utils
    from tools import memory_tool, skill_ledger, skill_manager_tool, skill_usage

    home = tmp_path / "home"
    skills_dir = home / "skills"
    skills_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / "config.yaml").write_text("auxiliary:\n  background_review:\n    enabled: true\n")
    monkeypatch.setattr(skill_ledger, "get_hermes_home", lambda: home)
    monkeypatch.setattr(skill_usage, "get_hermes_home", lambda: home)
    monkeypatch.setattr(memory_tool, "get_hermes_home", lambda: home)
    monkeypatch.setattr(skill_manager_tool, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_utils, "get_all_skills_dirs", lambda: [skills_dir])
    monkeypatch.setattr(run_agent_module.threading, "Thread", _ImmediateThread)
    db = SessionDB(db_path=home / "state.db")
    db.create_session("web-1", "api_server")
    yield {"home": home, "db": db}
    db.close()


def _agent(db, notifications="on"):
    agent = object.__new__(AIAgent)
    agent.model = "fake"
    agent.platform = "api_server"
    agent.provider = "openai"
    agent.base_url = ""
    agent.api_key = ""
    agent.api_mode = ""
    agent.session_id = "web-1"
    agent._parent_session_id = ""
    agent._credential_pool = None
    agent._memory_store = object()
    agent._memory_enabled = True
    agent._user_profile_enabled = False
    agent._cached_system_prompt = "cached"
    agent.session_start = dt.datetime(2026, 1, 1, 12, 0, 0)
    agent._MEMORY_REVIEW_PROMPT = "m"
    agent._SKILL_REVIEW_PROMPT = "s"
    agent._COMBINED_REVIEW_PROMPT = "ms"
    agent.background_review_callback = None
    agent.status_callback = None
    agent._safe_print = lambda *a, **k: None
    agent._background_review_agent = None
    agent._background_review_run = None
    agent._background_review_lock = threading.Lock()
    agent._active_children = []
    agent._active_children_lock = threading.Lock()
    agent._session_db = db
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "k" * 32}))
    adapter._wire_learning_notice(agent, {"display": {"memory_notifications": notifications}})
    return agent


def _fake_review(monkeypatch, work):
    class FakeReviewAgent:
        def __init__(self, **kwargs):
            self._session_messages = []

        def run_conversation(self, **kwargs):
            self._session_messages = work()

        def release_clients(self):
            pass

    monkeypatch.setattr(run_agent_module, "AIAgent", FakeReviewAgent)


def _skill_write():
    from tools.skill_manager_tool import skill_manage

    raw = skill_manage(action="create", name="weekly-report", content=SKILL)
    return [
        {"role": "assistant", "tool_calls": [{"id": "c1", "function": {
            "name": "skill_manage",
            "arguments": json.dumps({"action": "create", "name": "weekly-report"}),
        }}]},
        {"role": "tool", "tool_call_id": "c1", "content": raw},
    ]


def _rows(db):
    return [m for m in db.get_messages("web-1")]


def _review(agent):
    AIAgent._spawn_background_review(
        agent,
        messages_snapshot=[{"role": "user", "content": "поправка"}],
        review_skills=True,
        manual=True,
    )


def test_review_that_saved_a_skill_adds_exactly_one_notice(env, monkeypatch):
    _fake_review(monkeypatch, _skill_write)
    agent = _agent(env["db"])
    _review(agent)

    rows = _rows(env["db"])
    assert len(rows) == 1
    row = rows[0]
    assert row["role"] == "assistant"
    assert row["display_kind"] == DISPLAY_KIND
    assert "«weekly-report»" in row["content"]
    receipt = row["display_metadata"][RECEIPT_KEY]
    assert receipt["skills"][0]["entry_ids"]
    assert agent.background_review_receipt is None


def test_review_that_saved_nothing_adds_no_message(env, monkeypatch):
    _fake_review(monkeypatch, lambda: [])
    _review(_agent(env["db"]))
    assert _rows(env["db"]) == []


def test_memory_notifications_off_keeps_the_chat_quiet(env, monkeypatch):
    _fake_review(monkeypatch, _skill_write)
    _review(_agent(env["db"], notifications="off"))
    assert _rows(env["db"]) == []


def test_memory_only_review_names_memory(env, monkeypatch):
    from tools.memory_tool import load_on_disk_store

    def write_memory():
        text = "Отчёты сдаём по пятницам"
        raw = json.dumps(load_on_disk_store().add("memory", text))
        return [
            {"role": "assistant", "tool_calls": [{"id": "m1", "function": {
                "name": "memory",
                "arguments": json.dumps({"action": "add", "target": "memory", "content": text}),
            }}]},
            {"role": "tool", "tool_call_id": "m1", "content": raw},
        ]

    _fake_review(monkeypatch, write_memory)
    _review(_agent(env["db"]))
    rows = _rows(env["db"])
    assert len(rows) == 1
    assert "память" in rows[0]["content"]
    assert rows[0]["display_metadata"][RECEIPT_KEY]["memory"][0]["added"] == [
        "Отчёты сдаём по пятницам"
    ]


def test_callback_is_not_set_without_a_session_db():
    agent = MagicMock()
    agent._session_db = None
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "k" * 32}))
    adapter._wire_learning_notice(agent, {})
    assert agent.memory_notifications == "on"
