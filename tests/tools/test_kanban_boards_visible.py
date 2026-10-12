"""K21-302: kanban tool answers name the board and the agent sees the others."""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def two_boards(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", "test-worker")
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    from pathlib import Path as _Path
    monkeypatch.setattr(_Path, "home", lambda: tmp_path)
    from korra_cli import kanban_db as kb
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    kb.create_board("suppliers")
    conn = kb.connect(board="suppliers")
    try:
        far = kb.create_task(conn, title="supplier card", assignee="factory")
    finally:
        conn.close()
    conn = kb.connect()
    try:
        near = kb.create_task(conn, title="default card", assignee="factory")
    finally:
        conn.close()
    return kb, near, far


def test_list_names_board_and_other_boards_with_counts(two_boards):
    from tools import kanban_tools as kt
    d = json.loads(kt._handle_list({}))
    assert d["board"] == "default"
    assert d["other_boards"] == [{"board": "suppliers", "tasks": 1}]
    assert [t["title"] for t in d["tasks"]] == ["default card"]


def test_show_of_card_from_another_board_points_to_that_board(two_boards):
    _kb, _near, far = two_boards
    from tools import kanban_tools as kt
    miss = json.loads(kt._handle_show({"task_id": far}))
    assert "not found" in miss["error"]
    assert miss["board"] == "default"
    assert miss["found_on_boards"] == ["suppliers"]
    assert 'board="suppliers"' in miss["error"]
    hit = json.loads(kt._handle_show({"task_id": far, "board": "suppliers"}))
    assert hit["board"] == "suppliers"
    assert hit["task"]["id"] == far
    assert hit["other_boards"] == [{"board": "default", "tasks": 1}]


def test_unknown_card_lists_boards_with_counts(two_boards):
    from tools import kanban_tools as kt
    miss = json.loads(kt._handle_show({"task_id": "t_000000000000"}))
    assert "found_on_boards" not in miss
    assert miss["other_boards"] == [{"board": "suppliers", "tasks": 1}]


def test_mutation_answers_name_the_board(two_boards):
    _kb, near, _far = two_boards
    from tools import kanban_tools as kt
    d = json.loads(kt._handle_comment({"task_id": near, "body": "hello"}))
    assert d["board"] == "default"


def test_completion_does_not_flag_card_from_another_board_as_invented(two_boards):
    kb, near, far = two_boards
    conn = kb.connect()
    try:
        kb.claim_task(conn, near)
        assert kb.complete_task(conn, near, summary=f"handed over, see {far}")
        kinds = [e.kind for e in kb.list_events(conn, near)]
        assert "suspected_hallucinated_references" not in kinds
    finally:
        conn.close()


def test_completion_still_flags_really_unknown_reference(two_boards):
    kb, near, _far = two_boards
    conn = kb.connect()
    try:
        kb.claim_task(conn, near)
        assert kb.complete_task(conn, near, summary="see t_deadbeefcafe0")
        kinds = [e.kind for e in kb.list_events(conn, near)]
        assert "suspected_hallucinated_references" in kinds
    finally:
        conn.close()
