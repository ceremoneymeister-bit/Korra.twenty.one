"""The owner's board in chat is for turns that act for the owner (review R2, 23.09).

`kanban.chat_tools=main` lets the main agent plan on the board; it must not
hand the owner's cards to a visitor of the main agent's public bot. The
dispatcher-owned worker path is unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def board(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    for name in ("HERMES_KANBAN_TASK", "KORRA_KANBAN_TASK", "HERMES_KANBAN_BOARD", "KORRA_KANBAN_BOARD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from korra_cli import kanban_db as kb

    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="Private owner board task", assignee="default")
    finally:
        conn.close()
    from tools import kanban_tools as kt

    config = {"kanban": {"chat_tools": "main"}}
    monkeypatch.setattr("korra_cli.config.load_config", lambda: config)
    monkeypatch.setattr("korra_cli.profiles.get_active_profile_name", lambda: "default")
    return kt, tid, config


def _turn(session: dict):
    from gateway.session_context import set_session_vars

    return set_session_vars(cron_session=session.pop("cron_session", ""), **session)


OUTSIDER = {"platform": "telegram", "chat_type": "dm", "chat_id": "777", "user_id": "777"}
OWNER_DM = {"platform": "telegram", "chat_type": "dm", "chat_id": "42", "user_id": "42", "owner_principal": "live"}
GROUP = {"platform": "telegram", "chat_type": "group", "chat_id": "-1", "user_id": "42", "owner_principal": "live"}


@pytest.mark.parametrize(
    "session,allowed",
    [
        (OUTSIDER, False),
        (GROUP, False),
        ({"platform": "webhook", "chat_id": "hook"}, False),
        (OWNER_DM, True),
        ({"platform": "api_server"}, True),  # the owner's cabinet chat
        ({}, True),  # the owner's CLI/TUI: no messaging platform bound
    ],
)
def test_board_tools_follow_the_principal(board, session, allowed):
    from gateway.session_context import clear_session_vars

    kt, tid, _ = board
    tokens = _turn(dict(session))
    try:
        assert kt._check_kanban_mode() is allowed
        assert kt._check_kanban_orchestrator_mode() is allowed
        shown = kt._handle_show({"task_id": tid})
        assert ("Private owner board task" in shown) is allowed
        if not allowed:
            assert "belongs to the owner" in json.loads(shown)["error"]
    finally:
        clear_session_vars(tokens)


def test_public_bot_visitor_cannot_create_or_unblock_on_the_owners_board(board):
    """The review's `public-kanban`, with the safe expected result."""
    from gateway.session_context import clear_session_vars
    from korra_cli import kanban_db as kb

    kt, tid, _ = board
    tokens = _turn(dict(OUTSIDER))
    try:
        created = json.loads(kt._handle_create({"title": "Visitor task", "assignee": "default"}))
        listed = json.loads(kt._handle_list({}))
        unblocked = json.loads(kt._handle_unblock({"task_id": tid}))
    finally:
        clear_session_vars(tokens)
    assert all("belongs to the owner" in answer["error"] for answer in (created, listed, unblocked))
    conn = kb.connect()
    try:
        assert [t.title for t in kb.list_tasks(conn)] == ["Private owner board task"]
    finally:
        conn.close()


def test_explicit_kanban_toolset_does_not_open_the_board_to_visitors(board, monkeypatch):
    from gateway.session_context import clear_session_vars

    kt, _, _ = board
    monkeypatch.setattr("korra_cli.config.load_config", lambda: {"toolsets": ["kanban"]})
    tokens = _turn(dict(OUTSIDER))
    try:
        assert kt._check_kanban_orchestrator_mode() is False
    finally:
        clear_session_vars(tokens)


def test_scheduled_jobs_use_the_board_only_when_the_owner_created_them(board):
    from gateway.session_context import clear_session_vars, reset_background_owner, set_background_owner

    kt, _, _ = board
    for verdict in (True, False):
        token = set_background_owner(verdict)
        tokens = _turn({"cron_session": "1"})
        try:
            assert kt._check_kanban_orchestrator_mode() is verdict
        finally:
            clear_session_vars(tokens)
            reset_background_owner(token)


def test_dispatcher_worker_path_is_unchanged(board, monkeypatch):
    from gateway.session_context import clear_session_vars

    kt, tid, _ = board
    monkeypatch.setenv("KORRA_KANBAN_TASK", tid)
    tokens = _turn(dict(OUTSIDER))  # stray session state must not matter to a worker
    try:
        assert kt._check_kanban_mode() is True
        assert kt._check_kanban_orchestrator_mode() is False
        assert "Private owner board task" in kt._handle_show({})
    finally:
        clear_session_vars(tokens)


def test_board_schema_is_not_shared_between_principals(board):
    import model_tools
    from gateway.session_context import clear_session_vars
    from tools.registry import invalidate_check_fn_cache

    invalidate_check_fn_cache()
    model_tools._clear_tool_defs_cache()

    def names(session) -> set[str]:
        tokens = _turn(dict(session))
        try:
            return {d["function"]["name"] for d in model_tools.get_tool_definitions(
                enabled_toolsets=["kanban"], quiet_mode=True)}
        finally:
            clear_session_vars(tokens)

    for _ in range(2):
        assert "kanban_create" in names(OWNER_DM)
        assert not any(name.startswith("kanban_") for name in names(OUTSIDER))
    model_tools._clear_tool_defs_cache()
