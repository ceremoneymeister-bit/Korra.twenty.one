"""K21-299 / K21-290: a task born in a worker turn keeps the owner's origin."""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def kb_home(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "test-worker")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("HERMES_SESSION_KEY", raising=False)
    from pathlib import Path as _Path
    monkeypatch.setattr(_Path, "home", lambda: tmp_path)
    from korra_cli import kanban_db as kb
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    return kb


def _owner_card(kb, *, chat=True):
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="owner card", assignee="worker",
                             session_id="owner-chat" if chat else None)
        if chat:
            kb.add_notify_sub(conn, task_id=tid, platform="api_server", chat_id="owner-chat",
                              notifier_profile="main", delivery_mode="notify+wake")
        kb.claim_task(conn, tid)
        return tid
    finally:
        conn.close()


def _subs(kb, tid):
    conn = kb.connect()
    try:
        return [(s["platform"], s["chat_id"], s["notifier_profile"], s["last_event_id"])
                for s in kb.list_notify_subs(conn) if s["task_id"] == tid]
    finally:
        conn.close()


def test_subtask_without_parents_inherits_owner_chat(kb_home, monkeypatch):
    kb = kb_home
    creator = _owner_card(kb)
    monkeypatch.setenv("HERMES_KANBAN_TASK", creator)
    monkeypatch.setenv("HERMES_SESSION_ID", "worker-own-session")
    from tools import kanban_tools as kt
    out = json.loads(kt._handle_create({"title": "child", "assignee": "other"}))
    child = out["task_id"]
    assert out["subscribed"] is True
    conn = kb.connect()
    try:
        assert kb.get_task(conn, child).session_id == "owner-chat"
    finally:
        conn.close()
    rows = _subs(kb, child)
    assert [r[:3] for r in rows] == [("api_server", "owner-chat", "main")]
    assert rows[0][3] >= 1


def test_card_without_owner_chat_gives_child_no_invented_chat(kb_home, monkeypatch):
    kb = kb_home
    creator = _owner_card(kb, chat=False)
    monkeypatch.setenv("HERMES_KANBAN_TASK", creator)
    from tools import kanban_tools as kt
    out = json.loads(kt._handle_create({"title": "child", "assignee": "other"}))
    assert out["subscribed"] is False
    assert _subs(kb, out["task_id"]) == []


def test_cli_creation_without_chat_is_not_subscribed(kb_home):
    from tools import kanban_tools as kt
    out = json.loads(kt._handle_create({"title": "cron card", "assignee": "default"}))
    assert "task_id" in out, out
    assert out["subscribed"] is False


def test_worker_decision_belongs_to_the_chat_that_handed_over_the_card(kb_home, monkeypatch):
    kb = kb_home
    creator = _owner_card(kb)
    monkeypatch.setenv("HERMES_KANBAN_TASK", creator)
    from tools.send_message_tool import _decision_session_identity
    session_id, _key, _profile, _owner = _decision_session_identity()
    assert session_id == "owner-chat"
    explicit, *_ = _decision_session_identity(source_session_id="cron:job:run")
    assert explicit == "cron:job:run"


def test_outside_a_worker_the_decision_keeps_its_own_session(kb_home, monkeypatch):
    from tools.send_message_tool import _decision_session_identity
    monkeypatch.setenv("HERMES_SESSION_ID", "plain-session")
    session_id, *_ = _decision_session_identity()
    assert session_id != "owner-chat"
