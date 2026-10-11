"""kanban_show stays inside a size budget and says how to read the rest."""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def card(monkeypatch, tmp_path):
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
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="long history", assignee="test-worker",
                             body="B" * 9000, acceptance="owner")
    finally:
        conn.close()
    return kb, tid


def _bloat(kb, tid):
    conn = kb.connect()
    try:
        for i in range(60):
            kb.add_comment(conn, tid, "agent", f"comment {i} " + "x" * 3000)
        kb.claim_task(conn, tid)
        kb.block_task(conn, tid, reason="Нужен ваш выбор поставщика", kind="needs_input")
        revision = kb.block_revision(conn, tid)
        kb.respond_to_block(
            conn, tid, answer="Берём второго", author="Владелец", request_id="r1",
            revision=revision, source={"kind": "telegram", "chat": "owner-dm"},
        )
        kb.claim_task(conn, tid)
        kb.block_task(conn, tid, reason="Нужен ваш выбор срока " + "R" * 5000, kind="needs_input")
    finally:
        conn.close()


def test_long_history_fits_the_budget_and_tells_how_to_read_more(card):
    kb, tid = card
    _bloat(kb, tid)
    from tools import kanban_tools as kt
    raw = kt._handle_show({"task_id": tid})
    assert len(raw) <= 12000
    d = json.loads(raw)
    assert d["truncated"] is True
    assert d["counts"]["comments"]["total"] == 61
    assert d["counts"]["comments"]["shown"] < 61
    assert "section=" in d["read_more"]
    assert d["truncated_fields"]["body"] == 9000


def test_blocker_revision_and_owner_source_survive_truncation(card):
    kb, tid = card
    _bloat(kb, tid)
    from tools import kanban_tools as kt
    d = json.loads(kt._handle_show({"task_id": tid}))
    task = d["task"]
    conn = kb.connect()
    try:
        assert task["block_revision"] == kb.block_revision(conn, tid)
    finally:
        conn.close()
    assert task["block_kind"] == "needs_input"
    assert task["block_reason"].startswith("Нужен ваш выбор срока")
    source = d["last_owner_decision"]["source"]
    assert (source["kind"], source["chat"]) == ("telegram", "owner-dm")
    assert d["last_owner_decision"]["answer"] == "Берём второго"


def test_section_pages_the_part_that_was_cut(card):
    kb, tid = card
    _bloat(kb, tid)
    from tools import kanban_tools as kt
    first = json.loads(kt._handle_show({"task_id": tid, "section": "comments", "limit": 25}))
    assert len(first["items"]) == 25 and first["next_offset"] == 25 and first["total"] == 61
    last = json.loads(kt._handle_show({"task_id": tid, "section": "comments", "offset": 50, "limit": 25}))
    assert len(last["items"]) == 11 and last["next_offset"] is None
    body = json.loads(kt._handle_show({"task_id": tid, "section": "body", "offset": 8500, "limit": 2000}))
    assert body["text"] == "B" * 500 and body["next_offset"] is None
    assert body["total_chars"] == 9000


def test_max_chars_raises_the_budget(card):
    kb, tid = card
    _bloat(kb, tid)
    from tools import kanban_tools as kt
    small = json.loads(kt._handle_show({"task_id": tid}))
    big = json.loads(kt._handle_show({"task_id": tid, "max_chars": 60000}))
    assert big["counts"]["comments"]["shown"] > small["counts"]["comments"]["shown"]


def test_short_card_is_complete_and_not_marked_truncated(card):
    kb, _ = card
    conn = kb.connect()
    try:
        tid = kb.create_task(conn, title="short", assignee="test-worker")
    finally:
        conn.close()
    from tools import kanban_tools as kt
    d = json.loads(kt._handle_show({"task_id": tid}))
    assert d["truncated"] is False and d["read_more"] is None
    assert d["task"]["stage"] == "ready"


def test_stage_separates_working_acceptance_and_accepted(card):
    kb, tid = card
    from tools import kanban_tools as kt
    conn = kb.connect()
    try:
        kb.claim_task(conn, tid)
        working = json.loads(kt._handle_show({"task_id": tid}))["task"]
        assert working["stage"] == "executor_working"
        assert kb.complete_task(conn, tid, summary="сделано", result="итог", as_worker=True)
        waiting = json.loads(kt._handle_show({"task_id": tid}))["task"]
        assert waiting["status"] == "review"
        assert waiting["stage"] == "version_on_acceptance"
        assert waiting["submitted_version"] == kb.submitted_version(conn, tid)
        assert "not final" in waiting["stage_note"]
        kb.accept_result(conn, tid, author="Владелец", version=waiting["submitted_version"],
                         request_id="a1")
        done = json.loads(kt._handle_show({"task_id": tid}))["task"]
        assert done["stage"] == "accepted"
        assert "does not prove" in done["stage_note"]
    finally:
        conn.close()
