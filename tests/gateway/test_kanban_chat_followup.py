"""K21-299: a card's answer follows the owner's chat across a session reset."""

import asyncio
from pathlib import Path

from gateway.config import Platform
from gateway.run import GatewayRunner
from korra_cli import kanban_db as kb
from korra_constants import get_hermes_home
from korra_state import SessionDB

from tests.gateway.test_kanban_notifier_apiserver_wake import (
    ApiServerLikeAdapter,
    _make_runner,
    _run_one_notifier_tick,
)

KINDS = ["completed", "blocked", "gave_up", "crashed", "timed_out"]


def _sessions(*rows):
    db = SessionDB(db_path=Path(get_hermes_home()) / "state.db")
    for sid, kwargs, ended in rows:
        db.create_session(sid, "api_server", **kwargs)
        if ended:
            db.end_session(sid, ended)
    db.close()


def _card(chat_id="s1", notifier_profile=None):
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="ask owner", assignee="worker", session_id="worker-s")
        kb.add_notify_sub(conn, task_id=tid, platform="api_server", chat_id=chat_id,
                          notifier_profile=notifier_profile)
        kb.complete_task(conn, tid, summary="done")
        return tid
    finally:
        conn.close()


def _subs(tid):
    conn = kb.connect()
    try:
        return [(s["chat_id"], s["last_event_id"]) for s in kb.list_notify_subs(conn)
                if s["task_id"] == tid]
    finally:
        conn.close()


def _tick(monkeypatch):
    posts = []

    async def fake_self_post(adapter, *, text, session_id):
        posts.append(session_id)

    import gateway.wake as wake_mod

    monkeypatch.setattr(wake_mod, "_self_post_chat_completion", fake_self_post)
    runner = _make_runner({Platform.API_SERVER: ApiServerLikeAdapter()})
    asyncio.run(_run_one_notifier_tick(monkeypatch, runner))
    return posts


def _reset_pair(child_cfg=None):
    _sessions(
        ("s1", {"session_key": "key-a", "chat_id": "chat"}, "session_reset"),
        ("s2", {"session_key": "key-a", "chat_id": "chat", "parent_session_id": "s1",
                "model_config": child_cfg or {"_reset_from": "s1"}}, None),
    )


def test_answer_goes_to_new_session_after_reset(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "k.db"))
    kb.init_db()
    _reset_pair()
    tid = _card("s1")

    assert _tick(monkeypatch) == ["s2"]
    rows = _subs(tid)
    assert [r[0] for r in rows] == ["s2"] and rows[0][1] > 0
    assert _tick(monkeypatch) == []


def test_compression_tail_receives_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "k.db"))
    kb.init_db()
    _sessions(
        ("s1", {"session_key": "key-a"}, "compression"),
        ("s2", {"session_key": "key-a", "parent_session_id": "s1"}, None),
    )
    _card("s1")
    assert _tick(monkeypatch) == ["s2"]


def test_unlinked_new_chat_is_not_guessed(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "k.db"))
    kb.init_db()
    _sessions(
        ("s1", {"session_key": "key-a", "chat_id": "chat"}, "session_reset"),
        ("web-new", {"session_key": "key-b", "chat_id": "chat"}, None),
    )
    tid = _card("s1")
    assert _tick(monkeypatch) == ["s1"]
    assert [r[0] for r in _subs(tid)] == ["s1"]


def test_explicit_fork_is_not_a_successor(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "k.db"))
    kb.init_db()
    _reset_pair(child_cfg={"_branched_from": "s1"})
    _card("s1")
    assert _tick(monkeypatch) == ["s1"]


def test_other_profile_database_is_not_searched(tmp_path, monkeypatch):
    from gateway.kanban_watchers import _follow_owner_session

    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "k.db"))
    kb.init_db()
    _reset_pair()
    tid = _card("s1", notifier_profile="other")
    conn = kb.connect()
    try:
        sub = [s for s in kb.list_notify_subs(conn) if s["task_id"] == tid][0]
        out = _follow_owner_session(kb, conn, sub, "default", KINDS)
    finally:
        conn.close()
    assert out["chat_id"] == "s1"
    assert [r[0] for r in _subs(tid)] == ["s1"]


def test_reroute_keeps_cursor_and_merges_existing_route(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "k.db"))
    kb.init_db()
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="t", assignee="w")
        kb.add_notify_sub(conn, task_id=tid, platform="api_server", chat_id="s1")
        kb.add_notify_sub(conn, task_id=tid, platform="api_server", chat_id="s2")
        conn.execute("UPDATE kanban_notify_subs SET last_event_id = 7 WHERE chat_id = 's1'")
        conn.execute("UPDATE kanban_notify_subs SET last_event_id = 3 WHERE chat_id = 's2'")
        conn.commit()
        assert kb.reroute_notify_sub(conn, task_id=tid, platform="api_server",
                                     chat_id="s1", new_chat_id="s2")
        assert kb.reroute_notify_sub(conn, task_id=tid, platform="api_server",
                                     chat_id="s1", new_chat_id="s2") is False
    finally:
        conn.close()
    assert _subs(tid) == [("s2", 7)]
